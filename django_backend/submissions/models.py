from django.db import models
from django.utils import timezone

from core.utils import new_uuid


class Submission(models.Model):
    """Une soumission = un formulaire rempli pour un patient donne, a un
    instant donne."""

    id = models.CharField(primary_key=True, max_length=36, default=new_uuid, editable=False)
    form = models.ForeignKey(
        "clinical_forms.Form", on_delete=models.CASCADE,
        db_column="form_id", related_name="submissions",
    )
    patient = models.ForeignKey(
        "patients.Patient", on_delete=models.CASCADE,
        db_column="patient_id", related_name="submissions",
    )
    case = models.ForeignKey(
        "cases.Case", null=True, blank=True,
        on_delete=models.SET_NULL, db_column="case_id", related_name="submissions",
    )
    responses = models.JSONField()
    # Date de l'evenement, choisie par l'agent (peut etre anterieure a la
    # saisie : rattrapage d'un dossier papier). Distincte de created_at, qui
    # est l'horodatage reel de l'enregistrement.
    collected_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(
        "accounts.User", null=True, blank=True,
        on_delete=models.SET_NULL, db_column="created_by", related_name="+",
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    # Jeton genere UNE FOIS par le navigateur a l'ouverture d'un formulaire
    # vierge : deux requetes portant le meme jeton (double-clic, requete
    # rejouee par le reseau) renvoient le meme resultat au lieu de creer un
    # doublon. C'est aussi ce qui rend sure la reprise de la file d'attente
    # hors-ligne.
    idempotency_key = models.CharField(max_length=64, null=True, blank=True, unique=True)

    class Meta:
        db_table = "submissions"
