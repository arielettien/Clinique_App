"""Module 3 : tableurs deposes sur un projet."""
import os

from django.conf import settings
from django.http import FileResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.audit import log_audit
from projects.models import Project
from spreadsheets.models import Spreadsheet
from spreadsheets.services import (
    delete_spreadsheet_file,
    read_spreadsheet_preview,
    serialize_spreadsheet,
    store_spreadsheet_file,
    validate_spreadsheet_upload,
)


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def project_spreadsheets(request, project_id):
    if not Project.objects.filter(pk=project_id).exists():
        return Response({"detail": "Projet introuvable"}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        rows = Spreadsheet.objects.filter(project_id=project_id).select_related("uploaded_by")
        return Response([serialize_spreadsheet(s) for s in rows])

    uploaded = request.FILES.get("file")
    if not uploaded:
        raise ValidationError("Aucun fichier recu.")

    validate_spreadsheet_upload(uploaded.name, uploaded.size)
    content = uploaded.read()
    stored_filename = store_spreadsheet_file(content, uploaded.name)

    title = (request.data.get("title") or "").strip() or os.path.splitext(uploaded.name)[0]
    spreadsheet = Spreadsheet.objects.create(
        project_id=project_id,
        title=title[:255],
        description=request.data.get("description") or None,
        original_filename=uploaded.name[:255],
        stored_filename=stored_filename,
        content_type=uploaded.content_type,
        size_bytes=len(content),
        uploaded_by=request.user,
    )
    log_audit(request, user=request.user, action="create", entity_type="spreadsheet",
              entity_id=spreadsheet.id, project_id=project_id,
              details=f"Tableur depose : {spreadsheet.title} ({uploaded.name})")
    return Response(serialize_spreadsheet(spreadsheet), status=status.HTTP_201_CREATED)


@api_view(["GET", "PUT", "DELETE"])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def spreadsheet_detail(request, spreadsheet_id):
    spreadsheet = (Spreadsheet.objects.filter(pk=spreadsheet_id)
                   .select_related("uploaded_by").first())
    if not spreadsheet:
        return Response({"detail": "Tableur introuvable"}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        return Response(serialize_spreadsheet(spreadsheet))
    if request.method == "PUT":
        return _update_spreadsheet(request, spreadsheet)
    return _delete_spreadsheet(request, spreadsheet)


def _update_spreadsheet(request, spreadsheet):
    """Renomme le tableur, et remplace son fichier si une nouvelle version est
    fournie. Remplacer plutot que redeposer evite de se retrouver avec cinq
    fichiers presque identiques dont personne ne sait lequel fait foi."""
    title = (request.data.get("title") or "").strip()
    if title:
        spreadsheet.title = title[:255]
    if request.data.get("description") is not None:
        spreadsheet.description = request.data.get("description") or None

    uploaded = request.FILES.get("file")
    if uploaded:
        validate_spreadsheet_upload(uploaded.name, uploaded.size)
        content = uploaded.read()
        ancien_fichier = spreadsheet.stored_filename
        spreadsheet.stored_filename = store_spreadsheet_file(content, uploaded.name)
        spreadsheet.original_filename = uploaded.name[:255]
        spreadsheet.content_type = uploaded.content_type
        spreadsheet.size_bytes = len(content)
        delete_spreadsheet_file(ancien_fichier)

    spreadsheet.updated_at = timezone.now()
    spreadsheet.save()
    return Response(serialize_spreadsheet(spreadsheet))


def _delete_spreadsheet(request, spreadsheet):
    title = spreadsheet.title
    spreadsheet_id = spreadsheet.id
    project_id = spreadsheet.project_id
    stored_filename = spreadsheet.stored_filename

    spreadsheet.delete()
    delete_spreadsheet_file(stored_filename)
    log_audit(request, user=request.user, action="delete", entity_type="spreadsheet",
              entity_id=spreadsheet_id, project_id=project_id,
              details=f"Tableur supprime : {title}")
    return Response({"status": "ok"})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def spreadsheet_preview(request, spreadsheet_id):
    """Contenu du tableur, pour l'afficher directement dans l'application —
    sans avoir a le telecharger ni a ouvrir Excel."""
    spreadsheet = Spreadsheet.objects.filter(pk=spreadsheet_id).first()
    if not spreadsheet:
        return Response({"detail": "Tableur introuvable"}, status=status.HTTP_404_NOT_FOUND)

    try:
        sheets = read_spreadsheet_preview(spreadsheet.stored_filename)
    except Exception as e:  # noqa: BLE001 — message remonte tel quel a l'agent
        return Response(
            {"detail": f"Impossible de lire ce fichier : {e}"},
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    if sheets is None:
        return Response({"detail": "Fichier introuvable sur le disque."},
                        status=status.HTTP_404_NOT_FOUND)

    return Response({"spreadsheet": serialize_spreadsheet(spreadsheet), "sheets": sheets})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def spreadsheet_download(request, spreadsheet_id):
    spreadsheet = Spreadsheet.objects.filter(pk=spreadsheet_id).first()
    if not spreadsheet:
        return Response({"detail": "Tableur introuvable"}, status=status.HTTP_404_NOT_FOUND)

    path = os.path.join(settings.UPLOADS_DIR, spreadsheet.stored_filename)
    if not os.path.isfile(path):
        return Response({"detail": "Fichier introuvable sur le disque."},
                        status=status.HTTP_404_NOT_FOUND)

    # Toujours en telechargement, jamais affiche dans la page : un tableur
    # n'a aucune raison d'etre interprete par le navigateur.
    safe_filename = (spreadsheet.original_filename or "tableur.xlsx") \
        .replace('"', "'").replace("\r", "").replace("\n", "")
    response = FileResponse(
        open(path, "rb"),
        content_type=spreadsheet.content_type or "application/octet-stream",
    )
    response["Content-Disposition"] = f'attachment; filename="{safe_filename}"'
    response["X-Content-Type-Options"] = "nosniff"
    return response
