#!/bin/sh
# Chiffre chaque nouvelle sauvegarde .sql.gz produite par le service "backup"
# (mot de passe symétrique, AES256), puis supprime la version en clair — les
# sauvegardes contiennent l'intégralité des données patients, elles ne
# doivent jamais rester lisibles telles quelles sur le disque du serveur.
set -eu

if [ -z "${BACKUP_ENCRYPTION_PASSPHRASE:-}" ]; then
  echo "[backup-encrypt] BACKUP_ENCRYPTION_PASSPHRASE non définie — arrêt (voir .env.example)."
  exit 1
fi

echo "[backup-encrypt] En veille — vérifie /backups toutes les 10 minutes."
while true; do
  # Exclut explicitement "*-latest.sql.gz" : ce sont des liens symboliques
  # maintenus par l'outil de sauvegarde (pas du contenu à chiffrer en soi) —
  # "find -type f" les traite comme des fichiers normaux sur cette image
  # (BusyBox déréférence le lien), donc sans cette exclusion ils étaient
  # chiffrés une seconde fois (et le lien remplacé par une vraie copie).
  find /backups -type f -name '*.sql.gz' ! -name '*-latest.sql.gz' | while read -r f; do
    if [ ! -f "${f}.gpg" ]; then
      echo "[backup-encrypt] Chiffrement de $f"
      if gpg --batch --yes --passphrase "$BACKUP_ENCRYPTION_PASSPHRASE" \
             --symmetric --cipher-algo AES256 -o "${f}.gpg" "$f"; then
        dir=$(dirname "$f")
        base=$(basename "$f")
        # L'outil de sauvegarde maintient un lien symbolique "...-latest.sql.gz"
        # par dossier, pointant vers le fichier le plus récent — sans ce
        # rattrapage, supprimer le fichier en clair ci-dessous laisserait ce
        # lien cassé jusqu'au prochain cycle de sauvegarde.
        for link in "$dir"/*-latest.sql.gz; do
          [ -L "$link" ] || continue
          [ "$(readlink "$link")" = "$base" ] && ln -sf "${base}.gpg" "$link"
        done
        rm -f "$f"
      else
        echo "[backup-encrypt] ÉCHEC du chiffrement de $f — fichier en clair conservé, à vérifier."
      fi
    fi
  done
  sleep 600
done
