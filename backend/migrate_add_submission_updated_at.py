"""
Script à exécuter UNE SEULE FOIS pour ajouter la colonne "updated_at" à la
table "submissions" (nécessaire à la détection des modifications
simultanées — voir la conversation du 19/07/2026).

Utilisation :
    docker compose exec api python migrate_add_submission_updated_at.py

Même raison que pour migrate_add_cases.py : Base.metadata.create_all() ne
modifie jamais une table déjà existante, seulement les tables absentes.
"""
from sqlalchemy import text
from database import engine, Base
import models  # noqa: F401


def main():
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE submissions ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP"))
        conn.execute(text("UPDATE submissions SET updated_at = created_at WHERE updated_at IS NULL"))
    print("Colonne 'submissions.updated_at' vérifiée/ajoutée et initialisée.")


if __name__ == "__main__":
    main()
