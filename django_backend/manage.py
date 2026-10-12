#!/usr/bin/env python
"""Point d'entree des commandes Django (migrations, creation de compte, etc.)."""
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "clinique.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Django est introuvable. Verifie que les dependances sont installees "
            "(requirements.txt) et que l'environnement virtuel est actif."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
