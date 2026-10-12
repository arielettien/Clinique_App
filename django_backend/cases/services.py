"""Regles metier liees aux dossiers de suivi."""
from datetime import date, datetime

from django.db import IntegrityError, transaction

from cases.models import Case
from submissions.models import Submission


def find_next_visit_field(definition: dict):
    """Cherche le champ marque "date de prochaine visite" (un seul par
    formulaire, regle imposee cote constructeur de formulaire)."""
    for section in (definition or {}).get("sections", []):
        for field in section.get("fields", []):
            if field.get("next_visit_marker"):
                return field["id"]
    return None


def serialize_case(case: Case) -> dict:
    patient = case.patient
    last_submission = (Submission.objects.filter(case=case)
                       .select_related("form").order_by("-collected_at").first())
    visit_count = Submission.objects.filter(case=case).count()

    next_visit_at = None
    is_overdue = False
    if last_submission and last_submission.form:
        marker_field_id = find_next_visit_field(last_submission.form.definition)
        if marker_field_id:
            raw_value = last_submission.responses.get(marker_field_id)
            if raw_value:
                try:
                    next_visit_at = date.fromisoformat(str(raw_value))
                    is_overdue = case.status == "active" and next_visit_at < datetime.utcnow().date()
                except ValueError:
                    next_visit_at = None

    return {
        "id": case.id,
        "project_id": case.project_id,
        "patient_id": case.patient_id,
        "patient_name": f"{patient.first_name} {patient.last_name}" if patient else "Patient supprime",
        "patient_sex": patient.sex if patient else None,
        "patient_date_of_birth": patient.date_of_birth if patient else None,
        "patient_anonymous_code": patient.anonymous_code if patient else None,
        "case_number": case.case_number,
        "status": case.status,
        "enrolled_at": case.enrolled_at,
        "closed_at": case.closed_at,
        "closed_reason": case.closed_reason,
        "last_visit_at": last_submission.collected_at if last_submission else None,
        "visit_count": visit_count,
        "next_visit_at": next_visit_at,
        "is_overdue": is_overdue,
        "updated_at": case.updated_at,
    }


def generate_case_number(project_id: str, reference_date=None) -> str:
    """Numero de dossier du type "CAS-2026-0042".

    Se base sur le numero le plus haut REELLEMENT utilise cette annee dans ce
    projet, et non sur un comptage de dossiers : un comptage se trompe des
    qu'un dossier a ete supprime (le compte baisse, mais les numeros deja
    attribues aux dossiers restants ne bougent pas) et regenererait alors un
    numero deja pris. Comme rien n'est enregistre a chaque tentative ratee,
    les reprises regenereraient toutes exactement le meme numero casse.
    """
    year = (reference_date or datetime.utcnow()).year
    prefix = f"CAS-{year}-"
    existing = (Case.objects.filter(project_id=project_id, case_number__startswith=prefix)
                .values_list("case_number", flat=True))
    max_seq = 0
    for number in existing:
        suffix = number[len(prefix):]
        if suffix.isdigit():
            max_seq = max(max_seq, int(suffix))
    return f"{prefix}{max_seq + 1:04d}"


def create_case_with_number(case: Case, explicit_number=None, attempts: int = 5):
    """Enregistre un dossier en lui attribuant un numero libre.

    Boucle de reprise : deux creations simultanees dans le meme projet peuvent
    lire le meme "numero le plus haut" avant que l'une des deux n'ait ete
    validee — la contrainte d'unicite rejette alors la seconde, qui recalcule
    simplement le numero suivant.
    """
    for _attempt in range(attempts):
        case.case_number = explicit_number or generate_case_number(
            case.project_id, case.enrolled_at)
        try:
            with transaction.atomic():
                case.save(force_insert=True)
            return case
        except IntegrityError:
            if explicit_number:
                raise  # numero impose et deja pris : ce n'est pas une course
            continue
    raise IntegrityError("Impossible d'attribuer un numero de dossier libre.")
