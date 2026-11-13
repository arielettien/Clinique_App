"""
Configuration Django de Clinique App.

Reprend volontairement les memes variables d'environnement que la version
precedente (SECRET_KEY, DATABASE_URL, COOKIE_SECURE) pour que le fichier .env
existant continue de fonctionner sans modification.
"""
import os
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent.parent

# --- Securite -------------------------------------------------------------
# Meme garde-fou que la version precedente : l'application refuse de demarrer
# avec la cle d'exemple du depot, sinon n'importe qui la connaissant pourrait
# forger une session valide pour n'importe quel compte.
SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY or SECRET_KEY == "dev_secret_key_a_changer":
    raise RuntimeError(
        "SECRET_KEY absente ou laissee a la valeur par defaut du depot. "
        "Genere une cle aleatoire et fournis-la via la variable d'environnement "
        "SECRET_KEY (fichier .env, jamais versionne) avant de demarrer le serveur."
    )

DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() == "true"

# Reseau local uniquement : on accepte tout hote, le pare-feu du serveur est
# la vraie barriere (voir DEPLOIEMENT.md, etape 5).
ALLOWED_HOSTS = ["*"]
# Django 4+ exige l'origine exacte pour les requetes POST. Le frontend est
# servi par ce meme serveur, sur une IP locale variable d'un site a l'autre :
# on fait confiance a l'origine de la requete elle-meme.
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()
]

# --- Applications ---------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "rest_framework",
    "core",
    "accounts",
    "projects",
    "patients",
    "clinical_forms",
    "submissions",
    "cases",
    "attachments",
    "community",
    "spreadsheets",
    "workbooks",
    "indicators",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "clinique.urls"
WSGI_APPLICATION = "clinique.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
            ],
        },
    },
]

# --- Base de donnees ------------------------------------------------------
# On lit DATABASE_URL (meme format que la version precedente) plutot que des
# variables separees, pour que docker-compose.yml reste inchange.
_db_url = os.environ.get("DATABASE_URL", "")
if _db_url:
    _parsed = urlparse(_db_url)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": _parsed.path.lstrip("/"),
            "USER": _parsed.username,
            "PASSWORD": _parsed.password,
            "HOST": _parsed.hostname,
            "PORT": _parsed.port or 5432,
        }
    }
else:
    raise RuntimeError("DATABASE_URL absente — voir .env.example.")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Authentification -----------------------------------------------------
AUTH_USER_MODEL = "accounts.User"

AUTHENTICATION_BACKENDS = ["accounts.backends.EmailBackend"]

# Les mots de passe deja en base ont ete crees en bcrypt. BCryptPasswordHasher
# est place en premier pour que Django les reconnaisse sans qu'aucun
# utilisateur n'ait a reinitialiser le sien (voir la migration de reprise dans
# accounts/migrations/). Les nouveaux mots de passe utilisent le meme
# algorithme, pour rester coherent.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.BCryptPasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 8}},
]

# Cookie de session : secure=True uniquement derriere HTTPS (meme logique que
# la version precedente — en HTTP simple le navigateur refuserait le cookie et
# la connexion casserait silencieusement).
SESSION_COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").lower() == "true"
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 12 * 3600  # 12 h, comme la version precedente
SESSION_ENGINE = "django.contrib.sessions.backends.db"

# --- API ------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "UNAUTHENTICATED_USER": None,
    # "format" est un parametre METIER de cette application (csv / xlsx pour
    # les exports). Par defaut, l'API l'interprete comme un choix de
    # representation technique et renvoie "page introuvable" des qu'il vaut
    # autre chose que json : on desactive donc ce comportement, sans quoi
    # aucun export ne fonctionnerait.
    "URL_FORMAT_OVERRIDE": None,
}

# --- Fichiers -------------------------------------------------------------
# Le frontend (HTML/CSS/JS) est servi tel quel : il est inchange par rapport a
# la version precedente, il dialogue avec l'API en JSON.
STATIC_URL = "/static/"
FRONTEND_DIR = BASE_DIR / "frontend"

# --- Rechargement automatique du frontend (DEVELOPPEMENT UNIQUEMENT) -------
# gunicorn --reload ne surveille que les fichiers PYTHON : une modification de
# HTML/CSS/JS restait invisible, non pas a cause du serveur mais du Service
# Worker, qui sert l'app shell depuis son cache en priorite (cache-first).
# Quand ce reglage est actif, core/frontend.py :
#   1. sert le frontend avec Cache-Control: no-store,
#   2. injecte un petit script qui interroge /__dev/version et recharge la page
#      des qu'un fichier change,
#   3. remplace sw.js par une version neutralisee qui se desinstalle et vide
#      ses caches.
# Le point 3 DESACTIVE donc le mode hors-ligne : c'est voulu en developpement,
# ce serait un bug grave en production. Le reglage suit APP_RELOAD, qui vaut
# deja "false" sur le serveur de l'ONG (voir DEPLOIEMENT.md) ; on peut aussi le
# forcer independamment avec FRONTEND_LIVE_RELOAD, notamment pour remettre le
# vrai Service Worker le temps de tester le mode hors-ligne.
# docker-compose transmet une chaine VIDE quand la variable n'est pas
# definie dans .env : il faut la traiter comme "absente", sinon le reglage
# basculerait a false pour tout le monde sans que personne ne l'ait demande.
_live_reload = os.environ.get("FRONTEND_LIVE_RELOAD", "").strip()
if not _live_reload:
    _live_reload = os.environ.get("APP_RELOAD", "true")
FRONTEND_LIVE_RELOAD = _live_reload.lower() == "true"

# Fichiers envoyes depuis les formulaires (photos, signatures, documents).
UPLOADS_DIR = Path(os.environ.get("UPLOADS_DIR", BASE_DIR / "uploads"))
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD_SIZE_BYTES = 25 * 1024 * 1024

# --- Divers ---------------------------------------------------------------
LANGUAGE_CODE = "fr-fr"
TIME_ZONE = os.environ.get("TZ", "UTC")
USE_I18N = True
USE_TZ = False  # memes horodatages naifs que la version precedente
