"""
Script à exécuter UNE SEULE FOIS pour introduire les "Projets".

Ce script :
1. Crée la nouvelle table "projects"
2. Ajoute une colonne "project_id" aux tables "patients" et "forms"
   (si elle n'existe pas déjà — sans risque à relancer plusieurs fois)
3. Crée un projet "Général" par défaut
4. Range tous les patients et formulaires déjà existants (créés avant les
   projets) dans ce projet "Général", pour ne rien perdre

Utilisation :
    docker compose exec api python migrate_add_projects.py

Note technique (pour plus tard) : on utilise ici une petite migration
manuelle plutôt qu'un vrai outil de migration (Alembic), pour rester simple
tant que le schéma ne change pas trop souvent. Si on continue à faire
évoluer le schéma fréquemment, on basculera vers Alembic comme prévu dans
les spécifications techniques.
"""

from sqlalchemy import text

from database import SessionLocal, engine, Base
from models import Project, Patient, Form


def main():
    # 1. Crée la table "projects" si elle n'existe pas encore.
    Base.metadata.create_all(bind=engine)

    # 2. Ajoute les colonnes manquantes sur les tables déjà existantes.
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE patients ADD COLUMN IF NOT EXISTS project_id VARCHAR"))
        conn.execute(text("ALTER TABLE forms ADD COLUMN IF NOT EXISTS project_id VARCHAR"))
    print("Colonnes 'project_id' vérifiées/ajoutées sur patients et forms.")

    db = SessionLocal()
    try:
        # 3. Crée un projet par défaut s'il n'en existe aucun.
        default_project = db.query(Project).filter(Project.name == "Général").first()
        if not default_project:
            default_project = Project(
                name="Général",
                description=(
                    "Projet créé automatiquement pour regrouper les patients et "
                    "formulaires existants avant l'introduction des projets."
                ),
            )
            db.add(default_project)
            db.commit()
            db.refresh(default_project)
            print(f"Projet par défaut 'Général' créé (id={default_project.id}).")
        else:
            print("Le projet 'Général' existe déjà.")

        # 4. Range dans ce projet tout ce qui n'a pas encore de projet.
        orphan_patients = db.query(Patient).filter(Patient.project_id.is_(None)).all()
        for p in orphan_patients:
            p.project_id = default_project.id
        orphan_forms = db.query(Form).filter(Form.project_id.is_(None)).all()
        for f in orphan_forms:
            f.project_id = default_project.id
        db.commit()

        print(f"{len(orphan_patients)} patient(s) et {len(orphan_forms)} formulaire(s) "
              f"rangé(s) dans le projet 'Général'.")
        print("\nMigration terminée avec succès.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
