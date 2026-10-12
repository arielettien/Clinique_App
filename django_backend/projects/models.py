from django.db import models
from django.utils import timezone

from core.utils import new_uuid


class Project(models.Model):
    """Un projet = un espace de suivi separe (ex: 'Suivi Diabete',
    'Suivi Cancer', 'Nutrition Enfants'...). Chaque projet a ses propres
    patients et ses propres formulaires."""

    id = models.CharField(primary_key=True, max_length=36, default=new_uuid, editable=False)
    name = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    # Date metier, distincte de created_at (horodatage technique d'insertion,
    # jamais modifie) : le projet a pu demarrer avant d'etre cree dans
    # l'application, cette date-ci est saisie et modifiable par l'utilisateur.
    start_date = models.DateField(null=True, blank=True)
    ngo_name = models.CharField(max_length=255, null=True, blank=True)
    main_donor = models.CharField(max_length=255, null=True, blank=True)
    cover_image = models.ForeignKey(
        "attachments.Media", null=True, blank=True,
        on_delete=models.SET_NULL, db_column="cover_image_id", related_name="+",
    )

    class Meta:
        db_table = "projects"

    def __str__(self):
        return self.name
