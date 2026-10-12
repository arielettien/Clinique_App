"""Journal d'audit et verification d'etat du service."""
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from core.models import AuditLog
from core.permissions import IsAdmin

MAX_PAGE_SIZE = 200


@api_view(["GET"])
@permission_classes([IsAdmin])
def audit_logs(request):
    """Consultation du journal d'audit — reservee aux administrateurs.
    Volontairement limite aux actions sensibles, pas un journal exhaustif de
    toute activite (voir core/models.py)."""
    query = AuditLog.objects.all()

    project_id = request.query_params.get("project_id")
    if project_id:
        query = query.filter(project_id=project_id)
    action = request.query_params.get("action")
    if action:
        query = query.filter(action=action)

    page = max(1, int(request.query_params.get("page") or 1))
    page_size = min(MAX_PAGE_SIZE, max(1, int(request.query_params.get("page_size") or 50)))

    total = query.count()
    start = (page - 1) * page_size
    entries = query[start:start + page_size]

    return Response({
        "total": total,
        "page": page,
        "page_size": page_size,
        "entries": [{
            "id": e.id,
            "user_email": e.user_email,
            "action": e.action,
            "entity_type": e.entity_type,
            "entity_id": e.entity_id,
            "project_id": e.project_id,
            "details": e.details,
            "ip_address": e.ip_address,
            "created_at": e.created_at,
        } for e in entries],
    })


@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    """Verification d'etat, sans authentification : sert a confirmer que le
    service repond (surveillance, diagnostic apres un redemarrage)."""
    return Response({"status": "ok"})
