"""
Vérifie une soumission de formulaire par rapport à sa définition :
- champs obligatoires présents (sauf si masqués par une condition d'affichage)
- bornes min/max, et un format (expression régulière) pour les champs texte
- recalcule les champs "calculated" côté serveur (ne fait jamais confiance
  au calcul déjà fait dans le navigateur), en les traitant PAR ORDRE DE
  DÉPENDANCES (un calcul peut référencer un autre calcul défini plus bas
  dans le formulaire — corrige un bug où l'ordre du document seul faisait
  échouer ce genre de calcul)
- applique la logique conditionnelle ("relevant") : au niveau d'un champ,
  et au niveau d'une SECTION ENTIÈRE (comme un begin_group conditionnel de
  Kobo) — si la section n'est pas pertinente, tous ses champs sont ignorés
  et effacés d'un coup
- calcule les alertes ("alert_if") : informatives, elles ne bloquent jamais
  l'enregistrement, contrairement aux erreurs de validation

Point important : les champs de SAISIE non envoyés par le navigateur (jamais
rendus, ou décochés) sont explicitement pré-remplis à None avant d'évaluer
les conditions — sinon une condition qui les référence échoue silencieusement
et retombe sur son défaut, ce qui pouvait à tort rendre visible/obligatoire
un champ qui aurait dû rester masqué. Les champs CALCULÉS ne sont volontairement
PAS pré-remplis : leur absence temporaire (tant qu'ils n'ont pas encore été
évalués) est ce qui permet à la boucle de dépendances de savoir qu'il faut
réessayer plus tard, plutôt que de produire silencieusement un résultat vide.
"""

import re

from formula_engine import evaluate_formula, evaluate_condition, FormulaError


def _is_empty(value) -> bool:
    """Un champ est considéré vide si : rien, chaîne vide, ou liste vide
    (ce dernier cas concerne les questions "choix multiples" où aucune case
    n'a été cochée)."""
    if value is None:
        return True
    if isinstance(value, (str, list)):
        return len(value) == 0
    return False


def _clear_section_fields(section: dict, processed: dict):
    """Efface la valeur de tous les champs d'une section (utilisé quand la
    section entière n'est pas pertinente)."""
    for field in section.get("fields", []):
        processed[field["id"]] = None


def _input_field_ids(form_definition: dict):
    """Identifiants des champs de SAISIE (non calculés) uniquement — voir la
    note du docstring de module : seuls ceux-ci sont pré-remplis à None."""
    ids = []
    for section in form_definition.get("sections", []):
        for field in section.get("fields", []):
            if "id" in field and field.get("type") != "calculated":
                ids.append(field["id"])
    return ids


def validate_and_process_submission(form_definition: dict, responses: dict):
    # Les champs de saisie jamais envoyés deviennent explicitement vides,
    # AVANT d'évaluer la moindre condition (corrige un champ conditionnel
    # bloquant à tort une soumission valide).
    processed = {fid: None for fid in _input_field_ids(form_definition)}
    processed.update(responses)

    errors = []
    alerts = []

    # --- Phase 1 : pertinence (sections puis champs), avant tout calcul ------
    irrelevant_fields = set()
    for section in form_definition.get("sections", []):
        sec_relevant = section.get("relevant")
        if sec_relevant and not evaluate_condition(sec_relevant, processed, default=True):
            _clear_section_fields(section, processed)
            for f in section.get("fields", []):
                irrelevant_fields.add(f["id"])
            continue
        for field in section.get("fields", []):
            rel = field.get("relevant")
            if rel and not evaluate_condition(rel, processed, default=True):
                processed[field["id"]] = None
                irrelevant_fields.add(field["id"])

    # --- Phase 2 : calculs par ordre de dépendances ---------------------------
    # Passes répétées jusqu'à stabilisation : un calcul qui échoue parce qu'il
    # dépend d'un autre calcul pas encore fait est simplement réessayé au tour
    # suivant, jusqu'à ce que plus aucun ne progresse (dépendance circulaire ou
    # champ réellement inconnu — alors signalé comme erreur, pas de boucle infinie).
    calc_fields = []
    for section in form_definition.get("sections", []):
        for field in section.get("fields", []):
            if field.get("type") == "calculated" and field["id"] not in irrelevant_fields:
                calc_fields.append(field)

    pending = {f["id"]: f for f in calc_fields}
    while pending:
        progressed = False
        for fid in list(pending.keys()):
            field = pending[fid]
            formula = field.get("formula", "")
            try:
                processed[fid] = evaluate_formula(formula, processed)
                del pending[fid]
                progressed = True
            except FormulaError:
                continue  # dépend probablement d'un calcul pas encore fait -> réessai
            except ZeroDivisionError:
                errors.append(f"{field.get('label', fid)} : division par zéro dans le calcul.")
                del pending[fid]
                progressed = True
            except Exception as e:  # jamais de 500 silencieux sur un calcul cassé
                errors.append(f"{field.get('label', fid)} : erreur de calcul ({e}).")
                del pending[fid]
                progressed = True
        if not progressed:
            for fid, field in pending.items():
                try:
                    evaluate_formula(field.get("formula", ""), processed)
                except Exception as e:
                    errors.append(f"{field.get('label', fid)} : erreur de calcul ({e}).")
            break

    # --- Phase 3 : validation des champs de saisie + alertes -----------------
    for section in form_definition.get("sections", []):
        for field in section.get("fields", []):
            field_id = field["id"]
            if field_id in irrelevant_fields:
                continue
            field_type = field.get("type")
            label = field.get("label", field_id)

            if field_type != "calculated":
                value = processed.get(field_id)
                if field.get("required") and _is_empty(value):
                    errors.append(f"{label} est obligatoire.")
                elif not _is_empty(value):
                    if field_type == "boolean":
                        processed[field_id] = (str(value).lower() == "true")
                    elif field_type == "select_multiple":
                        processed[field_id] = value if isinstance(value, list) else [value]
                    elif field_type in ("number", "decimal"):
                        validation = field.get("validation", {})
                        try:
                            num_value = float(value)
                            processed[field_id] = num_value
                            if "min" in validation and num_value < validation["min"]:
                                errors.append(f"{label} doit être supérieur ou égal à {validation['min']}.")
                            if "max" in validation and num_value > validation["max"]:
                                errors.append(f"{label} doit être inférieur ou égal à {validation['max']}.")
                        except (TypeError, ValueError):
                            errors.append(f"{label} doit être un nombre.")
                    elif field_type == "text":
                        validation = field.get("validation", {})
                        pattern = validation.get("pattern")
                        if pattern:
                            try:
                                if not re.fullmatch(pattern, str(value)):
                                    errors.append(
                                        validation.get("pattern_message")
                                        or f"{label} : le format n'est pas valide."
                                    )
                            except re.error:
                                # Un motif invalide ne doit jamais faire planter une soumission —
                                # il aurait dû être détecté à l'import/l'enregistrement du formulaire.
                                pass

            alert_condition = field.get("alert_if")
            if alert_condition and evaluate_condition(alert_condition, processed, default=False):
                alerts.append({
                    "field_id": field_id,
                    "message": field.get("alert_message") or f"Alerte sur « {label} »",
                    "severity": field.get("alert_severity", "medium"),
                })

    return processed, errors, alerts
