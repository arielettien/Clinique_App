"""Service des pages du frontend.

Le frontend est inchange par rapport a la version precedente : des fichiers
HTML/CSS/JS autonomes qui dialoguent avec l'API en JSON. Django les sert tels
quels, sans passer par son moteur de gabarits — c'est ce qui permet au mode
hors-ligne (Service Worker + IndexedDB) de continuer a fonctionner a
l'identique.
"""
import mimetypes
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404

# Polices hebergees localement (frontend/fonts/, tableau de bord) : la table
# des types de Python ne connait pas .woff2 et les servirait en
# application/octet-stream.
mimetypes.add_type("font/woff2", ".woff2")


def serve_frontend(request, path=""):
    base = Path(settings.FRONTEND_DIR).resolve()
    candidate = (base / path).resolve() if path else (base / "index.html")

    # Garde-fou contre la traversee de chemin ("../../etc/passwd") : on refuse
    # tout chemin qui sortirait du dossier du frontend.
    if not str(candidate).startswith(str(base)):
        raise Http404("Fichier introuvable.")

    if candidate.is_dir():
        candidate = candidate / "index.html"
    if not candidate.is_file():
        raise Http404("Fichier introuvable.")

    content_type, _ = mimetypes.guess_type(str(candidate))
    response = FileResponse(open(candidate, "rb"), content_type=content_type or "application/octet-stream")
    # Le Service Worker doit pouvoir etre reverifie a chaque visite, sinon une
    # mise a jour de l'application peut rester invisible pendant des heures
    # sur les appareils deja utilises.
    if candidate.name == "sw.js":
        response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response
