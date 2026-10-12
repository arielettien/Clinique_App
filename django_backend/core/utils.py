"""Utilitaires partages par toutes les applications."""
import uuid


def new_uuid() -> str:
    """Identifiant primaire. Les tables existantes stockent des UUID sous
    forme de texte (et non le type natif uuid de PostgreSQL) — on conserve ce
    choix pour que les donnees deja en base restent lisibles telles quelles."""
    return str(uuid.uuid4())


def age_at(dob, reference_date):
    """Age en annees revolues a une date donnee.

    On calcule l'age A LA DATE DE L'EVENEMENT (et non aujourd'hui) : pour une
    analyse statistique, l'age qui compte est celui qu'avait la personne au
    moment du depistage, pas celui qu'elle a au moment ou l'on exporte.
    """
    if not dob:
        return None
    return (reference_date.year - dob.year
            - ((reference_date.month, reference_date.day) < (dob.month, dob.day)))
