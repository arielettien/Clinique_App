"""Reprise des codes de lien communautaire crees par la version precedente.

Meme principe que pour les mots de passe des comptes (voir
accounts/migrations/0002) : l'empreinte du code d'acces est stockee en bcrypt
brut ("$2b$12$..."). Django identifie l'algorithme par un prefixe — sans lui,
il ne saurait pas quel verificateur utiliser et tous les liens communautaires
deja actifs cesseraient de fonctionner, obligeant a regenerer puis rediffuser
chaque code aux agents de terrain.

On etiquette donc les empreintes existantes avec "bcrypt$" ; l'empreinte
elle-meme n'est pas modifiee, les codes deja distribues restent valables.

Sans effet sur une base vierge, et rejouable sans dommage.
"""
from django.db import migrations

BCRYPT_PREFIX = "bcrypt$"


def tag_bcrypt_hashes(apps, schema_editor):
    Form = apps.get_model("clinical_forms", "Form")
    for form in Form.objects.exclude(public_access_code_hash=None).iterator():
        current = form.public_access_code_hash or ""
        if current.startswith("$2"):
            form.public_access_code_hash = BCRYPT_PREFIX + current
            form.save(update_fields=["public_access_code_hash"])


def untag_bcrypt_hashes(apps, schema_editor):
    Form = apps.get_model("clinical_forms", "Form")
    for form in Form.objects.exclude(public_access_code_hash=None).iterator():
        current = form.public_access_code_hash or ""
        if current.startswith(BCRYPT_PREFIX):
            form.public_access_code_hash = current[len(BCRYPT_PREFIX):]
            form.save(update_fields=["public_access_code_hash"])


class Migration(migrations.Migration):

    dependencies = [
        ("clinical_forms", "0002_json_vers_jsonb"),
    ]

    operations = [
        migrations.RunPython(tag_bcrypt_hashes, untag_bcrypt_hashes),
    ]
