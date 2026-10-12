#!/bin/sh
# Déchiffre UNE sauvegarde précise, pour une restauration (voir usage plus
# bas). N'écrase jamais le fichier .gpg d'origine.
#
# Utilisation (depuis la racine du projet, avec le conteneur backup-encrypt
# en cours d'exécution) :
#   docker compose exec backup-encrypt /decrypt-backup.sh /backups/daily/clinic_db-2026-07-30.sql.gz.gpg
set -eu

if [ -z "${1:-}" ]; then
  echo "Usage : decrypt-backup.sh /backups/.../nom_du_fichier.sql.gz.gpg"
  exit 1
fi
if [ -z "${BACKUP_ENCRYPTION_PASSPHRASE:-}" ]; then
  echo "BACKUP_ENCRYPTION_PASSPHRASE non définie — impossible de déchiffrer."
  exit 1
fi

OUT="${1%.gpg}"
gpg --batch --yes --passphrase "$BACKUP_ENCRYPTION_PASSPHRASE" --decrypt -o "$OUT" "$1"
echo "Fichier déchiffré : $OUT"
echo "Pour restaurer dans Postgres : gunzip -c \"$OUT\" | docker compose exec -T db psql -U clinic_user -d clinic_db"
