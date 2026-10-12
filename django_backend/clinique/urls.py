"""Table des URL de l'application.

Les routes /api/... et /health doivent rester AVANT la route attrape-tout du
frontend, sinon cette derniere les masquerait (meme contrainte que dans la
version precedente).
"""
from django.urls import include, path, re_path

from core import views as core_views
from core.frontend import serve_frontend

urlpatterns = [
    path("api/", include("accounts.urls")),
    path("api/", include("projects.urls")),
    path("api/", include("patients.urls")),
    path("api/", include("clinical_forms.urls")),
    path("api/", include("submissions.urls")),
    path("api/", include("cases.urls")),
    path("api/", include("attachments.urls")),
    path("api/", include("community.urls")),
    path("api/", include("core.urls")),

    # A la racine, et non sous /api/ : c'est l'adresse que le stockage
    # hors-ligne interroge pour savoir si le serveur repond (voir
    # frontend/offline_db.js). La deplacer casserait la detection du retour
    # de connexion.
    path("health", core_views.health),

    # Frontend : toutes les autres adresses servent un fichier du dossier
    # frontend/ (index.html par defaut).
    re_path(r"^(?P<path>.*)$", serve_frontend, name="frontend"),
]
