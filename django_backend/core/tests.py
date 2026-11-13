"""Tests du parcours complet de l'application.

Lancer avec :  python manage.py test

Ces tests couvrent les chemins qui font vraiment mal quand ils cassent :
reprise de donnees antidatees, idempotence de la file d'attente hors-ligne,
attribution des codes et numeros, cloisonnement des droits, et acces
communautaire sans compte.
"""
import json
from io import BytesIO

import pandas as pd
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase

from accounts.models import User
from cases.models import Case
from clinical_forms.models import Form
from core.models import AuditLog
from patients.models import Patient
from projects.models import Project
from submissions.models import Submission

FORM_DEFINITION = {
    "sections": [{
        "title": "Mesures",
        "fields": [
            {"id": "poids", "label": "Poids (kg)", "type": "number"},
            {"id": "suivi", "label": "Admis en suivi", "type": "boolean",
             "triggers_referral": True},
        ],
    }]
}


class BaseApiTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="admin@test.ci", password="motdepasse123",
            full_name="Admin Test", role="admin")
        self.client = Client()
        self.login(self.client, "admin@test.ci")

    def login(self, client, email, password="motdepasse123"):
        return client.post("/api/auth/login", {"email": email, "password": password},
                           content_type="application/json")

    def create_project(self, name="Nutrition Enfants"):
        return self.client.post("/api/projects", {"name": name},
                                content_type="application/json").json()

    def create_form(self, project_id, name="Depistage"):
        return self.client.post(
            "/api/forms",
            {"project_id": project_id, "name": name, "form_type": "survey",
             "definition": FORM_DEFINITION},
            content_type="application/json").json()


class AuthenticationTests(BaseApiTest):
    def test_connexion_pose_le_cookie_anti_csrf(self):
        """Sans ce cookie, toutes les requetes qui modifient des donnees
        seraient refusees par le serveur."""
        client = Client()
        response = self.login(client, "admin@test.ci")
        self.assertEqual(response.status_code, 200)
        self.assertIn("csrftoken", response.cookies)

    def test_mauvais_mot_de_passe_refuse(self):
        client = Client()
        response = self.login(client, "admin@test.ci", password="faux")
        self.assertEqual(response.status_code, 401)

    def test_compte_desactive_recoit_un_message_specifique(self):
        """Message distinct de "mot de passe incorrect" : sinon l'agent
        cherche une faute de frappe dans un mot de passe pourtant correct."""
        User.objects.create_user(email="inactif@test.ci", password="motdepasse123",
                                 is_active=False)
        client = Client()
        response = self.login(client, "inactif@test.ci")
        self.assertEqual(response.status_code, 401)
        self.assertIn("desactive", response.json()["detail"].lower())

    def test_acces_refuse_sans_connexion(self):
        response = Client().get("/api/projects")
        self.assertIn(response.status_code, (401, 403))


class PermissionTests(BaseApiTest):
    def setUp(self):
        super().setUp()
        User.objects.create_user(email="agent@test.ci", password="motdepasse123",
                                 role="operator")
        self.agent = Client()
        self.login(self.agent, "agent@test.ci")

    def test_un_agent_ne_peut_pas_gerer_les_comptes(self):
        self.assertEqual(self.agent.get("/api/users").status_code, 403)

    def test_un_agent_ne_peut_pas_lire_le_journal_d_audit(self):
        self.assertEqual(self.agent.get("/api/audit-logs").status_code, 403)


class PatientTests(BaseApiTest):
    def setUp(self):
        super().setUp()
        self.project = self.create_project()

    def test_code_anonyme_attribue_automatiquement(self):
        response = self.client.post(
            "/api/patients",
            {"project_id": self.project["id"], "first_name": "Awa", "last_name": "Kone"},
            content_type="application/json")
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()["anonymous_code"].startswith("PAT-"))

    def test_doublon_de_nom_signale_avant_creation(self):
        """L'agent est averti AVANT de creer le doublon, pas apres."""
        payload = {"project_id": self.project["id"], "first_name": "Awa", "last_name": "Kone"}
        self.client.post("/api/patients", payload, content_type="application/json")
        response = self.client.post("/api/patients", payload, content_type="application/json")
        self.assertEqual(response.status_code, 409)
        self.assertIn("existing_patient_id", response.json()["detail"])

    def test_creation_possible_apres_confirmation_explicite(self):
        payload = {"project_id": self.project["id"], "first_name": "Awa", "last_name": "Kone"}
        self.client.post("/api/patients", payload, content_type="application/json")
        response = self.client.post("/api/patients?force=true", payload,
                                    content_type="application/json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Patient.objects.filter(project_id=self.project["id"]).count(), 2)

    def test_telephone_deja_utilise_refuse(self):
        payload = {"project_id": self.project["id"], "first_name": "Awa",
                   "last_name": "Kone", "phone": "07 12 34 56 78"}
        self.client.post("/api/patients", payload, content_type="application/json")
        # Meme numero, ecrit differemment : doit quand meme etre detecte.
        autre = {"project_id": self.project["id"], "first_name": "Koffi",
                 "last_name": "Yao", "phone": "0712345678"}
        response = self.client.post("/api/patients", autre, content_type="application/json")
        self.assertEqual(response.status_code, 409)

    def test_suppression_refusee_si_des_donnees_existent(self):
        patient = self.client.post(
            "/api/patients",
            {"project_id": self.project["id"], "first_name": "Awa", "last_name": "Kone"},
            content_type="application/json").json()
        form = self.create_form(self.project["id"])
        self.client.post("/api/submissions",
                         {"form_id": form["id"], "patient_id": patient["id"],
                          "responses": {"poids": 12}},
                         content_type="application/json")
        response = self.client.delete(f"/api/patients/{patient['id']}")
        self.assertEqual(response.status_code, 409)


class SubmissionTests(BaseApiTest):
    def setUp(self):
        super().setUp()
        self.project = self.create_project()
        self.form = self.create_form(self.project["id"])
        self.patient = self.client.post(
            "/api/patients",
            {"project_id": self.project["id"], "first_name": "Awa",
             "last_name": "Kone", "date_of_birth": "2026-04-18", "sex": "F"},
            content_type="application/json").json()

    def test_date_antidatee_conservee(self):
        """Rattrapage d'un dossier papier : la date saisie doit primer sur la
        date du jour."""
        response = self.client.post(
            "/api/submissions",
            {"form_id": self.form["id"], "patient_id": self.patient["id"],
             "responses": {"poids": 12}, "collected_at": "2024-02-08T00:00:00"},
            content_type="application/json")
        self.assertEqual(response.status_code, 201)
        self.assertTrue(str(response.json()["collected_at"]).startswith("2024-02-08"))

    def test_idempotence_pas_de_doublon_au_rejeu(self):
        """C'est ce qui rend sure la reprise de la file d'attente hors-ligne."""
        payload = {"form_id": self.form["id"], "patient_id": self.patient["id"],
                   "responses": {"poids": 12}, "idempotency_key": "jeton-1"}
        first = self.client.post("/api/submissions", payload, content_type="application/json")
        second = self.client.post("/api/submissions", payload, content_type="application/json")
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertTrue(second.json()["already_recorded"])

    def test_proposition_de_suivi_quand_le_critere_est_rempli(self):
        response = self.client.post(
            "/api/submissions",
            {"form_id": self.form["id"], "patient_id": self.patient["id"],
             "responses": {"poids": 12, "suivi": True}},
            content_type="application/json")
        self.assertTrue(response.json()["referral"]["suggest_referral"])


class CaseTests(BaseApiTest):
    def setUp(self):
        super().setUp()
        self.project = self.create_project()
        self.patient = self.client.post(
            "/api/patients",
            {"project_id": self.project["id"], "first_name": "Awa", "last_name": "Kone"},
            content_type="application/json").json()

    def test_numero_attribue_et_date_d_inscription_antidatee(self):
        response = self.client.post(
            "/api/cases",
            {"project_id": self.project["id"], "patient_id": self.patient["id"],
             "enrolled_at": "2025-01-01T00:00:00"},
            content_type="application/json")
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()["case_number"].startswith("CAS-"))
        self.assertTrue(str(response.json()["enrolled_at"]).startswith("2025-01-01"))

    def test_second_dossier_actif_refuse_sauf_confirmation(self):
        payload = {"project_id": self.project["id"], "patient_id": self.patient["id"]}
        self.client.post("/api/cases", payload, content_type="application/json")
        refuse = self.client.post("/api/cases", payload, content_type="application/json")
        self.assertEqual(refuse.status_code, 409)
        force = self.client.post("/api/cases?force=true", payload,
                                 content_type="application/json")
        self.assertEqual(force.status_code, 201)

    def test_suppression_au_milieu_ne_provoque_pas_de_collision(self):
        """Le piege que ce calcul evite : avec trois dossiers (0001, 0002,
        0003), supprimer celui du milieu ramene le COMPTAGE a 2, qui
        regenererait 0003 — un numero deja pris. En repartant du numero le
        plus haut reellement utilise, on obtient bien 0004."""
        payload = {"project_id": self.project["id"], "patient_id": self.patient["id"]}
        numeros = []
        for _ in range(3):
            case = self.client.post("/api/cases?force=true", payload,
                                    content_type="application/json").json()
            numeros.append((case["id"], case["case_number"]))

        milieu_id = numeros[1][0]
        self.client.delete(f"/api/cases/{milieu_id}")

        suivant = self.client.post("/api/cases?force=true", payload,
                                   content_type="application/json").json()
        numeros_restants = {n for _, n in numeros} - {numeros[1][1]}
        self.assertNotIn(suivant["case_number"], numeros_restants)
        self.assertEqual(suivant["case_number"], "CAS-2026-0004")

    def test_inscription_toujours_en_premier_dans_la_chronologie(self):
        """Une visite saisie le meme jour est enregistree a minuit, donc
        numeriquement AVANT la creation du dossier faite l'apres-midi."""
        case = self.client.post(
            "/api/cases",
            {"project_id": self.project["id"], "patient_id": self.patient["id"]},
            content_type="application/json").json()
        form = self.create_form(self.project["id"])
        self.client.post("/api/submissions",
                         {"form_id": form["id"], "patient_id": self.patient["id"],
                          "case_id": case["id"], "responses": {"poids": 10},
                          "collected_at": "2020-01-01T00:00:00"},
                         content_type="application/json")
        events = self.client.get(f"/api/cases/{case['id']}/timeline").json()["events"]
        self.assertEqual(events[0]["type"], "registration")


class ExportTests(BaseApiTest):
    def setUp(self):
        super().setUp()
        self.project = self.create_project()
        self.form = self.create_form(self.project["id"])
        patient = self.client.post(
            "/api/patients",
            {"project_id": self.project["id"], "first_name": "Awa", "last_name": "Kone"},
            content_type="application/json").json()
        self.client.post("/api/submissions",
                         {"form_id": self.form["id"], "patient_id": patient["id"],
                          "responses": {"poids": 12}},
                         content_type="application/json")

    def test_export_depistages_csv(self):
        response = self.client.get(f"/api/forms/{self.form['id']}/export?format=csv")
        self.assertEqual(response.status_code, 200)
        body = b"".join(response.streaming_content)
        # Le code anonyme figure a cote du nom, jamais a sa place : c'est a
        # l'utilisateur de retirer la colonne "patient" selon la situation.
        self.assertIn(b"code_anonyme", body)
        self.assertIn(b"patient", body)

    def test_export_personnes_csv(self):
        response = self.client.get(f"/api/forms/{self.form['id']}/export-patients?format=csv")
        self.assertEqual(response.status_code, 200)

    def test_export_dossiers_csv(self):
        response = self.client.get(f"/api/projects/{self.project['id']}/cases/export?format=csv")
        self.assertEqual(response.status_code, 200)

    def test_format_inconnu_refuse(self):
        response = self.client.get(f"/api/forms/{self.form['id']}/export?format=pdf")
        self.assertEqual(response.status_code, 400)

    def _entetes(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return b"".join(response.streaming_content).decode("utf-8-sig").splitlines()[0].split(";")

    def test_selection_de_variables_a_l_export(self):
        """Preparer un fichier pour Epi Info : on ne garde que les variables
        utiles a l'analyse, sans avoir a supprimer des colonnes dans le
        tableur ensuite."""
        colonnes = self._entetes(
            f"/api/forms/{self.form['id']}/export?format=csv"
            f"&identity=code_anonyme&identity=age&columns=poids")
        self.assertEqual(colonnes, ["code_anonyme", "age", "poids"])
        self.assertNotIn("patient", colonnes)
        self.assertNotIn("suivi", colonnes)

    def test_sans_selection_tout_est_exporte(self):
        colonnes = self._entetes(f"/api/forms/{self.form['id']}/export?format=csv")
        for attendu in ("patient", "code_anonyme", "age", "sexe", "poids", "suivi"):
            self.assertIn(attendu, colonnes)

    def test_question_homonyme_d_une_colonne_d_identite(self):
        """Un formulaire peut contenir une question nommee "age" ou "sexe".
        Les deux espaces de noms sont separes : cocher la colonne d'identite
        ne doit pas ramener la question du formulaire, ni l'inverse."""
        form = self.client.post(
            "/api/forms",
            {"project_id": self.project["id"], "name": "Homonymes", "form_type": "survey",
             "definition": {"sections": [{"title": "S", "fields": [
                 {"id": "age", "label": "Age declare", "type": "number"},
                 {"id": "poids", "label": "Poids", "type": "number"},
             ]}]}},
            content_type="application/json").json()
        patient = self.client.post(
            "/api/patients",
            {"project_id": self.project["id"], "first_name": "Koffi", "last_name": "Yao"},
            content_type="application/json").json()
        self.client.post("/api/submissions",
                         {"form_id": form["id"], "patient_id": patient["id"],
                          "responses": {"age": 30, "poids": 70}},
                         content_type="application/json")

        # Seule la colonne d'identite "age" est demandee.
        colonnes = self._entetes(
            f"/api/forms/{form['id']}/export?format=csv&identity=age&columns=poids")
        self.assertEqual(colonnes, ["age", "poids"])

        # Seule la question "age" du formulaire est demandee.
        colonnes = self._entetes(
            f"/api/forms/{form['id']}/export?format=csv&columns=age")
        self.assertEqual(colonnes, ["age"])


class CommunityLinkTests(BaseApiTest):
    def setUp(self):
        super().setUp()
        self.project = self.create_project()
        self.form = self.create_form(self.project["id"])
        activation = self.client.put(f"/api/forms/{self.form['id']}/public-link",
                                     {"enabled": True}, content_type="application/json")
        self.code = activation.json()["code"]
        self.anon = Client()

    def test_code_genere_une_seule_fois(self):
        self.assertTrue(self.code)
        # Une seconde consultation ne doit jamais redonner le code en clair.
        etat = self.client.get(f"/api/forms/{self.form['id']}/public-link").json()
        self.assertTrue(etat["has_code"])
        self.assertNotIn("code", etat)

    def test_acces_refuse_sans_code_ou_avec_mauvais_code(self):
        self.assertEqual(self.anon.get(f"/api/public/forms/{self.form['id']}").status_code, 403)
        response = self.anon.get(f"/api/public/forms/{self.form['id']}",
                                 headers={"X-Community-Code": "MAUVAIS"})
        self.assertEqual(response.status_code, 403)

    def test_parcours_complet_sans_compte(self):
        hdr = {"X-Community-Code": self.code}
        self.assertEqual(
            self.anon.get(f"/api/public/forms/{self.form['id']}", headers=hdr).status_code, 200)

        patient = self.anon.post(f"/api/public/forms/{self.form['id']}/patients",
                                 {"first_name": "Koffi", "last_name": "Yao"},
                                 content_type="application/json", headers=hdr)
        self.assertEqual(patient.status_code, 201)
        # Le projet est force a celui du formulaire : ce lien public ne doit
        # jamais permettre d'ecrire dans un autre projet.
        self.assertEqual(patient.json()["project_id"], self.project["id"])

        submission = self.anon.post(f"/api/public/forms/{self.form['id']}/submissions",
                                    {"patient_id": patient.json()["id"],
                                     "responses": {"poids": 9}},
                                    content_type="application/json", headers=hdr)
        self.assertEqual(submission.status_code, 201)

    def test_blocage_apres_essais_repetes_du_code(self):
        """Ce lien n'a aucun compte a verrouiller : sans ce blocage, rien ne
        freinerait un script essayant des codes au hasard."""
        for _ in range(9):
            self.anon.get(f"/api/public/forms/{self.form['id']}",
                          headers={"X-Community-Code": "FAUX"})
        response = self.anon.get(f"/api/public/forms/{self.form['id']}",
                                 headers={"X-Community-Code": "FAUX"})
        self.assertEqual(response.status_code, 429)


class FormTests(BaseApiTest):
    def setUp(self):
        super().setUp()
        self.project = self.create_project()
        self.form = self.create_form(self.project["id"])

    def test_formulaire_avec_soumissions_archive_au_lieu_d_etre_supprime(self):
        """Supprimer effacerait des donnees deja recueillies."""
        patient = self.client.post(
            "/api/patients",
            {"project_id": self.project["id"], "first_name": "Awa", "last_name": "Kone"},
            content_type="application/json").json()
        self.client.post("/api/submissions",
                         {"form_id": self.form["id"], "patient_id": patient["id"],
                          "responses": {"poids": 12}},
                         content_type="application/json")
        response = self.client.delete(f"/api/forms/{self.form['id']}")
        self.assertEqual(response.json()["status"], "archived")
        self.assertFalse(Form.objects.get(pk=self.form["id"]).is_active)

    def test_formulaire_vide_supprime_reellement(self):
        response = self.client.delete(f"/api/forms/{self.form['id']}")
        self.assertEqual(response.json()["status"], "deleted")
        self.assertFalse(Form.objects.filter(pk=self.form["id"]).exists())


class ImportStructureTests(BaseApiTest):
    """L'import doit reconnaitre le format du fichier tout seul : les
    questionnaires de l'ONG naissent dans Kobo, pas dans notre modele, et
    personne sur place ne peut les convertir a la main."""

    def setUp(self):
        super().setUp()
        self.project = self.create_project()

    def _xlsx(self, feuilles: dict) -> bytes:
        tampon = BytesIO()
        with pd.ExcelWriter(tampon, engine="openpyxl") as w:
            for nom, lignes in feuilles.items():
                pd.DataFrame(lignes).to_excel(w, sheet_name=nom, index=False)
        return tampon.getvalue()

    def _importer(self, contenu: bytes, nom="fichier.xlsx"):
        return self.client.post(
            "/api/forms/import",
            {"project_id": self.project["id"], "name": "Importe",
             "file": SimpleUploadedFile(nom, contenu)})

    def _champs(self, form_id):
        definition = Form.objects.get(pk=form_id).definition
        return {f["id"]: f for s in definition["sections"] for f in s["fields"]}, definition

    def test_modele_de_l_application(self):
        contenu = self._xlsx({"Structure": [
            {"id": "poids", "type": "number", "label": "Poids (kg)", "required": "oui", "min": 1, "max": 200},
        ]})
        reponse = self._importer(contenu)
        self.assertEqual(reponse.status_code, 201, reponse.content)
        self.assertEqual(reponse.json()["format"], "modele")
        champs, _ = self._champs(reponse.json()["id"])
        self.assertEqual(champs["poids"]["validation"], {"min": 1.0, "max": 200.0})

    def test_export_kobo_xlsform(self):
        """Les quatre ecarts Kobo/modele doivent etre absorbes sans aide."""
        contenu = self._xlsx({
            "survey": [
                {"type": "start", "name": "start"},
                {"type": "begin_group", "name": "g1", "label::Français (fr)": "Identité"},
                {"type": "text", "name": "nom", "label::Français (fr)": "Nom", "required": "yes"},
                {"type": "integer", "name": "age", "label::Français (fr)": "Âge",
                 "constraint": "${age} >= 15 and ${age} <= 80"},
                {"type": "select_one oui_non", "name": "contraception",
                 "label::Français (fr)": "Contraception ?"},
                {"type": "text", "name": "type_contra", "label::Français (fr)": "Laquelle ?",
                 "relevant": "${contraception} = 'oui'"},
                {"type": "end_group", "name": ""},
                {"type": "calculate", "name": "calc_adulte", "calculation": "if(${age} >= 18, 1, 0)"},
            ],
            "choices": [
                {"list_name": "oui_non", "name": "oui", "label::Français (fr)": "Oui"},
                {"list_name": "oui_non", "name": "non", "label::Français (fr)": "Non"},
            ],
        })
        reponse = self._importer(contenu)
        self.assertEqual(reponse.status_code, 201, reponse.content)
        corps = reponse.json()
        self.assertEqual(corps["format"], "xlsform")
        champs, definition = self._champs(corps["id"])

        self.assertNotIn("start", champs)                       # ligne technique retiree
        self.assertEqual(champs["contraception"]["type"], "select_one")
        self.assertEqual(champs["contraception"]["list_name"], "oui_non")
        self.assertEqual(champs["type_contra"]["relevant"], "contraception == 'oui'")
        self.assertEqual(champs["age"]["validation"], {"min": 15.0, "max": 80.0})
        self.assertTrue(champs["nom"]["required"])
        # Le calcul sans libelle est conserve, avec un libelle fabrique et une
        # formule traduite — et l'agent en est prevenu.
        self.assertEqual(champs["calc_adulte"]["type"], "calculated")
        self.assertEqual(champs["calc_adulte"]["formula"], "if(age >= 18, 1, 0)")
        self.assertTrue(champs["calc_adulte"]["label"])
        self.assertTrue(any("calc_adulte" in a for a in corps["avertissements"]))
        self.assertEqual(len(definition["choice_lists"]["oui_non"]), 2)
        self.assertEqual(definition["sections"][-1]["title"], "Variables calculées (automatique)")

    def test_condition_intraduisible_est_retiree_pas_bloquante(self):
        """Une fonction inconnue dans un relevant ne doit pas faire echouer
        tout l'import : la question reste visible et l'agent est prevenu."""
        contenu = self._xlsx({"survey": [
            {"type": "select_multiple sympt", "name": "symptomes", "label": "Symptômes"},
            {"type": "text", "name": "detail", "label": "Précisez",
             "relevant": "count-selected(${symptomes}) > 0"},
        ], "choices": [{"list_name": "sympt", "name": "fievre", "label": "Fièvre"}]})
        reponse = self._importer(contenu)
        self.assertEqual(reponse.status_code, 201, reponse.content)
        champs, _ = self._champs(reponse.json()["id"])
        self.assertNotIn("relevant", champs["detail"])
        self.assertTrue(any("count-selected" in a for a in reponse.json()["avertissements"]))

    def test_colonnes_en_francais(self):
        contenu = self._xlsx({"Feuil1": [
            {"Identifiant": "taille", "Type": "number", "Libellé": "Taille (cm)", "Obligatoire": "oui"},
        ]})
        reponse = self._importer(contenu)
        self.assertEqual(reponse.status_code, 201, reponse.content)
        champs, _ = self._champs(reponse.json()["id"])
        self.assertEqual(champs["taille"]["label"], "Taille (cm)")
        self.assertTrue(champs["taille"]["required"])

    def test_echec_explique_ce_qui_a_ete_vu(self):
        """Un fichier de reponses (une ligne par patient) n'est pas une
        structure : le message doit nommer les colonnes vues et ce qui manque,
        pas accuser une colonne qui n'existe pas."""
        contenu = self._xlsx({"Feuil1": [
            {"Nom": "Kone", "Prénom": "Awa", "Téléphone": "0700000000"},
        ]})
        reponse = self._importer(contenu)
        self.assertEqual(reponse.status_code, 422)
        detail = reponse.json()["detail"]
        self.assertIn("Colonnes trouvées : Nom, Prénom, Téléphone", detail)
        self.assertIn("label", detail)
        self.assertIn("RÉPONSES", detail)


class ImportEtudeTests(BaseApiTest):
    """Une etude deja realisee (Google Forms, Kobo...) arrive comme un fichier
    de reponses : une ligne par personne. L'import doit en tirer le
    questionnaire ET les fiches, ne jamais creer de doublon en cas de relance,
    et refuser une ligne sans bloquer les autres."""

    def setUp(self):
        super().setUp()
        self.project = self.create_project()
        self.lignes = [
            {"Marca temporal": "2025-03-04 10:12:00", "code_patiente": "P001", "prenom_patiente": "Awa",
             "nomfamille_patiente": "Kone", "telephone1_patiente": "0700000001", "sexe": "Féminin",
             "année de naissance": 1990, "date_enquete": "2025-03-04", "Poids (kg)": 61.5,
             "HIV_result": "Negatif", "Symptomes [fievre]": "Oui", "Symptomes [toux]": "", "Commentaire": "RAS"},
            {"Marca temporal": "2025-03-05 09:00:00", "code_patiente": "P002", "prenom_patiente": "Bakary",
             "nomfamille_patiente": "Traore", "telephone1_patiente": "0700000002", "sexe": "Homme",
             "année de naissance": 1985, "date_enquete": "2025-03-05", "Poids (kg)": 72,
             "HIV_result": "Positif", "Symptomes [fievre]": "", "Symptomes [toux]": "Oui", "Commentaire": ""},
            # Meme personne que la premiere ligne, revue : un patient, deux fiches.
            {"Marca temporal": "2025-06-01 11:00:00", "code_patiente": "P001", "prenom_patiente": "Awa",
             "nomfamille_patiente": "Kone", "telephone1_patiente": "0700000001", "sexe": "F",
             "année de naissance": 1990, "date_enquete": "2025-06-01", "Poids (kg)": 62,
             "HIV_result": "Negatif", "Symptomes [fievre]": "", "Symptomes [toux]": "", "Commentaire": "Controle"},
        ]

    def _xlsx(self, lignes) -> bytes:
        tampon = BytesIO()
        with pd.ExcelWriter(tampon, engine="openpyxl") as w:
            pd.DataFrame(lignes).to_excel(w, sheet_name="Feuil1", index=False)
        return tampon.getvalue()

    def _analyser(self, contenu):
        return self.client.post("/api/etudes/analyser",
                                {"project_id": self.project["id"], "file": SimpleUploadedFile("etude.xlsx", contenu)})

    def _importer(self, contenu, colonnes, a_blanc, nom="Etude HPV"):
        return self.client.post(
            "/api/etudes/importer",
            {"project_id": self.project["id"], "nom": nom, "a_blanc": "1" if a_blanc else "0",
             "colonnes": json.dumps(colonnes), "file": SimpleUploadedFile("etude.xlsx", contenu)})

    def test_analyse_devine_roles_et_types(self):
        reponse = self._analyser(self._xlsx(self.lignes))
        self.assertEqual(reponse.status_code, 200, reponse.content)
        prop = reponse.json()
        par_entete = {c["entete"]: c for c in prop["colonnes"]}
        self.assertEqual(prop["lignes"], 3)
        self.assertEqual(par_entete["prenom_patiente"]["role"], "prenom")
        self.assertEqual(par_entete["nomfamille_patiente"]["role"], "nom")
        self.assertEqual(par_entete["code_patiente"]["role"], "code")
        self.assertEqual(par_entete["telephone1_patiente"]["role"], "telephone")
        self.assertEqual(par_entete["sexe"]["role"], "sexe")
        self.assertEqual(par_entete["année de naissance"]["role"], "annee_naissance")
        self.assertEqual(par_entete["date_enquete"]["role"], "date_collecte")
        # L'horodatage Google Forms ne doit pas prendre la place de la vraie date.
        self.assertEqual(par_entete["Marca temporal"]["role"], "ignorer")
        self.assertEqual(par_entete["Poids (kg)"]["type"], "decimal")
        self.assertEqual(par_entete["HIV_result"]["type"], "select_one")
        self.assertEqual({o["label"] for o in par_entete["HIV_result"]["options"]}, {"Negatif", "Positif"})
        grille = [c for c in prop["colonnes"] if c["label"] == "Symptomes"][0]
        self.assertEqual(grille["type"], "select_multiple")
        self.assertEqual([o["label"] for o in grille["options"]], ["fievre", "toux"])
        self.assertEqual(par_entete["Commentaire"]["type"], "text")

    def test_a_blanc_ne_cree_rien_puis_import_cree_tout(self):
        contenu = self._xlsx(self.lignes)
        colonnes = self._analyser(contenu).json()["colonnes"]

        blanc = self._importer(contenu, colonnes, a_blanc=True)
        self.assertEqual(blanc.status_code, 200, blanc.content)
        self.assertEqual(blanc.json()["fiches"], 3)
        self.assertEqual(blanc.json()["patients_crees"], 2)
        self.assertEqual(Patient.objects.count(), 0)
        self.assertEqual(Submission.objects.count(), 0)
        self.assertEqual(Form.objects.count(), 0)

        reel = self._importer(contenu, colonnes, a_blanc=False)
        self.assertEqual(reel.status_code, 200, reel.content)
        rapport = reel.json()
        self.assertEqual((rapport["fiches"], rapport["patients_crees"], rapport["erreurs"]), (3, 2, []))

        # Le formulaire est visible dans la liste des formulaires du projet.
        formulaires = self.client.get(f"/api/forms?project_id={self.project['id']}").json()
        self.assertEqual([f["name"] for f in formulaires], ["Etude HPV"])
        form = Form.objects.get(pk=rapport["formulaire"]["id"])
        ids = [f["id"] for f in form.definition["sections"][0]["fields"]]
        self.assertNotIn("prenom_patiente", ids)              # l'identite est sur le patient, pas une question
        self.assertIn("poids_kg", ids)

        awa = Patient.objects.get(last_name="Kone")
        self.assertEqual(awa.sex, "F")
        self.assertEqual(str(awa.date_of_birth), "1990-07-01")   # annee seule -> 1er juillet
        self.assertEqual(awa.phone, "0700000001")
        self.assertTrue(awa.anonymous_code.startswith("PAT-"))
        self.assertEqual(Patient.objects.get(last_name="Traore").sex, "M")
        self.assertEqual(awa.submissions.count(), 2)
        fiche = awa.submissions.order_by("collected_at").first()
        self.assertEqual(fiche.collected_at.strftime("%Y-%m-%d"), "2025-03-04")
        self.assertEqual(fiche.responses["poids_kg"], 61.5)
        self.assertEqual(fiche.responses["hiv_result"], "negatif")
        self.assertEqual(fiche.responses["symptomes"], ["fievre"])

        # 400 dossiers medicaux crees d'un coup : ca doit se retrouver dans le
        # journal d'activite, avec les chiffres.
        trace = AuditLog.objects.filter(entity_type="study").first()
        self.assertIsNotNone(trace)
        self.assertIn("Etude HPV", trace.details)
        self.assertIn("3 fiche(s)", trace.details)

    def test_relance_sans_doublon_et_ligne_refusee_isolee(self):
        contenu = self._xlsx(self.lignes)
        colonnes = self._analyser(contenu).json()["colonnes"]
        self._importer(contenu, colonnes, a_blanc=False)

        # Relancer le meme fichier : rien de nouveau, rien en double.
        again = self._importer(contenu, colonnes, a_blanc=False).json()
        self.assertEqual((again["fiches"], again["patients_crees"], again["deja_enregistrees"]), (0, 0, 3))
        self.assertEqual(Submission.objects.count(), 3)
        self.assertEqual(Form.objects.count(), 1)

        # Une ligne de plus, avec un poids impossible a lire : elle est refusee
        # avec son numero Excel, les autres (deja importees) restent intactes.
        lignes = self.lignes + [dict(self.lignes[1], **{"code_patiente": "P003", "prenom_patiente": "Fatou",
                                                        "nomfamille_patiente": "Diallo", "telephone1_patiente": "0700000003",
                                                        "Poids (kg)": "soixante", "date_enquete": "2025-07-01"})]
        contenu2 = self._xlsx(lignes)
        colonnes2 = self._analyser(contenu2).json()["colonnes"]
        # Le melange de nombres et de texte fait deviner "texte" : l'agent
        # remet "decimal" a l'ecran, comme il le ferait pour de vrai.
        for c in colonnes2:
            if c["entete"] == "Poids (kg)":
                c["type"] = "decimal"
        rapport = self._importer(contenu2, colonnes2, a_blanc=False).json()
        self.assertEqual(rapport["deja_enregistrees"], 3)
        self.assertEqual(rapport["fiches"], 0)
        self.assertEqual(len(rapport["erreurs"]), 1)
        self.assertEqual(rapport["erreurs"][0]["ligne"], 5)     # ligne 1 = en-tetes
        self.assertIn("nombre", rapport["erreurs"][0]["message"])
        self.assertFalse(Patient.objects.filter(last_name="Diallo").exists())


class HealthTests(TestCase):
    def test_point_de_controle_accessible_sans_connexion(self):
        """Adresse interrogee par le stockage hors-ligne pour savoir si le
        serveur repond : la deplacer casserait la detection du retour de
        connexion."""
        response = Client().get("/health")
        self.assertEqual(response.status_code, 200)


class AccesPagesFrontendTests(TestCase):
    """Garde d'acces aux pages du frontend.

    Avant, Django servait index.html a tout le monde et c'est le JavaScript
    qui, une fois la page AFFICHEE, interrogeait /api/auth/me puis renvoyait
    vers la connexion : un visiteur non connecte voyait l'accueil clignoter
    une seconde. La redirection se fait desormais cote serveur, avant le
    moindre octet de HTML.

    Ces tests verifient surtout ce qui ne doit PAS etre redirige : la page de
    connexion elle-meme, le lien communautaire (rempli sans compte) et toutes
    les ressources. Rediriger une feuille de style rendrait la page de
    connexion illisible, et rediriger community.html couperait les agents de
    terrain qui n'ont pas de compte.
    """

    def setUp(self):
        User.objects.create_user(email="admin@test.ci", password="motdepasse123",
                                 full_name="Admin Test", role="admin")
        self.anonyme = Client()

    def test_accueil_redirige_vers_la_connexion(self):
        reponse = self.anonyme.get("/")
        self.assertEqual(reponse.status_code, 302)
        self.assertEqual(reponse["Location"], "/login.html")

    def test_page_interne_redirige_et_memorise_la_destination(self):
        """Un favori vers une page precise doit y ramener apres connexion,
        pas deposer l'agent sur l'accueil."""
        reponse = self.anonyme.get("/patients.html")
        self.assertEqual(reponse.status_code, 302)
        self.assertEqual(reponse["Location"], "/login.html?suivant=%2Fpatients.html")

    def test_la_redirection_n_est_jamais_mise_en_cache(self):
        """Une redirection memorisee continuerait d'envoyer vers la connexion
        meme une fois la session ouverte."""
        reponse = self.anonyme.get("/")
        self.assertIn("no-store", reponse["Cache-Control"])

    def test_page_de_connexion_accessible_sans_compte(self):
        self.assertEqual(self.anonyme.get("/login.html").status_code, 200)

    def test_lien_communautaire_accessible_sans_compte(self):
        """community.html est remplie par des agents sans compte, protegee par
        un code d'acces partage : la rediriger les couperait entierement."""
        self.assertEqual(self.anonyme.get("/community.html").status_code, 200)

    def test_les_ressources_restent_accessibles_sans_compte(self):
        """La page de connexion a besoin de ses styles, scripts et polices ;
        sw.js doit rester joignable pour pouvoir se mettre a jour."""
        for chemin in ("/style.css", "/style_blanc_bleu.css", "/icons.js",
                       "/sw.js", "/img/connexion.svg",
                       "/fonts/inter-latin-400-normal.woff2"):
            with self.subTest(chemin=chemin):
                self.assertEqual(self.anonyme.get(chemin).status_code, 200)

    def test_une_fois_connecte_les_pages_sont_servies(self):
        connecte = Client()
        connecte.post("/api/auth/login",
                      {"email": "admin@test.ci", "password": "motdepasse123"},
                      content_type="application/json")
        for chemin in ("/", "/patients.html"):
            with self.subTest(chemin=chemin):
                self.assertEqual(connecte.get(chemin).status_code, 200)
