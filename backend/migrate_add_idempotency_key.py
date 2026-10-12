"""
Script à exécuter UNE SEULE FOIS pour ajouter la colonne "idempotency_key" à
la table "submissions" — protège contre un doublon créé par une requête
envoyée deux fois (double-clic très rapide, requête rejouée par le réseau),
même quand ça ne passe pas par le bouton habituel de l'interface.

Utilisation :
    docker compose exec api python migrate_add_idempotency_key.py
"""
from sqlalchemy import text
from database import engine, Base
import models  # noqa: F401


def main():
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE submissions ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(64)"))
        # Unique seulement sur les valeurs non NULL (comportement standard
        # Postgres : plusieurs NULL sont autorisés même sous contrainte unique).
        existing = conn.execute(text(
            "SELECT 1 FROM pg_constraint WHERE conname = 'uq_submission_idempotency_key'"
        )).fetchone()
        if not existing:
            conn.execute(text(
                "ALTER TABLE submissions ADD CONSTRAINT uq_submission_idempotency_key UNIQUE (idempotency_key)"
            ))
    print("Colonne 'submissions.idempotency_key' vérifiée/ajoutée, avec contrainte d'unicité.")


if __name__ == "__main__":
    main()
