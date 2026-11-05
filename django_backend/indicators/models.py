"""Indicateurs de suivi-evaluation, sites et cibles du tableau de bord.

Principe directeur : une valeur d'indicateur n'est JAMAIS stockee. Elle est
toujours recalculee depuis les soumissions (voir services.py). On ne stocke
ici que la DEFINITION de l'indicateur, le referentiel des sites et les cibles
— ce qu'un humain decide, pas ce qui se deduit des donnees. Ainsi une
soumission corrigee ou antidatee apres coup est automatiquement prise en
compte, sans tache de recalcul a lancer ni chiffre perime a rattraper.
"""
from django.db import models
from django.utils import timezone

from core.utils import new_uuid

# Tranches d'age par defaut, [age_min, age_max] inclusifs ; None = sans
# borne superieure. Ce sont les tranches les plus demandees dans les rapports
# aux bailleurs (0-4 ans pour la petite enfance, 15-24 ans pour les jeunes).
DEFAULT_AGE_BANDS = [[0, 4], [5, 14], [15, 24], [25, 49], [50, None]]
DEFAULT_ON_TRACK_THRESHOLD = 85.0


def default_age_bands():
    # Une fonction et non la liste elle-meme : un default mutable partage
    # entre toutes les lignes serait modifie pour tout le monde a la fois.
    return [list(b) for b in DEFAULT_AGE_BANDS]


class DashboardSettings(models.Model):
    """Reglages du tableau de bord, un seul jeu par projet. Dans une table a
    part (et non sur Project) pour que la fonctionnalite reste autonome : la
    table projects n'est pas modifiee. Absent tant qu'aucun admin n'a rien
    regle — les valeurs par defaut s'appliquent alors (voir services)."""

    id = models.CharField(primary_key=True, max_length=36, default=new_uuid, editable=False)
    project = models.OneToOneField(
        "projects.Project", on_delete=models.CASCADE,
        db_column="project_id", related_name="dashboard_settings",
    )
    # Identifiant du champ de formulaire (select_one) qui contient le site.
    # Une soumission ne connait son site QUE par ce champ : sans lui, tout est
    # range dans "Non renseigne". Le meme id doit etre utilise dans tous les
    # formulaires du projet qui alimentent un indicateur.
    site_field_id = models.CharField(max_length=100, null=True, blank=True)
    # Seuil (en %) a partir duquel un taux d'atteinte est "En bonne voie".
    on_track_threshold = models.FloatField(default=DEFAULT_ON_TRACK_THRESHOLD)
    age_bands = models.JSONField(default=default_age_bands)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "dashboard_settings"


class Site(models.Model):
    """Site / structure sanitaire d'un projet, et sa region sanitaire.

    `code` est la VALEUR du choix dans le champ site du formulaire (pas son
    libelle) : c'est ce qui est reellement enregistre dans
    Submission.responses, et ce qui reste stable si l'on corrige
    l'orthographe du libelle affiche."""

    id = models.CharField(primary_key=True, max_length=36, default=new_uuid, editable=False)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE,
        db_column="project_id", related_name="sites",
    )
    code = models.CharField(max_length=100)
    name = models.CharField(max_length=255)
    region = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "sites"
        constraints = [
            models.UniqueConstraint(fields=["project", "code"], name="uq_site_project_code"),
        ]

    def __str__(self):
        return self.name


class Indicator(models.Model):
    CALC_COUNT = "count"
    CALC_COUNT_PATIENTS = "count_patients"
    CALC_SUM = "sum"
    CALC_PERCENTAGE = "percentage"
    CALCULATIONS = [
        (CALC_COUNT, "Nombre de fiches"),
        (CALC_COUNT_PATIENTS, "Nombre de personnes distinctes"),
        (CALC_SUM, "Somme d'un champ"),
        (CALC_PERCENTAGE, "Pourcentage"),
    ]

    id = models.CharField(primary_key=True, max_length=36, default=new_uuid, editable=False)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE,
        db_column="project_id", related_name="indicators",
    )
    # Le formulaire dont les soumissions alimentent l'indicateur. Supprimer le
    # formulaire supprime l'indicateur : sans source, il ne mesurerait plus rien.
    form = models.ForeignKey(
        "clinical_forms.Form", on_delete=models.CASCADE,
        db_column="form_id", related_name="indicators",
    )
    name = models.CharField(max_length=255)
    calculation = models.CharField(max_length=20, choices=CALCULATIONS, default=CALC_COUNT)
    # Champ numerique additionne (calcul "sum" uniquement).
    value_field_id = models.CharField(max_length=100, null=True, blank=True)
    # Conditions ecrites dans la syntaxe du moteur de formules du projet
    # (core/formula_engine.py), ex. "resultat_test == 'positif'". Vide = toutes
    # les soumissions. Pour un pourcentage : `condition` = numerateur,
    # `denominator_condition` = population de reference.
    condition = models.TextField(null=True, blank=True)
    denominator_condition = models.TextField(null=True, blank=True)
    # Cible ANNUELLE (12 mois) pour un indicateur additif ; taux vise (en %)
    # pour un pourcentage. Vide = pas de cible.
    target = models.FloatField(null=True, blank=True)
    unit = models.CharField(max_length=50, null=True, blank=True)
    display_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "indicators"
        ordering = ["display_order", "name"]

    def __str__(self):
        return self.name


class IndicatorTarget(models.Model):
    """Cible detaillee, OPTIONNELLE : remplace la repartition egale par defaut
    pour un mois donne, et pour un site donne ou pour tout le projet
    (site vide). Utile quand le bailleur fixe des cibles differentes selon la
    saison ou la taille du site."""

    id = models.CharField(primary_key=True, max_length=36, default=new_uuid, editable=False)
    indicator = models.ForeignKey(
        Indicator, on_delete=models.CASCADE,
        db_column="indicator_id", related_name="detailed_targets",
    )
    site = models.ForeignKey(
        Site, null=True, blank=True, on_delete=models.CASCADE,
        db_column="site_id", related_name="targets",
    )
    period = models.CharField(max_length=7)  # "AAAA-MM"
    value = models.FloatField()

    class Meta:
        db_table = "indicator_targets"
        constraints = [
            models.UniqueConstraint(fields=["indicator", "site", "period"],
                                    name="uq_indicator_target_site_period"),
            # NULL n'etant jamais egal a NULL en SQL, la contrainte ci-dessus
            # laisserait passer deux cibles "tout le projet" pour le meme mois.
            models.UniqueConstraint(fields=["indicator", "period"],
                                    condition=models.Q(site__isnull=True),
                                    name="uq_indicator_target_project_period"),
        ]
