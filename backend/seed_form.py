"""
Script à exécuter UNE SEULE FOIS pour créer le formulaire "Consultation de
routine". Peut être relancé sans risque : il ne crée rien si un formulaire
du même nom existe déjà.

Utilisation (dans un terminal, pendant que l'application tourne déjà) :
    docker compose exec api python seed_form.py
"""

from database import SessionLocal, engine, Base
from models import Form

CONSULTATION_FORM_DEFINITION = {
    "sections": [
        {
            "id": "vitals",
            "title": "Signes vitaux",
            "fields": [
                {
                    "id": "poids", "type": "decimal", "label": "Poids (kg)",
                    "required": True, "validation": {"min": 0.5, "max": 300},
                },
                {
                    "id": "taille", "type": "decimal", "label": "Taille (cm)",
                    "required": True, "validation": {"min": 20, "max": 250},
                },
                {
                    "id": "imc", "type": "calculated", "label": "IMC",
                    "formula": "poids / pow(taille / 100, 2)",
                },
                {
                    "id": "temperature", "type": "decimal", "label": "Température (°C)",
                    "validation": {"min": 30, "max": 45},
                },
                {
                    "id": "tension_systolique", "type": "number",
                    "label": "Tension systolique (mmHg)",
                },
                {
                    "id": "tension_diastolique", "type": "number",
                    "label": "Tension diastolique (mmHg)",
                },
                {"id": "notes", "type": "text", "label": "Notes"},
            ],
        }
    ]
}


def main():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        existing = db.query(Form).filter(Form.name == "Consultation de routine").first()
        if existing:
            print("Le formulaire 'Consultation de routine' existe déjà. Rien à faire.")
            return

        form = Form(
            name="Consultation de routine",
            form_type="survey",
            definition=CONSULTATION_FORM_DEFINITION,
        )
        db.add(form)
        db.commit()
        print("Formulaire 'Consultation de routine' créé avec succès.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
