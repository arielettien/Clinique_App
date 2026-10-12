"""Regles metier liees aux formulaires."""
import secrets

from clinical_forms.models import Form


def generate_access_code(length: int = 8) -> str:
    """Code court du lien communautaire, lisible sur un clavier de telephone :
    majuscules et chiffres seulement, sans caracteres ambigus (ni 0/O, ni
    1/I/l) — un agent qui dicte le code a quelqu'un au telephone ne doit pas
    avoir a preciser "le zero ou la lettre O ?"."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def fields_of(form: Form) -> list:
    """Tous les champs du formulaire, toutes sections confondues, a plat —
    pratique pour construire les colonnes d'export sans se soucier du
    decoupage en sections."""
    fields = []
    for section in (form.definition or {}).get("sections") or []:
        fields.extend(section.get("fields", []))
    return fields


def serialize_form_summary(f: Form) -> dict:
    return {"id": f.id, "name": f.name, "form_type": f.form_type, "project_id": f.project_id}


def serialize_form_full(f: Form) -> dict:
    return {
        "id": f.id,
        "name": f.name,
        "form_type": f.form_type,
        "project_id": f.project_id,
        "definition": f.definition,
        "public_link_enabled": bool(f.public_link_enabled),
    }


def find_referral_field_id(form_definition):
    """Cherche le champ S/N-S (Suivi / Non-Suivi) dans la definition d'un
    formulaire. Il ne peut y en avoir qu'un seul par formulaire, regle imposee
    cote constructeur de formulaire."""
    for section in (form_definition or {}).get("sections", []):
        for f in section.get("fields", []):
            if f.get("type") == "boolean" and f.get("triggers_referral"):
                return f["id"]
    return None


def check_referral_suggestion(form, patient_id, processed_responses):
    """Si ce formulaire a un champ S/N-S repondu "Oui", et que ce patient n'a
    pas deja un dossier de suivi actif dans ce projet, renvoie de quoi
    proposer d'en creer un.

    Ne cree jamais rien : se contente de signaler, la decision reste
    entierement manuelle.
    """
    from cases.models import Case

    field_id = find_referral_field_id(form.definition)
    if not field_id or not processed_responses.get(field_id):
        return None
    already_followed = Case.objects.filter(
        patient_id=patient_id, project_id=form.project_id, status="active",
    ).exists()
    if already_followed:
        return None
    return {"suggest_referral": True, "patient_id": patient_id}
