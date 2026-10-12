"""Lien communautaire : acces a UN formulaire precis, sans compte, via un
lien et un code d'acces partage.

Chaque route reverifie le code a chaque appel : il n'y a pas de session
separee a revoquer, donc regenerer le code invalide immediatement tous les
appareils deja debloques. L'acces reste toujours limite a CE formulaire et a
son projet — jamais a autre chose.
"""
from datetime import datetime

from django.contrib.auth.hashers import check_password
from django.db import IntegrityError
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from attachments.services import store_uploaded_media
from clinical_forms.models import Form
from core.audit import client_ip, log_audit
from core.form_validation import validate_and_process_submission
from core.ratelimit import public_code_limiter
from patients.models import Patient
from patients.services import (
    assign_patient_code,
    check_phone_unique,
    flag_potential_duplicates,
)
from patients.views import serialize_patient
from submissions.models import Submission
from submissions.services import check_age_consistency, check_unique_fields
from submissions.views import _already_recorded, _parse_datetime


def verify_public_code(request, form_id) -> Form:
    """Verifie le code d'acces et renvoie le formulaire correspondant.

    Protection contre les essais en boucle : ce lien n'a aucun compte a
    verrouiller, donc rien ne freinerait naturellement un script essayant des
    codes au hasard. Au-dela de quelques echecs, on refuse sans meme verifier
    le code.
    """
    form = Form.objects.filter(pk=form_id).first()
    if not form or not form.public_link_enabled or not form.public_access_code_hash:
        raise NotFound("Lien communautaire introuvable ou desactive.")

    key = f"{client_ip(request)}:{form_id}"
    public_code_limiter.check(key, "Trop de tentatives avec un code incorrect.")

    code = request.headers.get("X-Community-Code", "")
    if not code or not check_password(code, form.public_access_code_hash):
        public_code_limiter.record_failure(key)
        raise PermissionDenied("Code d'acces incorrect.")

    public_code_limiter.record_success(key)
    return form


@api_view(["GET"])
@permission_classes([AllowAny])
def public_form(request, form_id):
    form = verify_public_code(request, form_id)
    return Response({
        "id": form.id,
        "name": form.name,
        "project_id": form.project_id,
        "definition": form.definition,
    })


@api_view(["POST"])
@permission_classes([AllowAny])
def public_create_patient(request, form_id):
    form = verify_public_code(request, form_id)
    data = request.data

    provided_id = data.get("id")
    if provided_id:
        existing = Patient.objects.filter(pk=provided_id).first()
        if existing:
            return Response(serialize_patient(existing), status=status.HTTP_201_CREATED)

    phone_conflict = check_phone_unique(form.project_id, data.get("phone"))
    if phone_conflict:
        return Response({"detail": phone_conflict}, status=status.HTTP_409_CONFLICT)

    first_name = (data.get("first_name") or "").strip()
    last_name = (data.get("last_name") or "").strip()
    if not first_name or not last_name:
        raise ValidationError("Le prenom et le nom sont obligatoires.")

    patient = Patient(
        # Jamais le projet envoye par le client : toujours celui du
        # formulaire, sinon ce lien public permettrait d'ecrire dans
        # n'importe quel projet.
        project_id=form.project_id,
        first_name=first_name,
        last_name=last_name,
        date_of_birth=data.get("date_of_birth") or None,
        sex=data.get("sex") or None,
        phone=data.get("phone") or None,
    )
    if provided_id:
        patient.pk = provided_id
    patient.save(force_insert=True)

    assign_patient_code(patient)
    if provided_id:
        flag_potential_duplicates(patient)

    log_audit(request, user_email="Lien communautaire", action="create", entity_type="patient",
              entity_id=patient.id, project_id=form.project_id,
              details=f"Patient cree via lien communautaire (formulaire « {form.name} ») : "
                      f"{patient.first_name} {patient.last_name}")
    return Response(serialize_patient(patient), status=status.HTTP_201_CREATED)


@api_view(["POST"])
@permission_classes([AllowAny])
def public_create_submission(request, form_id):
    form = verify_public_code(request, form_id)
    data = request.data

    idempotency_key = data.get("idempotency_key")
    if idempotency_key:
        existing = Submission.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return Response(_already_recorded(existing), status=status.HTTP_201_CREATED)

    patient = Patient.objects.filter(pk=data.get("patient_id")).first()
    if not patient:
        return Response({"detail": "Patient introuvable"}, status=status.HTTP_404_NOT_FOUND)
    if patient.project_id != form.project_id:
        # Ne devrait jamais arriver par ce parcours (le patient est toujours
        # cree juste avant, avec le projet du formulaire) — filet de securite
        # contre un identifiant bricole a la main.
        return Response({"detail": "Ce patient n'appartient pas au projet de ce formulaire."},
                        status=status.HTTP_409_CONFLICT)

    collected_at = _parse_datetime(data.get("collected_at")) or datetime.utcnow()

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

    # Pas de dossier de suivi rattache (le lien communautaire ne fait que de
    # l'Enquete) et pas d'auteur (aucun compte) — la tracabilite passe par le
    # journal d'audit ci-dessous.
    submission = Submission(
        form=form,
        patient=patient,
        case=None,
        responses=processed_responses,
        collected_at=collected_at,
        created_by=None,
        idempotency_key=idempotency_key,
    )
    try:
        submission.save(force_insert=True)
    except IntegrityError:
        existing = Submission.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return Response(_already_recorded(existing), status=status.HTTP_201_CREATED)
        raise

    log_audit(request, user_email="Lien communautaire", action="create", entity_type="submission",
              entity_id=submission.id, project_id=form.project_id,
              details=f"Soumission creee via lien communautaire (formulaire « {form.name} »)")
    return Response({
        "id": submission.id,
        "responses": submission.responses,
        "collected_at": submission.collected_at,
        "alerts": alerts,
    }, status=status.HTTP_201_CREATED)


@api_view(["POST"])
@permission_classes([AllowAny])
@parser_classes([MultiPartParser, FormParser])
def public_upload_media(request, form_id):
    verify_public_code(request, form_id)
    uploaded = request.FILES.get("file")
    if not uploaded:
        raise ValidationError("Aucun fichier recu.")
    field_type = request.data.get("field_type") or "file"
    result = store_uploaded_media(
        uploaded.read(), uploaded.name, uploaded.content_type, field_type, uploaded_by=None)
    return Response(result)
