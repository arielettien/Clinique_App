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


def display_schema_of(form) -> list:
    """Ce qu'il faut pour RELIRE une soumission comme elle a ete saisie : les
    sections et les champs dans l'ordre du formulaire, le libelle de chaque
    champ, et le libelle de chaque option.

    Sans ca, un ecran qui relit une soumission ne dispose que du JSON brut des
    reponses. Il affiche alors l'identifiant technique du champ
    ("perimetre_brachial_mm") et la valeur stockee ("cs_abobo_sud"), dans
    l'ordre des cles JSON — c'est-a-dire ni le texte lu par l'agent, ni l'ordre
    dans lequel il l'a rempli. Sur un dossier medical, c'est une perte de sens,
    pas seulement une question d'esthetique.

    Les listes de choix partagees (list_name -> choice_lists) sont resolues
    ici, une fois, cote serveur : chaque ecran n'a pas a refaire ce travail.

    Les champs de type "note" sont ecartes : ce sont des textes d'explication
    affiches pendant la saisie, ils ne portent aucune reponse.
    """
    if not form:
        return []
    definition = form.definition or {}
    choice_lists = definition.get("choice_lists") or {}

    sections = []
    for section in definition.get("sections") or []:
        if not isinstance(section, dict):
            continue
        fields = []
        for f in section.get("fields") or []:
            if not isinstance(f, dict) or not f.get("id") or f.get("type") == "note":
                continue
            entry = {
                "id": f["id"],
                "label": f.get("label") or f["id"],
                "type": f.get("type") or "text",
            }
            # "options" en clair sur le champ, sinon la liste partagee designee
            # par list_name — les deux formes existent dans les formulaires
            # importes depuis Kobo comme dans ceux construits dans l'app.
            options = f.get("options")
            if not options and f.get("list_name"):
                options = choice_lists.get(f["list_name"])
            if options:
                entry["options"] = {
                    str(o["value"]): (o.get("label") or str(o["value"]))
                    for o in options
                    if isinstance(o, dict) and o.get("value") is not None
                }
            fields.append(entry)
        if fields:
            sections.append({
                "id": section.get("id") or "",
                "title": section.get("title") or "",
                "fields": fields,
            })
    return sections


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
