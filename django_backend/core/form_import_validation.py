"""
Vérifie qu'un fichier de formulaire importé (généré par une IA, ou écrit à la
main) respecte la structure attendue, avant de l'enregistrer en base.

Format attendu (voir aussi le guide d'import) :
{
  "choice_lists": {
    "oui_non": [{"value": "oui", "label": "Oui"}, {"value": "non", "label": "Non"}]
  },
  "sections": [
    {
      "id": "...", "title": "...",
      "relevant": "...",   (optionnel : condition d'affichage de la SECTION entière)
      "fields": [
        {
          "id": "identifiant_technique", "type": "text|number|decimal|date|
                select_one|select_multiple|boolean|calculated|note",
          "label": "Texte affiché",
          "hint": "Texte d'aide affiché sous la question (optionnel)",
          "required": true,
          "validation": {"min": 0, "max": 100, "pattern": "regex", "pattern_message": "..."},
          "options": [{"value": "...", "label": "..."}],
          "list_name": "oui_non",   (alternative à "options" : référence une liste partagée)
          "formula": "...",
          "relevant": "...",
          "alert_if": "...", "alert_message": "...", "alert_severity": "medium"
        }
      ]
    }
  ]
}
"""

import re

from core.formula_engine import validate_formula_syntax

ALLOWED_FIELD_TYPES = {
    "text", "number", "decimal", "date", "select_one", "select_multiple",
    "boolean", "calculated", "note",
}
ALLOWED_SEVERITIES = {"low", "medium", "high", "critical"}


def _validate_choice_lists(choice_lists):
    errors = []
    if choice_lists is None:
        return errors, {}
    if not isinstance(choice_lists, dict):
        return ["\"choice_lists\" doit être un objet (nom de liste -> liste de choix)."], {}

    for list_name, options in choice_lists.items():
        if not isinstance(options, list) or len(options) == 0:
            errors.append(f"Liste de choix '{list_name}' : doit contenir au moins une option.")
            continue
        for opt in options:
            if not isinstance(opt, dict) or "value" not in opt or "label" not in opt:
                errors.append(f"Liste de choix '{list_name}' : chaque option doit avoir 'value' et 'label'.")
                break
    return errors, choice_lists


def validate_form_definition(definition):
    errors = []

    if not isinstance(definition, dict):
        return ["Le fichier doit contenir un objet JSON (pas une liste ou un texte brut)."]

    sections = definition.get("sections")
    if not isinstance(sections, list) or len(sections) == 0:
        return ["Le fichier doit contenir au moins une section : \"sections\": [ ... ]."]

    choice_list_errors, choice_lists = _validate_choice_lists(definition.get("choice_lists"))
    errors.extend(choice_list_errors)

    seen_ids = set()

    for s_idx, section in enumerate(sections):
        if not isinstance(section, dict):
            errors.append(f"Section {s_idx + 1} : doit être un objet.")
            continue

        section_relevant = section.get("relevant")
        if section_relevant:
            err = validate_formula_syntax(section_relevant)
            if err:
                errors.append(f"Section {s_idx + 1} ('{section.get('title', '')}') : condition invalide ({err}).")

        fields = section.get("fields")
        if not isinstance(fields, list):
            errors.append(f"Section {s_idx + 1} : \"fields\" doit être une liste.")
            continue

        for f_idx, field in enumerate(fields):
            if not isinstance(field, dict):
                errors.append(f"Section {s_idx + 1}, champ {f_idx + 1} : doit être un objet.")
                continue

            field_id = field.get("id")
            field_type = field.get("type")

            if not field_id or not isinstance(field_id, str):
                errors.append(f"Section {s_idx + 1}, champ {f_idx + 1} : \"id\" manquant ou invalide.")
                continue
            if field_id in seen_ids:
                errors.append(f"Identifiant de champ en double : '{field_id}'.")
            seen_ids.add(field_id)

            if field_type not in ALLOWED_FIELD_TYPES:
                errors.append(
                    f"Champ '{field_id}' : type inconnu '{field_type}' "
                    f"(types valides : {', '.join(sorted(ALLOWED_FIELD_TYPES))})."
                )
                continue

            if not field.get("label"):
                errors.append(f"Champ '{field_id}' : \"label\" manquant.")

            if field_type in ("select_one", "select_multiple"):
                list_name = field.get("list_name")
                if list_name:
                    if list_name not in choice_lists:
                        errors.append(
                            f"Champ '{field_id}' : référence la liste '{list_name}', "
                            f"absente de \"choice_lists\"."
                        )
                else:
                    options = field.get("options")
                    if not isinstance(options, list) or len(options) == 0:
                        errors.append(f"Champ '{field_id}' : options manquantes (ou \"list_name\") pour un champ à choix.")
                    else:
                        for opt in options:
                            if not isinstance(opt, dict) or "value" not in opt or "label" not in opt:
                                errors.append(f"Champ '{field_id}' : chaque option doit avoir 'value' et 'label'.")
                                break

            if field_type == "calculated":
                formula = field.get("formula")
                if not formula:
                    errors.append(f"Champ '{field_id}' : formule manquante pour un champ calculé.")
                else:
                    err = validate_formula_syntax(formula)
                    if err:
                        errors.append(f"Champ '{field_id}' : formule invalide ({err}).")

            for cond_key in ("relevant", "alert_if"):
                cond = field.get(cond_key)
                if cond:
                    err = validate_formula_syntax(cond)
                    if err:
                        errors.append(f"Champ '{field_id}' : condition '{cond_key}' invalide ({err}).")

            severity = field.get("alert_severity")
            if severity and severity not in ALLOWED_SEVERITIES:
                errors.append(
                    f"Champ '{field_id}' : sévérité d'alerte inconnue '{severity}' "
                    f"(valeurs valides : {', '.join(sorted(ALLOWED_SEVERITIES))})."
                )

            validation = field.get("validation")
            if isinstance(validation, dict) and validation.get("pattern"):
                try:
                    re.compile(validation["pattern"])
                except re.error as e:
                    errors.append(f"Champ '{field_id}' : expression régulière invalide ({e}).")

    return errors
