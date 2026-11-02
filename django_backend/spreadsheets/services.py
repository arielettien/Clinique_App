"""Stockage et lecture des tableurs deposes sur un projet."""
import csv
import io
import os
import uuid

from django.conf import settings
from openpyxl import load_workbook
from rest_framework.exceptions import ValidationError

# Formats acceptes. Le ".xls" (ancien format Excel, anterieur a 2007) est
# volontairement refuse : le lire demanderait une bibliotheque supplementaire
# pour un format qu'Excel lui-meme convertit en un clic. Le message d'erreur
# le dit clairement plutot que d'echouer sans explication.
ACCEPTED_EXTENSIONS = (".xlsx", ".xlsm", ".csv")
LEGACY_EXCEL_EXTENSION = ".xls"

# Un tableur de suivi de projet reste un fichier de travail : au-dela, c'est
# qu'il contient probablement des images ou des donnees qui n'ont rien a y faire.
MAX_SPREADSHEET_SIZE_BYTES = 15 * 1024 * 1024  # 15 Mo

# Nombre de lignes renvoyees pour l'apercu a l'ecran. Au-dela, la page
# deviendrait lourde a charger sans rien apporter : le fichier complet reste
# telechargeable d'un clic.
PREVIEW_ROW_LIMIT = 300
PREVIEW_COLUMN_LIMIT = 60


def _extension(filename: str) -> str:
    return os.path.splitext(filename or "")[1].lower()


def validate_spreadsheet_upload(filename: str, size: int):
    ext = _extension(filename)
    if ext == LEGACY_EXCEL_EXTENSION:
        raise ValidationError(
            "L'ancien format Excel (.xls) n'est pas pris en charge. Ouvre le fichier "
            "dans Excel puis « Enregistrer sous » au format .xlsx, et renvoie-le."
        )
    if ext not in ACCEPTED_EXTENSIONS:
        raise ValidationError(
            f"Format non pris en charge ({ext or 'inconnu'}). "
            f"Formats acceptes : {', '.join(ACCEPTED_EXTENSIONS)}."
        )
    if size > MAX_SPREADSHEET_SIZE_BYTES:
        max_mo = MAX_SPREADSHEET_SIZE_BYTES // (1024 * 1024)
        raise ValidationError(f"Fichier trop volumineux (max {max_mo} Mo).")
    if size == 0:
        raise ValidationError("Fichier vide.")
    return ext


def store_spreadsheet_file(content: bytes, filename: str) -> str:
    """Ecrit le fichier sur disque sous un nom aleatoire et renvoie ce nom.

    Le nom d'origine n'est jamais utilise pour construire un chemin : cela
    ecarte d'un coup les collisions entre deux fichiers homonymes et les
    tentatives de traversee de repertoire.
    """
    ext = _extension(filename)
    stored_filename = f"{uuid.uuid4()}{ext}"
    with open(os.path.join(settings.UPLOADS_DIR, stored_filename), "wb") as f:
        f.write(content)
    return stored_filename


def delete_spreadsheet_file(stored_filename: str):
    """Supprime le fichier du disque. Son absence n'est pas une erreur : la
    fiche en base reste la seule source de verite, et on ne veut pas qu'un
    fichier deja disparu empeche de nettoyer la liste."""
    try:
        os.remove(os.path.join(settings.UPLOADS_DIR, stored_filename))
    except OSError:
        pass


def _normalise(value):
    """Valeur lisible pour l'affichage. Les dates/heures sont converties en
    texte : telles quelles, elles ne traversent pas le JSON."""
    if value is None:
        return ""
    if isinstance(value, (int, float, str, bool)):
        return value
    return str(value)


def _read_csv(content: bytes):
    # Les tableurs exportes depuis Excel en francais utilisent ";" et sont
    # souvent precedes d'une marque d'ordre des octets (BOM). On tente donc
    # ce cas d'abord, avant la virgule.
    text = content.decode("utf-8-sig", errors="replace")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ";" if sample.count(";") >= sample.count(",") else ","

    rows = []
    for i, row in enumerate(csv.reader(io.StringIO(text), delimiter=delimiter)):
        if i >= PREVIEW_ROW_LIMIT + 1:
            break
        rows.append([_normalise(c) for c in row[:PREVIEW_COLUMN_LIMIT]])
    return [{"name": "Feuille 1", "rows": rows, "truncated": len(rows) > PREVIEW_ROW_LIMIT}]


def _read_excel(content: bytes):
    # read_only : ne charge pas tout le classeur en memoire d'un coup.
    # data_only : renvoie le RESULTAT des formules plutot que leur texte —
    # c'est ce qu'on veut afficher. Un fichier jamais ouvert par Excel depuis
    # sa derniere modification n'a pas ces resultats en cache : les cellules
    # concernees apparaissent alors vides, ce qui est préférable a afficher
    # "=SOMME(B2:B9)" a l'ecran.
    workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    sheets = []
    try:
        for sheet in workbook.worksheets:
            rows = []
            truncated = False
            for i, row in enumerate(sheet.iter_rows(values_only=True)):
                if i >= PREVIEW_ROW_LIMIT:
                    truncated = True
                    break
                rows.append([_normalise(c) for c in row[:PREVIEW_COLUMN_LIMIT]])
            # Retire les lignes entierement vides en fin de feuille : Excel en
            # laisse souvent des centaines, qui rendraient l'apercu absurde.
            while rows and all(c == "" for c in rows[-1]):
                rows.pop()
            sheets.append({"name": sheet.title, "rows": rows, "truncated": truncated})
    finally:
        workbook.close()
    return sheets


def read_spreadsheet_preview(stored_filename: str):
    """Contenu du tableur, feuille par feuille, pour affichage a l'ecran."""
    path = os.path.join(settings.UPLOADS_DIR, stored_filename)
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as f:
        content = f.read()
    if _extension(stored_filename) == ".csv":
        return _read_csv(content)
    return _read_excel(content)


def serialize_spreadsheet(s) -> dict:
    return {
        "id": s.id,
        "project_id": s.project_id,
        "title": s.title,
        "description": s.description,
        "original_filename": s.original_filename,
        "size_bytes": s.size_bytes,
        "uploaded_by": s.uploaded_by.full_name or s.uploaded_by.email if s.uploaded_by else None,
        "uploaded_at": s.uploaded_at,
        "updated_at": s.updated_at,
    }
