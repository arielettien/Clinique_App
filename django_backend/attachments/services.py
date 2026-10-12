"""Stockage des fichiers envoyes depuis les formulaires."""
import os
import uuid

from django.conf import settings
from rest_framework.exceptions import ValidationError

from attachments.models import Media

ALLOWED_FIELD_TYPES_BY_CONTENT = {
    "image": ("image/",),
    "audio": ("audio/",),
    "video": ("video/",),
    "file": None,  # type generique : tout type de fichier accepte
    # Une signature est un dessin transforme en PNG par le navigateur :
    # techniquement une image, mais distinguee de "image" pour qu'on voie,
    # dans les metadonnees, qu'elle vient d'un pave de signature et non d'une
    # photo importee.
    "signature": ("image/",),
}

# Types refuses meme pour le champ generique "file" : un SVG ou un HTML peut
# contenir du JavaScript qui s'executerait dans le navigateur d'un soignant
# ouvrant le fichier. Aucun usage legitime de ces types dans cette application.
BLOCKED_CONTENT_TYPE_PREFIXES = (
    "image/svg", "text/html", "application/xhtml", "application/x-shockwave",
)

# Seuls ces types sont reaffiches directement dans le navigateur. Tout le
# reste force un telechargement, pour qu'un type de fichier inattendu ne
# puisse jamais s'executer comme une page dans l'origine de l'application.
SAFE_INLINE_CONTENT_TYPES = {
    "image/jpeg", "image/png", "image/gif", "image/webp",
    "audio/mpeg", "audio/wav", "audio/ogg", "audio/mp4", "audio/webm",
    "video/mp4", "video/webm", "video/ogg",
}


def store_uploaded_media(content: bytes, filename, content_type, field_type, uploaded_by=None):
    """Enregistre un fichier sur disque et sa fiche en base.

    Le nom reel sur disque est genere aleatoirement, jamais celui fourni par
    l'appareil : cela ecarte d'un coup les collisions de noms et les tentatives
    de traversee de chemin ("../../etc/passwd").
    """
    if field_type not in ALLOWED_FIELD_TYPES_BY_CONTENT:
        raise ValidationError(f"Type de champ media inconnu : '{field_type}'.")

    if len(content) > settings.MAX_UPLOAD_SIZE_BYTES:
        max_mo = settings.MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)
        raise ValidationError(f"Fichier trop volumineux (max {max_mo} Mo).")
    if len(content) == 0:
        raise ValidationError("Fichier vide.")

    content_type = content_type or "application/octet-stream"
    expected_prefixes = ALLOWED_FIELD_TYPES_BY_CONTENT[field_type]
    if expected_prefixes and not any(content_type.startswith(p) for p in expected_prefixes):
        raise ValidationError(f"Ce champ attend un fichier de type {field_type} ; recu : {content_type}.")
    if any(content_type.startswith(p) for p in BLOCKED_CONTENT_TYPE_PREFIXES):
        raise ValidationError(f"Type de fichier non autorise : {content_type}.")

    ext = os.path.splitext(filename or "")[1][:10]  # extension d'origine, tronquee par securite
    stored_filename = f"{uuid.uuid4()}{ext}"
    dest_path = os.path.join(settings.UPLOADS_DIR, stored_filename)
    with open(dest_path, "wb") as f:
        f.write(content)

    media = Media.objects.create(
        original_filename=(filename or "fichier")[:255],
        stored_filename=stored_filename,
        content_type=content_type,
        size_bytes=str(len(content)),
        field_type=field_type,
        uploaded_by=uploaded_by,
    )
    return {
        "id": media.id,
        "original_filename": media.original_filename,
        "content_type": media.content_type,
        "size_bytes": len(content),
    }
