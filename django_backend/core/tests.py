"""Tests du parcours complet de l'application.

Lancer avec :  python manage.py test

Ces tests couvrent les chemins qui font vraiment mal quand ils cassent :
reprise de donnees antidatees, idempotence de la file d'attente hors-ligne,
attribution des codes et numeros, cloisonnement des droits, et acces
communautaire sans compte.
"""
from django.test import Client, TestCase

from accounts.models import User
from cases.models import Case
from clinical_forms.models import Form
from patients.models import Patient
from projects.models import Project

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


class HealthTests(TestCase):
    def test_point_de_controle_accessible_sans_connexion(self):
        """Adresse interrogee par le stockage hors-ligne pour savoir si le
        serveur repond : la deplacer casserait la detection du retour de
        connexion."""
        response = Client().get("/health")
        self.assertEqual(response.status_code, 200)
