"""Creation du premier compte administrateur.

Utilisation (le conteneur doit deja tourner, dans un AUTRE terminal que celui
ou "docker compose up" est lance) :

    docker compose exec api python manage.py create_admin

Reponds ensuite aux questions posees (email, nom, mot de passe).
"""
import getpass

from django.core.management.base import BaseCommand

from accounts.models import User

MIN_PASSWORD_LENGTH = 8


class Command(BaseCommand):
    help = "Cree le premier compte administrateur de la clinique."

    def handle(self, *args, **options):
        self.stdout.write("=== Creation du compte administrateur de la clinique ===")
        email = input("Email de connexion : ").strip()

        if User.objects.filter(email=email).exists():
            self.stdout.write(self.style.WARNING(
                f"Un compte existe deja avec l'email '{email}'. Rien n'a ete cree."))
            return

        full_name = input("Nom complet : ").strip()
        password = getpass.getpass(
            "Mot de passe (rien ne s'affiche en tapant, c'est normal) : ")
        password_confirm = getpass.getpass("Confirme le mot de passe : ")

        if password != password_confirm:
            self.stdout.write(self.style.ERROR(
                "Les deux mots de passe ne correspondent pas. Relance la commande pour reessayer."))
            return

        if len(password) < MIN_PASSWORD_LENGTH:
            self.stdout.write(self.style.ERROR(
                f"Le mot de passe doit faire au moins {MIN_PASSWORD_LENGTH} caracteres. "
                f"Relance la commande pour reessayer."))
            return

        User.objects.create_user(email=email, password=password,
                                 full_name=full_name, role="admin")
        self.stdout.write(self.style.SUCCESS(f"\nCompte cree avec succes : {email}"))
        self.stdout.write("Tu peux maintenant te connecter sur http://localhost:8000")
