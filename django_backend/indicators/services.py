"""Calcul des indicateurs du tableau de bord.

Toute valeur affichee est RECALCULEE ici depuis les soumissions, a chaque
demande : rien n'est stocke (voir models.py).

Methode : on charge les soumissions du formulaire source sur la periode en
UNE requete (avec le patient), on transforme chacune en une "ligne" deja
evaluee (mois, site, sexe, tranche d'age, la condition est-elle remplie ?),
puis chaque vue du tableau de bord (par mois, par site, par region, par age
et sexe) n'est qu'une agregation sur un sous-ensemble de ces lignes.

Pourquoi agreger un sous-ensemble plutot qu'additionner des sous-totaux :
deux calculs ne sont PAS additifs. Un pourcentage annuel n'est pas la somme
(ni la moyenne) des pourcentages mensuels, et une personne depistee en mars
puis en juin compte deux fois si l'on additionne les "personnes distinctes"
de chaque mois. Recalculer sur l'ensemble concerne donne toujours le bon
chiffre, quel que soit le type d'indicateur.
"""
import re
from dataclasses import dataclass
from datetime import datetime

from django.utils import timezone

from core.formula_engine import evaluate_condition
from core.utils import age_at
from indicators.models import (
    DEFAULT_ON_TRACK_THRESHOLD, DashboardSettings, Indicator, IndicatorTarget,
    Site, default_age_bands,
)
from projects.models import Project
from submissions.models import Submission

NOT_PROVIDED = "Non renseigné"
# Valeur de filtre qui designe "Non renseigne" (site ou tranche d'age absents).
NOT_PROVIDED_KEY = "nr"
MAX_PERIOD_MONTHS = 36
STATUS_LABELS = {"reached": "Atteint", "on_track": "En bonne voie", "late": "En retard"}
MONTH_NAMES = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
               "août", "septembre", "octobre", "novembre", "décembre"]
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class DashboardError(ValueError):
    """Parametre de requete invalide : la vue la transforme en reponse 400."""


# --- Periodes (cles "AAAA-MM") --------------------------------------------

def month_key(d) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def add_months(key: str, n: int) -> str:
    year, month = map(int, key.split("-"))
    index = year * 12 + (month - 1) + n
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def month_label(key: str) -> str:
    year, month = map(int, key.split("-"))
    return f"{MONTH_NAMES[month - 1]} {year}"


def default_period(today=None, months=12):
    """Fenetre glissante qui s'arrete au dernier mois COMPLET : le mois en
    cours, a moitie saisi, ferait croire a une chute brutale de l'activite."""
    today = today or timezone.now().date()
    end = add_months(month_key(today), -1)
    return add_months(end, -(months - 1)), end


def resolve_period(start, end):
    if not start and not end:
        start, end = default_period()
    elif not start or not end:
        raise DashboardError("Indique à la fois le mois de début et le mois de fin.")
    if not _MONTH_RE.match(start) or not _MONTH_RE.match(end):
        raise DashboardError("Les mois doivent être au format AAAA-MM (ex. 2026-01).")
    if start > end:
        raise DashboardError("Le mois de début doit précéder le mois de fin.")
    periods = [start]
    while periods[-1] < end:
        periods.append(add_months(periods[-1], 1))
    if len(periods) > MAX_PERIOD_MONTHS:
        raise DashboardError(f"La période ne peut pas dépasser {MAX_PERIOD_MONTHS} mois.")
    return periods


def _month_start(key: str):
    # Datetime "naif" : le projet tourne avec USE_TZ = False (horodatages
    # herites de la version precedente), un datetime avec fuseau serait refuse
    # par la base.
    year, month = map(int, key.split("-"))
    return datetime(year, month, 1)


# --- Reglages du projet ---------------------------------------------------

def age_band_defs(bands):
    """[[0, 4], [50, None]] -> definitions avec cle de filtre et libelle."""
    defs = []
    for low, high in bands:
        if high is None:
            defs.append({"key": f"{low}+", "label": f"{low} ans et +", "min": low, "max": None})
        else:
            defs.append({"key": f"{low}-{high}", "label": f"{low}–{high} ans", "min": low, "max": high})
    return defs


def validate_age_bands(bands):
    """Renvoie un message d'erreur, ou None si les tranches sont utilisables :
    croissantes, sans chevauchement, seule la derniere peut etre ouverte."""
    if not isinstance(bands, list) or not bands:
        return "Il faut au moins une tranche d'âge."
    previous_high = -1
    for i, band in enumerate(bands):
        if not isinstance(band, (list, tuple)) or len(band) != 2:
            return "Chaque tranche doit avoir un âge minimum et un âge maximum."
        low, high = band
        if not isinstance(low, int) or low < 0:
            return "L'âge minimum d'une tranche doit être un nombre entier positif."
        if high is None and i != len(bands) - 1:
            return "Seule la dernière tranche peut rester sans âge maximum."
        if high is not None and (not isinstance(high, int) or high < low):
            return f"La tranche {low}–{high} est incohérente."
        if low <= previous_high:
            return "Les tranches d'âge se chevauchent ou ne sont pas dans l'ordre."
        previous_high = high if high is not None else low
    return None


def get_settings(project) -> dict:
    s = DashboardSettings.objects.filter(project=project).first()
    bands = (s.age_bands if s and s.age_bands else None) or default_age_bands()
    return {
        "site_field_id": (s.site_field_id if s else None) or None,
        "on_track_threshold": s.on_track_threshold if s else DEFAULT_ON_TRACK_THRESHOLD,
        "age_bands": bands,
        "age_band_defs": age_band_defs(bands),
    }


def _band_for(age, defs):
    if age is None:
        return None
    for d in defs:
        if age >= d["min"] and (d["max"] is None or age <= d["max"]):
            return d["key"]
    return None


def _normalize_sex(value):
    """Patient.sex vaut "F"/"M" dans l'application, mais une base reprise de
    l'ancienne version peut contenir "Feminin", "masculin"... : on ne garde
    que l'initiale. Tout le reste compte comme "Non renseigne"."""
    initial = (value or "").strip()[:1].upper()
    return initial if initial in ("F", "M") else None


# --- Lignes evaluees ------------------------------------------------------

@dataclass(slots=True)
class Row:
    month: str
    site: str | None
    sex: str | None
    band: str | None
    patient_id: str
    num: bool          # la condition (numerateur) est remplie
    den: bool          # la soumission fait partie du denominateur
    val: float | None  # valeur additionnee (calcul "sum")


def _form_field_ids(definition):
    return [f["id"] for s in (definition or {}).get("sections", [])
            for f in s.get("fields", []) if "id" in f]


def _to_float(value):
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        return None


class SubmissionCache:
    """Soumissions par formulaire, chargees une seule fois par requete HTTP :
    plusieurs indicateurs du meme projet partagent souvent le meme formulaire
    (tableau "tous les indicateurs"), inutile de relire la base pour chacun."""

    def __init__(self, periods):
        self.low = _month_start(periods[0])
        self.high = _month_start(add_months(periods[-1], 1))
        self._by_form = {}

    def get(self, form_id):
        if form_id not in self._by_form:
            # collected_at (date de l'evenement) et non created_at (date de
            # saisie) : une fiche papier de mars saisie en mai compte en mars.
            self._by_form[form_id] = list(
                Submission.objects
                .filter(form_id=form_id, collected_at__gte=self.low, collected_at__lt=self.high)
                .select_related("patient", "form")
            )
        return self._by_form[form_id]


def build_rows(indicator, submissions, cfg):
    site_field = cfg["site_field_id"]
    defs = cfg["age_band_defs"]
    calc = indicator.calculation
    rows = []
    field_ids = None
    for s in submissions:
        if field_ids is None:
            field_ids = _form_field_ids(s.form.definition)
        local_date = s.collected_at.date()
        patient = s.patient
        age = age_at(patient.date_of_birth, local_date) if patient else None
        sex = _normalize_sex(patient.sex if patient else None)
        responses = s.responses or {}

        # Variables disponibles dans les conditions : l'age et le sexe du
        # patient (sous les noms "age" et "sexe"), puis tous les champs du
        # formulaire pre-remplis a vide (sinon une condition sur un champ non
        # repondu echoue et retombe sur sa valeur par defaut — meme piege que
        # dans form_validation.py), puis les reponses. Un champ du formulaire
        # nomme "age" ou "sexe" l'emporte donc sur la variable patient.
        values = {"age": age, "sexe": sex}
        values.update({fid: None for fid in field_ids})
        values.update(responses)

        # default=False : une condition qui ne peut pas s'evaluer (faute de
        # frappe, champ supprime du formulaire) ne doit pas gonfler le chiffre
        # envoye au bailleur. Mieux vaut un zero visible qu'un faux positif.
        den = evaluate_condition(indicator.denominator_condition, values, default=False) \
            if calc == Indicator.CALC_PERCENTAGE else True
        num = den and evaluate_condition(indicator.condition, values, default=False)
        val = _to_float(responses.get(indicator.value_field_id)) \
            if calc == Indicator.CALC_SUM and num else None

        site = responses.get(site_field) if site_field else None
        site = str(site).strip() if site not in (None, "", []) else None
        rows.append(Row(
            month=month_key(local_date), site=site, sex=sex, band=_band_for(age, defs),
            patient_id=s.patient_id, num=num, den=den, val=val,
        ))
    return rows


def aggregate(calc, rows):
    if calc == Indicator.CALC_COUNT_PATIENTS:
        return len({r.patient_id for r in rows if r.num})
    if calc == Indicator.CALC_SUM:
        return round(sum(r.val for r in rows if r.num and r.val is not None), 2)
    if calc == Indicator.CALC_PERCENTAGE:
        den = sum(1 for r in rows if r.den)
        if not den:
            return None
        return round(100 * sum(1 for r in rows if r.num) / den, 1)
    return sum(1 for r in rows if r.num)


def filter_rows(rows, site=None, sex=None, age=None):
    out = rows
    if site:
        out = [r for r in out if (r.site or NOT_PROVIDED_KEY) == site]
    if sex:
        out = [r for r in out if r.sex == sex]
    if age:
        out = [r for r in out if (r.band or NOT_PROVIDED_KEY) == age]
    return out


# --- Cibles ---------------------------------------------------------------

class Targets:
    """Ventilation de la cible d'un indicateur par mois et par site.

    - Indicateur additif : la cible annuelle est repartie a parts egales,
      1/12 par mois, puis a parts egales entre les sites du projet.
    - Pourcentage : la cible est un taux, identique pour chaque mois, chaque
      site et toute la periode — on ne repartit pas un taux.
    - Une cible detaillee (IndicatorTarget) remplace la part par defaut du
      mois (et du site) qu'elle vise.

    Les filtres sexe et age ne changent pas la cible : elle porte sur toute la
    population du projet, et le bailleur la fixe ainsi.
    """

    def __init__(self, indicator, sites):
        self.total = indicator.target
        self.is_pct = indicator.calculation == Indicator.CALC_PERCENTAGE
        self.sites = sites
        self.site_by_code = {s.code: s for s in sites}
        self.overrides = {(t.site_id, t.period): t.value
                          for t in IndicatorTarget.objects.filter(indicator=indicator)}
        self.override_months = {p for (sid, p) in self.overrides if sid}

    def month(self, period, site_code=None):
        if site_code:
            site = self.site_by_code.get(site_code)
            if not site:
                return None  # site inconnu du referentiel : pas de cible
            override = self.overrides.get((site.id, period))
            if override is not None:
                return override
            if self.total is None:
                return None
            return self.total if self.is_pct else self.total / 12 / len(self.sites)

        override = self.overrides.get((None, period))
        if override is not None:
            return override
        if self.is_pct:
            return self.total
        if self.sites and period in self.override_months:
            # Des cibles par site existent pour ce mois : la cible du projet
            # est leur somme, pour que le total du tableau reste coherent avec
            # ses lignes.
            parts = [self.month(period, s.code) for s in self.sites]
            parts = [p for p in parts if p is not None]
            return round(sum(parts), 2) if parts else None
        return self.total / 12 if self.total is not None else None

    def over(self, periods, site_code=None):
        values = [v for v in (self.month(p, site_code) for p in periods) if v is not None]
        if not values:
            return None
        if self.is_pct:
            # "Cible cumulee" d'un pourcentage = la cible elle-meme (moyenne
            # des cibles mensuelles, egale a la cible sans cible detaillee).
            return round(sum(values) / len(values), 1)
        return round(sum(values), 2)


def progress(value, target):
    if value is None or not target:
        return None
    return round(100 * value / target, 1)


def status_for(rate, threshold):
    if rate is None:
        return None
    if rate >= 100:
        return "reached"
    if rate >= threshold:
        return "on_track"
    return "late"


# --- Assemblage -----------------------------------------------------------

def serialize_indicator(i: Indicator) -> dict:
    return {
        "id": i.id, "project_id": i.project_id, "form_id": i.form_id, "name": i.name,
        "calculation": i.calculation, "value_field_id": i.value_field_id,
        "condition": i.condition, "denominator_condition": i.denominator_condition,
        "target": i.target, "unit": i.unit, "display_order": i.display_order,
    }


def serialize_site(s: Site) -> dict:
    return {"id": s.id, "code": s.code, "name": s.name, "region": s.region}


def donors_payload():
    """Bailleurs distincts. Le champ est du texte libre : on regroupe sans
    tenir compte des espaces ni de la casse ("UNICEF " = "Unicef"), sinon le
    meme bailleur apparaitrait deux fois dans la liste."""
    groups = {}
    for p in Project.objects.order_by("name"):
        name = (p.main_donor or "").strip()
        key = name.casefold()
        group = groups.setdefault(key, {"name": name or "Sans bailleur", "projects": []})
        group["projects"].append({"id": p.id, "name": p.name})
    # Les projets sans bailleur sont regroupes a la fin plutot qu'ignores :
    # sinon leurs indicateurs deviendraient inaccessibles depuis cet ecran.
    return sorted(groups.values(), key=lambda g: (g["name"] == "Sans bailleur", g["name"].casefold()))


def _check_filters(cfg, site, sex, age):
    if sex and sex not in ("F", "M"):
        raise DashboardError("Le filtre sexe doit valoir F ou M.")
    valid_ages = {d["key"] for d in cfg["age_band_defs"]} | {NOT_PROVIDED_KEY}
    if age and age not in valid_ages:
        raise DashboardError("Tranche d'âge inconnue pour ce projet.")


def compute_dashboard(project, indicator, periods, site=None, sex=None, age=None):
    cfg = get_settings(project)
    _check_filters(cfg, site, sex, age)
    threshold = cfg["on_track_threshold"]
    sites = list(Site.objects.filter(project=project).order_by("region", "name"))
    site_by_code = {s.code: s for s in sites}
    indicators = list(Indicator.objects.filter(project=project))
    cache = SubmissionCache(periods)

    base = {
        "donor": (project.main_donor or "").strip() or None,
        "project": {"id": project.id, "name": project.name},
        "period": {"start": periods[0], "end": periods[-1], "months": periods,
                   "labels": [month_label(p) for p in periods]},
        "filters": {"site": site or None, "sexe": sex or None, "age": age or None},
        "sites": [serialize_site(s) for s in sites],
        "age_bands": [{"key": d["key"], "label": d["label"]} for d in cfg["age_band_defs"]],
        "threshold": threshold,
        "site_field_id": cfg["site_field_id"],
        "computed_at": timezone.now(),
    }
    if indicator is None:
        return {**base, "indicator": None, "indicators_count": 0}

    calc = indicator.calculation
    is_pct = calc == Indicator.CALC_PERCENTAGE
    targets = Targets(indicator, sites)
    rows_all = build_rows(indicator, cache.get(indicator.form_id), cfg)
    rows_site = filter_rows(rows_all, site=site)
    rows = filter_rows(rows_site, sex=sex, age=age)

    def agg(rs):
        return aggregate(calc, rs)

    # Serie mensuelle, avec le cumul calcule ici sur l'ensemble des mois deja
    # ecoules (et non en additionnant les valeurs mensuelles cote navigateur :
    # faux pour un pourcentage ou des personnes distinctes).
    by_month, cumulative_rows = [], []
    for i, p in enumerate(periods):
        month_rows = [r for r in rows if r.month == p]
        cumulative_rows.extend(month_rows)
        by_month.append({
            "period": p, "value": agg(month_rows), "target": _r(targets.month(p, site)),
            "cumulative": agg(cumulative_rows),
            "cumulative_target": targets.over(periods[:i + 1], site),
        })

    value = agg(rows)
    target = targets.over(periods, site)
    rate = progress(value, target)
    last = by_month[-1]["value"]
    previous = by_month[-2]["value"] if len(by_month) > 1 else None
    variation = round(100 * (last - previous) / previous, 1) \
        if last is not None and previous not in (None, 0) else None
    kpi = {
        "value": value, "target": target, "progress": rate, "status": status_for(rate, threshold),
        "months": len(periods),
        "last_month": {"period": periods[-1], "value": last},
        "previous_month": {"period": periods[-2] if len(periods) > 1 else None, "value": previous},
        "variation": variation,
    }

    # Tous les indicateurs du projet, sous les memes filtres.
    all_indicators = []
    for ind in indicators:
        if ind.id == indicator.id:
            v, t = value, target
        else:
            other_rows = filter_rows(build_rows(ind, cache.get(ind.form_id), cfg),
                                     site=site, sex=sex, age=age)
            v = aggregate(ind.calculation, other_rows)
            t = Targets(ind, sites).over(periods, site)
        r = progress(v, t)
        all_indicators.append({"id": ind.id, "name": ind.name, "unit": ind.unit,
                               "calculation": ind.calculation, "value": v, "target": t,
                               "progress": r, "status": status_for(r, threshold)})

    # Regions sanitaires.
    region_rows = {}
    for r in rows:
        s = site_by_code.get(r.site)
        region_rows.setdefault((s.region if s and s.region else NOT_PROVIDED), []).append(r)
    if not site:
        for s in sites:  # une region sans activite apparait quand meme, a zero
            region_rows.setdefault(s.region or NOT_PROVIDED, [])
    # La part se calcule sur la SOMME des regions, pas sur le total : pour
    # des "personnes distinctes", une personne vue dans deux regions compte
    # dans chacune, et des parts rapportees au total depasseraient 100 %.
    # Pour un comptage ou une somme, les deux denominateurs sont egaux.
    # Pas de part pour un pourcentage (une part d'un taux n'a pas de sens).
    region_values = {region: agg(rs) for region, rs in region_rows.items()}
    region_sum = sum(v for v in region_values.values() if v) if not is_pct else 0
    by_region = []
    for region, v in region_values.items():
        share = round(100 * v / region_sum, 1) if region_sum and v is not None else None
        by_region.append({"region": region, "value": v, "share": share})
    by_region.sort(key=lambda x: (x["value"] is None, -(x["value"] or 0), x["region"]))

    # Pyramide age x sexe : ignore volontairement les filtres sexe et age
    # (sinon une moitie de la pyramide disparaitrait), garde le filtre site.
    band_keys = [d["key"] for d in cfg["age_band_defs"]]
    band_labels = {d["key"]: d["label"] for d in cfg["age_band_defs"]}
    if any(r.band is None for r in rows_site):
        band_keys.append(NOT_PROVIDED_KEY)
        band_labels[NOT_PROVIDED_KEY] = NOT_PROVIDED
    by_age_sex = []
    for key in band_keys:
        band = [r for r in rows_site if (r.band or NOT_PROVIDED_KEY) == key]
        by_age_sex.append({
            "age_band": key, "label": band_labels[key],
            "F": agg([r for r in band if r.sex == "F"]),
            "M": agg([r for r in band if r.sex == "M"]),
            "NR": agg([r for r in band if r.sex is None]),
        })
    age_sex_totals = {k: agg([r for r in rows_site if r.sex == s])
                      for k, s in (("F", "F"), ("M", "M"), ("NR", None))}

    # Detail par site. Les colonnes F/H restent calculees sur les lignes deja
    # filtrees ; la colonne exclue par le filtre sexe vaut None ("—").
    site_rows = {}
    for r in rows:
        site_rows.setdefault(r.site or NOT_PROVIDED_KEY, []).append(r)
    for s in sites:
        if not site or site == s.code:
            site_rows.setdefault(s.code, [])

    def site_line(label, region, rs, t):
        v = agg(rs)
        rate_ = progress(v, t)
        return {
            "site": label, "region": region,
            "F": agg([r for r in rs if r.sex == "F"]) if sex in (None, "", "F") else None,
            "M": agg([r for r in rs if r.sex == "M"]) if sex in (None, "", "M") else None,
            "value": v, "target": t, "progress": rate_, "status": status_for(rate_, threshold),
        }

    by_site = []
    for code, rs in site_rows.items():
        s = site_by_code.get(code)
        if s:
            line = site_line(s.name, s.region or NOT_PROVIDED, rs, targets.over(periods, code))
        elif code == NOT_PROVIDED_KEY:
            line = site_line(NOT_PROVIDED, NOT_PROVIDED, rs, None)
        else:
            # Valeur saisie dans le formulaire mais absente du referentiel :
            # affichee telle quelle pour que l'admin la voie et l'ajoute.
            line = site_line(code, NOT_PROVIDED, rs, None)
        line["code"] = code
        by_site.append(line)
    by_site.sort(key=lambda x: (x["progress"] is None, -(x["progress"] or 0),
                                -(x["value"] or 0), x["site"]))
    total = site_line("Total", "", rows, target)

    return {
        **base,
        "indicator": {**serialize_indicator(indicator),
                      "calculation_label": dict(Indicator.CALCULATIONS)[calc]},
        "indicators_count": len(indicators),
        "kpi": kpi,
        "by_month": by_month,
        "all_indicators": all_indicators,
        "by_region": by_region,
        "by_age_sex": by_age_sex,
        "age_sex_totals": age_sex_totals,
        "by_site": by_site,
        "by_site_total": total,
    }


def _r(v):
    return round(v, 2) if v is not None else None
