"""
Script à exécuter UNE SEULE FOIS pour créer le premier compte administrateur
de la clinique.

Utilisation (le conteneur "api" doit déjà être en train de tourner,
dans un AUTRE terminal que celui où "docker compose up" tourne) :

    docker compose exec api python create_admin.py

Ensuite, réponds aux questions posées (email, nom, mot de passe).
"""

import getpass

from database import SessionLocal, engine, Base
from models import User
from security import hash_password


def main():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        print("=== Création du compte administrateur de la clinique ===")
        email = input("Email de connexion : ").strip()

        existing = db.query(User).filter(User.email == email).first()
        if existing:
            print(f"Un compte existe déjà avec l'email '{email}'. Rien n'a été créé.")
            return

        full_name = input("Nom complet : ").strip()
        password = getpass.getpass("Mot de passe (rien ne s'affiche en tapant, c'est normal) : ")
        password_confirm = getpass.getpass("Confirme le mot de passe : ")

        if password != password_confirm:
            print("Les deux mots de passe ne correspondent pas. Relance le script pour réessayer.")
            return

        if len(password) < 8:
            print("Le mot de passe doit faire au moins 8 caractères. Relance le script pour réessayer.")
            return

        user = User(
            email=email,
            full_name=full_name,
            hashed_password=hash_password(password),
            role="admin",
        )
        db.add(user)
        db.commit()
        print(f"\nCompte créé avec succès : {email}")
        print("Tu peux maintenant te connecter sur http://localhost:8000")
    finally:
        db.close()


if __name__ == "__main__":
    main()
