from django.db import models
from django.utils import timezone

from core.utils import new_uuid


class Media(models.Model):
    """Un fichier envoye depuis un formulaire (photo/audio/video/fichier/
    signature). Le contenu reel est sur disque (dossier uploads/), cette table
    ne garde que les metadonnees + le nom de fichier stocke (aleatoire, jamais
    le nom d'origine, pour eviter tout risque de collision ou de traversee de
    chemin)."""

    id = models.CharField(primary_key=True, max_length=36, default=new_uuid, editable=False)
    original_filename = models.CharField(max_length=255)
    stored_filename = models.CharField(max_length=255)  # nom reel sur disque
    content_type = models.CharField(max_length=100, null=True, blank=True)
    size_bytes = models.CharField(max_length=20, null=True, blank=True)
    field_type = models.CharField(max_length=20, null=True, blank=True)
    uploaded_by = models.ForeignKey(
        "accounts.User", null=True, blank=True,
        on_delete=models.SET_NULL, db_column="uploaded_by", related_name="+",
    )
    uploaded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "media"
