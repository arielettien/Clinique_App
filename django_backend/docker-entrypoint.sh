#!/bin/sh
# Demarre l'application en HTTPS si un certificat existe (voir la commande
# generate_cert), sinon en HTTP simple — pour ne jamais empecher l'application
# de demarrer avant que quiconque ait eu la main pour generer un certificat
# (premier demarrage, ou installation sur une nouvelle machine).
set -e

# --fake-initial : si les tables existent deja (base reprise de la version
# precedente), Django enregistre la migration initiale comme deja passee au
# lieu d'essayer de recreer des tables existantes. Sur une base vierge, les
# migrations s'appliquent normalement. Les deux cas fonctionnent donc sans
# aucune intervention.
echo "[demarrage] Application des migrations de base de donnees..."
python manage.py migrate --fake-initial --noinput

# Un seul worker, avec plusieurs fils d'execution : les compteurs de
# tentatives (connexion, code du lien communautaire) vivent dans la memoire du
# processus — plusieurs workers auraient chacun leur propre compteur, ce qui
# multiplierait d'autant le nombre d'essais autorises avant blocage. Largement
# suffisant pour l'usage vise : quelques utilisateurs sur un reseau local.
GUNICORN_OPTS="--bind 0.0.0.0:8000 --workers 1 --threads 4 --timeout 120 --access-logfile - --error-logfile -"

# Rechargement automatique a chaque modification de fichier : pratique pendant
# le developpement, inutile sur un serveur installe durablement. Mettre
# APP_RELOAD=false dans le fichier .env pour le desactiver en production.
if [ "${APP_RELOAD:-true}" != "false" ]; then
  GUNICORN_OPTS="$GUNICORN_OPTS --reload"
fi

if [ -f /app/certs/cert.pem ] && [ -f /app/certs/key.pem ]; then
  echo "[demarrage] Certificat trouve dans certs/ : lancement en HTTPS."
  exec gunicorn clinique.wsgi:application $GUNICORN_OPTS \
    --certfile /app/certs/cert.pem --keyfile /app/certs/key.pem
else
  echo "[demarrage] Aucun certificat dans certs/ (voir generate_cert) : lancement en HTTP simple."
  exec gunicorn clinique.wsgi:application $GUNICORN_OPTS
fi
