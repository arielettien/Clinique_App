"""Exports et statistiques des dossiers de suivi."""
from datetime import datetime
from io import BytesIO

import pandas as pd
from django.http import FileResponse
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from cases.models import Case
from cases.services import serialize_case
from clinical_forms.models import Form
from core.audit import log_audit
from core.spreadsheet_io import slugify_py
from core.utils import age_at
from patients.models import Patient
from projects.models import Project
from submissions.models import Submission

XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _dataframe_to_bytes(df, export_format, sheet_name):
    if export_format == "xlsx":
        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name=sheet_name)
        buffer.seek(0)
        return buffer.read(), XLSX_CONTENT_TYPE, "xlsx"
    # Marque d'ordre des octets (BOM) + separateur ";" : c'est ce qui permet a
    # Excel en francais d'ouvrir le fichier avec les accents corrects et les
    # colonnes bien separees, sans passer par un assistant d'import.
    content = ("﻿" + df.to_csv(index=False, sep=";")).encode("utf-8")
    return content, "text/csv", "csv"


def _attachment(content, media_type, filename):
    response = FileResponse(BytesIO(content), content_type=media_type)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _bad_format(export_format):
    return Response(
        {"detail": f"Format d'export non reconnu : '{export_format}' (csv ou xlsx attendu)."},
        status=status.HTTP_400_BAD_REQUEST,
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def export_cases(request, project_id):
    """Export de la liste des dossiers de suivi — une ligne par DOSSIER
    (identite, statut, dates, motif de cloture). Les visites, elles, sont
    deja exportables par formulaire."""
    export_format = request.query_params.get("format", "csv")
    if export_format not in ("csv", "xlsx"):
        return _bad_format(export_format)

    project = Project.objects.filter(pk=project_id).first()
    if not project:
        return Response({"detail": "Projet introuvable"}, status=status.HTTP_404_NOT_FOUND)

    query = Case.objects.filter(project_id=project_id).select_related("patient")
    case_status = request.query_params.get("status")
    if case_status:
        query = query.filter(status=case_status)

    today = datetime.utcnow().date()
    rows = []
    for c in query.order_by("enrolled_at"):
        s = serialize_case(c)
        # Age a aujourd'hui, et non a une date de visite precise : un dossier
        # couvre une periode, pas un instant. Coherent avec l'age affiche a
        # l'ecran dans la liste des dossiers.
        age_value = age_at(s["patient_date_of_birth"], today)
        rows.append({
            "numero_dossier": s["case_number"],
            "patient": s["patient_name"],
            "code_anonyme": s["patient_anonymous_code"] or "",
            "age": age_value if age_value is not None else "",
            "sexe": s["patient_sex"] or "",
            "statut": s["status"],
            "inscrit_le": s["enrolled_at"].strftime("%Y-%m-%d") if s["enrolled_at"] else "",
            "derniere_visite": s["last_visit_at"].strftime("%Y-%m-%d") if s["last_visit_at"] else "",
            "nb_visites": s["visit_count"],
            "cloture_le": s["closed_at"].strftime("%Y-%m-%d") if s["closed_at"] else "",
            "motif_cloture": s["closed_reason"] or "",
        })

    columns = ["numero_dossier", "patient", "code_anonyme", "age", "sexe", "statut",
               "inscrit_le", "derniere_visite", "nb_visites", "cloture_le", "motif_cloture"]
    content, media_type, ext = _dataframe_to_bytes(
        pd.DataFrame(rows, columns=columns), export_format, "Dossiers")

    log_audit(request, user=request.user, action="export", entity_type="case_list",
              entity_id=project_id, project_id=project_id,
              details=f"Export de la liste des dossiers ({export_format}) — {len(rows)} ligne(s)")
    return _attachment(content, media_type, f"{slugify_py(project.name)}_dossiers.{ext}")


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def export_project_summary(request, project_id):
    """Fiche resume du projet (identite + compteurs). Pour un export complet
    des donnees recueillies, utiliser l'export par formulaire (Enquete) et
    l'export des dossiers (Suivi), disponibles separement."""
    export_format = request.query_params.get("format", "csv")
    if export_format not in ("csv", "xlsx"):
        return _bad_format(export_format)

    project = Project.objects.filter(pk=project_id).first()
    if not project:
        return Response({"detail": "Projet introuvable"}, status=status.HTTP_404_NOT_FOUND)

    form_ids = list(Form.objects.filter(project_id=project_id).values_list("id", flat=True))
    rows = [{
        "nom_projet": project.name,
        "description": project.description or "",
        "cree_le": project.created_at.strftime("%Y-%m-%d") if project.created_at else "",
        "nb_formulaires": len(form_ids),
        "nb_patients": Patient.objects.filter(project_id=project_id).count(),
        "nb_soumissions": (Submission.objects.filter(form_id__in=form_ids).count()
                           if form_ids else 0),
        "cas_actifs": Case.objects.filter(project_id=project_id, status="active").count(),
        "cas_clotures": Case.objects.filter(project_id=project_id, status="closed").count(),
        "cas_perdus_de_vue": Case.objects.filter(project_id=project_id, status="lost").count(),
    }]
    content, media_type, ext = _dataframe_to_bytes(
        pd.DataFrame(rows, columns=list(rows[0].keys())), export_format, "Resume")

    log_audit(request, user=request.user, action="export", entity_type="project_summary",
              entity_id=project_id, project_id=project_id,
              details=f"Export resume du projet ({export_format})")
    return _attachment(content, media_type, f"{slugify_py(project.name)}_resume.{ext}")


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def case_stats(request, project_id):
    active_cases = list(Case.objects.filter(project_id=project_id, status="active")
                        .select_related("patient"))
    overdue = sum(1 for c in active_cases if serialize_case(c)["is_overdue"])

    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    closed = Case.objects.filter(project_id=project_id, status="closed").count()
    lost = Case.objects.filter(project_id=project_id, status="lost").count()

    return Response({
        "active": len(active_cases),
        "overdue": overdue,
        "closed": closed,
        "lost": lost,
        "closed_this_month": Case.objects.filter(
            project_id=project_id, status__in=["closed", "lost"], closed_at__gte=month_start,
        ).count(),
        "total": len(active_cases) + closed + lost,
    })
