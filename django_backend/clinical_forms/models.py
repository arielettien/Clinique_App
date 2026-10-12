from django.db import models
from django.utils import timezone

from core.utils import new_uuid


class Form(models.Model):
    """Un formulaire clinique : sa definition (sections/champs/formules) est
    stockee en JSON, ce qui permet d'en creer de nouveaux sans jamais toucher
    au code ni au schema de base de donnees."""

    id = models.CharField(primary_key=True, max_length=36, default=new_uuid, editable=False)
    project = models.ForeignKey(
        "projects.Project", null=True, blank=True,
        on_delete=models.CASCADE, db_column="project_id", related_name="forms",
    )
    name = models.CharField(max_length=255)
    form_type = models.CharField(max_length=50, default="survey")  # survey / follow_up / registration
    definition = models.JSONField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    # Lien communautaire : un formulaire peut etre ouvert sans compte via un
    # lien + un code d'acces partage. Seul le HASH du code est stocke (jamais
    # en clair) — comme un mot de passe. Regenerer le code invalide
    # immediatement tous les appareils deja debloques, puisque chaque requete
    # re-verifie contre ce hash.
    public_link_enabled = models.BooleanField(default=False)
    public_access_code_hash = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        db_table = "forms"

    def __str__(self):
        return self.name
