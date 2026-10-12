"""
Script à exécuter UNE SEULE FOIS pour empêcher deux cas du même projet
d'avoir le même numéro de dossier (corrige la collision sous accès
simultané — deux créations en même temps pouvaient obtenir le même numéro).

Utilisation :
    docker compose exec api python migrate_add_case_number_unique.py

Ce script vérifie D'ABORD qu'aucun doublon n'existe déjà (une collision qui
s'est déjà produite avant ce correctif) — si c'est le cas, il les affiche et
s'arrête SANS rien modifier, pour que tu puisses décider quoi faire de ces
dossiers avant d'ajouter la contrainte (sinon la contrainte échouerait à la
création de toute façon).
"""
from sqlalchemy import text
from database import engine, Base, SessionLocal
from models import Case
import models  # noqa: F401


def main():
    db = SessionLocal()
    try:
        print("Vérification des doublons existants (project_id, case_number)...")
        rows = db.execute(text("""
            SELECT project_id, case_number, COUNT(*) as n
            FROM cases
            GROUP BY project_id, case_number
            HAVING COUNT(*) > 1
        """)).fetchall()

        if rows:
            print(f"\n⚠️  {len(rows)} numéro(s) de dossier en doublon trouvé(s) — la contrainte ne sera PAS ajoutée :\n")
            for r in rows:
                print(f"  - projet {r[0]} : numéro '{r[1]}' utilisé par {r[2]} dossiers différents")
            print(
                "\nRenomme ou fusionne ces dossiers en double avant de relancer ce script "
                "(par exemple en changeant le numéro du plus récent via 'Modifier ce dossier' "
                "dans l'interface). Rien n'a été modifié."
            )
            return

        print("Aucun doublon — ajout de la contrainte d'unicité...")
    finally:
        db.close()

    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        # Idempotent : ne re-tente pas si la contrainte existe déjà.
        existing = conn.execute(text("""
            SELECT 1 FROM pg_constraint WHERE conname = 'uq_case_project_number'
        """)).fetchone()
        if existing:
            print("La contrainte existe déjà — rien à faire.")
            return
        conn.execute(text(
            "ALTER TABLE cases ADD CONSTRAINT uq_case_project_number UNIQUE (project_id, case_number)"
        ))
    print("Contrainte 'uq_case_project_number' ajoutée avec succès.")


if __name__ == "__main__":
    main()
