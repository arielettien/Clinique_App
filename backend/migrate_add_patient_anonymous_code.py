"""
Script à exécuter UNE SEULE FOIS pour ajouter le code anonyme des patients
("PAT-2026-0042") — attribué à TOUT patient (admis en Suivi ou pas), pour
pouvoir les identifier dans un document destiné à l'extérieur (bailleur) sans
jamais y faire apparaître leur nom. Distinct du numéro de dossier de suivi.

Ce script :
1. Ajoute la colonne "anonymous_code" à la table "patients" (si absente)
2. Ajoute la contrainte d'unicité (project_id, anonymous_code)
3. Attribue rétroactivement un code à chaque patient qui n'en a pas encore,
   projet par projet, dans l'ordre de création (le plus ancien patient d'un
   projet reçoit 0001, etc.)

Utilisation :
    docker compose exec api python migrate_add_patient_anonymous_code.py
"""
from sqlalchemy import text

from database import engine, Base, SessionLocal
from models import Patient
import models  # noqa: F401


def main():
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE patients ADD COLUMN IF NOT EXISTS anonymous_code VARCHAR(50)"))
    print("Colonne 'patients.anonymous_code' vérifiée/ajoutée.")

    db = SessionLocal()
    try:
        project_ids = [r[0] for r in db.execute(text("SELECT DISTINCT project_id FROM patients WHERE project_id IS NOT NULL")).all()]
        total_assigned = 0
        for project_id in project_ids:
            patients = (
                db.query(Patient)
                .filter(Patient.project_id == project_id, Patient.anonymous_code.is_(None))
                .order_by(Patient.created_at.asc())
                .all()
            )
            if not patients:
                continue
            # Repart du numéro déjà le plus haut utilisé cette année-là dans ce
            # projet (au cas où ce script serait relancé après une première
            # attribution partielle), plutôt que d'un simple comptage.
            for p in patients:
                year = p.created_at.year if p.created_at else 2026
                prefix = f"PAT-{year}-"
                existing = db.execute(text(
                    "SELECT anonymous_code FROM patients WHERE project_id = :pid AND anonymous_code LIKE :prefix"
                ), {"pid": project_id, "prefix": f"{prefix}%"}).all()
                max_seq = 0
                for (code,) in existing:
                    suffix = code[len(prefix):]
                    if suffix.isdigit():
                        max_seq = max(max_seq, int(suffix))
                p.anonymous_code = f"{prefix}{max_seq + 1:04d}"
                db.commit()
                total_assigned += 1
        print(f"{total_assigned} patient(s) ont reçu un code anonyme.")
    finally:
        db.close()

    with engine.begin() as conn:
        existing = conn.execute(text(
            "SELECT 1 FROM pg_constraint WHERE conname = 'uq_patient_project_anon_code'"
        )).fetchone()
        if not existing:
            conn.execute(text(
                "ALTER TABLE patients ADD CONSTRAINT uq_patient_project_anon_code UNIQUE (project_id, anonymous_code)"
            ))
            print("Contrainte 'uq_patient_project_anon_code' ajoutée.")
        else:
            print("La contrainte d'unicité existe déjà.")

    print("\nMigration terminée avec succès.")


if __name__ == "__main__":
    main()
