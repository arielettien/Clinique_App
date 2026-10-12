"""Regles metier liees aux patients (codes anonymes, doublons, telephone)."""
import re
from datetime import datetime

from django.db import IntegrityError, transaction
from django.db.models import Q

from patients.models import Patient, PotentialDuplicate


def generate_patient_code(project_id: str, reference_date=None) -> str:
    """Code anonyme du type "PAT-2026-0042".

    Se base sur le code le plus haut REELLEMENT utilise cette annee dans ce
    projet, et non sur un comptage de patients : un comptage se trompe des
    qu'un patient a ete supprime (il reattribuerait un numero deja pris).
    """
    year = (reference_date or datetime.utcnow()).year
    prefix = f"PAT-{year}-"
    existing = (
        Patient.objects.filter(project_id=project_id, anonymous_code__startswith=prefix)
        .values_list("anonymous_code", flat=True)
    )
    max_seq = 0
    for code in existing:
        if code:
            suffix = code[len(prefix):]
            if suffix.isdigit():
                max_seq = max(max_seq, int(suffix))
    return f"{prefix}{max_seq + 1:04d}"


def assign_patient_code(patient: Patient):
    """Attribue un code anonyme a un patient qui n'en a pas encore.

    Boucle de reprise : deux creations simultanees dans le meme projet
    peuvent lire le meme "code le plus haut" avant que l'une des deux n'ait
    ete validee — la contrainte d'unicite rejette alors la seconde, qui
    recalcule simplement le code suivant.
    """
    if patient.anonymous_code or not patient.project_id:
        return
    for _attempt in range(5):
        patient.anonymous_code = generate_patient_code(patient.project_id)
        try:
            with transaction.atomic():
                patient.save(update_fields=["anonymous_code"])
            return
        except IntegrityError:
            continue
    # Apres 5 echecs (extremement improbable) : le patient reste sans code.
    # Rien de bloquant, il pourra en recevoir un plus tard.
    patient.anonymous_code = None


def find_matching_patient_by_name(project_id, first_name, last_name):
    """Cherche un patient deja existant du meme projet portant exactement les
    memes prenom et nom (insensible a la casse et aux espaces) — sert a
    avertir AVANT de creer un doublon en ligne."""
    fn = (first_name or "").strip().lower()
    ln = (last_name or "").strip().lower()
    if not fn or not ln:
        return None
    for other in Patient.objects.filter(project_id=project_id):
        if other.first_name.strip().lower() == fn and other.last_name.strip().lower() == ln:
            return other
    return None


def flag_potential_duplicates(new_patient: Patient):
    """Signale les autres patients du meme projet portant les memes prenom et
    nom. Ne fusionne jamais rien automatiquement : deux personnes peuvent
    legitimement porter le meme nom, seul un humain peut trancher.

    Appele uniquement pour un patient cree hors-ligne : la creation en ligne,
    elle, avertit AVANT creation (voir find_matching_patient_by_name), ce qui
    est impossible sans reseau.
    """
    fn = new_patient.first_name.strip().lower()
    ln = new_patient.last_name.strip().lower()
    others = Patient.objects.filter(project_id=new_patient.project_id).exclude(pk=new_patient.pk)

    for other in others:
        if other.first_name.strip().lower() != fn or other.last_name.strip().lower() != ln:
            continue
        # Evite de signaler deux fois la meme paire, dans un sens ou l'autre.
        same_pair = (
            (Q(patient_a_id=new_patient.pk) & Q(patient_b_id=other.pk))
            | (Q(patient_a_id=other.pk) & Q(patient_b_id=new_patient.pk))
        )
        already = PotentialDuplicate.objects.filter(
            project_id=new_patient.project_id, status="open",
        ).filter(same_pair).exists()
        if not already:
            PotentialDuplicate.objects.create(
                project_id=new_patient.project_id,
                patient_a_id=new_patient.pk,
                patient_b_id=other.pk,
            )


def check_phone_unique(project_id, phone, exclude_patient_id=None):
    """Verifie qu'aucun AUTRE patient du meme projet n'a deja ce numero.

    Un champ vide n'est jamais compare (plusieurs patients peuvent
    legitimement ne pas avoir de telephone). La comparaison ignore espaces,
    tirets et points, pour absorber les variantes de saisie
    (ex : "07 12 34 56 78" et "0712345678" sont le meme numero).
    """
    if not phone or not phone.strip():
        return None
    norm_phone = re.sub(r"[\s\-.]", "", phone)
    query = Patient.objects.filter(project_id=project_id)
    if exclude_patient_id:
        query = query.exclude(pk=exclude_patient_id)
    for other in query:
        if other.phone and re.sub(r"[\s\-.]", "", other.phone) == norm_phone:
            return (f"Le numero de telephone « {phone} » est deja utilise par "
                    f"{other.first_name} {other.last_name} dans ce projet.")
    return None
