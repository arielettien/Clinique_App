"""
Script à exécuter UNE SEULE FOIS pour ajouter les colonnes du lien
communautaire ("public_link_enabled", "public_access_code_hash") à la table
"forms" — voir point 7 : un formulaire peut être ouvert sans compte via un
lien + un code d'accès partagé (community.html).

Utilisation :
    docker compose exec api python migrate_add_public_link.py
"""
from sqlalchemy import text
from database import engine, Base
import models  # noqa: F401


def main():
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE forms ADD COLUMN IF NOT EXISTS public_link_enabled BOOLEAN DEFAULT FALSE"
        ))
        conn.execute(text(
            "ALTER TABLE forms ADD COLUMN IF NOT EXISTS public_access_code_hash VARCHAR"
        ))
    print("Colonnes 'forms.public_link_enabled' et 'forms.public_access_code_hash' vérifiées/ajoutées.")


if __name__ == "__main__":
    main()
