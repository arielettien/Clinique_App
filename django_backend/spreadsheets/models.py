from django.db import models
from django.utils import timezone

from core.utils import new_uuid


class Spreadsheet(models.Model):
    """Un tableur deposse sur un projet (Module 3).

    Raison d'etre : chaque gestionnaire suivait ses projets dans son propre
    fichier Excel, sur son propre ordinateur. Des que l'un etait absent, ses
    tableurs devenaient introuvables pour tout le monde. Les deposer ici les
    rend consultables en permanence, par n'importe qui dans l'equipe.

    Comme pour les autres fichiers de l'application, le contenu reel vit sur
    disque sous un nom genere aleatoirement ; cette table ne garde que les
    metadonnees et le nom d'origine, affiche a l'ecran.
    """

    id = models.CharField(primary_key=True, max_length=36, default=new_uuid, editable=False)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE,
        db_column="project_id", related_name="spreadsheets",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)

    original_filename = models.CharField(max_length=255)
    stored_filename = models.CharField(max_length=255)  # nom reel sur disque
    content_type = models.CharField(max_length=150, null=True, blank=True)
    size_bytes = models.BigIntegerField(default=0)

    uploaded_by = models.ForeignKey(
        "accounts.User", null=True, blank=True,
        on_delete=models.SET_NULL, db_column="uploaded_by", related_name="+",
    )
    uploaded_at = models.DateTimeField(default=timezone.now)
    # Mis a jour quand une nouvelle version du fichier remplace l'ancienne :
    # un tableur de suivi vit et change, l'historique de ses mises a jour
    # compte autant que son contenu.
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "spreadsheets"
        ordering = ["-updated_at"]

    def __str__(self):
        return self.title
