"""
Reconnaissance du format d'un fichier de STRUCTURE de formulaire, avant import.

Pourquoi ce module existe : l'import n'acceptait que notre propre modèle
(feuille "Structure", colonnes id/type/label/...). Or les questionnaires de
l'ONG naissent ailleurs — dans Kobo Toolbox surtout — et un export Kobo était
refusé avec un message qui envoyait chercher au mauvais endroit (« vérifie que
la colonne label est bien remplie », alors que la colonne s'appelait
"label::Français (fr)"). Chaque nouveau fichier demandait une conversion à la
main par un développeur : intenable pour une équipe qui n'en a pas sous la main.

Ce module fait donc deux choses :
  1. il reconnaît le format (modèle de l'application, export Kobo/XLSForm,
     tableau simple avec des en-têtes en français...) et ramène tout aux
     colonnes de notre modèle, que rows_to_sections() sait lire ;
  2. quand rien n'est reconnu, il explique CE QU'IL A VU (feuilles, colonnes)
     et ce qu'il attendait, pour que l'agent corrige son fichier sans aide.

Les quatre écarts entre Kobo et notre modèle, et comment on les traite :
  - le libellé est dans "label::Langue (xx)" → on prend la colonne "label"
    quelle que soit sa langue ;
  - "select_one oui_non" dans la colonne type → type seul + list_name ;
  - ${champ} et "=" dans les expressions → nom nu et "==" (notre moteur n'a
    pas de "=" seul : l'expression serait rejetée telle quelle) ;
  - une question sans libellé (typiquement un "calculate") → on lui en
    fabrique un, sinon elle disparaît à l'import et les formules qui en
    dépendent tombent sur une variable inexistante.
"""

import re
import unicodedata
from io import BytesIO

import pandas as pd

from core.spreadsheet_io import TEMPLATE_COLUMNS, _clean, rows_to_sections

# Pour chaque colonne de notre modèle, les en-têtes acceptés (après
# normalisation : minuscules, sans accents, espaces → "_", suffixe de langue
# "::xx" retiré). "name" est le nom de Kobo pour l'identifiant.
ALIAS = {
    "id": ("id", "name", "identifiant", "code", "variable"),
    "type": ("type",),
    "label": ("label", "libelle", "question", "intitule", "texte"),
    "hint": ("hint", "aide", "indication", "texte_d_aide"),
    "required": ("required", "obligatoire", "requis"),
    "min": ("min", "minimum"),
    "max": ("max", "maximum"),
    "pattern": ("pattern", "regex", "motif"),
    "pattern_message": ("pattern_message", "message_motif"),
    "options": ("options", "choix"),
    "list_name": ("list_name", "liste"),
    "formula": ("formula", "formule", "calculation", "calcul"),
    "relevant": ("relevant", "condition", "affiche_si", "visible_si"),
    "alert_if": ("alert_if", "alerte_si"),
    "alert_message": ("alert_message", "message_alerte"),
    "alert_severity": ("alert_severity", "gravite"),
}
_ALIAS_INVERSE = {a: canon for canon, alias in ALIAS.items() for a in alias}

# Lignes techniques de Kobo : pas de libellé, pas une question posée à
# l'agent. Notre modèle n'a pas d'équivalent, on les retire sans bruit.
META_XLSFORM = {"start", "end", "today", "username", "deviceid", "simserial",
                "phonenumber", "subscriberid", "audit", "caseid"}

TYPES_XLSFORM = {
    "text": "text", "integer": "number", "decimal": "decimal", "date": "date",
    "datetime": "date", "calculate": "calculated", "note": "note",
    "select_one": "select_one", "select_multiple": "select_multiple",
    "range": "number", "time": "text",
}

FONCTIONS_MOTEUR = {"if", "selected", "coalesce", "pow", "sqrt", "abs", "round"}
MOTS_RESERVES = {"and", "or", "not", "true", "false"} | FONCTIONS_MOTEUR


def _normaliser_entete(valeur) -> str:
    s = unicodedata.normalize("NFKD", str(valeur)).encode("ascii", "ignore").decode()
    s = s.strip().lower()
    s = s.split("::")[0].strip()          # "label::français (fr)" → "label"
    s = re.sub(r"[\s\-]+", "_", s)
    return s


def _renommer_colonnes(df: pd.DataFrame) -> tuple:
    """Ramène les en-têtes aux noms de notre modèle. Renvoie (df, reconnues,
    ignorées) pour pouvoir expliquer à l'agent ce qui a été compris."""
    nouvelles, reconnues, ignorees = [], [], []
    for col in df.columns:
        norm = _normaliser_entete(col)
        canon = _ALIAS_INVERSE.get(norm)
        if canon and canon not in nouvelles:
            nouvelles.append(canon)
            reconnues.append(canon)
        else:
            nouvelles.append(norm)
            ignorees.append(str(col).strip())
    df = df.copy()
    df.columns = nouvelles
    return df, reconnues, ignorees


def _nom_feuille(xls: pd.ExcelFile, voulu: str):
    for nom in xls.sheet_names:
        if nom.strip().lower() == voulu:
            return nom
    return None


# --- Détection --------------------------------------------------------------

def detecter(content: bytes, filename: str) -> dict:
    """Dit quel format on a sous les yeux, et ce qu'on y voit. Ne lit que les
    en-têtes : c'est aussi ce qui alimente le message d'erreur."""
    info = {"format": "tableau", "feuilles": [], "feuille_lue": None, "colonnes": [],
            "reconnues": [], "ignorees": []}

    if filename.lower().endswith(".csv"):
        info["format"] = "csv"
        df = pd.read_csv(BytesIO(content), sep=None, engine="python", nrows=0)
    else:
        with pd.ExcelFile(BytesIO(content)) as xls:
            info["feuilles"] = list(xls.sheet_names)
            if _nom_feuille(xls, "survey"):
                info["format"] = "xlsform"
                info["feuille_lue"] = _nom_feuille(xls, "survey")
            elif _nom_feuille(xls, "structure"):
                info["format"] = "modele"
                info["feuille_lue"] = _nom_feuille(xls, "structure")
            else:
                info["feuille_lue"] = xls.sheet_names[0]
            df = pd.read_excel(xls, sheet_name=info["feuille_lue"], nrows=0)

    info["colonnes"] = [str(c).strip() for c in df.columns]
    _, info["reconnues"], info["ignorees"] = _renommer_colonnes(df)
    return info


# --- Kobo / XLSForm ---------------------------------------------------------

def _traduire_expression(expr: str, ou: str, avertissements: list, abandonnable: bool) -> str:
    """Passe une expression de la syntaxe Kobo à celle de notre moteur.

    `abandonnable` vaut vrai pour une condition d'affichage : si elle est
    intraduisible, mieux vaut la retirer (la question reste alors toujours
    visible) que de laisser une expression que le moteur rejettera, ce qui
    ferait échouer l'import entier. Une question affichée à tort se voit et
    se saute ; une question cachée à tort fait perdre la donnée sans alerte.
    Une formule de calcul, elle, n'est jamais retirée : on la garde et le
    validateur la signalera, parce qu'un calcul silencieusement absent est
    pire qu'un import refusé."""
    if not expr:
        return ""
    sortie = re.sub(r"\$\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}", r"\1", expr)
    sortie = re.sub(r"(?<![=!<>])=(?!=)", "==", sortie)   # "=" seul → "==", sans toucher ==, !=, <=, >=
    sortie = re.sub(r"\bdiv\b", "/", sortie)

    inconnues = [f for f in re.findall(r"([a-zA-Z_][a-zA-Z0-9_\-]*)\s*\(", sortie)
                 if f not in FONCTIONS_MOTEUR]
    if inconnues:
        if abandonnable:
            avertissements.append(
                f"{ou} : la fonction « {inconnues[0]} » n'existe pas dans l'application. "
                f"Condition retirée, la question sera toujours affichée. "
                f"Expression d'origine : {expr}")
            return ""
        avertissements.append(
            f"{ou} : la fonction « {inconnues[0]} » n'existe pas dans l'application, "
            f"la formule est à réécrire.")
    if re.search(r"(?<![0-9A-Za-z_])\.(?![0-9])", sortie):
        avertissements.append(f"{ou} : contient un « . » (le « self » de Kobo) — à vérifier.")
    return sortie


def _bornes_depuis_contrainte(contrainte: str, nom_champ: str) -> tuple:
    """Un constraint de la forme ${x} >= A and ${x} <= B devient min/max :
    strictement équivalent, et bloquant comme chez Kobo. Toute autre forme est
    signalée plutôt que traduite à l'aveugle — une condition médicale mal
    transposée passerait inaperçue."""
    if not contrainte:
        return "", "", True
    c = contrainte.replace("${" + nom_champ + "}", "SELF")
    c = re.sub(r"(?<![0-9A-Za-z_])\.(?![0-9])", "SELF", c)
    m = re.fullmatch(r"\s*SELF\s*>=\s*(-?\d+\.?\d*)\s+and\s+SELF\s*<=\s*(-?\d+\.?\d*)\s*", c, re.I)
    if m:
        return m.group(1), m.group(2), True
    m = re.fullmatch(r"\s*SELF\s*>=\s*(-?\d+\.?\d*)\s*", c, re.I)
    if m:
        return m.group(1), "", True
    m = re.fullmatch(r"\s*SELF\s*<=\s*(-?\d+\.?\d*)\s*", c, re.I)
    if m:
        return "", m.group(1), True
    return "", "", False


def _lire_choices(content: bytes, nom_feuille: str) -> dict:
    """Feuille de listes de choix, dans notre forme (list_name/value/label)
    comme dans celle de Kobo (list_name/name/label::xx)."""
    with pd.ExcelFile(BytesIO(content)) as xls:
        reel = _nom_feuille(xls, nom_feuille)
        if not reel:
            return {}
        df = pd.read_excel(xls, sheet_name=reel)
    df.columns = [_normaliser_entete(c) for c in df.columns]
    listes: dict = {}
    for r in df.to_dict(orient="records"):
        nom = _clean(r.get("list_name"))
        valeur = _clean(r.get("value")) or _clean(r.get("name"))
        libelle = _clean(r.get("label"))
        if nom and valeur:
            listes.setdefault(nom, []).append({"value": valeur, "label": libelle or valeur})
    return listes


def _convertir_xlsform(content: bytes, feuille_survey: str) -> tuple:
    """Renvoie (lignes normalisées, listes de choix, avertissements)."""
    avertissements: list = []
    df = pd.read_excel(BytesIO(content), sheet_name=feuille_survey)
    # La table d'alias fait le gros du travail : "name" → id, "calculation"
    # → formula, "label::Français (fr)" → label. Les colonnes propres a Kobo
    # (constraint, appearance) restent sous leur nom normalise.
    df, _, _ = _renommer_colonnes(df)

    lignes: list = []
    noms_gardes: set = set()
    for r in df.to_dict(orient="records"):
        brut = _clean(r.get("type"))
        if not brut:
            continue
        morceaux = brut.split()
        tete = morceaux[0].lower().replace("-", "_")
        if tete in ("begin", "end") and len(morceaux) > 1:      # "begin group" avec espace
            tete = f"{tete}_{morceaux[1].lower()}"
            morceaux = [tete] + morceaux[2:]
        nom = _clean(r.get("id"))          # "name" chez Kobo, renommé "id" par l'alias
        libelle = _clean(r.get("label"))

        if tete in META_XLSFORM:
            continue

        if tete in ("begin_group", "end_group", "begin_repeat", "end_repeat"):
            if tete.endswith("repeat"):
                avertissements.append(
                    f"« {nom or libelle} » : groupe répétable (repeat), non géré — aplati en section simple.")
            marqueur = "begin_group" if tete.startswith("begin") else "end_group"
            ligne = {"type": marqueur, "label": libelle}
            if marqueur == "begin_group":
                ligne["relevant"] = _traduire_expression(
                    _clean(r.get("relevant")), f"section « {libelle or nom} »", avertissements, True)
            lignes.append(ligne)
            continue

        if tete not in TYPES_XLSFORM:
            avertissements.append(f"« {nom} » : type « {brut} » sans équivalent — question retirée.")
            continue

        type_app = TYPES_XLSFORM[tete]
        liste = ""
        if tete.startswith("select") and len(morceaux) > 1:
            liste = morceaux[1]
            if "or_other" in morceaux[2:]:
                avertissements.append(f"« {nom} » : l'option « autre » libre (or_other) n'est pas reprise.")

        if not libelle:
            libelle = (nom or "question").replace("_", " ").capitalize()
            avertissements.append(
                f"« {nom} » : aucun libellé dans le fichier — « {libelle} » lui a été donné (champ {type_app}).")

        mini, maxi, ok = _bornes_depuis_contrainte(_clean(r.get("constraint")), nom)
        if not ok:
            avertissements.append(
                f"« {nom} » : contrainte « {_clean(r.get('constraint'))} » non traduite — à reporter à la main.")
        if _clean(r.get("appearance")):
            avertissements.append(
                f"« {nom} » : apparence « {_clean(r.get('appearance'))} » ignorée (sans effet ici).")

        lignes.append({
            "id": nom, "type": type_app, "label": libelle,
            "hint": _clean(r.get("hint")),
            "required": "oui" if _clean(r.get("required")).lower() in ("yes", "true", "1", "oui") else "",
            "min": mini, "max": maxi, "list_name": liste,
            "formula": _traduire_expression(
                _clean(r.get("formula")), f"calcul « {nom} »", avertissements, False),
            "relevant": _traduire_expression(
                _clean(r.get("relevant")), f"affichage « {nom} »", avertissements, True),
        })
        noms_gardes.add(nom)

    # Un ${champ} qui visait une ligne technique ou retirée ne vaut plus rien :
    # le moteur tomberait sur une variable inexistante au moment de la saisie.
    # Les valeurs entre guillemets sont du texte, pas des références.
    for l in lignes:
        for cle in ("relevant", "formula"):
            hors_texte = re.sub(r"'[^']*'|\"[^\"]*\"", " ", l.get(cle) or "")
            for ident in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", hors_texte):
                if ident not in noms_gardes and ident not in MOTS_RESERVES:
                    avertissements.append(
                        f"« {l.get('id') or l.get('label')} » : {cle} utilise « {ident} », "
                        f"qui n'existe plus après conversion.")

    # Kobo laisse souvent les "calculate" après le dernier end_group. Sans
    # groupe, ils finiraient dans une section au titre par défaut
    # ("Questions") — trompeur pour un agent, puisque ce sont des valeurs
    # calculées seules. On les regroupe sous un titre honnête.
    fin = len(lignes)
    while fin > 0 and lignes[fin - 1].get("type") == "calculated":
        fin -= 1
    if fin < len(lignes) and (fin == 0 or lignes[fin - 1].get("type") == "end_group"):
        lignes.insert(fin, {"type": "begin_group", "label": "Variables calculées (automatique)"})
        lignes.append({"type": "end_group", "label": ""})

    choix = _lire_choices(content, "choices")
    utilisees = {l["list_name"] for l in lignes if l.get("list_name")}
    for manquante in sorted(utilisees - set(choix)):
        avertissements.append(f"Liste « {manquante} » utilisée mais absente de la feuille choices.")

    return lignes, choix, avertissements


# --- Point d'entrée ---------------------------------------------------------

def lire_structure(content: bytes, filename: str) -> tuple:
    """Renvoie (sections, choice_lists, rapport). Le rapport dit quel format a
    été reconnu et liste les avertissements à montrer à l'agent : ce qui a été
    adapté, retiré ou inventé pendant la conversion ne doit jamais être
    silencieux sur un questionnaire médical."""
    info = detecter(content, filename)

    if info["format"] == "xlsform":
        lignes, choice_lists, avertissements = _convertir_xlsform(content, info["feuille_lue"])
    else:
        if info["format"] == "csv":
            df = pd.read_csv(BytesIO(content), sep=None, engine="python")
            choice_lists = {}
        else:
            df = pd.read_excel(BytesIO(content), sheet_name=info["feuille_lue"])
            choice_lists = _lire_choices(content, "choices")
        df, _, _ = _renommer_colonnes(df)
        lignes = df.to_dict(orient="records")
        avertissements = []

    sections = rows_to_sections(lignes)
    rapport = {**info, "avertissements": list(dict.fromkeys(avertissements))}
    return sections, choice_lists, rapport


NOMS_FORMATS = {
    "modele": "modèle de l'application (feuille « Structure »)",
    "xlsform": "export Kobo / XLSForm (feuille « survey »)",
    "csv": "fichier CSV",
    "tableau": "tableau simple (première feuille)",
}


def expliquer_echec(rapport: dict) -> str:
    """Message d'erreur quand aucune question n'a été reconnue. Il décrit ce
    que l'application a VU, pas seulement ce qu'elle attendait : c'est la
    seule façon pour l'agent de comprendre ce qui cloche sans développeur."""
    lignes = ["Aucune question reconnue dans ce fichier."]
    lignes.append(f"Format détecté : {NOMS_FORMATS.get(rapport['format'], rapport['format'])}.")
    if rapport.get("feuilles"):
        lignes.append("Feuilles présentes : " + ", ".join(rapport["feuilles"]) + ".")
    if rapport.get("feuille_lue"):
        lignes.append(f"Feuille lue : {rapport['feuille_lue']}.")
    colonnes = rapport.get("colonnes") or []
    lignes.append("Colonnes trouvées : " + (", ".join(colonnes[:20]) if colonnes else "aucune")
                  + (" …" if len(colonnes) > 20 else "") + ".")
    reconnues = rapport.get("reconnues") or []
    if reconnues:
        lignes.append("Colonnes comprises : " + ", ".join(reconnues) + ".")
    if "label" not in reconnues:
        lignes.append("Il manque la colonne du libellé des questions : « label » "
                      "(ou « libellé », « question », ou « label::Langue » comme dans Kobo).")
    elif "type" not in reconnues:
        lignes.append("Il manque la colonne « type » (text, number, date, select_one…).")
    else:
        lignes.append("Les colonnes attendues sont là mais toutes les lignes ont un libellé vide.")
    lignes.append("Formats acceptés : le modèle de l'application (à télécharger depuis un "
                  "formulaire existant) ou un export Kobo/XLSForm (feuilles survey + choices). "
                  "Un fichier de RÉPONSES (une ligne par patient) n'est pas une structure de formulaire.")
    return " ".join(lignes)
