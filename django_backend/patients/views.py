"""Patients : identite, recherche, doublons potentiels."""
from django.db.models import Q
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from cases.models import Case
from core.audit import log_audit
from patients.models import Patient, PotentialDuplicate
from patients.services import (
    assign_patient_code,
    check_phone_unique,
    find_matching_patient_by_name,
    flag_potential_duplicates,
)
from submissions.models import Submission

# Une recherche renvoie au plus ce nombre de resultats : c'est une aide a la
# saisie, pas un export — au-dela, l'agent doit affiner sa recherche.
SEARCH_RESULT_LIMIT = 8


def serialize_patient(p: Patient) -> dict:
    return {
        "id": p.id,
        "project_id": p.project_id,
        "first_name": p.first_name,
        "last_name": p.last_name,
        "date_of_birth": p.date_of_birth,
        "sex": p.sex,
        "phone": p.phone,
        "anonymous_code": p.anonymous_code,
        "created_at": p.created_at,
    }


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def patients_collection(request):
    if request.method == "GET":
        return _list_patients(request)
    return _create_patient(request)


def _list_patients(request):
    query = Patient.objects.order_by("-created_at")

    project_id = request.query_params.get("project_id")
    if project_id:
        query = query.filter(project_id=project_id)

    search = request.query_params.get("search")
    if search:
        # Chaque mot tape doit se retrouver dans le prenom OU le nom
        # (ex: "yao b" -> prenom~yao OU nom~yao, ET prenom~b OU nom~b).
        for term in search.strip().split():
            query = query.filter(
                Q(first_name__icontains=term) | Q(last_name__icontains=term)
            )
        return Response([serialize_patient(p) for p in query[:SEARCH_RESULT_LIMIT]])

    # Pagination optionnelle : sans page ni page_size, on renvoie tout
    # (comportement inchange pour les appelants existants). Le total part en
    # en-tete plutot que dans le corps, pour garder une reponse "liste simple".
    page = request.query_params.get("page")
    page_size = request.query_params.get("page_size")
    if page or page_size:
        page = max(1, int(page or 1))
        page_size = min(500, max(1, int(page_size or 50)))
        total_count = query.count()
        start = (page - 1) * page_size
        response = Response([serialize_patient(p) for p in query[start:start + page_size]])
        response["X-Total-Count"] = str(total_count)
        return response

    return Response([serialize_patient(p) for p in query])


def _create_patient(request):
    data = request.data
    force = request.query_params.get("force", "").lower() == "true"

    provided_id = data.get("id")
    project_id = data.get("project_id")
    first_name = (data.get("first_name") or "").strip()
    last_name = (data.get("last_name") or "").strip()

    if not first_name or not last_name:
        raise ValidationError("Le prenom et le nom sont obligatoires.")

    # Un identifiant fourni signifie que ce patient a ete cree hors-ligne. Si
    # une synchronisation precedente avait deja reussi sans que la
    # confirmation revienne jusqu'au navigateur (coupure au mauvais moment),
    # on ne recree pas un doublon : on renvoie la fiche deja existante.
    if provided_id:
        existing = Patient.objects.filter(pk=provided_id).first()
        if existing:
            return Response(serialize_patient(existing), status=status.HTTP_201_CREATED)

    phone_conflict = check_phone_unique(project_id, data.get("phone"))
    if phone_conflict:
        return Response({"detail": phone_conflict}, status=status.HTTP_409_CONFLICT)

    # Avertit AVANT de creer un doublon en ligne (l'agent a pu ne pas
    # remarquer la recherche) — ?force=true passe outre, apres confirmation
    # explicite. Ne s'applique pas a une creation hors-ligne : celle-ci est
    # signalee apres coup, la recherche etant impossible sans reseau.
    if not provided_id and not force:
        match = find_matching_patient_by_name(project_id, first_name, last_name)
        if match:
            return Response(
                {"detail": {
                    "msg": (f"Un patient nomme {match.first_name} {match.last_name} existe deja "
                            f"dans ce projet (cree le {match.created_at.strftime('%d/%m/%Y')})."),
                    "existing_patient_id": match.id,
                    "existing_patient_name": f"{match.first_name} {match.last_name}",
                }},
                status=status.HTTP_409_CONFLICT,
            )

    patient = Patient(
        project_id=project_id,
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

    # Un patient cree hors-ligne n'a pas pu etre compare a la base complete au
    # moment de sa saisie : on verifie donc maintenant, et on signale plutot
    # que de fusionner (risque de melanger deux vrais patients homonymes).
    if provided_id:
        flag_potential_duplicates(patient)

    return Response(serialize_patient(patient), status=status.HTTP_201_CREATED)


@api_view(["GET", "PUT", "DELETE"])
@permission_classes([IsAuthenticated])
def patient_detail(request, patient_id):
    patient = Patient.objects.filter(pk=patient_id).first()
    if not patient:
        return Response({"detail": "Patient introuvable"}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        return Response(serialize_patient(patient))
    if request.method == "PUT":
        return _update_patient(request, patient)
    return _delete_patient(request, patient)


def _update_patient(request, patient):
    """Met a jour l'identite d'un patient existant — utilise notamment quand
    on reutilise une fiche retrouvee par la recherche (telephone qui a change,
    faute d'orthographe corrigee, etc.)."""
    data = request.data
    phone_conflict = check_phone_unique(patient.project_id, data.get("phone"),
                                        exclude_patient_id=patient.pk)
    if phone_conflict:
        return Response({"detail": phone_conflict}, status=status.HTTP_409_CONFLICT)

    patient.first_name = (data.get("first_name") or "").strip() or patient.first_name
    patient.last_name = (data.get("last_name") or "").strip() or patient.last_name
    patient.date_of_birth = data.get("date_of_birth") or None
    patient.sex = data.get("sex") or None
    patient.phone = data.get("phone") or None
    patient.save()
    return Response(serialize_patient(patient))


def _delete_patient(request, patient):
    """Supprime un patient seulement s'il n'a aucune soumission ni aucun cas
    de suivi rattache — sinon on perdrait de vraies donnees cliniques. Couvre
    le cas d'une fiche creee par erreur (doublon, mauvais clic)."""
    submission_count = Submission.objects.filter(patient=patient).count()
    case_count = Case.objects.filter(patient=patient).count()
    if submission_count > 0 or case_count > 0:
        return Response(
            {"detail": f"Ce patient a {submission_count} soumission(s) et {case_count} cas de "
                       f"suivi — impossible de le supprimer sans perdre des donnees."},
            status=status.HTTP_409_CONFLICT,
        )

    patient_name = f"{patient.first_name} {patient.last_name}"
    patient_id = patient.pk
    project_id = patient.project_id
    patient.delete()
    log_audit(request, user=request.user, action="delete", entity_type="patient",
              entity_id=patient_id, project_id=project_id,
              details=f"Patient supprime : {patient_name}")
    return Response({"status": "ok"})


# --- Doublons potentiels --------------------------------------------------

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def project_potential_duplicates(request, project_id):
    """Paires de patients signalees comme possiblement identiques (memes
    prenom et nom), detectees apres la synchronisation d'un patient cree
    hors-ligne. Jamais fusionne automatiquement — a trancher manuellement."""
    rows = (PotentialDuplicate.objects
            .filter(project_id=project_id, status="open")
            .select_related("patient_a", "patient_b")
            .order_by("-detected_at"))

    def short(p):
        return {
            "id": p.id, "first_name": p.first_name, "last_name": p.last_name,
            "date_of_birth": p.date_of_birth, "phone": p.phone, "created_at": p.created_at,
        }

    result = []
    for r in rows:
        if not r.patient_a or not r.patient_b:
            continue
        result.append({
            "id": r.id,
            "detected_at": r.detected_at,
            "patient_a": short(r.patient_a),
            "patient_b": short(r.patient_b),
        })
    return Response(result)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def dismiss_potential_duplicate(request, dup_id):
    """Marque une paire comme verifiee (pas un vrai doublon, ou deja traitee
    manuellement) — ne supprime ni ne fusionne aucun patient, se contente de
    la retirer de la liste a traiter."""
    dup = PotentialDuplicate.objects.filter(pk=dup_id).first()
    if not dup:
        return Response({"detail": "Signalement introuvable"}, status=status.HTTP_404_NOT_FOUND)

    dup.status = "dismissed"
    dup.save(update_fields=["status"])
    log_audit(request, user=request.user, action="update", entity_type="potential_duplicate",
              entity_id=dup_id, project_id=dup.project_id,
              details="Doublon potentiel traite/ecarte")
    return Response({"status": "ok"})
