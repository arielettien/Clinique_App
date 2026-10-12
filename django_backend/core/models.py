from django.db import models
from django.utils import timezone

from core.utils import new_uuid


class AuditLog(models.Model):
    """Journal des actions sensibles — volontairement pas exhaustif (ni les
    consultations de page, ni chaque soumission, ce qui noierait
    l'information utile sous du bruit). Se limite a ce qui compte pour la
    securite et la responsabilite : connexions (reussies et echouees),
    gestion des comptes, suppressions difficiles a annuler, exports."""

    id = models.CharField(primary_key=True, max_length=36, default=new_uuid, editable=False)
    # Nullable : une connexion echouee ne permet pas toujours d'identifier un
    # utilisateur existant.
    user = models.ForeignKey(
        "accounts.User", null=True, blank=True,
        on_delete=models.SET_NULL, db_column="user_id", related_name="+",
    )
    # Conserve meme si le compte est supprime plus tard.
    user_email = models.CharField(max_length=255, null=True, blank=True)
    action = models.CharField(max_length=50)  # login / login_failed / create / update / delete / export
    entity_type = models.CharField(max_length=100, null=True, blank=True)
    entity_id = models.CharField(max_length=36, null=True, blank=True)
    project_id = models.CharField(max_length=36, null=True, blank=True)
    details = models.TextField(null=True, blank=True)
    ip_address = models.CharField(max_length=64, null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "audit_logs"
        ordering = ["-created_at"]
