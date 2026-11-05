"""Export du tableau de bord (CSV ou Excel) : le contexte, le detail par site
et la serie mensuelle, tels qu'affiches a l'ecran.

Le fichier part souvent tel quel chez le bailleur : il doit dire lui-meme de
quoi il parle (bailleur, projet, indicateur, periode, filtres), sans qu'on
ait besoin de la capture d'ecran a cote.
"""
from io import BytesIO, StringIO

import pandas as pd

from cases.exports import XLSX_CONTENT_TYPE
from indicators.services import NOT_PROVIDED, STATUS_LABELS, month_label

SEX_LABELS = {"F": "Femmes", "M": "Hommes"}


def _context_rows(payload):
    f = payload["filters"]
    site_label = "Tous les sites"
    if f["site"]:
        match = next((s for s in payload["sites"] if s["code"] == f["site"]), None)
        site_label = match["name"] if match else (NOT_PROVIDED if f["site"] == "nr" else f["site"])
    age_label = "Toutes"
    if f["age"]:
        match = next((b for b in payload["age_bands"] if b["key"] == f["age"]), None)
        age_label = match["label"] if match else NOT_PROVIDED
    ind = payload["indicator"]
    period = payload["period"]
    computed = payload["computed_at"].strftime("%d/%m/%Y %H:%M")
    return [
        ("Bailleur", payload["donor"] or "Sans bailleur"),
        ("Projet", payload["project"]["name"]),
        ("Indicateur", ind["name"]),
        ("Type de calcul", ind["calculation_label"]),
        ("Unité", ind["unit"] or ""),
        ("Période", f"{month_label(period['start'])} – {month_label(period['end'])}"),
        ("Site / structure sanitaire", site_label),
        ("Sexe", SEX_LABELS.get(f["sexe"], "Tous")),
        ("Tranche d'âge", age_label),
        ("Calculé le", computed),
    ]


def _site_frame(payload):
    lines = payload["by_site"] + [payload["by_site_total"]]
    return pd.DataFrame([{
        "Site": l["site"],
        "Région": l["region"],
        "Femmes": l["F"],
        "Hommes": l["M"],
        "Réalisé": l["value"],
        "Cible": l["target"],
        "Taux d'atteinte (%)": l["progress"],
        "Statut": STATUS_LABELS.get(l["status"], ""),
    } for l in lines])


def _month_frame(payload):
    return pd.DataFrame([{
        "Mois": month_label(m["period"]),
        "Réalisé": m["value"],
        "Cible": m["target"],
        "Réalisé cumulé": m["cumulative"],
        "Cible cumulée": m["cumulative_target"],
    } for m in payload["by_month"]])


def _as_float(df, columns):
    # Colonnes numeriques forcees en flottant : sinon un melange d'entiers et
    # de vides reste en "object" et pandas n'applique pas la virgule decimale.
    for c in columns:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def build_dashboard_export(payload, export_format):
    context = pd.DataFrame(_context_rows(payload), columns=["Champ", "Valeur"])
    sites = _as_float(_site_frame(payload),
                      ["Femmes", "Hommes", "Réalisé", "Cible", "Taux d'atteinte (%)"])
    months = _as_float(_month_frame(payload),
                       ["Réalisé", "Cible", "Réalisé cumulé", "Cible cumulée"])

    if export_format == "xlsx":
        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            context.to_excel(writer, index=False, sheet_name="Contexte")
            sites.to_excel(writer, index=False, sheet_name="Détail par site")
            months.to_excel(writer, index=False, sheet_name="Série mensuelle")
            for sheet in writer.sheets.values():
                for column in sheet.columns:
                    width = max(len(str(c.value or "")) for c in column)
                    sheet.column_dimensions[column[0].column_letter].width = min(max(width + 2, 10), 60)
        return buffer.getvalue(), XLSX_CONTENT_TYPE, "xlsx"

    # CSV : trois blocs separes par une ligne vide. BOM + ";" + virgule
    # decimale : c'est ce qu'attend Excel en francais pour ouvrir le fichier
    # d'un double-clic, accents et nombres compris (sinon 92.4 devient du
    # texte, ou une date).
    out = StringIO()
    context.to_csv(out, index=False, sep=";")
    for title, frame in (("Détail par site", sites), ("Série mensuelle", months)):
        out.write(f"\n{title}\n")
        frame.to_csv(out, index=False, sep=";", decimal=",")
    return ("﻿" + out.getvalue()).encode("utf-8"), "text/csv", "csv"
