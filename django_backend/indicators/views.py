"""Tableau de bord des indicateurs : lecture (tout le personnel connecte) et
configuration (administrateurs seulement : indicateurs, sites, reglages,
cibles detaillees)."""
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.text import slugify
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from cases.exports import _attachment, _bad_format
from clinical_forms.models import Form
from core.audit import log_audit
from core.formula_engine import validate_formula_syntax
from core.permissions import IsAdmin
from indicators.exports import build_dashboard_export
from indicators.models import DashboardSettings, Indicator, IndicatorTarget, Site
from indicators.services import (
    DashboardError, compute_dashboard, donors_payload, get_settings, resolve_period,
    serialize_indicator, serialize_site, validate_age_bands,
)
from projects.models import Project


def _not_found(what):
    return Response({"detail": f"{what} introuvable"}, status=status.HTTP_404_NOT_FOUND)


def _bad_request(message):
    return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)


def _forbidden_unless_admin(request):
    if not IsAdmin().has_permission(request, None):
        return Response({"detail": "Action reservee aux administrateurs."},
                        status=status.HTTP_403_FORBIDDEN)
    return None


# --- Lecture ----------------------------------------------------------------

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def donors(request):
    return Response(donors_payload())


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def project_dashboard(request, project_id):
    project = Project.objects.filter(pk=project_id).first()
    if not project:
        return _not_found("Projet")

    export_format = request.query_params.get("format")
    if export_format and export_format not in ("csv", "xlsx"):
        return _bad_format(export_format)

    indicator_id = request.query_params.get("indicator_id")
    if indicator_id:
        # Filtre sur le projet : un identifiant d'indicateur d'un AUTRE projet
        # ne doit jamais etre calcule ici sous le nom de ce projet.
        indicator = Indicator.objects.filter(pk=indicator_id, project=project).first()
        if not indicator:
            return _not_found("Indicateur")
    else:
        indicator = Indicator.objects.filter(project=project).first()

    try:
        periods = resolve_period(request.query_params.get("start"), request.query_params.get("end"))
        payload = compute_dashboard(
            project, indicator, periods,
            site=request.query_params.get("site") or None,
            sex=request.query_params.get("sexe") or None,
            age=request.query_params.get("age") or None,
        )
    except DashboardError as e:
        return _bad_request(str(e))

    if not export_format:
        return Response(payload)
    if indicator is None:
        return _bad_request("Aucun indicateur à exporter pour ce projet.")

    content, media_type, ext = build_dashboard_export(payload, export_format)
    log_audit(request, user=request.user, action="export", entity_type="dashboard",
              entity_id=indicator.id, project_id=project.id,
              details=f"Tableau de bord ({ext}) : {indicator.name}, "
                      f"{periods[0]} a {periods[-1]}, filtres {payload['filters']}")
    filename = f"tableau_de_bord_{slugify(project.name)}_{slugify(indicator.name)}_{periods[-1]}.{ext}"
    return _attachment(content, media_type, filename)


# --- Reglages ----------------------------------------------------------------

def _resolve_options(field, choice_lists):
    options = field.get("options") or choice_lists.get(field.get("list_name") or "", []) or []
    return [{"value": str(o.get("value")), "label": o.get("label") or str(o.get("value"))}
            for o in options if isinstance(o, dict) and o.get("value") not in (None, "")]


def _forms_overview(project):
    """Formulaires du projet et leurs champs : de quoi choisir, dans l'ecran de
    configuration, le formulaire source, le champ site et le champ a
    additionner, plutot que de taper des identifiants techniques de memoire."""
    out = []
    for form in Form.objects.filter(project=project).order_by("name"):
        definition = form.definition or {}
        choice_lists = definition.get("choice_lists") or {}
        fields = []
        for section in definition.get("sections", []):
            for f in section.get("fields", []):
                if "id" not in f or f.get("type") == "note":
                    continue
                item = {"id": f["id"], "label": f.get("label") or f["id"], "type": f.get("type")}
                if f.get("type") in ("select_one", "select_multiple"):
                    item["options"] = _resolve_options(f, choice_lists)
                fields.append(item)
        out.append({"id": form.id, "name": form.name, "is_active": form.is_active, "fields": fields})
    return out


@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated])
def dashboard_settings(request, project_id):
    project = Project.objects.filter(pk=project_id).first()
    if not project:
        return _not_found("Projet")

    if request.method == "PUT":
        denied = _forbidden_unless_admin(request)
        if denied:
            return denied
        data = request.data
        try:
            threshold = float(data.get("on_track_threshold", 85))
        except (TypeError, ValueError):
            return _bad_request("Le seuil « En bonne voie » doit être un nombre.")
        if not 0 < threshold < 100:
            return _bad_request("Le seuil « En bonne voie » doit être compris entre 0 et 100 %.")
        bands = data.get("age_bands")
        error = validate_age_bands(bands)
        if error:
            return _bad_request(error)
        s, _ = DashboardSettings.objects.get_or_create(project=project)
        s.site_field_id = (data.get("site_field_id") or "").strip() or None
        s.on_track_threshold = threshold
        s.age_bands = bands
        s.updated_at = timezone.now()
        s.save()

    cfg = get_settings(project)
    return Response({
        "site_field_id": cfg["site_field_id"],
        "on_track_threshold": cfg["on_track_threshold"],
        "age_bands": cfg["age_bands"],
        "forms": _forms_overview(project),
    })


# --- Indicateurs -------------------------------------------------------------

def _apply_indicator(indicator, project, data):
    """Valide et applique la saisie. Renvoie un message d'erreur ou None."""
    name = (data.get("name") or "").strip()
    if not name:
        return "Le nom de l'indicateur est obligatoire."
    form = Form.objects.filter(pk=data.get("form_id"), project=project).first()
    if not form:
        return "Choisis le formulaire qui alimente cet indicateur."
    calculation = data.get("calculation") or Indicator.CALC_COUNT
    if calculation not in dict(Indicator.CALCULATIONS):
        return "Type de calcul inconnu."
    value_field_id = (data.get("value_field_id") or "").strip() or None
    if calculation == Indicator.CALC_SUM and not value_field_id:
        return "Choisis le champ à additionner."
    condition = (data.get("condition") or "").strip() or None
    denominator = (data.get("denominator_condition") or "").strip() or None
    for label, expr in (("La condition", condition), ("La condition du dénominateur", denominator)):
        error = validate_formula_syntax(expr) if expr else None
        if error:
            return f"{label} n'est pas valide : {error}"
    target = data.get("target")
    if target in (None, ""):
        target = None
    else:
        try:
            target = float(str(target).replace(",", "."))
        except ValueError:
            return "La cible doit être un nombre."
        if target < 0:
            return "La cible ne peut pas être négative."
    try:
        display_order = int(data.get("display_order") or 0)
    except (TypeError, ValueError):
        display_order = 0

    indicator.project = project
    indicator.form = form
    indicator.name = name
    indicator.calculation = calculation
    indicator.value_field_id = value_field_id if calculation == Indicator.CALC_SUM else None
    indicator.condition = condition
    indicator.denominator_condition = denominator if calculation == Indicator.CALC_PERCENTAGE else None
    indicator.target = target
    indicator.unit = (data.get("unit") or "").strip() or None
    indicator.display_order = display_order
    return None


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def indicators_collection(request, project_id):
    project = Project.objects.filter(pk=project_id).first()
    if not project:
        return _not_found("Projet")
    if request.method == "GET":
        return Response([serialize_indicator(i) for i in Indicator.objects.filter(project=project)])

    denied = _forbidden_unless_admin(request)
    if denied:
        return denied
    indicator = Indicator()
    error = _apply_indicator(indicator, project, request.data)
    if error:
        return _bad_request(error)
    indicator.save()
    return Response(serialize_indicator(indicator), status=status.HTTP_201_CREATED)


@api_view(["PUT", "DELETE"])
@permission_classes([IsAuthenticated])
def indicator_detail(request, indicator_id):
    denied = _forbidden_unless_admin(request)
    if denied:
        return denied
    indicator = Indicator.objects.select_related("project").filter(pk=indicator_id).first()
    if not indicator:
        return _not_found("Indicateur")

    if request.method == "PUT":
        error = _apply_indicator(indicator, indicator.project, request.data)
        if error:
            return _bad_request(error)
        indicator.save()
        return Response(serialize_indicator(indicator))

    name, project_id = indicator.name, indicator.project_id
    indicator.delete()
    log_audit(request, user=request.user, action="delete", entity_type="indicator",
              entity_id=indicator_id, project_id=project_id,
              details=f"Indicateur supprime : {name}")
    return Response({"status": "ok"})


# --- Cibles detaillees -------------------------------------------------------

@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated])
def indicator_targets(request, indicator_id):
    indicator = Indicator.objects.filter(pk=indicator_id).first()
    if not indicator:
        return _not_found("Indicateur")

    if request.method == "PUT":
        denied = _forbidden_unless_admin(request)
        if denied:
            return denied
        items = request.data.get("targets")
        if not isinstance(items, list):
            return _bad_request("Liste de cibles attendue.")
        site_ids = set(Site.objects.filter(project_id=indicator.project_id).values_list("id", flat=True))
        new_targets, seen = [], set()
        for item in items:
            site_id = item.get("site_id") or None
            period = str(item.get("period") or "")
            if site_id and site_id not in site_ids:
                return _bad_request("Un des sites ne fait pas partie de ce projet.")
            try:
                resolve_period(period, period)
                value = float(str(item.get("value")).replace(",", "."))
            except (DashboardError, ValueError):
                return _bad_request(f"Cible invalide pour le mois « {period} ».")
            if value < 0:
                return _bad_request("Une cible ne peut pas être négative.")
            if (site_id, period) in seen:
                return _bad_request(f"Deux cibles saisies pour le même site et le mois {period}.")
            seen.add((site_id, period))
            new_targets.append(IndicatorTarget(indicator=indicator, site_id=site_id,
                                               period=period, value=value))
        # Remplacement complet, dans une transaction : l'ecran renvoie toujours
        # la liste entiere, ce qui evite de gerer ajout/modif/suppression une a une.
        with transaction.atomic():
            IndicatorTarget.objects.filter(indicator=indicator).delete()
            IndicatorTarget.objects.bulk_create(new_targets)

    targets = IndicatorTarget.objects.filter(indicator=indicator).order_by("period", "site_id")
    return Response([{"id": t.id, "site_id": t.site_id, "period": t.period, "value": t.value}
                     for t in targets])


# --- Sites ----------------------------------------------------------------

def _apply_site(site, data):
    code = (data.get("code") or "").strip()
    name = (data.get("name") or "").strip()
    if not code or not name:
        return "Le code et le nom du site sont obligatoires."
    site.code = code
    site.name = name
    site.region = (data.get("region") or "").strip() or None
    return None


def _save_site(site):
    try:
        with transaction.atomic():
            site.save()
    except IntegrityError:
        return _bad_request(f"Un site porte déjà le code « {site.code} » dans ce projet.")
    return None


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def sites_collection(request, project_id):
    project = Project.objects.filter(pk=project_id).first()
    if not project:
        return _not_found("Projet")
    if request.method == "GET":
        sites = Site.objects.filter(project=project).order_by("region", "name")
        return Response([serialize_site(s) for s in sites])

    denied = _forbidden_unless_admin(request)
    if denied:
        return denied
    site = Site(project=project)
    error = _apply_site(site, request.data)
    if error:
        return _bad_request(error)
    failure = _save_site(site)
    return failure or Response(serialize_site(site), status=status.HTTP_201_CREATED)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def sites_import(request, project_id):
    """Cree les sites manquants a partir des choix du champ site des
    formulaires : evite de ressaisir a la main une liste deja tapee dans le
    constructeur de formulaire. Les sites existants ne sont pas modifies."""
    denied = _forbidden_unless_admin(request)
    if denied:
        return denied
    project = Project.objects.filter(pk=project_id).first()
    if not project:
        return _not_found("Projet")
    field_id = get_settings(project)["site_field_id"]
    if not field_id:
        return _bad_request("Choisis d'abord, dans les réglages, le champ qui contient le site.")
    existing = set(Site.objects.filter(project=project).values_list("code", flat=True))
    created = []
    for form in _forms_overview(project):
        for f in form["fields"]:
            if f["id"] != field_id:
                continue
            for option in f.get("options", []):
                if option["value"] not in existing:
                    existing.add(option["value"])
                    created.append(Site(project=project, code=option["value"], name=option["label"]))
    Site.objects.bulk_create(created)
    return Response({"created": len(created)})


@api_view(["PUT", "DELETE"])
@permission_classes([IsAuthenticated])
def site_detail(request, site_id):
    denied = _forbidden_unless_admin(request)
    if denied:
        return denied
    site = Site.objects.filter(pk=site_id).first()
    if not site:
        return _not_found("Site")

    if request.method == "PUT":
        error = _apply_site(site, request.data)
        if error:
            return _bad_request(error)
        failure = _save_site(site)
        return failure or Response(serialize_site(site))

    name, project_id = site.name, site.project_id
    site.delete()
    log_audit(request, user=request.user, action="delete", entity_type="site",
              entity_id=site_id, project_id=project_id, details=f"Site supprime : {name}")
    return Response({"status": "ok"})
