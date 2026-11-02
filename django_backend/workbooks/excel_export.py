"""Export d'un classeur au format Excel (.xlsx).

Objectif : que le fichier s'ouvre dans Excel en restant VIVANT — les
formules continuent de se recalculer, elles ne sont pas figées en valeurs.
"""
import re

from openpyxl import Workbook as ExcelWorkbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

# Le fichier .xlsx stocke toujours les formules avec les noms ANGLAIS et la
# virgule comme séparateur d'arguments, quelle que soit la langue d'Excel —
# c'est Excel qui les retraduit à l'affichage. Une formule écrite "SOMME(A1;A2)"
# serait donc refusée à l'ouverture.
NOMS_VERS_ANGLAIS = {
    "SOMME": "SUM", "MOYENNE": "AVERAGE", "NB": "COUNT", "NBVAL": "COUNTA",
    "ARRONDI": "ROUND", "RACINE": "SQRT", "ENT": "INT", "PUISSANCE": "POWER",
    "MEDIANE": "MEDIAN", "SI": "IF", "ABS": "ABS", "MIN": "MIN", "MAX": "MAX",
}

FORMATS_EXCEL = {
    "nombre0": "#,##0",
    "nombre2": "#,##0.00",
    "monnaie": '#,##0 "FCFA"',
    # Excel multiplie lui-même par 100 avec ce format : on écrit donc la
    # valeur brute (0,15) et non la valeur affichée (15).
    "pourcent": "0.0%",
}

ALIGNEMENTS = {"left": "left", "center": "center", "right": "right"}
# Excel nomme "center" l'alignement vertical du milieu ; l'application le
# nomme "middle" (le mot que comprend CSS).
ALIGNEMENTS_VERTICAUX = {"top": "top", "middle": "center", "bottom": "bottom"}


def _traduire_formule(formule: str) -> str:
    """Convertit une formule de l'application vers la syntaxe du fichier Excel."""
    resultat = []
    i = 0
    while i < len(formule):
        c = formule[i]
        if c == '"':                       # chaîne : recopiée telle quelle
            j = formule.find('"', i + 1)
            j = len(formule) - 1 if j == -1 else j
            resultat.append(formule[i:j + 1])
            i = j + 1
            continue
        if c == ";":                       # séparateur d'arguments
            resultat.append(",")
            i += 1
            continue
        if c.isalpha():
            j = i
            while j < len(formule) and (formule[j].isalnum() or formule[j] in "_$"):
                j += 1
            mot = formule[i:j]
            if j < len(formule) and formule[j] == "(":
                resultat.append(NOMS_VERS_ANGLAIS.get(mot.upper(), mot.upper()))
            else:
                resultat.append(mot)
            i = j
            continue
        resultat.append(c)
        i += 1
    return "".join(resultat)


def _valeur_pour_excel(brute, format_nombre):
    """Convertit la saisie en une valeur qu'Excel comprend."""
    if brute is None or brute == "":
        return None
    texte = str(brute)
    if texte.startswith("="):
        return "=" + _traduire_formule(texte[1:])

    # Une saisie numérique doit arriver comme un NOMBRE dans Excel, sinon
    # elle y reste du texte et aucun calcul n'est possible dessus.
    normalise = texte.strip().replace(" ", "").replace(" ", "")
    if re.fullmatch(r"-?\d+(?:[.,]\d+)?", normalise):
        nombre = float(normalise.replace(",", "."))
        # Pas de conversion pour les pourcentages : l'application et Excel
        # utilisent la MEME echelle (0,15 s'affiche "15 %" des deux cotes).
        # Diviser par 100 ici transformerait 15 % en 0,15 % a l'ouverture.
        return int(nombre) if nombre.is_integer() else nombre
    return texte


def _couleur_excel(valeur):
    """"#aabbcc" -> "FFAABBCC" (openpyxl attend l'alpha en tête)."""
    if not valeur or not isinstance(valeur, str) or not valeur.startswith("#"):
        return None
    hexa = valeur[1:]
    if len(hexa) == 3:
        hexa = "".join(c * 2 for c in hexa)
    if len(hexa) != 6:
        return None
    return "FF" + hexa.upper()


def _appliquer_style(cellule, style, taille_base=11):
    couleur_texte = _couleur_excel(style.get("c"))
    cellule.font = Font(
        bold=bool(style.get("b")),
        italic=bool(style.get("i")),
        color=couleur_texte,
        size=round(taille_base * (style.get("fs", 100) / 100), 1),
    )
    if style.get("a") or style.get("va") or style.get("w"):
        cellule.alignment = Alignment(
            horizontal=ALIGNEMENTS.get(style.get("a")) if style.get("a") else None,
            vertical=ALIGNEMENTS_VERTICAUX.get(style.get("va")) if style.get("va") else None,
            wrap_text=bool(style.get("w")),
        )
    fond = _couleur_excel(style.get("bg"))
    if fond:
        cellule.fill = PatternFill(fill_type="solid", start_color=fond, end_color=fond)
    if style.get("nf") in FORMATS_EXCEL:
        cellule.number_format = FORMATS_EXCEL[style["nf"]]


def _appliquer_casse(valeur, style):
    """MAJUSCULES/minuscules sont un style d'affichage dans l'application ;
    Excel n'a pas d'équivalent, on applique donc la transformation au texte
    pour que le fichier ressemble à ce qui était affiché."""
    casse = style.get("tt")
    if not casse or not isinstance(valeur, str) or valeur.startswith("="):
        return valeur
    return valeur.upper() if casse == "upper" else valeur.lower()


def construire_xlsx(workbook) -> bytes:
    """Produit le contenu binaire du fichier .xlsx."""
    from io import BytesIO

    classeur = ExcelWorkbook()
    classeur.remove(classeur.active)

    feuilles = (workbook.content or {}).get("sheets") or []
    if not feuilles:
        feuilles = [{"name": "Feuille 1", "cells": {}, "merges": [], "rows": 1, "cols": 1}]

    for feuille in feuilles:
        # Excel refuse ces caractères dans un nom d'onglet, et le limite à 31
        # caractères : sans ce nettoyage, le fichier produit est illisible.
        nom = re.sub(r"[\\/*?:\[\]]", "-", str(feuille.get("name") or "Feuille"))[:31] or "Feuille"
        onglet = classeur.create_sheet(title=nom)

        for ref, cellule in (feuille.get("cells") or {}).items():
            m = re.fullmatch(r"([A-Z]+)(\d+)", ref.upper())
            if not m:
                continue
            style = cellule.get("s") or {}
            valeur = _valeur_pour_excel(cellule.get("v"), style.get("nf"))
            valeur = _appliquer_casse(valeur, style)
            if valeur is None and not style:
                continue
            case = onglet[ref.upper()]
            case.value = valeur
            if style:
                _appliquer_style(case, style)

        for fusion in feuille.get("merges") or []:
            try:
                onglet.merge_cells(
                    start_row=fusion["l"] + 1, start_column=fusion["c"] + 1,
                    end_row=fusion["l"] + fusion["rs"], end_column=fusion["c"] + fusion["cs"],
                )
            except (KeyError, TypeError, ValueError):
                continue  # fusion mal formée : on préfère un fichier sans elle

        for ligne, hauteur_px in (feuille.get("rowHeights") or {}).items():
            try:
                # Excel mesure les lignes en points (1 px ecran ~ 0,75 pt).
                onglet.row_dimensions[int(ligne) + 1].height = round(int(hauteur_px) * 0.75, 1)
            except (TypeError, ValueError):
                continue

        for colonne, largeur_px in (feuille.get("colWidths") or {}).items():
            try:
                # Excel mesure les colonnes en caractères, pas en pixels.
                lettre = get_column_letter(int(colonne) + 1)
                onglet.column_dimensions[lettre].width = max(3, round(int(largeur_px) / 7.5, 1))
            except (TypeError, ValueError):
                continue

    tampon = BytesIO()
    classeur.save(tampon)
    tampon.seek(0)
    return tampon.read()
