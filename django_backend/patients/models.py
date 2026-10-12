from django.db import models
from django.utils import timezone

from core.utils import new_uuid


class Patient(models.Model):
    id = models.CharField(primary_key=True, max_length=36, default=new_uuid, editable=False)
    project = models.ForeignKey(
        "projects.Project", null=True, blank=True,
        on_delete=models.CASCADE, db_column="project_id", related_name="patients",
    )
    first_name = models.CharField(max_length=255)
    last_name = models.CharField(max_length=255)
    date_of_birth = models.DateField(null=True, blank=True)
    sex = models.CharField(max_length=10, null=True, blank=True)
    phone = models.CharField(max_length=50, null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    # Code anonyme (ex: "PAT-2026-0042"), attribue a TOUT patient des sa
    # creation (admis en Suivi ou pas) — sert a identifier un patient dans un
    # document destine a l'exterieur (ex: un bailleur) sans jamais y faire
    # apparaitre son nom. Unique par projet, volontairement distinct du numero
    # de dossier de suivi (prefixe different) pour ne jamais les confondre :
    # un patient non admis en Suivi a un code mais pas de dossier.
    anonymous_code = models.CharField(max_length=50, null=True, blank=True)

    class Meta:
        db_table = "patients"
        constraints = [
            models.UniqueConstraint(
                fields=["project", "anonymous_code"],
                name="uq_patient_project_anon_code",
            )
        ]

    def __str__(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


class PotentialDuplicate(models.Model):
    """Detecte quand un patient cree hors-ligne (donc sans avoir pu verifier
    contre toute la base au moment de sa creation) partage nom/prenom avec un
    patient deja existant du meme projet. Jamais fusionne automatiquement —
    seulement signale pour qu'un admin tranche."""

    id = models.CharField(primary_key=True, max_length=36, default=new_uuid, editable=False)
    project_id = models.CharField(max_length=36)
    patient_a = models.ForeignKey(
        "patients.Patient", on_delete=models.CASCADE,
        db_column="patient_id_a", related_name="+",
    )
    patient_b = models.ForeignKey(
        "patients.Patient", on_delete=models.CASCADE,
        db_column="patient_id_b", related_name="+",
    )
    detected_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=20, default="open")  # open / dismissed

    class Meta:
        db_table = "potential_duplicates"
