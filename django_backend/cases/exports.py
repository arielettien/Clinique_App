"""Exports et statistiques des dossiers de suivi."""
import re
from datetime import datetime, time
from io import BytesIO

import pandas as pd
from django.db.models import Count, Q
from django.http import FileResponse
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from cases.models import Case
from cases.services import serialize_case
from clinical_forms.exports import _parse_date, colonnes_identite, lignes_par_evenement
from clinical_forms.models import Form
from clinical_forms.services import fields_of
from core.audit import log_audit
from core.spreadsheet_io import _expand_row_and_columns, slugify_py
from core.utils import age_at
from patients.models import Patient
from projects.models import Project
from submissions.models import Submission

XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _dataframe_to_bytes(df, export_format, sheet_name):
    if export_format == "xlsx":
        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name=sheet_name)
        buffer.seek(0)
        return buffer.read(), XLSX_CONTENT_TYPE, "xlsx"
    # Marque d'ordre des octets (BOM) + separateur ";" : c'est ce qui permet a
    # Excel en francais d'ouvrir le fichier avec les accents corrects et les
    # colonnes bien separees, sans passer par un assistant d'import.
    content = ("﻿" + df.to_csv(index=False, sep=";")).encode("utf-8")
    return content, "text/csv", "csv"


def _attachment(content, media_type, filename):
    response = FileResponse(BytesIO(content), content_type=media_type)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _bad_format(export_format):
    return Response(
        {"detail": f"Format d'export non reconnu : '{export_format}' (csv ou xlsx attendu)."},
        status=status.HTTP_400_BAD_REQUEST,
    )


# --- Periode et dossiers --------------------------------------------------

def _periode(request):
    """(debut, fin) en dates, chacune facultative (absente = sans limite).
    Une periode a l'envers est refusee : elle donnerait un fichier vide, que
    l'agent prendrait pour « aucun dossier » au lieu d'une erreur de saisie."""
    debut = _parse_date(request.query_params.get("start"))
    fin = _parse_date(request.query_params.get("end"))
    if debut and fin and debut > fin:
        raise ValueError("La date de début est postérieure à la date de fin.")
    return debut, fin


def _texte_periode(debut, fin):
    if not debut and not fin:
        return "toute la période"
    d = debut.strftime("%d/%m/%Y") if debut else "le début"
    f = fin.strftime("%d/%m/%Y") if fin else "aujourd'hui"
    return f"du {d} au {f}"


def _dossiers_de_la_periode(project_id, debut, fin):
    """Les dossiers OUVERTS a un moment de la periode : inscrits avant sa fin,
    et pas clotures (ni perdus de vue) avant son debut. Pour un rapport
    annuel, un dossier clos l'annee precedente n'en fait pas partie ; un
    dossier ouvert en 2025 et toujours actif, si."""
    query = Case.objects.filter(project_id=project_id).select_related("patient")
    if fin:
        query = query.filter(enrolled_at__lte=datetime.combine(fin, time.max))
    if debut:
        query = query.filter(Q(closed_at__isnull=True) | Q(closed_at__gte=datetime.combine(debut, time.min)))
    return query.order_by("enrolled_at")


def _fiches_de_la_periode(query, debut, fin):
    if debut:
        query = query.filter(collected_at__gte=datetime.combine(debut, time.min))
    if fin:
        query = query.filter(collected_at__lte=datetime.combine(fin, time.max))
    return query


COLONNES_DOSSIERS = [
    "numero_dossier", "patient", "code_anonyme", "age", "sexe", "statut", "inscrit_le",
    "derniere_visite", "nb_visites", "nb_visites_periode", "prochain_rdv", "en_retard",
    "cloture_le", "motif_cloture",
]
LIBELLES_DOSSIERS = {
    "numero_dossier": "N° de dossier", "patient": "Patient (nom — à retirer avant tout envoi extérieur)",
    "code_anonyme": "Code anonyme", "age": "Âge aujourd'hui", "sexe": "Sexe",
    "statut": "Statut : active = en cours, closed = clos, lost = perdu de vue",
    "inscrit_le": "Date d'inscription", "derniere_visite": "Date de la dernière visite",
    "nb_visites": "Nombre de visites (toutes dates)", "nb_visites_periode": "Nombre de visites sur la période",
    "prochain_rdv": "Prochain rendez-vous prévu", "en_retard": "Rendez-vous dépassé (oui / non)",
    "cloture_le": "Date de clôture", "motif_cloture": "Motif de clôture",
}


def _lignes_dossiers(cases, debut, fin):
    """Une ligne par dossier. Le prochain rendez-vous et le retard sont ceux
    calcules par serialize_case — les memes que dans la liste a l'ecran."""
    ids = [c.id for c in cases]
    visites_periode = dict(
        _fiches_de_la_periode(Submission.objects.filter(case_id__in=ids), debut, fin)
        .values_list("case_id").annotate(n=Count("id")))
    today = datetime.utcnow().date()
    rows = []
    for c in cases:
        s = serialize_case(c)
        # Age a aujourd'hui, et non a une date de visite precise : un dossier
        # couvre une periode, pas un instant. Coherent avec l'age affiche a
        # l'ecran dans la liste des dossiers.
        age_value = age_at(s["patient_date_of_birth"], today)
        rows.append({
            "numero_dossier": s["case_number"],
            "patient": s["patient_name"],
            "code_anonyme": s["patient_anonymous_code"] or "",
            "age": age_value if age_value is not None else "",
            "sexe": s["patient_sex"] or "",
            "statut": s["status"],
            "inscrit_le": s["enrolled_at"].strftime("%Y-%m-%d") if s["enrolled_at"] else "",
            "derniere_visite": s["last_visit_at"].strftime("%Y-%m-%d") if s["last_visit_at"] else "",
            "nb_visites": s["visit_count"],
            "nb_visites_periode": visites_periode.get(c.id, 0),
            "prochain_rdv": s["next_visit_at"].isoformat() if s["next_visit_at"] else "",
            "en_retard": "oui" if s["is_overdue"] else "non",
            "cloture_le": s["closed_at"].strftime("%Y-%m-%d") if s["closed_at"] else "",
            "motif_cloture": s["closed_reason"] or "",
        })
    return rows


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def export_cases(request, project_id):
    """Export de la liste des dossiers de suivi — une ligne par DOSSIER
    (identite, statut, dates, prochain rendez-vous, motif de cloture). Le
    contenu des visites est dans l'export par formulaire, ou dans l'export
    complet ci-dessous qui reunit les deux."""
    export_format = request.query_params.get("format", "csv")
    if export_format not in ("csv", "xlsx"):
        return _bad_format(export_format)

    project = Project.objects.filter(pk=project_id).first()
    if not project:
        return Response({"detail": "Projet introuvable"}, status=status.HTTP_404_NOT_FOUND)
    try:
        debut, fin = _periode(request)
    except ValueError as e:
        return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    query = _dossiers_de_la_periode(project_id, debut, fin)
    case_status = request.query_params.get("status")
    if case_status:
        query = query.filter(status=case_status)
    rows = _lignes_dossiers(list(query), debut, fin)

    content, media_type, ext = _dataframe_to_bytes(
        pd.DataFrame(rows, columns=COLONNES_DOSSIERS), export_format, "Dossiers")

    log_audit(request, user=request.user, action="export", entity_type="case_list",
              entity_id=project_id, project_id=project_id,
              details=f"Export de la liste des dossiers ({export_format}, {_texte_periode(debut, fin)}) "
                      f"— {len(rows)} ligne(s)")
    return _attachment(content, media_type, f"{slugify_py(project.name)}_dossiers.{ext}")


# --- Export complet du suivi ----------------------------------------------

_INTERDITS_FEUILLE = re.compile(r"[\[\]:*?/\\]")
# Ordre des feuilles de visites : celui du parcours d'un patient.
_ORDRE_TYPES = {"registration": 0, "follow_up": 1, "closure": 2}


def _nom_de_feuille(nom, pris):
    """Excel impose : 31 caracteres au plus, ni [ ] : * ? / \\, et des noms
    uniques sans tenir compte des majuscules. Deux formulaires au nom
    identique au-dela de 31 caracteres recoivent donc un suffixe."""
    base = _INTERDITS_FEUILLE.sub(" ", nom or "").strip().strip("'")[:31] or "Formulaire"
    candidat, n = base, 2
    while candidat.lower() in {p.lower() for p in pris}:
        suffixe = f" ({n})"
        candidat = base[:31 - len(suffixe)] + suffixe
        n += 1
    pris.add(candidat)
    return candidat


def _mettre_en_forme(ws, df):
    """En-tete fige et colonnes a une largeur lisible : le fichier est ouvert
    par des agents dans Excel, pas par un logiciel de statistiques."""
    ws.freeze_panes = "A2"
    for i, col in enumerate(df.columns, start=1):
        valeurs = [len(str(v)) for v in df[col].head(200)]
        largeur = max([len(str(col))] + valeurs)
        ws.column_dimensions[get_column_letter(i)].width = min(max(10, largeur + 2), 45)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def export_suivi_complet(request, project_id):
    """Tout le suivi d'une periode dans un seul classeur Excel : une feuille
    « Dossiers », une feuille par formulaire de visite, et le numero de
    dossier sur chaque ligne de visite pour les relier.

    C'est le format des logiciels de suivi reconnus (export complet de
    REDCap, exports « cases » + « forms » de CommCare, DHIS2 Tracker) : une
    ligne par visite, jamais « Visite1, Visite2... » sur une seule ligne,
    qui casse des que les patients n'ont pas le meme nombre de visites et
    qu'aucun logiciel de statistiques n'attend. Excel uniquement : un CSV
    n'a qu'une feuille."""
    project = Project.objects.filter(pk=project_id).first()
    if not project:
        return Response({"detail": "Projet introuvable"}, status=status.HTTP_404_NOT_FOUND)
    try:
        debut, fin = _periode(request)
    except ValueError as e:
        return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    cases = list(_dossiers_de_la_periode(project_id, debut, fin))
    df_dossiers = pd.DataFrame(_lignes_dossiers(cases, debut, fin), columns=COLONNES_DOSSIERS)

    # Les visites sont prises par leur date, pas par l'appartenance de leur
    # dossier a la liste ci-dessus : une visite de la periode appartient
    # forcement a un dossier ouvert pendant la periode.
    fiches = _fiches_de_la_periode(
        Submission.objects.filter(case__project_id=project_id), debut, fin,
    ).select_related("patient", "case", "form").order_by("collected_at")
    par_formulaire = {}
    for s in fiches:
        par_formulaire.setdefault(s.form_id, (s.form, []))[1].append(s)
    formulaires = sorted(par_formulaire.values(),
                         key=lambda fs: (_ORDRE_TYPES.get(fs[0].form_type, 3), fs[0].name.lower()))

    base_url = request.build_absolute_uri("/").rstrip("/")
    noms_pris = {"À lire", "Dossiers", "Dictionnaire"}
    feuilles = []                 # (nom, DataFrame)
    dictionnaire = [{"feuille": "Dossiers", "colonne": c, "libelle": LIBELLES_DOSSIERS[c]}
                    for c in COLONNES_DOSSIERS]
    for form, subs in formulaires:
        fields = fields_of(form)
        colonnes, libelles = colonnes_identite(form, avec_dossier=True)
        rows = lignes_par_evenement(subs, fields, base_url)
        cols, lignes, libelles_cols = _expand_row_and_columns(fields, rows, colonnes, libelles)
        nom = _nom_de_feuille(form.name, noms_pris)
        feuilles.append((nom, pd.DataFrame(lignes, columns=cols)))
        dictionnaire.extend({"feuille": nom, "colonne": c, "libelle": libelles_cols.get(c, "")}
                            for c in cols)
    nb_visites = sum(len(df) for _, df in feuilles)

    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        # Feuille « À lire » en premier : ce que contient le fichier et
        # comment s'en servir, pour la personne qui l'ouvre sans contexte —
        # un bailleur, un statisticien, ou l'agent lui-meme dans six mois.
        lire = writer.book.create_sheet("À lire", 0)
        gras = Font(bold=True)
        lire.append(["Suivi complet", project.name])
        lire["A1"].font = Font(bold=True, size=14)
        lire.append(["Période", _texte_periode(debut, fin)])
        lire.append(["Exporté le", datetime.now().strftime("%d/%m/%Y à %H:%M")])
        lire.append([])
        lire.append(["Feuille", "Contenu", "Lignes"])
        for cellule in lire[lire.max_row]:
            cellule.font = gras
        lire.append(["Dossiers", "Un dossier de suivi par ligne. Sont inclus les dossiers ouverts pendant la "
                                 "période : inscrits avant sa fin, et pas clos avant son début.", len(df_dossiers)])
        for nom, df in feuilles:
            lire.append([nom, "Une visite par ligne, avec toutes les réponses. La colonne numero_dossier "
                              "la relie à la feuille Dossiers.", len(df)])
        lire.append(["Dictionnaire", "Le libellé de chaque colonne de chaque feuille.", len(dictionnaire)])
        lire.append([])
        lire.append(["À savoir"])
        lire[f"A{lire.max_row}"].font = gras
        for note in (
            "N/A = question non posée (masquée par une condition d'affichage) ; case vide = question posée, "
            "sans réponse.",
            "Choix multiples : une colonne par option, 1 = cochée, 0 = non cochée.",
            "La colonne « patient » contient le nom : supprime-la avant tout envoi à l'extérieur. Le code "
            "anonyme et le numéro de dossier suffisent pour relier les feuilles.",
        ):
            lire.append(["", note])
        lire.column_dimensions["A"].width = 22
        lire.column_dimensions["B"].width = 100
        lire.column_dimensions["C"].width = 10

        df_dossiers.to_excel(writer, index=False, sheet_name="Dossiers")
        _mettre_en_forme(writer.sheets["Dossiers"], df_dossiers)
        for nom, df in feuilles:
            df.to_excel(writer, index=False, sheet_name=nom)
            _mettre_en_forme(writer.sheets[nom], df)
        df_dico = pd.DataFrame(dictionnaire, columns=["feuille", "colonne", "libelle"])
        df_dico.to_excel(writer, index=False, sheet_name="Dictionnaire")
        _mettre_en_forme(writer.sheets["Dictionnaire"], df_dico)
    buffer.seek(0)

    log_audit(request, user=request.user, action="export", entity_type="case_full_export",
              entity_id=project_id, project_id=project_id,
              details=f"Export complet du suivi (xlsx, {_texte_periode(debut, fin)}) — "
                      f"{len(df_dossiers)} dossier(s), {nb_visites} visite(s) "
                      f"sur {len(feuilles)} formulaire(s)")
    suffixe = f"_{debut.isoformat() if debut else 'debut'}_{fin.isoformat() if fin else 'aujourdhui'}" \
        if (debut or fin) else ""
    return _attachment(buffer.read(), XLSX_CONTENT_TYPE,
                       f"{slugify_py(project.name)}_suivi_complet{suffixe}.xlsx")


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def export_project_summary(request, project_id):
    """Fiche resume du projet (identite + compteurs). Pour un export complet
    des donnees recueillies, utiliser l'export par formulaire (Enquete) et
    l'export des dossiers (Suivi), disponibles separement."""
    export_format = request.query_params.get("format", "csv")
    if export_format not in ("csv", "xlsx"):
        return _bad_format(export_format)

    project = Project.objects.filter(pk=project_id).first()
    if not project:
        return Response({"detail": "Projet introuvable"}, status=status.HTTP_404_NOT_FOUND)

    form_ids = list(Form.objects.filter(project_id=project_id).values_list("id", flat=True))
    rows = [{
        "nom_projet": project.name,
        "description": project.description or "",
        "cree_le": project.created_at.strftime("%Y-%m-%d") if project.created_at else "",
        "nb_formulaires": len(form_ids),
        "nb_patients": Patient.objects.filter(project_id=project_id).count(),
        "nb_soumissions": (Submission.objects.filter(form_id__in=form_ids).count()
                           if form_ids else 0),
        "cas_actifs": Case.objects.filter(project_id=project_id, status="active").count(),
        "cas_clotures": Case.objects.filter(project_id=project_id, status="closed").count(),
        "cas_perdus_de_vue": Case.objects.filter(project_id=project_id, status="lost").count(),
    }]
    content, media_type, ext = _dataframe_to_bytes(
        pd.DataFrame(rows, columns=list(rows[0].keys())), export_format, "Resume")

    log_audit(request, user=request.user, action="export", entity_type="project_summary",
              entity_id=project_id, project_id=project_id,
              details=f"Export resume du projet ({export_format})")
    return _attachment(content, media_type, f"{slugify_py(project.name)}_resume.{ext}")


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def case_stats(request, project_id):
    active_cases = list(Case.objects.filter(project_id=project_id, status="active")
                        .select_related("patient"))
    overdue = sum(1 for c in active_cases if serialize_case(c)["is_overdue"])

    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    closed = Case.objects.filter(project_id=project_id, status="closed").count()
    lost = Case.objects.filter(project_id=project_id, status="lost").count()

    return Response({
        "active": len(active_cases),
        "overdue": overdue,
        "closed": closed,
        "lost": lost,
        "closed_this_month": Case.objects.filter(
            project_id=project_id, status__in=["closed", "lost"], closed_at__gte=month_start,
        ).count(),
        "total": len(active_cases) + closed + lost,
    })
