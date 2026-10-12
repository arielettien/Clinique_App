"""Controles metier appliques a une soumission avant enregistrement."""
from clinical_forms.services import fields_of
from core.utils import age_at
from submissions.models import Submission

# Tolerance sur l'age saisi a la main, en annees. Une personne qui declare
# "45 ans" alors qu'elle en a 44 selon sa date de naissance n'est pas une
# erreur de saisie : c'est la facon normale d'arrondir un age quand
# l'anniversaire approche.
AGE_TOLERANCE_YEARS = 1


def check_unique_fields(form, responses, exclude_submission_id=None):
    """Verifie qu'aucune AUTRE soumission de ce meme formulaire ne porte deja
    la meme valeur pour un champ marque "valeur unique" dans le constructeur
    (numero de telephone, numero de fiche...).

    Comparaison insensible a la casse et aux espaces. Un champ vide n'est
    jamais compare : plusieurs soumissions peuvent legitimement le laisser non
    renseigne. Portee volontairement limitee a CE formulaire, pas a tous les
    formulaires du projet.
    """
    errors = []
    unique_fields = [
        f for f in fields_of(form)
        if isinstance(f.get("validation"), dict)
        and f["validation"].get("unique")
        and f.get("type") != "note"
    ]
    if not unique_fields:
        return errors

    others = list(Submission.objects.filter(form_id=form.id))
    for field in unique_fields:
        fid = field["id"]
        value = responses.get(fid)
        if value is None or str(value).strip() == "":
            continue
        norm_value = str(value).strip().lower()
        for other in others:
            if exclude_submission_id and other.id == exclude_submission_id:
                continue
            other_value = (other.responses or {}).get(fid)
            if other_value is None or str(other_value).strip() == "":
                continue
            if str(other_value).strip().lower() == norm_value:
                label = field.get("label", fid)
                errors.append(f"{label} : la valeur « {value} » est deja utilisee par une "
                              f"autre soumission de ce formulaire.")
                break
    return errors


def check_age_consistency(form, responses, patient, reference_date):
    """Si un champ numerique est marque "comparer a la date de naissance",
    verifie qu'il correspond a l'age reel du patient a la date de la visite.

    Sans ce controle, un age saisi a la main peut contredire silencieusement
    la date de naissance deja connue du patient — c'est une remontee du
    terrain, pas une precaution theorique.
    """
    errors = []
    if not patient or not patient.date_of_birth:
        return errors
    real_age = age_at(patient.date_of_birth, reference_date)
    if real_age is None:
        return errors

    for field in fields_of(form):
        validation = field.get("validation")
        if not (isinstance(validation, dict) and validation.get("check_against_dob")):
            continue
        value = responses.get(field["id"])
        if value is None or str(value).strip() == "":
            continue
        try:
            entered_age = float(value)
        except (TypeError, ValueError):
            continue  # deja signale ailleurs comme "doit etre un nombre"
        if abs(entered_age - real_age) > AGE_TOLERANCE_YEARS:
            label = field.get("label", field["id"])
            errors.append(
                f"{label} ({value} ans) ne correspond pas a la date de naissance du patient "
                f"(age reel a la date de cette visite : {real_age} ans). Verifie la date de "
                f"naissance ou corrige cet age."
            )
    return errors


def field_types_of(form):
    """Type de chaque champ, indexe par identifiant — permet au frontend de
    savoir comment afficher une reponse (photo, signature, texte...)."""
    types = {}
    if not form:
        return types
    for section in (form.definition or {}).get("sections", []):
        for f in section.get("fields", []):
            types[f["id"]] = f.get("type")
    return types
