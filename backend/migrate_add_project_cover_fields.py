"""
Script à exécuter UNE SEULE FOIS pour ajouter les nouveaux champs de la fiche
projet (page d'accueil) : date de création éditable, ONG de développement,
bailleur principal, et image de couverture.

Utilisation :
    docker compose exec api python migrate_add_project_cover_fields.py
"""
from sqlalchemy import text

from database import engine, Base
import models  # noqa: F401


def main():
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE projects ADD COLUMN IF NOT EXISTS start_date DATE"))
        conn.execute(text("ALTER TABLE projects ADD COLUMN IF NOT EXISTS ngo_name VARCHAR(255)"))
        conn.execute(text("ALTER TABLE projects ADD COLUMN IF NOT EXISTS main_donor VARCHAR(255)"))
        conn.execute(text("ALTER TABLE projects ADD COLUMN IF NOT EXISTS cover_image_id VARCHAR"))
    print("Colonnes 'projects.start_date/ngo_name/main_donor/cover_image_id' vérifiées/ajoutées.")


if __name__ == "__main__":
    main()
