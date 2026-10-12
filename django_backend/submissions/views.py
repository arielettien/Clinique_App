"""Soumissions : formulaires remplis pour un patient, a un instant donne."""
from datetime import datetime, time

from django.db import IntegrityError
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from cases.models import Case
from clinical_forms.models import Form
from clinical_forms.services import check_referral_suggestion, find_referral_field_id
from core.audit import log_audit
from core.form_validation import validate_and_process_submission
from patients.models import Patient
from submissions.models import Submission
from submissions.services import check_age_consistency, check_unique_fields, field_types_of


def _parse_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    # La base stocke des horodatages sans fuseau (meme convention que la
    # version precedente) : on retire l'eventuel fuseau plutot que de
    # melanger les deux, ce qui provoquerait des comparaisons incoherentes.
    return parsed.replace(tzinfo=None)


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _already_recorded(existing):
    return {
        "id": existing.id,
        "responses": existing.responses,
        "collected_at": existing.collected_at,
        "alerts": [],
        "already_recorded": True,
    }


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_submission(request):
    data = request.data
    idempotency_key = data.get("idempotency_key")

    # Idempotence : si ce jeton a deja servi (double-clic, requete rejouee par
    # le reseau, reprise de la file d'attente hors-ligne), on renvoie le
    # resultat deja enregistre au lieu d'en creer un second.
    if idempotency_key:
        existing = Submission.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return Response(_already_recorded(existing), status=status.HTTP_201_CREATED)

    form = Form.objects.filter(pk=data.get("form_id")).first()
    if not form:
        return Response({"detail": "Formulaire introuvable"}, status=status.HTTP_404_NOT_FOUND)
    patient = Patient.objects.filter(pk=data.get("patient_id")).first()
    if not patient:
        return Response({"detail": "Patient introuvable"}, status=status.HTTP_404_NOT_FOUND)
    if patient.project_id != form.project_id:
        return Response({"detail": "Ce patient et ce formulaire n'appartiennent pas au meme projet."},
                        status=status.HTTP_409_CONFLICT)

    case_id = data.get("case_id")
    if case_id:
        case = Case.objects.filter(pk=case_id).first()
        if not case:
            return Response({"detail": "Cas de suivi introuvable"}, status=status.HTTP_404_NOT_FOUND)
        if case.project_id != form.project_id:
            return Response({"detail": "Ce cas de suivi n'appartient pas au meme projet que le formulaire."},
                            status=status.HTTP_409_CONFLICT)

    collected_at = _parse_datetime(data.get("collected_at")) or datetime.utcnow()

    # Le serveur recalcule systematiquement les champs calcules et revalide
    # tout : ce que le navigateur a calcule n'est jamais pris pour argent
    # comptant.
    processed_responses, errors, alerts = validate_and_process_submission(
        form.definition, data.get("responses") or {})
    if errors:
        return Response({"detail": " | ".join(errors)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

    unique_errors = check_unique_fields(form, processed_responses)
    if unique_errors:
        return Response({"detail": " | ".join(unique_errors)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

    age_errors = check_age_consistency(form, processed_responses, patient, collected_at)
    if age_errors:
        return Response({"detail": " | ".join(age_errors)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

    submission = Submission(
        form=form,
        patient=patient,
        case_id=case_id,
        responses=processed_responses,
        collected_at=collected_at,
        created_by=request.user,
        idempotency_key=idempotency_key,
    )
    try:
        submission.save(force_insert=True)
    except IntegrityError:
        # Course rare : une requete jumelle a insere le meme jeton entre notre
        # verification ci-dessus et cet instant. On renvoie la sienne plutot
        # que de faire echouer l'enregistrement.
        existing = Submission.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return Response(_already_recorded(existing), status=status.HTTP_201_CREATED)
        raise

    referral = check_referral_suggestion(form, patient.id, processed_responses)
    return Response({
        "id": submission.id,
        "responses": submission.responses,
        "collected_at": submission.collected_at,
        "alerts": alerts,
        "referral": referral,
    }, status=status.HTTP_201_CREATED)


@api_view(["GET", "PUT", "DELETE"])
@permission_classes([IsAuthenticated])
def submission_detail(request, submission_id):
    submission = Submission.objects.filter(pk=submission_id).select_related("form", "patient").first()
    if not submission:
        return Response({"detail": "Soumission introuvable"}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        form = submission.form
        patient = submission.patient
        return Response({
            "id": submission.id,
            "form_id": submission.form_id,
            "form_name": form.name if form else None,
            "form_definition": form.definition if form else None,
            "patient_id": submission.patient_id,
            "patient_name": f"{patient.first_name} {patient.last_name}" if patient else None,
            "responses": submission.responses,
            "collected_at": submission.collected_at,
            "updated_at": submission.updated_at,
        })

    if request.method == "PUT":
        return _update_submission(request, submission)
    return _delete_submission(request, submission)


def _update_submission(request, submission):
    """Corrige les reponses d'une soumission deja enregistree (erreur de
    saisie) — modifie celle-ci en place, ne cree jamais un nouvel
    enregistrement. Pour une nouvelle visite, voir la creation."""
    data = request.data

    expected_updated_at = _parse_datetime(data.get("expected_updated_at"))
    if expected_updated_at and submission.updated_at:
        if submission.updated_at != expected_updated_at:
            return Response(
                {"detail": "Quelqu'un d'autre a modifie cette soumission entre-temps. "
                           "Recharge la page pour voir la version actuelle avant de corriger a nouveau."},
                status=status.HTTP_409_CONFLICT,
            )

    form = submission.form
    if not form:
        return Response({"detail": "Formulaire introuvable"}, status=status.HTTP_404_NOT_FOUND)

    processed_responses, errors, alerts = validate_and_process_submission(
        form.definition, data.get("responses") or {})
    if errors:
        return Response({"detail": " | ".join(errors)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

    unique_errors = check_unique_fields(form, processed_responses, exclude_submission_id=submission.id)
    if unique_errors:
        return Response({"detail": " | ".join(unique_errors)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

    collected_at = _parse_datetime(data.get("collected_at"))
    age_errors = check_age_consistency(form, processed_responses, submission.patient,
                                       collected_at or submission.collected_at)
    if age_errors:
        return Response({"detail": " | ".join(age_errors)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

    submission.responses = processed_responses
    if collected_at:
        submission.collected_at = collected_at
    submission.updated_at = datetime.utcnow()
    submission.save()

    referral = check_referral_suggestion(form, submission.patient_id, processed_responses)
    return Response({
        "id": submission.id,
        "responses": submission.responses,
        "collected_at": submission.collected_at,
        "alerts": alerts,
        "referral": referral,
    })


def _delete_submission(request, submission):
    form = submission.form
    submission_id = submission.id
    submission.delete()
    log_audit(request, user=request.user, action="delete", entity_type="submission",
              entity_id=submission_id, project_id=(form.project_id if form else None),
              details=f"Soumission supprimee (formulaire : {form.name if form else '—'})")
    return Response({"status": "ok"})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def patient_submissions(request, patient_id):
    if not Patient.objects.filter(pk=patient_id).exists():
        return Response({"detail": "Patient introuvable"}, status=status.HTTP_404_NOT_FOUND)

    submissions = (Submission.objects.filter(patient_id=patient_id)
                   .select_related("form").order_by("-collected_at"))

    result = []
    for s in submissions:
        form = s.form
        result.append({
            "id": s.id,
            "form_name": form.name if form else "Formulaire supprime",
            "form_type": form.form_type if form else None,
            "responses": s.responses,
            "collected_at": s.collected_at,
            # collected_at ne porte qu'une date choisie par l'agent (le champ
            # "Date de cet evenement" n'a pas d'heure) : son heure est donc
            # toujours minuit. created_at, lui, est l'horodatage reel de
            # l'enregistrement — c'est celui que l'ecran affiche, pour montrer
            # une heure qui corresponde a la realite plutot qu'un minuit de
            # convention.
            "created_at": s.created_at,
            "field_types": field_types_of(form),
        })
    return Response(result)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def form_submissions(request, form_id):
    """Vue agregee : toutes les reponses recues pour CE formulaire, tous
    patients confondus."""
    form = Form.objects.filter(pk=form_id).first()
    if not form:
        return Response({"detail": "Formulaire introuvable"}, status=status.HTTP_404_NOT_FOUND)

    query = Submission.objects.filter(form_id=form_id).select_related("patient")
    start = _parse_date(request.query_params.get("start"))
    end = _parse_date(request.query_params.get("end"))
    if start:
        query = query.filter(collected_at__gte=datetime.combine(start, time.min))
    if end:
        query = query.filter(collected_at__lte=datetime.combine(end, time.max))
    query = query.order_by("-collected_at")

    # Pagination optionnelle : sans page ni page_size, on renvoie tout
    # (comportement inchange pour les appelants existants).
    page = request.query_params.get("page")
    page_size = request.query_params.get("page_size")
    total_count = None
    if page or page_size:
        total_count = query.count()
        page = max(1, int(page or 1))
        page_size = min(500, max(1, int(page_size or 100)))
        start_index = (page - 1) * page_size
        query = query[start_index:start_index + page_size]

    submissions = list(query)

    # Statut d'admission en Suivi, seulement si ce formulaire a un champ
    # S/N-S : "admitted" (dossier actif deja cree), "pending" (S/N-S = Oui
    # mais toujours aucun dossier — "plus tard" a ete clique),
    # "not_admitted" (S/N-S = Non ou vide). None si le formulaire n'a pas de
    # champ S/N-S : la colonne n'a alors aucun sens.
    referral_field_id = find_referral_field_id(form.definition)
    followed_patient_ids = set()
    if referral_field_id:
        followed_patient_ids = set(
            Case.objects.filter(project_id=form.project_id, status="active")
            .values_list("patient_id", flat=True)
        )

    result = []
    for s in submissions:
        patient = s.patient
        referral_status = None
        if referral_field_id:
            if s.responses.get(referral_field_id):
                referral_status = "admitted" if s.patient_id in followed_patient_ids else "pending"
            else:
                referral_status = "not_admitted"
        result.append({
            "id": s.id,
            "patient_name": f"{patient.first_name} {patient.last_name}" if patient else "Patient supprime",
            "patient_id": s.patient_id,
            "responses": s.responses,
            "collected_at": s.collected_at,
            "referral_status": referral_status,
        })

    body = {
        "form": {"id": form.id, "name": form.name, "project_id": form.project_id,
                 "definition": form.definition},
        "submissions": result,
        "has_referral_field": referral_field_id is not None,
    }
    if total_count is not None:
        body["total"] = total_count
    return Response(body)
