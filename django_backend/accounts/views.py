"""Authentification et gestion des comptes utilisateurs."""
from django.contrib.auth import authenticate, login, logout
from django.middleware.csrf import get_token
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ParseError, PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from accounts.models import User
from cases.models import Case
from core.audit import log_audit, client_ip
from core.permissions import IsAdmin
from core.ratelimit import login_limiter
from submissions.models import Submission


def serialize_user(u: User) -> dict:
    return {
        "id": u.id,
        "email": u.email,
        "full_name": u.full_name,
        "role": u.role,
        "is_active": u.is_active,
        "created_at": u.created_at,
    }


# --- Authentification -----------------------------------------------------

@api_view(["POST"])
@permission_classes([AllowAny])
def login_view(request):
    email = (request.data.get("email") or "").strip()
    password = request.data.get("password") or ""
    if not email or not password:
        raise ValidationError("Email et mot de passe requis.")

    # Verifie AVANT de tester le mot de passe : au-dela de quelques echecs,
    # on refuse sans meme consulter la base.
    limiter_key = f"{client_ip(request)}:{email}"
    login_limiter.check(limiter_key, "Trop de tentatives de connexion.")

    user = authenticate(request, username=email, password=password)
    if user is None:
        login_limiter.record_failure(limiter_key)
        log_audit(request, user_email=email, action="login_failed",
                  details="Email ou mot de passe incorrect")
        return Response({"detail": "Email ou mot de passe incorrect"},
                        status=status.HTTP_401_UNAUTHORIZED)

    login_limiter.record_success(limiter_key)

    if not user.is_active:
        log_audit(request, user=user, action="login_failed", details="Compte desactive")
        return Response({"detail": "Compte desactive"}, status=status.HTTP_401_UNAUTHORIZED)

    # Session cote serveur : la deconnexion l'invalide reellement, et un
    # changement de mot de passe invalide les sessions deja ouvertes.
    login(request, user)
    # Pose le cookie anti-CSRF des la connexion : le frontend le rattache
    # ensuite automatiquement a chaque requete qui modifie des donnees
    # (voir app_shell.js). Sans lui, le premier enregistrement serait refuse.
    get_token(request)
    log_audit(request, user=user, action="login")
    return Response({"full_name": user.full_name, "role": user.role})


@api_view(["POST"])
@permission_classes([AllowAny])
def logout_view(request):
    logout(request)
    return Response({"status": "ok"})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def me_view(request):
    # Appelee au chargement de chaque page : c'est l'endroit naturel pour
    # s'assurer que le cookie anti-CSRF est present, y compris apres une
    # session restauree sans nouvelle connexion.
    get_token(request)
    u = request.user
    return Response({"email": u.email, "full_name": u.full_name, "role": u.role})


# --- Gestion des comptes (reserve aux administrateurs) --------------------

@api_view(["GET", "POST"])
@permission_classes([IsAdmin])
def users_collection(request):
    if request.method == "GET":
        users = User.objects.order_by("created_at")
        return Response([serialize_user(u) for u in users])

    email = (request.data.get("email") or "").strip()
    full_name = request.data.get("full_name") or ""
    password = request.data.get("password") or ""
    role = request.data.get("role") or "operator"

    if not email:
        raise ValidationError("Email requis.")
    if len(password) < 8:
        raise ValidationError("Le mot de passe doit faire au moins 8 caracteres.")
    if role not in ("admin", "operator"):
        raise ValidationError("Role invalide (admin ou operator).")
    if User.objects.filter(email=email).exists():
        return Response({"detail": "Un compte existe deja avec cet email."},
                        status=status.HTTP_409_CONFLICT)

    user = User.objects.create_user(email=email, password=password,
                                    full_name=full_name, role=role)
    log_audit(request, user=request.user, action="create", entity_type="user",
              entity_id=user.id, details=f"Compte cree : {user.email} ({user.role})")
    return Response(serialize_user(user), status=status.HTTP_201_CREATED)


@api_view(["PUT", "DELETE"])
@permission_classes([IsAdmin])
def user_detail(request, user_id):
    user = User.objects.filter(pk=user_id).first()
    if not user:
        return Response({"detail": "Compte introuvable."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "DELETE":
        return _delete_user(request, user)
    return _update_user(request, user)


def _update_user(request, user):
    changes = []
    data = request.data

    if data.get("full_name") is not None:
        user.full_name = data["full_name"]

    if data.get("role") is not None:
        role = data["role"]
        if role not in ("admin", "operator"):
            raise ValidationError("Role invalide (admin ou operator).")
        if user.id == request.user.id and role != "admin":
            raise ParseError("Impossible de retirer ton propre statut admin.")
        if role != user.role:
            changes.append(f"role -> {role}")
        user.role = role

    if data.get("is_active") is not None:
        is_active = bool(data["is_active"])
        if user.id == request.user.id and not is_active:
            raise ParseError("Impossible de desactiver ton propre compte.")
        if is_active != user.is_active:
            changes.append("reactive" if is_active else "desactive")
        user.is_active = is_active

    if data.get("password"):
        if len(data["password"]) < 8:
            raise ValidationError("Le mot de passe doit faire au moins 8 caracteres.")
        user.set_password(data["password"])
        # Invalide les sessions deja ouvertes avec l'ancien mot de passe
        # (utile en particulier si ce changement fait suite a un compte
        # compromis). Django s'en charge via le hash de session, le compteur
        # reste incremente pour rester coherent avec l'historique.
        user.token_version += 1
        changes.append("mot de passe reinitialise")

    user.save()
    if changes:
        log_audit(request, user=request.user, action="update", entity_type="user",
                  entity_id=user.id, details=f"{user.email} : {', '.join(changes)}")
    return Response(serialize_user(user))


def _delete_user(request, user):
    """Suppression definitive, autorisee seulement si ce compte n'a jamais
    cree la moindre donnee (soumission/cas) — sinon la suppression casserait
    la tracabilite de qui a fait quoi. Un compte deja utilise doit etre
    desactive plutot que supprime."""
    if user.id == request.user.id:
        raise ParseError("Impossible de supprimer ton propre compte.")

    created_counts = {
        "soumission(s)": Submission.objects.filter(created_by=user).count(),
        "cas": Case.objects.filter(created_by=user).count(),
    }
    total_created = sum(created_counts.values())
    if total_created > 0:
        details = ", ".join(f"{v} {k}" for k, v in created_counts.items() if v > 0)
        return Response(
            {"detail": f"Ce compte a deja cree des donnees ({details}) — impossible de le "
                       f"supprimer sans perdre la tracabilite. Desactive-le plutot."},
            status=status.HTTP_409_CONFLICT,
        )

    deleted_email = user.email
    deleted_id = user.id
    user.delete()
    log_audit(request, user=request.user, action="delete", entity_type="user",
              entity_id=deleted_id, details=f"Compte supprime : {deleted_email}")
    return Response({"status": "ok"})
