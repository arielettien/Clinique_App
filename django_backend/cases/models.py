from django.db import models
from django.utils import timezone

from core.utils import new_uuid


class Case(models.Model):
    """Un cas = un patient suivi dans le temps (Module Suivi). Rattache au
    patient par cle etrangere, sans champs demographiques dupliques."""

    id = models.CharField(primary_key=True, max_length=36, default=new_uuid, editable=False)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE,
        db_column="project_id", related_name="cases",
    )
    patient = models.ForeignKey(
        "patients.Patient", on_delete=models.CASCADE,
        db_column="patient_id", related_name="cases",
    )
    case_number = models.CharField(max_length=100)
    status = models.CharField(max_length=50, default="active")  # active / closed / lost
    enrolled_at = models.DateTimeField(default=timezone.now)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_reason = models.CharField(max_length=255, null=True, blank=True)
    # Pas de cle etrangere stricte : evite une dependance circulaire avec
    # submissions.case_id.
    source_submission_id = models.CharField(max_length=36, null=True, blank=True)
    created_by = models.ForeignKey(
        "accounts.User", null=True, blank=True,
        on_delete=models.SET_NULL, db_column="created_by", related_name="+",
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "cases"
        constraints = [
            models.UniqueConstraint(
                fields=["project", "case_number"], name="uq_case_project_number",
            )
        ]

    def __str__(self):
        return self.case_number
