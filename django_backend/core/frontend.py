"""Service des pages du frontend.

Le frontend est inchange par rapport a la version precedente : des fichiers
HTML/CSS/JS autonomes qui dialoguent avec l'API en JSON. Django les sert tels
quels, sans passer par son moteur de gabarits — c'est ce qui permet au mode
hors-ligne (Service Worker + IndexedDB) de continuer a fonctionner a
l'identique.

Ce module porte aussi le rechargement automatique du frontend en
developpement (voir FRONTEND_LIVE_RELOAD dans clinique/settings.py). Tout ce
qui s'y rapporte est regroupe en bas du fichier et n'a AUCUN effet quand le
reglage est desactive : en production, serve_frontend se comporte exactement
comme avant.
"""
import mimetypes
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect

# Polices hebergees localement (frontend/fonts/, tableau de bord) : la table
# des types de Python ne connait pas .woff2 et les servirait en
# application/octet-stream.
mimetypes.add_type("font/woff2", ".woff2")

SANS_CACHE = "no-cache, no-store, must-revalidate"

# Pages volontairement accessibles sans compte : l'ecran de connexion
# lui-meme, et le lien communautaire (formulaire rempli par un agent de
# terrain sans compte, protege par un code d'acces partage — voir l'app
# community).
PAGES_PUBLIQUES = {"login.html", "community.html"}


def _exige_une_session(candidate: Path) -> bool:
    """Cette page doit-elle etre refusee a un visiteur non connecte ?

    Seules les PAGES (.html) sont concernees. Les ressources (feuilles de
    style, scripts, polices, images, sw.js) restent toujours accessibles : la
    page de connexion elle-meme en depend, les rediriger la rendrait illisible.
    """
    if candidate.suffix.lower() != ".html":
        return False
    return candidate.name not in PAGES_PUBLIQUES


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

    # Controle de session AVANT de servir quoi que ce soit. Jusqu'ici, "/"
    # renvoyait index.html a tout le monde et c'est app_shell.js qui, apres
    # avoir affiche la page puis interroge /api/auth/me, renvoyait vers la
    # connexion : un visiteur non connecte voyait donc l'accueil clignoter
    # pendant une seconde avant d'etre redirige. On tranche ici, avant le
    # moindre octet de HTML.
    if _exige_une_session(candidate) and not request.user.is_authenticated:
        demandee = request.get_full_path()
        cible = "/login.html"
        # On memorise la page demandee pour y revenir une fois connecte : sans
        # cela, un favori pointant sur un dossier precis ramenait toujours a
        # l'accueil.
        if demandee and demandee not in ("/", "/index.html"):
            cible += "?suivant=" + quote(demandee, safe="")
        reponse = redirect(cible)
        # Jamais mise en cache : une redirection memorisee continuerait
        # d'envoyer vers la connexion meme apres ouverture de la session.
        reponse["Cache-Control"] = SANS_CACHE
        return reponse

    live = getattr(settings, "FRONTEND_LIVE_RELOAD", False)

    # En developpement, sw.js est remplace par une version neutralisee : sans
    # cela, le Service Worker deja installe sur le navigateur continuerait de
    # servir l'ancienne page depuis son cache, et aucune modification ne
    # serait visible — quel que soit le rechargement cote serveur.
    if live and candidate.name == "sw.js":
        response = HttpResponse(_SW_NEUTRALISE, content_type="text/javascript")
        response["Cache-Control"] = SANS_CACHE
        return response

    content_type, _ = mimetypes.guess_type(str(candidate))
    content_type = content_type or "application/octet-stream"

    # En developpement, on injecte le script de rechargement dans les pages
    # HTML. On lit alors le fichier en memoire au lieu de le diffuser : les
    # pages de cette application font quelques dizaines de kilo-octets, c'est
    # sans consequence, et uniquement en developpement.
    if live and content_type == "text/html":
        contenu = candidate.read_bytes()
        balise = b"</body>"
        if balise in contenu:
            contenu = contenu.replace(balise, _SCRIPT_INJECTE + balise, 1)
        else:
            contenu += _SCRIPT_INJECTE
        response = HttpResponse(contenu, content_type=content_type)
    else:
        response = FileResponse(open(candidate, "rb"), content_type=content_type)

    # Le Service Worker doit pouvoir etre reverifie a chaque visite, sinon une
    # mise a jour de l'application peut rester invisible pendant des heures
    # sur les appareils deja utilises.
    if candidate.name == "sw.js" or live:
        response["Cache-Control"] = SANS_CACHE
    return response


# ---------------------------------------------------------------------------
# Rechargement automatique — developpement uniquement
# ---------------------------------------------------------------------------

_SCRIPT_INJECTE = b'<script src="/__dev/live-reload.js" defer></script>\n'

# Service Worker de remplacement : il ne met rien en cache, vide les caches
# laisses par les versions precedentes, se desinstalle, puis force le
# rechargement des onglets ouverts pour qu'ils cessent immediatement d'etre
# servis depuis l'ancien cache.
_SW_NEUTRALISE = """// Service Worker NEUTRALISE (mode developpement, FRONTEND_LIVE_RELOAD).
// Le vrai sw.js est dans frontend/sw.js ; il revient des que le reglage est
// desactive (FRONTEND_LIVE_RELOAD=false), ce qu'il faut faire pour tester le
// mode hors-ligne.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const noms = await caches.keys();
    await Promise.all(noms.map((n) => caches.delete(n)));
    await self.registration.unregister();
    const onglets = await self.clients.matchAll({ type: 'window' });
    onglets.forEach((c) => c.navigate(c.url));
  })());
});
// Aucun gestionnaire 'fetch' : toutes les requetes vont directement au reseau.
"""

# Interrogation courte et repetee plutot qu'une connexion maintenue ouverte :
# gunicorn tourne ici avec UN seul worker et 4 fils d'execution (voir
# docker-entrypoint.sh). Un flux SSE garde un fil mobilise par onglet ouvert ;
# a trois onglets de developpement, le serveur n'aurait presque plus de quoi
# repondre aux vraies requetes.
_SCRIPT_LIVE_RELOAD = """// Rechargement automatique — developpement uniquement.
(function () {
  'use strict';
  var connue = null;
  var echecs = 0;

  async function verifier() {
    try {
      const r = await fetch('/__dev/version', { cache: 'no-store' });
      if (!r.ok) return;
      const v = (await r.json()).version;
      echecs = 0;
      if (connue === null) { connue = v; return; }
      if (v !== connue) { location.reload(); }
    } catch (e) {
      // Serveur en train de redemarrer (gunicorn --reload) : on ne fait rien.
      // Au-dela de quelques echecs d'affilee on espace, pour ne pas inonder
      // la console d'erreurs quand le serveur est simplement arrete.
      echecs += 1;
    }
  }

  function boucle() {
    verifier().finally(() => setTimeout(boucle, echecs > 5 ? 5000 : 1000));
  }

  console.info('[dev] Rechargement automatique actif. '
    + 'Le Service Worker est neutralise : le mode hors-ligne est indisponible '
    + 'tant que FRONTEND_LIVE_RELOAD vaut true.');
  boucle();
})();
"""


def _version_frontend() -> str:
    """Empreinte des sources du frontend : date de modification la plus recente
    et nombre de fichiers. Le nombre permet de detecter aussi un ajout ou une
    suppression, qui ne changent pas forcement la date la plus recente."""
    base = Path(settings.FRONTEND_DIR)
    recent = 0.0
    nombre = 0
    for chemin in base.rglob("*"):
        if chemin.suffix.lower() in (".html", ".css", ".js") and chemin.is_file():
            recent = max(recent, chemin.stat().st_mtime)
            nombre += 1
    return f"{recent:.3f}-{nombre}"


def dev_version(request):
    if not getattr(settings, "FRONTEND_LIVE_RELOAD", False):
        raise Http404("Rechargement automatique desactive.")
    response = JsonResponse({"version": _version_frontend()})
    response["Cache-Control"] = SANS_CACHE
    return response


def dev_live_reload_script(request):
    if not getattr(settings, "FRONTEND_LIVE_RELOAD", False):
        raise Http404("Rechargement automatique desactive.")
    response = HttpResponse(_SCRIPT_LIVE_RELOAD, content_type="text/javascript")
    response["Cache-Control"] = SANS_CACHE
    return response
