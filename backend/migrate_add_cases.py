"""
Script à exécuter UNE SEULE FOIS pour finaliser l'introduction du Module Suivi.

Ce script :
1. Crée la table "cases" si elle n'existe pas déjà
2. Ajoute la colonne "case_id" à la table "submissions" (si elle n'existe
   pas déjà — sans risque à relancer plusieurs fois)

Utilisation :
    docker compose exec api python migrate_add_cases.py

Pourquoi ce script est nécessaire : Base.metadata.create_all() (lancé
automatiquement au démarrage du serveur) crée les tables ABSENTES, mais ne
modifie jamais une table déjà existante. Comme "submissions" existait déjà
avant l'ajout du Module Suivi, la nouvelle colonne "case_id" n'a jamais été
ajoutée toute seule — d'où les erreurs "Erreur de chargement" et
"Impossible de créer le dossier de suivi" sur le tableau de bord.
"""

from sqlalchemy import text

from database import engine, Base
import models  # noqa: F401 (nécessaire pour enregistrer tous les modèles avant create_all)


def main():
    # 1. Crée la table "cases" si elle n'existe pas encore.
    Base.metadata.create_all(bind=engine)
    print("Table 'cases' vérifiée/créée.")

    # 2. Ajoute la colonne manquante sur la table déjà existante.
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE submissions ADD COLUMN IF NOT EXISTS case_id VARCHAR"))
    print("Colonne 'submissions.case_id' vérifiée/ajoutée.")

    print("\nMigration terminée avec succès.")


if __name__ == "__main__":
    main()
