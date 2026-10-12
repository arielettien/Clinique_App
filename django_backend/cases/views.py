"""Dossiers de suivi : inscription d'un patient suivi dans le temps."""
from datetime import datetime

from django.db import IntegrityError
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from cases.models import Case
from cases.services import create_case_with_number, serialize_case
from core.audit import log_audit
from patients.models import Patient
from projects.models import Project
from submissions.models import Submission
from submissions.services import field_types_of
from submissions.views import _parse_datetime


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def project_cases(request, project_id):
    query = Case.objects.filter(project_id=project_id).select_related("patient")
    case_status = request.query_params.get("status")
    if case_status:
        query = query.filter(status=case_status)

    results = [serialize_case(c) for c in query.order_by("-enrolled_at")]

    search = request.query_params.get("search")
    if search:
        term = search.strip().lower()
        results = [r for r in results
                   if term in r["patient_name"].lower() or term in r["case_number"].lower()]

    overdue = request.query_params.get("overdue")
    if overdue is not None and overdue != "":
        want_overdue = str(overdue).lower() in ("true", "1")
        results = [r for r in results if r["is_overdue"] == want_overdue]

    if request.query_params.get("sort") == "activity":
        results.sort(key=lambda r: r["last_visit_at"] or r["enrolled_at"], reverse=True)

    return Response(results)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_case(request):
    data = request.data
    force = request.query_params.get("force", "").lower() == "true"
    project_id = data.get("project_id")
    patient_id = data.get("patient_id")

    if not Project.objects.filter(pk=project_id).exists():
        return Response({"detail": "Projet introuvable"}, status=status.HTTP_404_NOT_FOUND)
    patient = Patient.objects.filter(pk=patient_id).first()
    if not patient:
        return Response({"detail": "Patient introuvable"}, status=status.HTTP_404_NOT_FOUND)
    if patient.project_id != project_id:
        return Response({"detail": "Ce patient n'appartient pas au projet indique."},
                        status=status.HTTP_409_CONFLICT)

    # Dossier cree hors-ligne dont la synchronisation est rejouee (coupure
    # juste apres un premier succes) : on ne recree pas de doublon, on renvoie
    # le dossier deja enregistre sous ce meme identifiant.
    provided_id = data.get("id")
    if provided_id:
        existing = Case.objects.filter(pk=provided_id).select_related("patient").first()
        if existing:
            return Response(serialize_case(existing), status=status.HTTP_201_CREATED)

    if not force:
        existing_active = Case.objects.filter(
            patient_id=patient_id, project_id=project_id, status="active").first()
        if existing_active:
            return Response(
                {"detail": {
                    "msg": f"Ce patient a deja un dossier actif : {existing_active.case_number}.",
                    "existing_case_id": existing_active.id,
                    "existing_case_number": existing_active.case_number,
                }},
                status=status.HTTP_409_CONFLICT,
            )

    case = Case(
        project_id=project_id,
        patient_id=patient_id,
        source_submission_id=data.get("source_submission_id"),
        # Date reelle d'inscription : permet de rattraper un dossier papier
        # ouvert il y a des mois. Vide = aujourd'hui.
        enrolled_at=_parse_datetime(data.get("enrolled_at")) or datetime.utcnow(),
        created_by=request.user,
    )
    if provided_id:
        case.pk = provided_id

    try:
        create_case_with_number(case, explicit_number=data.get("case_number"))
    except IntegrityError:
        if data.get("case_number"):
            return Response({"detail": "Ce numero de dossier existe deja dans ce projet."},
                            status=status.HTTP_409_CONFLICT)
        return Response({"detail": "Impossible d'attribuer un numero de dossier unique, reessaie."},
                        status=status.HTTP_409_CONFLICT)

    return Response(serialize_case(case), status=status.HTTP_201_CREATED)


@api_view(["GET", "PUT", "DELETE"])
@permission_classes([IsAuthenticated])
def case_detail(request, case_id):
    case = Case.objects.filter(pk=case_id).select_related("patient").first()
    if not case:
        return Response({"detail": "Cas introuvable"}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        return Response(serialize_case(case))
    if request.method == "PUT":
        return _update_case(request, case)
    return _delete_case(request, case)


def _update_case(request, case):
    """Corrige le numero de dossier ou la date d'inscription. Le patient
    rattache n'est volontairement pas modifiable ici : mieux vaut supprimer et
    recreer le dossier que risquer de melanger deux historiques."""
    data = request.data
    expected_updated_at = _parse_datetime(data.get("expected_updated_at"))
    if expected_updated_at and case.updated_at and case.updated_at != expected_updated_at:
        return Response(
            {"detail": "Quelqu'un d'autre a modifie ce dossier entre-temps. "
                       "Recharge la page avant de corriger a nouveau."},
            status=status.HTTP_409_CONFLICT,
        )

    if data.get("case_number"):
        case.case_number = data["case_number"]
    enrolled_at = _parse_datetime(data.get("enrolled_at"))
    if enrolled_at:
        case.enrolled_at = enrolled_at
    case.updated_at = datetime.utcnow()
    case.save()
    return Response(serialize_case(case))


def _delete_case(request, case):
    """Suppression autorisee uniquement si le dossier n'a aucune visite
    rattachee (doublon cree par erreur). Sinon on oriente vers la cloture,
    pour ne jamais perdre de donnees de suivi reelles."""
    visit_count = Submission.objects.filter(case=case).count()
    if visit_count > 0:
        return Response(
            {"detail": f"Ce dossier a {visit_count} visite(s) enregistree(s) — impossible de "
                       f"le supprimer. Cloture-le plutot."},
            status=status.HTTP_409_CONFLICT,
        )

    case_number = case.case_number
    case_id = case.id
    project_id = case.project_id
    case.delete()
    log_audit(request, user=request.user, action="delete", entity_type="case",
              entity_id=case_id, project_id=project_id,
              details=f"Dossier supprime : {case_number}")
    return Response({"status": "ok"})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def close_case(request, case_id):
    case = Case.objects.filter(pk=case_id).select_related("patient").first()
    if not case:
        return Response({"detail": "Cas introuvable"}, status=status.HTTP_404_NOT_FOUND)

    new_status = request.data.get("status")
    case.status = new_status if new_status in ("closed", "lost") else "closed"
    # Cloture retroactive possible (rattrapage d'un dossier papier) ; vide =
    # maintenant.
    case.closed_at = _parse_datetime(request.data.get("closed_at")) or datetime.utcnow()
    case.closed_reason = request.data.get("reason")
    case.updated_at = datetime.utcnow()
    case.save()
    return Response(serialize_case(case))


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def reopen_case(request, case_id):
    case = Case.objects.filter(pk=case_id).select_related("patient").first()
    if not case:
        return Response({"detail": "Cas introuvable"}, status=status.HTTP_404_NOT_FOUND)

    case.status = "active"
    case.closed_at = None
    case.closed_reason = None
    case.updated_at = datetime.utcnow()
    case.save()
    return Response(serialize_case(case))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def case_timeline(request, case_id):
    case = Case.objects.filter(pk=case_id).select_related("patient").first()
    if not case:
        return Response({"detail": "Cas introuvable"}, status=status.HTTP_404_NOT_FOUND)

    events = [{
        "type": "registration",
        "at": case.enrolled_at,
        "title": "Inscription au suivi",
        "description": f"Dossier {case.case_number} ouvert",
    }]

    for s in (Submission.objects.filter(case=case)
              .select_related("form").order_by("collected_at")):
        events.append({
            "type": "follow_up",
            "at": s.collected_at,
            "title": s.form.name if s.form else "Visite",
            "submission_id": s.id,
            "form_id": s.form_id,
            "responses": s.responses,
            "field_types": field_types_of(s.form),
        })

    if case.status in ("closed", "lost") and case.closed_at:
        events.append({
            "type": "closure",
            "at": case.closed_at,
            "title": "Cloture du dossier" if case.status == "closed" else "Perdu de vue",
            "description": case.closed_reason or "",
        })

    # L'inscription precede TOUJOURS toute visite de ce dossier (une
    # soumission ne peut exister avec ce dossier qu'apres sa creation). Mais
    # une visite saisie le meme jour via un simple selecteur de date est
    # enregistree a minuit, donc numeriquement AVANT l'heure reelle de
    # creation du dossier dans l'apres-midi. On force donc l'inscription en
    # premiere position plutot que de se fier a la comparaison brute des
    # horodatages.
    events.sort(key=lambda e: (e["type"] != "registration", e["at"]))
    return Response({"case": serialize_case(case), "events": events})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def patient_cases(request, patient_id):
    """Utilise notamment par l'historique patient pour savoir si ce patient a
    deja un dossier de suivi (et proposer "Voir le dossier" plutot que
    "Creer")."""
    cases = (Case.objects.filter(patient_id=patient_id)
             .select_related("patient").order_by("-enrolled_at"))
    return Response([serialize_case(c) for c in cases])
