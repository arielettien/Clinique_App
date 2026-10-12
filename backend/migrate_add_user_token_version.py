"""
Script à exécuter UNE SEULE FOIS pour ajouter la colonne "token_version" à
la table "users" — nécessaire pour que se déconnecter (ou se faire
réinitialiser son mot de passe par un admin) invalide vraiment la session en
cours côté serveur, pas seulement le cookie dans le navigateur. Avant cette
colonne, un jeton de session volé/copié restait valable jusqu'à 12h après une
déconnexion.

Utilisation :
    docker compose exec api python migrate_add_user_token_version.py
"""
from sqlalchemy import text

from database import engine, Base
import models  # noqa: F401


def main():
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0"))
    print("Colonne 'users.token_version' vérifiée/ajoutée.")
    print("\nMigration terminée avec succès.")


if __name__ == "__main__":
    main()
