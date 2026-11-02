"""Module Tableur : classeurs crees et modifies dans l'application."""
from io import BytesIO

from django.http import FileResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.audit import log_audit
from workbooks.excel_export import construire_xlsx
from workbooks.models import Workbook

# Garde-fous : un classeur reste un outil de pilotage, pas une base de
# donnees. Au-dela, c'est que le besoin releve d'un vrai module de
# l'application — et la page deviendrait de toute facon inutilisable.
MAX_SHEETS = 20
MAX_CELLS_PER_SHEET = 20000


def _auteur(user):
    if not user:
        return None
    return user.full_name or user.email


def serialize_workbook(w, with_content=False) -> dict:
    data = {
        "id": w.id,
        "title": w.title,
        "description": w.description,
        "created_by": _auteur(w.created_by),
        "created_at": w.created_at,
        "updated_by": _auteur(w.updated_by),
        "updated_at": w.updated_at,
    }
    if with_content:
        data["content"] = w.content or {}
    return data


def _valider_contenu(content):
    """Refuse un classeur demesure avant de l'enregistrer : une fois en base,
    il rendrait la page impossible a ouvrir, y compris pour le corriger."""
    if not isinstance(content, dict):
        raise ValidationError("Contenu du classeur invalide.")
    sheets = content.get("sheets")
    if not isinstance(sheets, list):
        raise ValidationError("Contenu du classeur invalide : feuilles manquantes.")
    if len(sheets) > MAX_SHEETS:
        raise ValidationError(f"Trop de feuilles (maximum {MAX_SHEETS}).")
    for sheet in sheets:
        cells = sheet.get("cells") or {}
        if len(cells) > MAX_CELLS_PER_SHEET:
            raise ValidationError(
                f"La feuille « {sheet.get('name', '?')} » contient trop de cellules "
                f"remplies (maximum {MAX_CELLS_PER_SHEET})."
            )
    return content


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def workbooks_collection(request):
    if request.method == "GET":
        rows = Workbook.objects.select_related("created_by", "updated_by")
        return Response([serialize_workbook(w) for w in rows])

    title = (request.data.get("title") or "").strip()
    if not title:
        raise ValidationError("Donne un nom a ce classeur.")

    workbook = Workbook.objects.create(
        title=title[:255],
        description=request.data.get("description") or None,
        content=_valider_contenu(request.data.get("content") or {"sheets": []}),
        created_by=request.user,
        updated_by=request.user,
    )
    log_audit(request, user=request.user, action="create", entity_type="workbook",
              entity_id=workbook.id, details=f"Classeur cree : {workbook.title}")
    return Response(serialize_workbook(workbook, with_content=True),
                    status=status.HTTP_201_CREATED)


@api_view(["GET", "PUT", "DELETE"])
@permission_classes([IsAuthenticated])
def workbook_detail(request, workbook_id):
    workbook = (Workbook.objects.filter(pk=workbook_id)
                .select_related("created_by", "updated_by").first())
    if not workbook:
        return Response({"detail": "Classeur introuvable"}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        return Response(serialize_workbook(workbook, with_content=True))

    if request.method == "PUT":
        title = (request.data.get("title") or "").strip()
        if title:
            workbook.title = title[:255]
        if request.data.get("description") is not None:
            workbook.description = request.data.get("description") or None
        if request.data.get("content") is not None:
            workbook.content = _valider_contenu(request.data.get("content"))
        workbook.updated_at = timezone.now()
        workbook.updated_by = request.user
        workbook.save()
        return Response(serialize_workbook(workbook, with_content=True))

    title = workbook.title
    workbook_id = workbook.id
    workbook.delete()
    log_audit(request, user=request.user, action="delete", entity_type="workbook",
              entity_id=workbook_id, details=f"Classeur supprime : {title}")
    return Response({"status": "ok"})


def _nom_de_fichier(titre: str) -> str:
    """Nom de fichier sur : on retire ce qu'un systeme de fichiers refuse
    (et ce qui pourrait manipuler l'en-tete HTTP), et on ne laisse jamais
    le nom vide."""
    interdits = set('\\/*?:\"<>|') | {chr(13), chr(10)}
    propre = "".join("-" if c in interdits else c for c in (titre or "")).strip()
    return (propre or "classeur")[:120]


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def workbook_export(request, workbook_id):
    """Télécharge le classeur au format Excel.

    Les formules sont traduites, pas figées : le fichier reste vivant une
    fois ouvert dans Excel, où les totaux continuent de se recalculer.
    """
    workbook = Workbook.objects.filter(pk=workbook_id).first()
    if not workbook:
        return Response({"detail": "Classeur introuvable"}, status=status.HTTP_404_NOT_FOUND)

    try:
        contenu = construire_xlsx(workbook)
    except Exception as e:  # noqa: BLE001 — message remonte tel quel a l'agent
        return Response({"detail": f"Export impossible : {e}"},
                        status=status.HTTP_422_UNPROCESSABLE_ENTITY)

    log_audit(request, user=request.user, action="export", entity_type="workbook",
              entity_id=workbook.id, details=f"Classeur exporte en Excel : {workbook.title}")

    reponse = FileResponse(
        BytesIO(contenu),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    reponse["Content-Disposition"] = f'attachment; filename="{_nom_de_fichier(workbook.title)}.xlsx"'
    reponse["X-Content-Type-Options"] = "nosniff"
    return reponse
