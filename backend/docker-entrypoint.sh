#!/bin/sh
# Démarre l'API en HTTPS si un certificat existe (voir generate_cert.py),
# sinon démarre en HTTP simple comme avant — pour ne jamais empêcher
# l'application de démarrer avant que quiconque n'ait eu la main pour générer
# un certificat (premier démarrage, ou clone du projet sur une autre machine).
#
# --reload (rechargement à chaud dès qu'un fichier backend change) est une
# fonctionnalité de développement, activée par défaut (voir .env.example)
# pour garder le confort documenté dans CLAUDE.md — à désactiver
# (UVICORN_RELOAD=false) une fois l'application installée durablement en
# production, où ce rechargement automatique n'a plus d'utilité.
RELOAD_FLAG=""
if [ "${UVICORN_RELOAD:-true}" != "false" ]; then
  RELOAD_FLAG="--reload"
fi

if [ -f /app/certs/cert.pem ] && [ -f /app/certs/key.pem ]; then
  echo "[démarrage] Certificat trouvé dans certs/ : lancement en HTTPS."
  exec uvicorn main:app --host 0.0.0.0 --port 8000 $RELOAD_FLAG \
    --ssl-certfile /app/certs/cert.pem --ssl-keyfile /app/certs/key.pem
else
  echo "[démarrage] Aucun certificat dans certs/ (voir generate_cert.py) : lancement en HTTP simple."
  exec uvicorn main:app --host 0.0.0.0 --port 8000 $RELOAD_FLAG
fi
