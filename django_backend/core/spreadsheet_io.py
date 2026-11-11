"""
Export des données recueillies (CSV / Excel) et export/import de la
STRUCTURE d'un formulaire (au format tableur, pour dupliquer/sauvegarder/
construire un formulaire hors ligne dans Excel).

Note importante : le format de structure utilisé ici est NOTRE PROPRE format
(colonnes id/type/label/...), pas le format XLSForm de Kobo.org — les deux
sont différents en interne, notamment sur la syntaxe des conditions. Un
fichier exporté directement depuis Kobo.org ne pourra donc pas être importé
tel quel ici.

Depuis cette version : gère aussi
- les conditions "relevant" au niveau d'une SECTION entière (colonne
  "relevant" de la ligne begin_group),
- les listes de choix réutilisables entre plusieurs questions (feuille Excel
  séparée "Choices", exactement comme la feuille "choices" de Kobo.org),
- un texte d'aide ("hint") par question,
- une validation par expression régulière ("pattern"/"pattern_message").

La feuille "Choices" (et donc les listes réutilisables) n'existe qu'au
format Excel — un simple CSV n'a qu'une seule feuille, donc l'import CSV
reste limité aux options définies en ligne (colonne "options").
"""

import re
import unicodedata
from io import BytesIO

import pandas as pd

TEMPLATE_COLUMNS = [
    "id", "type", "label", "hint", "required", "min", "max", "pattern", "pattern_message",
    "options", "list_name", "formula", "relevant", "alert_if", "alert_message", "alert_severity",
]
CHOICES_COLUMNS = ["list_name", "value", "label"]


def slugify_py(text: str) -> str:
    s = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    if not s:
        s = "champ"
    if s[0].isdigit():
        s = "q_" + s
    return s


# --- Export de la STRUCTURE d'un formulaire (pour sauvegarde / duplication) ---

def field_to_row(field: dict) -> dict:
    options = field.get("options") or []
    options_str = "|".join(f"{o.get('value', '')}:{o.get('label', '')}" for o in options)
    validation = field.get("validation") or {}
    return {
        "id": field.get("id", ""),
        "type": field.get("type", "text"),
        "label": field.get("label", ""),
        "hint": field.get("hint", ""),
        "required": "oui" if field.get("required") else "",
        "min": validation.get("min", ""),
        "max": validation.get("max", ""),
        "pattern": validation.get("pattern", ""),
        "pattern_message": validation.get("pattern_message", ""),
        "options": "" if field.get("list_name") else options_str,
        "list_name": field.get("list_name", ""),
        "formula": field.get("formula", ""),
        "relevant": field.get("relevant", ""),
        "alert_if": field.get("alert_if", ""),
        "alert_message": field.get("alert_message", ""),
        "alert_severity": field.get("alert_severity", ""),
    }


def build_template_bytes(form_name: str, sections: list, choice_lists: dict | None = None) -> bytes:
    """Construit le fichier Excel de structure : une feuille "Structure" avec
    une paire de lignes begin_group / end_group autour de chaque section
    (avec sa condition d'affichage éventuelle dans la colonne "relevant" de
    la ligne begin_group — exactement la convention XLSForm de Kobo.org),
    et une feuille "Choices" listant les listes de choix réutilisables."""
    empty_row = {col: "" for col in TEMPLATE_COLUMNS}
    rows = []
    for section in sections:
        begin_row = dict(empty_row)
        begin_row["type"] = "begin_group"
        begin_row["label"] = section.get("title", "")
        begin_row["relevant"] = section.get("relevant", "")
        rows.append(begin_row)

        for f in section.get("fields", []):
            rows.append(field_to_row(f))

        end_row = dict(empty_row)
        end_row["type"] = "end_group"
        rows.append(end_row)

    df = pd.DataFrame(rows, columns=TEMPLATE_COLUMNS)

    choice_rows = []
    for list_name, options in (choice_lists or {}).items():
        for o in options:
            choice_rows.append({"list_name": list_name, "value": o.get("value", ""), "label": o.get("label", "")})
    choices_df = pd.DataFrame(choice_rows, columns=CHOICES_COLUMNS)

    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Structure")
        choices_df.to_excel(writer, index=False, sheet_name="Choices")
    buffer.seek(0)
    return buffer.read()


# --- Import de la STRUCTURE d'un formulaire depuis un fichier (CSV ou Excel) ---

def _clean(value) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    return "" if s.lower() == "nan" else s


def _parse_choices_sheet(content: bytes) -> dict:
    """Lit la feuille "Choices" d'un fichier Excel (si elle existe) et
    construit le dictionnaire des listes de choix réutilisables."""
    try:
        df = pd.read_excel(BytesIO(content), sheet_name="Choices")
    except (ValueError, KeyError):
        return {}
    df.columns = [str(c).strip().lower() for c in df.columns]

    choice_lists: dict = {}
    for row in df.to_dict(orient="records"):
        list_name = _clean(row.get("list_name"))
        value = _clean(row.get("value"))
        label = _clean(row.get("label"))
        if not list_name or not value:
            continue
        choice_lists.setdefault(list_name, []).append({"value": value, "label": label or value})
    return choice_lists


def row_to_field(row: dict, used_ids: set) -> dict | None:
    label = _clean(row.get("label"))
    if not label:
        return None

    field_id = _clean(row.get("id")) or slugify_py(label)
    base_id = field_id
    n = 2
    while field_id in used_ids:
        field_id = f"{base_id}_{n}"
        n += 1
    used_ids.add(field_id)

    field_type = _clean(row.get("type")).lower() or "text"
    field = {"id": field_id, "type": field_type, "label": label}

    hint = _clean(row.get("hint"))
    if hint:
        field["hint"] = hint

    if _clean(row.get("required")).lower() in ("oui", "true", "1", "yes", "vrai"):
        field["required"] = True

    validation = {}
    min_val, max_val = _clean(row.get("min")), _clean(row.get("max"))
    if min_val:
        try:
            validation["min"] = float(min_val)
        except ValueError:
            pass
    if max_val:
        try:
            validation["max"] = float(max_val)
        except ValueError:
            pass
    pattern = _clean(row.get("pattern"))
    if pattern:
        validation["pattern"] = pattern
        pattern_message = _clean(row.get("pattern_message"))
        if pattern_message:
            validation["pattern_message"] = pattern_message
    if validation:
        field["validation"] = validation

    list_name = _clean(row.get("list_name"))
    if list_name:
        field["list_name"] = list_name
    else:
        options_str = _clean(row.get("options"))
        if options_str:
            options = []
            for part in options_str.split("|"):
                if ":" in part:
                    val, lbl = part.split(":", 1)
                    options.append({"value": val.strip(), "label": lbl.strip()})
            if options:
                field["options"] = options

    for key in ("formula", "relevant", "alert_if", "alert_message", "alert_severity"):
        v = _clean(row.get(key))
        if v:
            field[key] = v

    return field


def parse_spreadsheet_to_sections(content: bytes, filename: str):
    """Lit un fichier CSV/Excel et le transforme en (sections, choice_lists).

    Conservée pour les appels existants : tout passe désormais par
    core.form_import_formats, qui reconnaît le format du fichier (modèle de
    l'application, export Kobo/XLSForm, colonnes en français...) avant de
    confier les lignes normalisées à rows_to_sections() ci-dessous."""
    from core.form_import_formats import lire_structure  # import tardif : ce module nous importe

    sections, choice_lists, _rapport = lire_structure(content, filename)
    return sections, choice_lists


def rows_to_sections(rows: list) -> list:
    """Transforme des lignes DÉJÀ normalisées (clés = colonnes du modèle de
    l'application : id/type/label/...) en liste de sections.

    Reconnaît les lignes begin_group / end_group (convention XLSForm de Kobo)
    pour délimiter les groupes, avec la condition d'affichage de la section
    dans la colonne "relevant" du begin_group. Un fichier sans ces marqueurs
    (les anciens exports, ou un fichier tout simple) donne une seule section
    par défaut — pleinement rétrocompatible."""
    used_ids: set = set()
    sections = []
    section_counter = 1

    def new_default_section():
        return {"id": "main", "title": "Questions", "fields": []}

    current_section = new_default_section()

    for row in rows:
        row_type = _clean(row.get("type")).lower()

        if row_type == "begin_group":
            if current_section["fields"]:
                sections.append(current_section)
            title = _clean(row.get("label")) or f"Section {section_counter}"
            current_section = {"id": slugify_py(title) or f"section_{section_counter}", "title": title, "fields": []}
            relevant = _clean(row.get("relevant"))
            if relevant:
                current_section["relevant"] = relevant
            section_counter += 1
            continue

        if row_type == "end_group":
            sections.append(current_section)
            current_section = new_default_section()
            continue

        field = row_to_field(row, used_ids)
        if field:
            current_section["fields"].append(field)

    if current_section["fields"]:
        sections.append(current_section)

    if not sections:
        sections = [new_default_section()]

    return sections


def _has_sheet(content: bytes, sheet_name: str) -> bool:
    try:
        with pd.ExcelFile(BytesIO(content)) as xls:
            return sheet_name in xls.sheet_names
    except Exception:
        return False


# --- Export des DONNÉES recueillies (soumissions) ---

def _expand_row_and_columns(fields: list, rows: list, fixed_columns: list = None, fixed_labels: dict = None):
    """Construit la liste des colonnes et aplatit chaque ligne, en transformant
    chaque question "choix multiples" en plusieurs colonnes binaires (0/1),
    une par choix possible — c'est le format directement exploitable pour une
    analyse statistique (fréquences, chi², OR, régression logistique), y
    compris dans un outil comme Epi Info.

    Déduplique aussi systématiquement les noms de colonnes : si un champ
    "choix multiples" a une option nommée "autre" (colonne générée
    "xxx_autre") ET qu'un AUTRE champ du formulaire s'appelle justement
    "xxx_autre" (le classique champ texte "Précisez l'autre raison"), les
    deux ne doivent pas finir avec le même nom de colonne dans l'export.

    `fixed_columns`/`fixed_labels` : colonnes "d'identité" placées avant les
    questions du formulaire, réutilisées telles quelles depuis chaque `row`
    (par défaut, celles de l'export "une ligne par dépistage" ; l'export
    "une ligne par personne", dans main.py, passe ses propres colonnes —
    identifiant, sexe, âge, nombre de dépistages... — via ces paramètres,
    tout en réutilisant cette même fonction pour les questions du formulaire)."""
    if fixed_columns is None:
        # "age" et "sexe" sont les deux seules données personnelles du patient
        # incluses dans cet export — exception délibérée au principe général
        # "aucune donnée personnelle dans les exports" (anonymat + inutile à
        # l'analyse) : ces deux-là sont au contraire essentielles à l'analyse
        # statistique et ne sont pas identifiantes. L'âge est calculé à la
        # date de LA SOUMISSION (voir main.py), pas à la date d'aujourd'hui.
        # "code_anonyme" est affiché CÔTE À CÔTE avec "patient" (le nom),
        # jamais à sa place — c'est à qui exporte de retirer la colonne
        # "patient" lui-même selon la situation (ex: avant l'envoi à un
        # bailleur), pas une case "anonymiser" automatique.
        # nb_depistages_periode/premier_depistage/dernier_depistage : calculés
        # sur l'ensemble déjà filtré par date, puis répétés sur chacune des
        # lignes de ce patient (une ligne = un dépistage, mais ce trio donne
        # le contexte temporel complet sans avoir à regrouper les lignes
        # soi-même dans le tableur).
        fixed_columns = [
            "id_soumission", "patient", "code_anonyme", "age", "sexe", "date",
            "nb_depistages_periode", "premier_depistage", "dernier_depistage",
        ]
    if fixed_labels is None:
        fixed_labels = {
            "id_soumission": "ID soumission", "patient": "Patient", "code_anonyme": "Code anonyme",
            "age": "Âge", "sexe": "Sexe", "date": "Date",
            "nb_depistages_periode": "Nombre de dépistages (période)",
            "premier_depistage": "Premier dépistage", "dernier_depistage": "Dernier dépistage",
        }
    columns = list(fixed_columns)
    used_column_names = set(columns)
    simple_column_map = {}   # field_id -> nom de colonne final
    multi_column_map = {}    # field_id -> liste de (valeur_option, nom de colonne final)
    column_labels = dict(fixed_labels)

    def unique_name(base):
        name = base
        n = 2
        while name in used_column_names:
            name = f"{base}_{n}"
            n += 1
        used_column_names.add(name)
        return name

    for f in fields:
        if f.get("type") == "note":
            continue
        if f.get("type") == "select_multiple":
            option_columns = []
            for o in (f.get("options") or []):
                opt_val = o.get("value", "")
                col_name = unique_name(f"{f['id']}_{opt_val}")
                columns.append(col_name)
                option_columns.append((opt_val, col_name))
                column_labels[col_name] = f"{f.get('label', '')} = {o.get('label', '')} (1 = oui, 0 = non)"
            multi_column_map[f["id"]] = option_columns
        else:
            col_name = unique_name(f["id"])
            columns.append(col_name)
            simple_column_map[f["id"]] = col_name
            column_labels[col_name] = f.get("label", "")

    expanded_rows = []
    for row in rows:
        new_row = {col: (row.get(col) if row.get(col) is not None else "") for col in fixed_columns}
        for f in fields:
            if f.get("type") == "note":
                continue
            fid = f["id"]
            if fid in multi_column_map:
                raw = row.get(fid)
                # None = section/champ masqué par une condition d'affichage (non
                # applicable) -> on ne doit surtout pas afficher 0 partout, ce qui
                # laisserait croire qu'aucune case n'a été cochée alors que la
                # question ne s'appliquait pas.
                if raw is None and fid in row:
                    for opt_val, col_name in multi_column_map[fid]:
                        new_row[col_name] = "N/A"
                else:
                    selected = raw or []
                    if isinstance(selected, str):
                        selected = [selected]  # tolère d'anciennes soumissions à valeur unique
                    for opt_val, col_name in multi_column_map[fid]:
                        new_row[col_name] = 1 if opt_val in selected else 0
            elif fid in simple_column_map:
                value = row.get(fid)
                new_row[simple_column_map[fid]] = "N/A" if value is None else value
        expanded_rows.append(new_row)

    return columns, expanded_rows, column_labels


DEFAULT_FIXED_COLUMNS = [
    "id_soumission", "patient", "code_anonyme", "age", "sexe", "date",
    "nb_depistages_periode", "premier_depistage", "dernier_depistage",
]
DEFAULT_FIXED_LABELS = {
    "id_soumission": "ID soumission", "patient": "Patient", "code_anonyme": "Code anonyme",
    "age": "Âge", "sexe": "Sexe", "date": "Date",
    "nb_depistages_periode": "Nombre de dépistages (période)",
    "premier_depistage": "Premier dépistage", "dernier_depistage": "Dernier dépistage",
}


def build_data_export_bytes(form_name: str, fields: list, rows: list, fmt: str,
                            fixed_columns: list = None, fixed_labels: dict = None):
    columns, expanded_rows, column_labels = _expand_row_and_columns(
        fields, rows, fixed_columns, fixed_labels)
    df = pd.DataFrame(expanded_rows, columns=columns)

    if fmt == "xlsx":
        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Données")
            identity_columns = set(fixed_columns if fixed_columns is not None else DEFAULT_FIXED_COLUMNS)
            dict_rows = [{"id": col, "label": column_labels.get(col, "")}
                         for col in columns if col not in identity_columns]
            pd.DataFrame(dict_rows, columns=["id", "label"]).to_excel(writer, index=False, sheet_name="Dictionnaire")
        buffer.seek(0)
        return buffer.read(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
    else:
        csv_text = df.to_csv(index=False, sep=";")
        return ("\ufeff" + csv_text).encode("utf-8"), "text/csv", "csv"


def build_patient_export_bytes(rows: list, columns: list, fmt: str):
    """Export anonymisé « une ligne par personne » (pas par dépistage), pour
    analyse statistique/épidémiologique — voir build_data_export_bytes pour
    l'export « une ligne par dépistage »."""
    df = pd.DataFrame(rows, columns=columns)

    if fmt == "xlsx":
        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Personnes")
        buffer.seek(0)
        return buffer.read(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
    else:
        csv_text = df.to_csv(index=False, sep=";")
        return ("\ufeff" + csv_text).encode("utf-8"), "text/csv", "csv"


def build_patient_data_export_bytes(fields: list, rows: list, fixed_columns: list, fixed_labels: dict, fmt: str):
    """Comme build_patient_export_bytes (une ligne par personne), mais inclut
    en plus les reponses du formulaire, eclatees exactement comme dans
    build_data_export_bytes (choix multiples -> colonnes binaires, "N/A" pour
    un champ masque par une condition d'affichage). Chaque `row` doit deja
    contenir, en plus des `fixed_columns` (identifiant, sexe, age...), les
    reponses BRUTES de la soumission choisie pour representer cette personne
    (voir main.py : la plus recente) sous leurs `field_id` d'origine."""
    columns, expanded_rows, column_labels = _expand_row_and_columns(fields, rows, fixed_columns, fixed_labels)
    df = pd.DataFrame(expanded_rows, columns=columns)

    if fmt == "xlsx":
        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Personnes")
            dict_rows = [{"id": col, "label": column_labels.get(col, "")} for col in columns if col not in fixed_columns]
            pd.DataFrame(dict_rows, columns=["id", "label"]).to_excel(writer, index=False, sheet_name="Dictionnaire")
        buffer.seek(0)
        return buffer.read(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
    else:
        csv_text = df.to_csv(index=False, sep=";")
        return ("\ufeff" + csv_text).encode("utf-8"), "text/csv", "csv"
