"""Tests du tableau de bord des indicateurs.

Couvrent les erreurs qui fausseraient un chiffre envoye a un bailleur :
pourcentage additionne mois par mois, personne comptee deux fois, fiche
antidatee rangee dans le mauvais mois, patient sans sexe ni age "perdu" du
total, et ventilation des cibles.
"""
from datetime import date, datetime

from django.test import Client, TestCase

from accounts.models import User
from clinical_forms.models import Form
from core.models import AuditLog
from indicators.models import DashboardSettings, Indicator, IndicatorTarget, Site
from patients.models import Patient
from projects.models import Project
from submissions.models import Submission

DEFINITION = {
    "choice_lists": {"sites": [{"value": "abj", "label": "CHU Abidjan"},
                               {"value": "bke", "label": "CHR Bouaké"}]},
    "sections": [{"title": "Dépistage", "fields": [
        {"id": "site", "label": "Site", "type": "select_one", "list_name": "sites"},
        {"id": "resultat", "label": "Résultat", "type": "select_one",
         "options": [{"value": "positif", "label": "Positif"}, {"value": "negatif", "label": "Négatif"}]},
        {"id": "nb", "label": "Nombre", "type": "number"},
    ]}],
}
PERIOD = {"start": "2026-01", "end": "2026-03"}


class DashboardTestBase(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(email="admin@test.ci", password="motdepasse123",
                                              full_name="Admin", role="admin")
        self.client = Client()
        self.client.post("/api/auth/login", {"email": "admin@test.ci", "password": "motdepasse123"},
                         content_type="application/json")
        self.project = Project.objects.create(name="Drépanocytose", main_donor="Recover")
        self.form = Form.objects.create(project=self.project, name="Dépistage", definition=DEFINITION)
        DashboardSettings.objects.create(project=self.project, site_field_id="site")
        self.abj = Site.objects.create(project=self.project, code="abj", name="CHU Abidjan", region="Abidjan")
        self.bke = Site.objects.create(project=self.project, code="bke", name="CHR Bouaké", region="Gbêkê")
        self.awa = self.patient("Awa", "F", date(2000, 5, 1))
        self.yao = self.patient("Yao", "M", date(1960, 1, 1))

    def patient(self, name, sex, dob):
        return Patient.objects.create(project=self.project, first_name=name, last_name="Test",
                                      sex=sex, date_of_birth=dob)

    def submit(self, patient, when, **responses):
        return Submission.objects.create(form=self.form, patient=patient, responses=responses,
                                         collected_at=datetime.fromisoformat(when))

    def indicator(self, **kwargs):
        data = {"project": self.project, "form": self.form, "name": "Personnes dépistées"}
        data.update(kwargs)
        return Indicator.objects.create(**data)

    def dashboard(self, indicator, **params):
        query = {**PERIOD, "indicator_id": indicator.id, **params}
        return self.client.get(f"/api/projects/{self.project.id}/dashboard", query)


class CalculTests(DashboardTestBase):
    def test_fiche_antidatee_comptee_dans_le_mois_de_l_evenement(self):
        """Saisie aujourd'hui, mais collected_at en fevrier : c'est fevrier
        qui doit monter, pas le mois de saisie."""
        ind = self.indicator()
        self.submit(self.awa, "2026-02-14T10:00", site="abj")
        data = self.dashboard(ind).json()
        self.assertEqual([m["value"] for m in data["by_month"]], [0, 1, 0])
        self.assertEqual(data["kpi"]["value"], 1)

    def test_pourcentage_recalcule_sur_la_periode_et_non_additionne(self):
        ind = self.indicator(name="Taux de positivité", calculation="percentage",
                             condition="resultat == 'positif'")
        self.submit(self.awa, "2026-01-10T09:00", site="abj", resultat="positif")
        self.submit(self.awa, "2026-02-10T09:00", site="abj", resultat="positif")
        for _ in range(2):
            self.submit(self.yao, "2026-02-11T09:00", site="bke", resultat="negatif")
        data = self.dashboard(ind).json()
        self.assertEqual([m["value"] for m in data["by_month"]], [100.0, 33.3, None])
        # 2 positifs sur 4 = 50 %, ni 133 % (somme) ni 66,7 % (moyenne).
        self.assertEqual(data["kpi"]["value"], 50.0)
        self.assertEqual(data["by_month"][1]["cumulative"], 50.0)

    def test_personnes_distinctes_non_comptees_deux_fois(self):
        ind = self.indicator(calculation="count_patients")
        self.submit(self.awa, "2026-01-05T09:00", site="abj")
        self.submit(self.awa, "2026-03-05T09:00", site="abj")
        data = self.dashboard(ind).json()
        self.assertEqual([m["value"] for m in data["by_month"]], [1, 0, 1])
        self.assertEqual(data["kpi"]["value"], 1)
        self.assertEqual(data["by_month"][2]["cumulative"], 1)

    def test_somme_d_un_champ(self):
        ind = self.indicator(calculation="sum", value_field_id="nb")
        self.submit(self.awa, "2026-01-05T09:00", site="abj", nb=3)
        self.submit(self.yao, "2026-01-06T09:00", site="abj", nb="2,5")
        self.assertEqual(self.dashboard(ind).json()["kpi"]["value"], 5.5)

    def test_patient_sans_sexe_ni_age_garde_dans_le_total(self):
        ind = self.indicator()
        inconnu = self.patient("Inconnu", None, None)
        self.submit(inconnu, "2026-01-05T09:00", site="abj")
        self.submit(self.awa, "2026-01-05T09:00", site="abj")
        data = self.dashboard(ind).json()
        self.assertEqual(data["kpi"]["value"], 2)
        nr = next(b for b in data["by_age_sex"] if b["age_band"] == "nr")
        self.assertEqual(nr["NR"], 1)
        self.assertEqual(data["age_sex_totals"], {"F": 1, "M": 0, "NR": 1})

    def test_filtre_sexe_et_colonne_exclue(self):
        ind = self.indicator()
        self.submit(self.awa, "2026-01-05T09:00", site="abj")
        self.submit(self.yao, "2026-01-05T09:00", site="abj")
        data = self.dashboard(ind, sexe="F").json()
        self.assertEqual(data["kpi"]["value"], 1)
        abj = next(s for s in data["by_site"] if s["code"] == "abj")
        self.assertEqual((abj["F"], abj["M"]), (1, None))
        # La pyramide ignore le filtre sexe.
        self.assertEqual(data["age_sex_totals"]["M"], 1)

    def test_site_absent_du_formulaire_range_dans_non_renseigne(self):
        ind = self.indicator()
        self.submit(self.awa, "2026-01-05T09:00")
        data = self.dashboard(ind).json()
        self.assertIn("nr", [s["code"] for s in data["by_site"]])
        self.assertEqual(data["by_site_total"]["value"], 1)


class CibleTests(DashboardTestBase):
    def test_cible_annuelle_repartie_par_mois_et_par_site(self):
        ind = self.indicator(target=120)
        data = self.dashboard(ind).json()
        self.assertEqual(data["kpi"]["target"], 30)  # 3 mois x 120/12
        abj = next(s for s in data["by_site"] if s["code"] == "abj")
        self.assertEqual(abj["target"], 15)  # partagee entre 2 sites

    def test_cible_detaillee_remplace_la_part_par_defaut(self):
        ind = self.indicator(target=120)
        IndicatorTarget.objects.create(indicator=ind, site=self.abj, period="2026-01", value=40)
        data = self.dashboard(ind).json()
        # Janvier : 40 (abj) + 5 (bke) ; fevrier et mars : 10 chacun.
        self.assertEqual([m["target"] for m in data["by_month"]], [45, 10, 10])

    def test_cible_d_un_pourcentage_non_cumulee(self):
        ind = self.indicator(calculation="percentage", target=80)
        self.assertEqual(self.dashboard(ind).json()["kpi"]["target"], 80)

    def test_statut_selon_le_seuil(self):
        ind = self.indicator(target=12)  # 3 sur la periode
        for _ in range(3):
            self.submit(self.awa, "2026-01-05T09:00", site="abj")
        self.assertEqual(self.dashboard(ind).json()["kpi"]["status"], "reached")


class ApiTests(DashboardTestBase):
    def test_bailleurs_regroupes_sans_tenir_compte_de_la_casse(self):
        Project.objects.create(name="Paludisme", main_donor=" recover ")
        Project.objects.create(name="Sans financement")
        data = self.client.get("/api/donors").json()
        self.assertEqual(data[0]["name"], "Recover")
        self.assertEqual(len(data[0]["projects"]), 2)
        self.assertEqual(data[-1]["name"], "Sans bailleur")

    def test_export_csv_lisible_par_excel_et_trace(self):
        ind = self.indicator(calculation="percentage", condition="resultat == 'positif'")
        self.submit(self.awa, "2026-01-10T09:00", site="abj", resultat="positif")
        for _ in range(2):
            self.submit(self.yao, "2026-01-10T09:00", site="abj", resultat="negatif")
        response = self.dashboard(ind, format="csv")
        self.assertEqual(response.status_code, 200)
        content = b"".join(response.streaming_content).decode("utf-8")
        self.assertTrue(content.startswith("﻿"))
        self.assertIn("Bailleur;Recover", content)
        self.assertIn("33,3", content)
        self.assertNotIn("33.3", content)
        self.assertTrue(AuditLog.objects.filter(action="export", entity_type="dashboard").exists())

    def test_export_excel(self):
        ind = self.indicator()
        response = self.dashboard(ind, format="xlsx")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(b"".join(response.streaming_content).startswith(b"PK"))

    def test_format_inconnu_refuse(self):
        self.assertEqual(self.dashboard(self.indicator(), format="pdf").status_code, 400)

    def test_mois_invalide_refuse(self):
        self.assertEqual(self.dashboard(self.indicator(), start="2026-13").status_code, 400)

    def test_indicateur_d_un_autre_projet_introuvable(self):
        autre = Project.objects.create(name="Autre")
        autre_form = Form.objects.create(project=autre, name="F", definition=DEFINITION)
        ind = Indicator.objects.create(project=autre, form=autre_form, name="X")
        response = self.client.get(f"/api/projects/{self.project.id}/dashboard",
                                   {**PERIOD, "indicator_id": ind.id})
        self.assertEqual(response.status_code, 404)

    def test_projet_sans_indicateur_renvoie_un_etat_vide(self):
        projet = Project.objects.create(name="Vide")
        data = self.client.get(f"/api/projects/{projet.id}/dashboard").json()
        self.assertIsNone(data["indicator"])

    def test_operateur_ne_peut_pas_creer_d_indicateur(self):
        User.objects.create_user(email="op@test.ci", password="motdepasse123", role="operator")
        op = Client()
        op.post("/api/auth/login", {"email": "op@test.ci", "password": "motdepasse123"},
                content_type="application/json")
        response = op.post(f"/api/projects/{self.project.id}/indicators",
                           {"name": "X", "form_id": self.form.id}, content_type="application/json")
        self.assertEqual(response.status_code, 403)
        # ... mais peut consulter le tableau de bord.
        self.assertEqual(op.get(f"/api/projects/{self.project.id}/dashboard").status_code, 200)

    def test_condition_invalide_refusee_a_la_creation(self):
        response = self.client.post(
            f"/api/projects/{self.project.id}/indicators",
            {"name": "X", "form_id": self.form.id, "condition": "resultat === 'positif'"},
            content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_import_des_sites_depuis_le_formulaire(self):
        Site.objects.all().delete()
        response = self.client.post(f"/api/projects/{self.project.id}/sites/import")
        self.assertEqual(response.json()["created"], 2)
        self.assertEqual(Site.objects.get(code="bke").name, "CHR Bouaké")
