"""Journal des actions sensibles."""
from core.models import AuditLog


def client_ip(request):
    """Adresse de l'appareil a l'origine de la requete. On lit d'abord
    X-Forwarded-For au cas ou un reverse proxy serait ajoute un jour devant
    l'application (sinon toutes les lignes du journal porteraient l'adresse du
    proxy, ce qui les rendrait inutiles)."""
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def log_audit(request=None, user=None, user_email=None, action="",
              entity_type=None, entity_id=None, project_id=None, details=None):
    """Enregistre une action sensible. Volontairement limite aux connexions, a
    la gestion des comptes, aux suppressions et aux exports — pas a chaque
    consultation de page ni chaque soumission de formulaire, ce qui noierait
    l'information utile sous du bruit.

    N'importe quelle erreur ici est avalee : le journal d'audit ne doit jamais
    faire echouer l'action reelle qu'il est cense simplement noter."""
    try:
        AuditLog.objects.create(
            user=user if (user and getattr(user, "pk", None)) else None,
            user_email=user_email or (user.email if user else None),
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            project_id=project_id,
            details=details,
            ip_address=client_ip(request),
        )
    except Exception:  # noqa: BLE001 — voir docstring
        pass
