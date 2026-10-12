"""Medias : envoi et diffusion des fichiers joints aux formulaires."""
import os

from django.conf import settings
from django.http import FileResponse
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from attachments.models import Media
from attachments.services import SAFE_INLINE_CONTENT_TYPES, store_uploaded_media


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def upload_media(request):
    uploaded = request.FILES.get("file")
    if not uploaded:
        raise ValidationError("Aucun fichier recu.")
    field_type = request.data.get("field_type") or "file"
    result = store_uploaded_media(
        uploaded.read(), uploaded.name, uploaded.content_type,
        field_type, uploaded_by=request.user,
    )
    return Response(result)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_media(request, media_id):
    """Diffuse le fichier — jamais accessible sans etre connecte : ce sont
    potentiellement des photos et documents de patients."""
    media = Media.objects.filter(pk=media_id).first()
    if not media:
        return Response({"detail": "Fichier introuvable."}, status=status.HTTP_404_NOT_FOUND)

    path = os.path.join(settings.UPLOADS_DIR, media.stored_filename)
    if not os.path.isfile(path):
        return Response({"detail": "Fichier introuvable sur le disque."},
                        status=status.HTTP_404_NOT_FOUND)

    content_type = media.content_type or "application/octet-stream"
    disposition = "inline" if content_type in SAFE_INLINE_CONTENT_TYPES else "attachment"
    # Le nom d'origine vient de l'appareil qui a envoye le fichier : on ne lui
    # fait jamais confiance tel quel dans un en-tete HTTP, ou des guillemets
    # ou retours a la ligne pourraient le manipuler.
    safe_filename = (media.original_filename or "fichier").replace('"', "'").replace("\r", "").replace("\n", "")

    response = FileResponse(open(path, "rb"), content_type=content_type)
    response["Content-Disposition"] = f'{disposition}; filename="{safe_filename}"'
    # Empeche le navigateur de reinterpreter le contenu malgre le type
    # declare : sans cet en-tete, un fichier deguise pourrait s'executer.
    response["X-Content-Type-Options"] = "nosniff"
    return response
