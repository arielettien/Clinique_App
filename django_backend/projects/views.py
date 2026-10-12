"""Projets : un espace de suivi separe, avec ses propres patients et formulaires."""
from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from cases.models import Case
from clinical_forms.models import Form
from core.audit import log_audit
from core.permissions import IsAdmin
from patients.models import Patient
from projects.models import Project
from submissions.models import Submission


def serialize_project(p: Project) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "start_date": p.start_date,
        "ngo_name": p.ngo_name,
        "main_donor": p.main_donor,
        "cover_image_id": p.cover_image_id,
        "created_at": p.created_at,
    }


def _apply_payload(project: Project, data: dict):
    name = (data.get("name") or "").strip()
    if not name:
        raise ValidationError("Le nom du projet est obligatoire.")
    project.name = name
    project.description = data.get("description")
    project.start_date = data.get("start_date") or None
    project.ngo_name = data.get("ngo_name")
    project.main_donor = data.get("main_donor")
    project.cover_image_id = data.get("cover_image_id")


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def projects_collection(request):
    if request.method == "GET":
        projects = Project.objects.order_by("-created_at")
        return Response([serialize_project(p) for p in projects])

    project = Project()
    _apply_payload(project, request.data)
    project.save()
    return Response(serialize_project(project), status=status.HTTP_201_CREATED)


@api_view(["GET", "PUT", "DELETE"])
@permission_classes([IsAuthenticated])
def project_detail(request, project_id):
    project = Project.objects.filter(pk=project_id).first()
    if not project:
        return Response({"detail": "Projet introuvable"}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        return Response(serialize_project(project))

    if request.method == "PUT":
        _apply_payload(project, request.data)
        project.save()
        return Response(serialize_project(project))

    # La suppression d'un projet est reservee aux administrateurs : elle
    # emporte tout son contenu et n'est pas annulable.
    if not IsAdmin().has_permission(request, None):
        return Response({"detail": "Action reservee aux administrateurs."},
                        status=status.HTTP_403_FORBIDDEN)
    return _delete_project(request, project)


@transaction.atomic
def _delete_project(request, project):
    # Aucune suppression en cascade n'est definie au niveau de la base : on
    # supprime nous-memes, dans l'ordre, tout ce qui depend du projet avant le
    # projet lui-meme (soumissions -> formulaires, cas, puis patients).
    project_id = project.id
    project_name = project.name

    form_ids = list(Form.objects.filter(project_id=project_id).values_list("id", flat=True))
    if form_ids:
        Submission.objects.filter(form_id__in=form_ids).delete()
        Form.objects.filter(project_id=project_id).delete()
    Case.objects.filter(project_id=project_id).delete()
    Patient.objects.filter(project_id=project_id).delete()
    project.delete()

    log_audit(request, user=request.user, action="delete", entity_type="project",
              entity_id=project_id, project_id=project_id,
              details=f"Projet supprime : {project_name}")
    return Response({"status": "ok"})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def project_stats(request, project_id):
    return Response({
        "patient_count": Patient.objects.filter(project_id=project_id).count(),
        "form_count": Form.objects.filter(project_id=project_id, is_active=True).count(),
    })
