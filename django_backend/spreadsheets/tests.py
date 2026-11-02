"""Tests du module 3 (tableurs).

Couvre ce qui ferait vraiment mal en cas de regression : qu'un tableur
depose reste consultable par tout le monde (la raison d'etre du module), que
l'apercu lise correctement un vrai classeur, et que les formats qui n'ont
rien a faire la soient refuses avec un message comprehensible.
"""
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.test.client import MULTIPART_CONTENT, encode_multipart
from openpyxl import Workbook

from accounts.models import User
from projects.models import Project
from spreadsheets.models import Spreadsheet


def classeur_de_test() -> bytes:
    wb = Workbook()
    f1 = wb.active
    f1.title = "Budget"
    f1.append(["Poste", "Prevu", "Depense"])
    f1.append(["Materiel", 1200, 980])
    f2 = wb.create_sheet("Activites")
    f2.append(["Mois", "Seances"])
    f2.append(["Janvier", 12])
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


class SpreadsheetTests(TestCase):
    def setUp(self):
        User.objects.create_user(email="gestionnaire@test.ci", password="motdepasse123",
                                 full_name="Gestionnaire A", role="admin")
        self.client = Client()
        self.client.post("/api/auth/login",
                         {"email": "gestionnaire@test.ci", "password": "motdepasse123"},
                         content_type="application/json")
        self.project = self.client.post("/api/projects", {"name": "Projet Nutrition"},
                                        content_type="application/json").json()

    def deposer(self, nom="suivi.xlsx", contenu=None, titre="Suivi budgetaire"):
        return self.client.post(
            f"/api/projects/{self.project['id']}/spreadsheets",
            {"file": SimpleUploadedFile(nom, contenu if contenu is not None else classeur_de_test()),
             "title": titre})

    def test_depot_et_apercu_d_un_classeur(self):
        response = self.deposer()
        self.assertEqual(response.status_code, 201)

        apercu = self.client.get(f"/api/spreadsheets/{response.json()['id']}/preview").json()
        feuilles = apercu["sheets"]
        self.assertEqual([f["name"] for f in feuilles], ["Budget", "Activites"])
        self.assertEqual(feuilles[0]["rows"][0], ["Poste", "Prevu", "Depense"])
        self.assertEqual(feuilles[0]["rows"][1], ["Materiel", 1200, 980])

    def test_un_collegue_voit_le_tableau_depose_par_l_autre(self):
        """La raison d'etre du module : quand un gestionnaire est absent, ses
        tableurs restent consultables par le reste de l'equipe."""
        self.deposer(titre="Tableur du gestionnaire A")

        User.objects.create_user(email="patron@test.ci", password="motdepasse123", role="admin")
        autre = Client()
        autre.post("/api/auth/login", {"email": "patron@test.ci", "password": "motdepasse123"},
                   content_type="application/json")

        liste = autre.get(f"/api/projects/{self.project['id']}/spreadsheets").json()
        self.assertEqual(len(liste), 1)
        self.assertEqual(liste[0]["title"], "Tableur du gestionnaire A")
        self.assertEqual(
            autre.get(f"/api/spreadsheets/{liste[0]['id']}/preview").status_code, 200)

    def test_csv_francais_avec_point_virgule(self):
        """Un CSV exporte depuis Excel en francais utilise ";" et commence par
        une marque d'ordre des octets : les deux doivent passer inapercus."""
        csv = "﻿Nom;Age;Ville\nKouassi;34;Abidjan\n".encode("utf-8")
        response = self.deposer(nom="liste.csv", contenu=csv, titre="Liste")
        self.assertEqual(response.status_code, 201)

        feuilles = self.client.get(
            f"/api/spreadsheets/{response.json()['id']}/preview").json()["sheets"]
        self.assertEqual(feuilles[0]["rows"][0], ["Nom", "Age", "Ville"])
        self.assertEqual(feuilles[0]["rows"][1], ["Kouassi", "34", "Abidjan"])

    def test_ancien_format_excel_refuse_avec_explication(self):
        response = self.deposer(nom="vieux.xls", contenu=b"x" * 50)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Enregistrer sous", str(response.json()))

    def test_fichier_qui_n_est_pas_un_tableur_refuse(self):
        response = self.deposer(nom="programme.exe", contenu=b"x" * 50)
        self.assertEqual(response.status_code, 400)

    def test_telechargement_force_jamais_affiche_dans_la_page(self):
        sid = self.deposer().json()["id"]
        response = self.client.get(f"/api/spreadsheets/{sid}/download")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")

    def test_remplacement_par_une_version_plus_recente(self):
        """Remplacer plutot que redeposer : sinon personne ne sait quel
        fichier fait foi."""
        sid = self.deposer().json()["id"]
        ancien_fichier = Spreadsheet.objects.get(pk=sid).stored_filename

        wb = Workbook()
        wb.active.append(["Poste", "Prevu"])
        wb.active.append(["Materiel", 2000])
        buffer = io.BytesIO()
        wb.save(buffer)

        # Un PUT multipart s'encode a la main avec le client de test : la
        # forme « dictionnaire » n'est acceptee que par post().
        response = self.client.put(
            f"/api/spreadsheets/{sid}",
            data=encode_multipart("BoUnDaRy",
                                  {"file": SimpleUploadedFile("v2.xlsx", buffer.getvalue())}),
            content_type="multipart/form-data; boundary=BoUnDaRy")
        self.assertEqual(response.status_code, 200)

        rafraichi = Spreadsheet.objects.get(pk=sid)
        self.assertNotEqual(rafraichi.stored_filename, ancien_fichier)
        self.assertEqual(Spreadsheet.objects.count(), 1)

    def test_acces_refuse_sans_connexion(self):
        sid = self.deposer().json()["id"]
        anonyme = Client()
        self.assertIn(anonyme.get(f"/api/spreadsheets/{sid}/preview").status_code, (401, 403))
        self.assertIn(
            anonyme.get(f"/api/projects/{self.project['id']}/spreadsheets").status_code, (401, 403))

    def test_suppression(self):
        sid = self.deposer().json()["id"]
        self.assertEqual(self.client.delete(f"/api/spreadsheets/{sid}").status_code, 200)
        self.assertEqual(Spreadsheet.objects.count(), 0)
