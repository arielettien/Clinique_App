"""Tests du module Tableur."""
from django.test import Client, TestCase

from accounts.models import User
from workbooks.models import Workbook

CONTENU = {
    "sheets": [{
        "name": "Budget",
        "cells": {"A1": {"v": "Poste"}, "B1": {"v": "Montant"},
                  "B2": {"v": "1200"}, "B3": {"v": "=SOMME(B2:B2)"}},
        "merges": [{"l": 0, "c": 0, "rs": 1, "cs": 2}],
        "rows": 40, "cols": 15,
    }]
}


class WorkbookTests(TestCase):
    def setUp(self):
        User.objects.create_user(email="gestionnaire@test.ci", password="motdepasse123",
                                 full_name="Gestionnaire A", role="admin")
        self.client = Client()
        self.client.post("/api/auth/login",
                         {"email": "gestionnaire@test.ci", "password": "motdepasse123"},
                         content_type="application/json")

    def creer(self, titre="Budget 2026"):
        return self.client.post("/api/workbooks", {"title": titre, "content": CONTENU},
                                content_type="application/json")

    def test_creation_et_relecture(self):
        response = self.creer()
        self.assertEqual(response.status_code, 201)

        relu = self.client.get(f"/api/workbooks/{response.json()['id']}").json()
        feuille = relu["content"]["sheets"][0]
        self.assertEqual(feuille["name"], "Budget")
        self.assertEqual(feuille["cells"]["B3"]["v"], "=SOMME(B2:B2)")
        # Les fusions font partie du contenu : les perdre casserait la mise
        # en page du classeur a la reouverture.
        self.assertEqual(feuille["merges"], [{"l": 0, "c": 0, "rs": 1, "cs": 2}])

    def test_titre_obligatoire(self):
        response = self.client.post("/api/workbooks", {"title": "  ", "content": CONTENU},
                                    content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_un_collegue_voit_et_modifie_le_classeur_de_l_autre(self):
        """Les classeurs sont partages : c'est tout l'interet par rapport a un
        fichier Excel pose sur un seul ordinateur."""
        wid = self.creer(titre="Budget partage").json()["id"]

        User.objects.create_user(email="patron@test.ci", password="motdepasse123",
                                 full_name="Patron", role="admin")
        autre = Client()
        autre.post("/api/auth/login", {"email": "patron@test.ci", "password": "motdepasse123"},
                   content_type="application/json")

        liste = autre.get("/api/workbooks").json()
        self.assertEqual(len(liste), 1)
        self.assertEqual(liste[0]["title"], "Budget partage")

        contenu = dict(CONTENU)
        contenu["sheets"][0]["cells"]["B4"] = {"v": "42"}
        response = autre.put(f"/api/workbooks/{wid}", {"content": contenu},
                             content_type="application/json")
        self.assertEqual(response.status_code, 200)
        # On trace qui a modifie en dernier : sans ca, impossible de savoir a
        # qui demander une explication sur un chiffre.
        self.assertEqual(response.json()["updated_by"], "Patron")

    def test_classeur_demesure_refuse(self):
        """Un classeur trop gros rendrait la page impossible a ouvrir, donc
        impossible a corriger : mieux vaut refuser l'enregistrement."""
        enorme = {"sheets": [{"name": "X", "rows": 5, "cols": 5, "merges": [],
                              "cells": {f"A{i}": {"v": i} for i in range(1, 25000)}}]}
        response = self.client.post("/api/workbooks", {"title": "Trop gros", "content": enorme},
                                    content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_contenu_invalide_refuse(self):
        response = self.client.post("/api/workbooks", {"title": "Casse", "content": {"sheets": "pas une liste"}},
                                    content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_acces_refuse_sans_connexion(self):
        wid = self.creer().json()["id"]
        anonyme = Client()
        self.assertIn(anonyme.get("/api/workbooks").status_code, (401, 403))
        self.assertIn(anonyme.get(f"/api/workbooks/{wid}").status_code, (401, 403))

    def test_suppression(self):
        wid = self.creer().json()["id"]
        self.assertEqual(self.client.delete(f"/api/workbooks/{wid}").status_code, 200)
        self.assertEqual(Workbook.objects.count(), 0)


class ExportExcelTests(TestCase):
    """L'export doit produire un fichier qui s'ouvre dans Excel en restant
    VIVANT : les formules continuent de se recalculer, elles ne sont pas
    figées en valeurs."""

    def setUp(self):
        User.objects.create_user(email="gestionnaire@test.ci", password="motdepasse123",
                                 full_name="Gestionnaire", role="admin")
        self.client = Client()
        self.client.post("/api/auth/login",
                         {"email": "gestionnaire@test.ci", "password": "motdepasse123"},
                         content_type="application/json")

    def exporter(self, contenu, titre="Budget"):
        wid = self.client.post("/api/workbooks", {"title": titre, "content": contenu},
                               content_type="application/json").json()["id"]
        response = self.client.get(f"/api/workbooks/{wid}/export")
        self.assertEqual(response.status_code, 200)
        import io as _io
        from openpyxl import load_workbook
        return response, load_workbook(_io.BytesIO(b"".join(response.streaming_content)))

    def test_formules_traduites_en_anglais(self):
        """Un fichier .xlsx stocke toujours les formules en anglais avec des
        virgules : "SOMME(A1;A2)" serait refusé par Excel à l'ouverture."""
        contenu = {"sheets": [{"name": "F", "rows": 5, "cols": 5, "merges": [], "colWidths": {},
                               "cells": {"A1": {"v": "2"}, "A2": {"v": "3"},
                                         "A3": {"v": "=SOMME(A1:A2)"},
                                         "A4": {"v": '=SI(A3>4;"grand";"petit")'},
                                         "A5": {"v": "=MOYENNE(A1:A2)"}}}]}
        _, wb = self.exporter(contenu)
        f = wb["F"]
        self.assertEqual(f["A3"].value, "=SUM(A1:A2)")
        self.assertEqual(f["A4"].value, '=IF(A3>4,"grand","petit")')
        self.assertEqual(f["A5"].value, "=AVERAGE(A1:A2)")

    def test_les_nombres_arrivent_comme_nombres(self):
        """Un nombre exporté comme texte ne serait calculable dans Excel."""
        contenu = {"sheets": [{"name": "F", "rows": 3, "cols": 3, "merges": [], "colWidths": {},
                               "cells": {"A1": {"v": "1200"}, "A2": {"v": "3,5"},
                                         "A3": {"v": "Materiel"}}}]}
        _, wb = self.exporter(contenu)
        f = wb["F"]
        self.assertEqual(f["A1"].value, 1200)
        self.assertEqual(f["A2"].value, 3.5)
        self.assertEqual(f["A3"].value, "Materiel")

    def test_pourcentage_garde_la_meme_valeur(self):
        """L'application et Excel utilisent la même échelle : 0,15 s'affiche
        « 15 % » des deux côtés. Une conversion ici diviserait par 100."""
        contenu = {"sheets": [{"name": "F", "rows": 3, "cols": 3, "merges": [], "colWidths": {},
                               "cells": {"A1": {"v": "0,15", "s": {"nf": "pourcent"}}}}]}
        _, wb = self.exporter(contenu)
        self.assertEqual(wb["F"]["A1"].value, 0.15)
        self.assertEqual(wb["F"]["A1"].number_format, "0.0%")

    def test_mise_en_forme_fusions_et_largeurs(self):
        contenu = {"sheets": [{"name": "F", "rows": 5, "cols": 5,
                               "merges": [{"l": 0, "c": 0, "rs": 1, "cs": 3}],
                               "colWidths": {0: 150},
                               "cells": {"A1": {"v": "titre", "s": {
                                   "b": 1, "i": 1, "a": "center",
                                   "bg": "#4a86e8", "c": "#ffffff",
                                   "fs": 130, "tt": "upper"}}}}]}
        _, wb = self.exporter(contenu)
        f = wb["F"]
        # MAJUSCULES est un style d'affichage dans l'application ; Excel n'a
        # pas d'équivalent, on applique donc la transformation au texte.
        self.assertEqual(f["A1"].value, "TITRE")
        self.assertTrue(f["A1"].font.bold)
        self.assertTrue(f["A1"].font.italic)
        self.assertEqual(f["A1"].font.color.rgb, "FFFFFFFF")
        self.assertEqual(f["A1"].fill.start_color.rgb, "FF4A86E8")
        self.assertEqual(f["A1"].alignment.horizontal, "center")
        self.assertEqual(str(list(f.merged_cells.ranges)[0]), "A1:C1")
        self.assertGreater(f.column_dimensions["A"].width, 15)

    def test_nom_de_fichier_nettoye(self):
        """Un titre contenant / ou : produirait un nom de fichier invalide."""
        contenu = {"sheets": [{"name": "F", "rows": 2, "cols": 2, "merges": [],
                               "colWidths": {}, "cells": {}}]}
        response, _ = self.exporter(contenu, titre='Budget / 2026 : "final"')
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertNotIn("/", response["Content-Disposition"].split("filename=")[1])

    def test_nom_de_feuille_trop_long_ou_interdit(self):
        """Excel limite les onglets à 31 caractères et interdit certains
        signes : sans nettoyage, le fichier produit est illisible."""
        contenu = {"sheets": [{"name": "Suivi/budget: " + "x" * 40, "rows": 2, "cols": 2,
                               "merges": [], "colWidths": {}, "cells": {}}]}
        _, wb = self.exporter(contenu)
        nom = wb.sheetnames[0]
        self.assertLessEqual(len(nom), 31)
        self.assertNotIn("/", nom)

    def test_retour_a_la_ligne_et_alignement_vertical(self):
        """Ces deux réglages doivent suivre dans le fichier Excel, sinon le
        fichier téléchargé ne ressemble plus à ce qui était affiché."""
        contenu = {"sheets": [{"name": "F", "rows": 4, "cols": 3, "merges": [], "colWidths": {},
                               "cells": {
                                   "A1": {"v": "texte long", "s": {"w": 1, "va": "middle", "a": "center"}},
                                   "A2": {"v": "haut", "s": {"va": "top"}},
                                   "A3": {"v": "bas", "s": {"va": "bottom"}}}}]}
        _, wb = self.exporter(contenu)
        f = wb["F"]
        self.assertTrue(f["A1"].alignment.wrap_text)
        # Excel nomme "center" ce que CSS nomme "middle".
        self.assertEqual(f["A1"].alignment.vertical, "center")
        self.assertEqual(f["A1"].alignment.horizontal, "center")
        self.assertEqual(f["A2"].alignment.vertical, "top")
        self.assertEqual(f["A3"].alignment.vertical, "bottom")

    def test_hauteurs_de_ligne_exportees(self):
        contenu = {"sheets": [{"name": "F", "rows": 4, "cols": 3, "merges": [],
                               "colWidths": {0: 150}, "rowHeights": {0: 60},
                               "cells": {"A1": {"v": "titre"}}}]}
        _, wb = self.exporter(contenu)
        # Excel mesure les lignes en points, l'application en pixels.
        self.assertEqual(wb["F"].row_dimensions[1].height, 45.0)
