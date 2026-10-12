"""Permissions de l'API."""
from rest_framework.permissions import BasePermission


class IsAdmin(BasePermission):
    """A utiliser EN PLUS de l'authentification sur les actions sensibles
    (suppression de projet, gestion des comptes, consultation du journal) —
    etre connecte prouve l'identite, pas le droit d'agir."""

    message = "Action reservee aux administrateurs."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role == "admin")
