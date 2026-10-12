"""Formulaires : definition, lien communautaire, modele de structure, import."""
from io import BytesIO

from django.contrib.auth.hashers import make_password
from django.http import FileResponse
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from clinical_forms.models import Form
from clinical_forms.services import (
    generate_access_code,
    serialize_form_full,
    serialize_form_summary,
)
from core.audit import log_audit
from core.form_import_validation import validate_form_definition
from core.permissions import IsAdmin
from core.spreadsheet_io import build_template_bytes, parse_spreadsheet_to_sections, slugify_py
from projects.models import Project
from submissions.models import Submission

XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def forms_collection(request):
    if request.method == "GET":
        query = Form.objects.filter(is_active=True)
        project_id = request.query_params.get("project_id")
        if project_id:
            query = query.filter(project_id=project_id)
        return Response([serialize_form_summary(f) for f in query])

    # La creation d'un formulaire est reservee aux administrateurs.
    if not IsAdmin().has_permission(request, None):
        return Response({"detail": "Action reservee aux administrateurs."},
                        status=status.HTTP_403_FORBIDDEN)

    data = request.data
    form = Form.objects.create(
        project_id=data.get("project_id"),
        name=data.get("name") or "",
        form_type=data.get("form_type") or "survey",
        definition=data.get("definition") or {},
    )
    return Response({"id": form.id, "name": form.name}, status=status.HTTP_201_CREATED)


@api_view(["GET", "PUT", "DELETE"])
@permission_classes([IsAuthenticated])
def form_detail(request, form_id):
    form = Form.objects.filter(pk=form_id).first()
    if not form:
        return Response({"detail": "Formulaire introuvable"}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        return Response(serialize_form_full(form))

    if not IsAdmin().has_permission(request, None):
        return Response({"detail": "Action reservee aux administrateurs."},
                        status=status.HTTP_403_FORBIDDEN)

    if request.method == "PUT":
        data = request.data
        form.name = data.get("name") or form.name
        if data.get("form_type"):
            form.form_type = data["form_type"]
        form.definition = data.get("definition") or form.definition
        form.save()
        return Response({"id": form.id, "name": form.name})

    return _delete_form(request, form)


def _delete_form(request, form):
    """Supprime un formulaire seulement s'il n'a aucune soumission ; sinon
    l'archive (is_active=False) pour ne jamais perdre de donnees deja
    recueillies."""
    submission_count = Submission.objects.filter(form=form).count()
    if submission_count > 0:
        form.is_active = False
        form.save(update_fields=["is_active"])
        log_audit(request, user=request.user, action="update", entity_type="form",
                  entity_id=form.id, project_id=form.project_id,
                  details=f"Formulaire '{form.name}' archive (avait {submission_count} soumission(s))")
        return Response({
            "status": "archived",
            "message": f"{submission_count} soumission(s) existent pour ce formulaire : "
                       f"il a ete archive plutot que supprime.",
        })

    form_name = form.name
    form_id = form.id
    project_id = form.project_id
    form.delete()
    log_audit(request, user=request.user, action="delete", entity_type="form",
              entity_id=form_id, project_id=project_id,
              details=f"Formulaire supprime : {form_name}")
    return Response({"status": "deleted"})


# --- Lien communautaire ---------------------------------------------------

@api_view(["GET", "PUT"])
@permission_classes([IsAdmin])
def public_link(request, form_id):
    form = Form.objects.filter(pk=form_id).first()
    if not form:
        return Response({"detail": "Formulaire introuvable"}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        # Jamais le code lui-meme : seul son empreinte est stockee, comme un
        # mot de passe. On indique seulement s'il est actif et si un code a
        # deja ete genere au moins une fois.
        return Response({
            "enabled": bool(form.public_link_enabled),
            "has_code": bool(form.public_access_code_hash),
        })

    # A l'activation, si aucun code n'existe encore, un premier code est
    # genere et renvoye EN CLAIR une seule fois — impossible a recuperer
    # ensuite (voir /regenerate pour en obtenir un nouveau).
    enabled = bool(request.data.get("enabled"))
    form.public_link_enabled = enabled
    plain_code = None
    if enabled and not form.public_access_code_hash:
        plain_code = generate_access_code()
        form.public_access_code_hash = make_password(plain_code)
    form.save(update_fields=["public_link_enabled", "public_access_code_hash"])

    log_audit(request, user=request.user, action="update", entity_type="form",
              entity_id=form.id, project_id=form.project_id,
              details=f"Lien communautaire {'active' if enabled else 'desactive'} pour « {form.name} »")
    return Response({
        "enabled": bool(form.public_link_enabled),
        "has_code": bool(form.public_access_code_hash),
        "code": plain_code,
    })


@api_view(["POST"])
@permission_classes([IsAdmin])
def regenerate_public_code(request, form_id):
    """Regenere le code d'acces — invalide IMMEDIATEMENT tous les appareils
    deja debloques avec l'ancien code, puisque chaque requete publique
    reverifie contre l'empreinte actuelle. Utile si le code a fuite ou qu'un
    agent quitte l'equipe."""
    form = Form.objects.filter(pk=form_id).first()
    if not form:
        return Response({"detail": "Formulaire introuvable"}, status=status.HTTP_404_NOT_FOUND)

    plain_code = generate_access_code()
    form.public_access_code_hash = make_password(plain_code)
    form.save(update_fields=["public_access_code_hash"])

    log_audit(request, user=request.user, action="update", entity_type="form",
              entity_id=form.id, project_id=form.project_id,
              details=f"Code du lien communautaire regenere pour « {form.name} »")
    return Response({"enabled": bool(form.public_link_enabled), "has_code": True, "code": plain_code})


# --- Structure : modele et import ----------------------------------------

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def export_form_template(request, form_id):
    form = Form.objects.filter(pk=form_id).first()
    if not form:
        return Response({"detail": "Formulaire introuvable"}, status=status.HTTP_404_NOT_FOUND)

    content = build_template_bytes(
        form.name,
        (form.definition or {}).get("sections") or [],
        (form.definition or {}).get("choice_lists"),
    )
    filename = f"{slugify_py(form.name)}_structure.xlsx"
    response = FileResponse(BytesIO(content), content_type=XLSX_CONTENT_TYPE)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@api_view(["POST"])
@permission_classes([IsAdmin])
def import_form(request):
    project_id = request.data.get("project_id")
    name = request.data.get("name")
    uploaded = request.FILES.get("file")
    if not uploaded:
        raise ValidationError("Aucun fichier recu.")

    content = uploaded.read()
    try:
        sections, choice_lists = parse_spreadsheet_to_sections(content, uploaded.name or "formulaire.xlsx")
    except Exception as e:  # noqa: BLE001 — message remonte tel quel a l'agent
        return Response({"detail": f"Impossible de lire le fichier : {e}"},
                        status=status.HTTP_422_UNPROCESSABLE_ENTITY)

    total_fields = sum(len(s.get("fields", [])) for s in sections)
    if total_fields == 0:
        return Response(
            {"detail": "Aucune question valide trouvee (verifie que la colonne 'label' est bien remplie)."},
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    definition = {"sections": sections}
    if choice_lists:
        definition["choice_lists"] = choice_lists

    structure_errors = validate_form_definition(definition)
    if structure_errors:
        return Response({"detail": " | ".join(structure_errors)},
                        status=status.HTTP_422_UNPROCESSABLE_ENTITY)

    if not Project.objects.filter(pk=project_id).exists():
        return Response({"detail": "Projet introuvable"}, status=status.HTTP_404_NOT_FOUND)

    form = Form.objects.create(project_id=project_id, name=name,
                               form_type="survey", definition=definition)
    return Response({"id": form.id, "name": form.name, "questions_imported": total_fields},
                    status=status.HTTP_201_CREATED)
