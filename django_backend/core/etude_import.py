"""
Import d'une ÉTUDE déjà réalisée : un fichier de réponses (Google Forms,
export Kobo de données, tableau quelconque) dont on reconstruit à la fois le
questionnaire et les fiches.

Pourquoi : l'ONG a des études terminées avant Clinique App — questionnaire
Google Forms, 400 répondantes, résultats dans un Excel. L'import de
structure (form_import_formats) ne sait lire qu'une liste de questions ; un
fichier de réponses est construit à l'envers (une ligne = une personne, une
colonne = une question). Or ce fichier contient TOUT : la première ligne est
le questionnaire, les suivantes sont les données. Ce module tire les deux du
même fichier.

Il est volontairement sans Django : il lit un tableau, devine, prépare. Les
écritures en base, la revalidation de chaque fiche et le journal d'activité
sont dans clinical_forms/etudes.py, qui réutilise les mêmes règles qu'une
saisie à la main (codes patients, revalidation serveur, idempotence).

Ce que la devinette fait, et pourquoi elle reste modifiable par l'agent :
- le RÔLE d'une colonne (prénom, nom, téléphone, code, date de collecte...)
  est deviné d'après l'en-tête. Ces colonnes deviennent le patient, pas des
  questions. Une devinette d'en-tête se trompe parfois ("nom_structure" n'est
  pas un nom de patient) : d'où l'écran de vérification avant import.
- le TYPE d'une question est deviné d'après les VALEURS, pas l'en-tête : des
  dates → date, des nombres → nombre, peu de valeurs distinctes → choix
  unique, les colonnes "Question [option]" d'une grille Google Forms → une
  seule question à choix multiples.
- l'identité est obligatoire côté application (prénom et nom), pas dans une
  étude anonymisée : sans nom, on pose « Patient » + le code. C'est une
  convention honnête, visible, et réversible.
"""

import hashlib
import json
import re
import unicodedata
from datetime import date, datetime
from io import BytesIO

import pandas as pd

from core.spreadsheet_io import slugify_py

ROLES = ("question", "prenom", "nom", "code", "telephone", "sexe",
         "date_naissance", "annee_naissance", "date_collecte", "ignorer")
ROLES_UNIQUES = {"prenom", "nom", "code", "telephone", "sexe",
                 "date_naissance", "annee_naissance", "date_collecte"}
TYPES_QUESTION = ("text", "number", "decimal", "date", "select_one", "select_multiple")

# Au-delà de ce nombre de valeurs distinctes, une colonne n'est plus une liste
# de choix mais du texte libre.
MAX_CHOIX = 12
# Valeurs qui veulent dire "non coché" dans une grille à cases.
NEGATIFS = {"", "non", "no", "0", "false", "faux", "nan", "none", "-"}
GRILLE = re.compile(r"^(.*?)\s*\[(.+?)\]\s*$")


def _norm(texte) -> str:
    s = unicodedata.normalize("NFKD", str(texte)).encode("ascii", "ignore").decode()
    return re.sub(r"[\s\-]+", "_", s.strip().lower())


def _vide(v) -> bool:
    if v is None:
        return True
    try:
        if pd.isna(v):
            return True
    except (TypeError, ValueError):
        pass
    return str(v).strip() == ""


def _texte(v) -> str:
    if _vide(v):
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _nombre(v):
    if _vide(v):
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _date(v):
    """Renvoie une date ou None. Accepte les vraies dates Excel, l'ISO, et le
    format français jj/mm/aaaa — les trois cohabitent dans un export."""
    if _vide(v):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, pd.Timestamp):
        return v.date()
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(s[:len(fmt) + 2 if "%H" in fmt else len(s)], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s).date()
    except ValueError:
        return None


def _datetime(v):
    if _vide(v):
        return None
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime().replace(tzinfo=None)
    if isinstance(v, datetime):
        return v.replace(tzinfo=None)
    d = _date(v)
    return datetime(d.year, d.month, d.day) if d else None


def _sexe(v):
    """"F"/"M" comme dans l'application. "Homme" commence par H : on le
    traite explicitement, sinon il tomberait dans "non renseigné"."""
    s = _norm(_texte(v))
    if not s:
        return None
    if s[0] == "f" or s.startswith("w"):
        return "F"
    if s[0] in ("m", "h"):
        return "M"
    return None


# --- Lecture ----------------------------------------------------------------

def lire_tableau(content: bytes, filename: str) -> tuple:
    """(en-têtes, lignes) — chaque ligne est un dict en-tête → valeur brute.
    dtype=object pour garder les vrais types Excel (dates, nombres) au lieu
    de tout aplatir en texte."""
    if filename.lower().endswith(".csv"):
        df = pd.read_csv(BytesIO(content), sep=None, engine="python", dtype=object)
    else:
        df = pd.read_excel(BytesIO(content), sheet_name=0, dtype=object)
    entetes = [str(c).strip() for c in df.columns]
    df.columns = entetes
    lignes = [r for r in df.to_dict(orient="records")
              if any(not _vide(v) for v in r.values())]
    return entetes, lignes


# --- Devinette des rôles ----------------------------------------------------

def _role_devine(entete: str) -> str:
    n = _norm(entete)
    if re.search(r"correo|e_?mail|courriel", n):
        return "ignorer"
    if re.search(r"marca_temporal|timestamp|horodat", n):
        return "date_collecte"
    if re.search(r"^date_(enquete|collecte|visite|depistage|entretien)$|^date$", n):
        return "date_collecte"
    if re.search(r"prenom|first_?name", n):
        return "prenom"
    if re.search(r"nom_?famille|last_?name|^nom(_patient\w*)?$|^nom_?de_?famille", n):
        return "nom"
    if re.search(r"code_?patient|id_?patient|identifiant_?patient|^code$|^id_?client$", n):
        return "code"
    if re.search(r"tel|phone", n):
        return "telephone"
    if re.search(r"^sexe?$|^genre$|^sex$", n):
        return "sexe"
    if re.search(r"date_?(de_?)?naiss|birth_?date|^dob$", n):
        return "date_naissance"
    if re.search(r"annee_?(de_?)?naiss|year_?of_?birth|birth_?year", n):
        return "annee_naissance"
    return "question"


def _est_horodatage(entete: str) -> bool:
    return bool(re.search(r"marca_temporal|timestamp|horodat", _norm(entete)))


def _score_role(entete: str, role: str) -> int:
    """Quand plusieurs colonnes se disputent un rôle d'identité, laquelle le
    mérite ? "telephone1_patiente" plus que "avez_internet_dans_telephone_
    portable" ; "date_enquete" plus que l'horodatage automatique de Google."""
    n = _norm(entete)
    score = 0
    if "patient" in n:
        score += 1
    if role == "telephone" and re.match(r"^(tel|phone|numero|portable|mobile)", n):
        score += 2
    if role == "date_collecte" and _est_horodatage(entete):
        score -= 2
    return score


def _meme_base(a: str, b: str) -> bool:
    """"telephone2_patiente" est une copie de "telephone1_patiente",
    "code_patienteQ2" de "code_patiente" : même nom une fois les numéros ôtés."""
    return re.sub(r"q?\d+", "", _norm(a)) == re.sub(r"q?\d+", "", _norm(b))


# --- Devinette des types ----------------------------------------------------

def _type_devine(valeurs: list) -> tuple:
    """(type, options). Décide d'après les valeurs non vides."""
    pleines = [v for v in valeurs if not _vide(v)]
    if not pleines:
        return "text", []
    if all(_date(v) is not None for v in pleines) and not all(_nombre(v) is not None for v in pleines):
        return "date", []
    nombres = [_nombre(v) for v in pleines]
    if all(n is not None for n in nombres):
        return ("number" if all(float(n).is_integer() for n in nombres) else "decimal"), []
    textes = [_texte(v) for v in pleines]
    distincts = list(dict.fromkeys(textes))
    # Cases à cocher Google Forms dans UNE colonne : "fièvre, douleur, toux".
    if any(", " in t for t in textes):
        jetons = list(dict.fromkeys(j.strip() for t in textes for j in t.split(",") if j.strip()))
        if 1 < len(jetons) <= MAX_CHOIX * 2 and len(jetons) < len(textes):
            return "select_multiple", _options(jetons)
    # Une liste de choix, ça se répète : au moins une valeur doit apparaître
    # deux fois. Sinon "RAS" / "Contrôle" sur trois lignes passerait pour une
    # liste, alors que c'est du texte libre. Au-delà de quatre valeurs, on
    # exige en plus que les répétitions dominent, pas un hasard sur du texte.
    if (len(distincts) <= MAX_CHOIX and len(pleines) > len(distincts)
            and (len(distincts) <= 4 or len(pleines) >= 2 * len(distincts))):
        return "select_one", _options(distincts)
    return "text", []


def _options(libelles: list) -> list:
    options, vus = [], set()
    for lib in libelles:
        val = slugify_py(lib)[:50] or "valeur"
        base, n = val, 2
        while val in vus:
            val = f"{base}_{n}"
            n += 1
        vus.add(val)
        options.append({"value": val, "label": lib})
    return options


def _id_unique(base: str, pris: set) -> str:
    ident = slugify_py(base)[:60] or "question"
    cand, n = ident, 2
    while cand in pris:
        cand = f"{ident}_{n}"
        n += 1
    pris.add(cand)
    return cand


# --- Analyse ----------------------------------------------------------------

def analyser(content: bytes, filename: str) -> dict:
    """Propose, pour chaque colonne (ou groupe de colonnes d'une grille), un
    rôle et un type. Rien n'est écrit : c'est l'écran de vérification."""
    entetes, lignes = lire_tableau(content, filename)
    colonnes: list = []
    ids: set = set()
    avertissements: list = []

    # Grilles Google Forms : "Symptômes [fièvre]", "Symptômes [toux]"... → une
    # seule question à choix multiples, une option par colonne.
    grilles: dict = {}
    for h in entetes:
        m = GRILLE.match(h)
        if m:
            grilles.setdefault(m.group(1).strip(), []).append((h, m.group(2).strip()))
    grilles = {b: cols for b, cols in grilles.items() if len(cols) >= 2}
    deja_dans_grille = {h for cols in grilles.values() for h, _ in cols}

    # Un rôle d'identité ne peut aller qu'à UNE colonne. Quand plusieurs se le
    # disputent, la mieux notée l'emporte (voir _score_role) ; à égalité, la
    # première du fichier. Les perdantes ne sont pas jetées : une vraie copie
    # (telephone2, code_patienteQ2) ou l'horodatage Google sont ignorés, mais
    # "avez_internet_dans_telephone_portable" est une question, pas un numéro.
    simples = [h for h in entetes if h not in deja_dans_grille]
    devines = {h: _role_devine(h) for h in simples}
    attribue: dict = {}
    for role in ROLES_UNIQUES:
        candidats = [h for h in simples if devines[h] == role]
        if candidats:
            candidats.sort(key=lambda h: -_score_role(h, role))     # tri stable : ordre du fichier à score égal
            attribue[candidats[0]] = role
    roles_pris = set(attribue.values())

    for h in simples:
        valeurs = [l.get(h) for l in lignes]
        role = devines[h]
        if role in ROLES_UNIQUES and h not in attribue:
            gagnant = next(g for g, r in attribue.items() if r == role)
            role = "ignorer" if (_meme_base(h, gagnant) or _est_horodatage(h)) else "question"
        elif role in ROLES_UNIQUES:
            role = attribue[h]
        pleines = [v for v in valeurs if not _vide(v)]
        exemples = list(dict.fromkeys(_texte(v) for v in pleines))[:3]
        col = {"entete": h, "sources": [h], "role": role, "type": "text", "options": [],
               "label": h, "id": "", "remplies": len(pleines), "distinctes": len(set(_texte(v) for v in pleines)),
               "exemples": exemples}
        if role == "question":
            col["type"], col["options"] = _type_devine(valeurs)
            col["id"] = _id_unique(h, ids)
        colonnes.append(col)

    for base, cols in grilles.items():
        col = {"entete": f"{base} [grille de {len(cols)} cases]", "sources": [h for h, _ in cols],
               "role": "question", "type": "select_multiple",
               "options": _options([opt for _, opt in cols]),
               "label": base, "id": _id_unique(base, ids),
               "remplies": sum(1 for l in lignes if any(not _vide(l.get(h)) for h, _ in cols)),
               "distinctes": len(cols), "exemples": [opt for _, opt in cols][:3]}
        colonnes.append(col)

    if "date_collecte" not in roles_pris:
        avertissements.append("Aucune colonne de date reconnue : les fiches seront datées du jour de l'import.")
    if "prenom" not in roles_pris and "nom" not in roles_pris:
        if "code" in roles_pris:
            avertissements.append("Pas de nom de patient : chaque patient sera nommé « Patient » suivi de son code.")
        else:
            avertissements.append("Ni nom ni code patient reconnus : désigne une colonne d'identité, "
                                  "sinon chaque ligne deviendra un patient sans nom.")
    if not any(c["role"] == "question" for c in colonnes):
        avertissements.append("Aucune colonne ne ressemble à une question.")

    return {"fichier": filename, "lignes": len(lignes), "colonnes": colonnes,
            "avertissements": avertissements}


# --- Construction du formulaire ---------------------------------------------

def construire_definition(nom_etude: str, colonnes: list, fichier: str = "") -> dict:
    """Le questionnaire, tel que l'application le stocke. Une seule section :
    Google Forms n'en a pas, et inventer un découpage serait mentir."""
    fields = []
    for c in colonnes:
        if c.get("role") != "question":
            continue
        f = {"id": c["id"], "type": c["type"], "label": c.get("label") or c["entete"]}
        if c["type"] in ("select_one", "select_multiple"):
            f["options"] = c.get("options") or []
        fields.append(f)
    return {
        "sections": [{"id": "questions", "title": nom_etude, "fields": fields}],
        # Trace de l'origine : sert à retrouver ce formulaire si le même
        # fichier est réimporté (jamais deux formulaires pour une étude).
        "etude_import": {"nom": nom_etude, "fichier": fichier},
    }


# --- Préparation d'une ligne ------------------------------------------------

def _valeur(col: dict, ligne: dict):
    t = col["type"]
    if col["type"] == "select_multiple" and len(col["sources"]) > 1:
        choisis = []
        for src, opt in zip(col["sources"], col["options"]):
            if _texte(ligne.get(src)).lower() not in NEGATIFS:
                choisis.append(opt["value"])
        return choisis or None
    brut = ligne.get(col["sources"][0])
    if _vide(brut):
        return None
    if t == "select_multiple":
        par_libelle = {o["label"]: o["value"] for o in col["options"]}
        return [par_libelle.get(j.strip(), slugify_py(j.strip())) for j in _texte(brut).split(",") if j.strip()]
    if t == "select_one":
        par_libelle = {o["label"]: o["value"] for o in col["options"]}
        return par_libelle.get(_texte(brut), slugify_py(_texte(brut)))
    if t in ("number", "decimal"):
        n = _nombre(brut)
        return n if n is not None else _texte(brut)   # laissé au validateur : "doit être un nombre"
    if t == "date":
        d = _date(brut)
        return d.isoformat() if d else _texte(brut)
    return _texte(brut)


def preparer_ligne(ligne: dict, colonnes: list, numero: int) -> dict:
    """Sépare ce qui devient le patient de ce qui devient la fiche, et calcule
    une signature stable de la ligne (pour ne jamais l'importer deux fois)."""
    patient = {"prenom": "", "nom": "", "code": "", "telephone": "", "sexe": None, "date_naissance": None}
    responses: dict = {}
    collected_at = None
    avertissements: list = []

    for c in colonnes:
        role = c.get("role")
        if role == "ignorer":
            continue
        if role == "question":
            v = _valeur(c, ligne)
            if v is not None:
                responses[c["id"]] = v
            continue
        brut = ligne.get(c["sources"][0])
        if role in ("prenom", "nom", "code", "telephone"):
            patient[role] = _texte(brut)
        elif role == "sexe":
            patient["sexe"] = _sexe(brut)
            if patient["sexe"] is None and not _vide(brut):
                avertissements.append(f"ligne {numero} : sexe « {_texte(brut)} » non reconnu, laissé vide.")
        elif role == "date_naissance":
            patient["date_naissance"] = _date(brut)
        elif role == "annee_naissance":
            n = _nombre(brut)
            if n and 1900 < n <= date.today().year:
                # Année seule : le 1er juillet minimise l'erreur moyenne (six
                # mois au pire), sans conséquence sur des tranches de 5 ans.
                patient["date_naissance"] = date(int(n), 7, 1)
        elif role == "date_collecte":
            collected_at = _datetime(brut)

    if not patient["prenom"] and not patient["nom"]:
        if patient["code"]:
            patient["prenom"], patient["nom"] = "Patient", patient["code"]
        else:
            patient["prenom"], patient["nom"] = "Patient", f"ligne {numero}"
            avertissements.append(f"ligne {numero} : aucune identité, patient nommé « Patient ligne {numero} ».")
    elif not patient["prenom"]:
        patient["prenom"] = "—"
    elif not patient["nom"]:
        patient["nom"] = patient["code"] or "—"

    brut_trie = {h: _texte(v) for h, v in sorted(ligne.items(), key=lambda kv: str(kv[0]))}
    signature = hashlib.sha256(json.dumps(brut_trie, ensure_ascii=False).encode()).hexdigest()

    return {"patient": patient, "responses": responses, "collected_at": collected_at,
            "signature": signature, "avertissements": avertissements}


def cle_idempotence(project_id: str, nom_etude: str, signature: str) -> str:
    """64 caractères max (contrainte du modèle). Dépend du projet et du nom de
    l'étude : la même ligne dans deux études distinctes reste deux fiches."""
    h = hashlib.sha256(f"{project_id}|{nom_etude}|{signature}".encode()).hexdigest()
    return f"etude:{h[:56]}"
