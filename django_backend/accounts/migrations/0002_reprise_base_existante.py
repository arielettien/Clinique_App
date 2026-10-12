"""Reprise d'une base creee par la version precedente de l'application.

Deux ajustements sont necessaires pour que les comptes existants continuent
de fonctionner sans qu'aucun utilisateur n'ait a reinitialiser son mot de
passe :

1. La colonne "last_login" n'existait pas (Django l'attend sur tout compte).
2. Les mots de passe sont stockes en bcrypt brut ("$2b$12$..."). Django
   identifie l'algorithme par un prefixe : sans lui, il ne sait pas quel
   verificateur utiliser et refuserait toutes les connexions. On ajoute donc
   le prefixe "bcrypt$" devant les empreintes existantes — l'empreinte
   elle-meme n'est pas modifiee, seulement etiquetee.

Les deux operations sont sans effet sur une base vierge (la colonne vient
d'etre creee par la migration initiale, et il n'y a aucun compte a etiqueter)
et peuvent etre rejouees sans dommage.
"""
from django.db import migrations

BCRYPT_PREFIX = "bcrypt$"


def _column_names(connection, table):
    with connection.cursor() as cursor:
        return {c.name for c in connection.introspection.get_table_description(cursor, table)}


def add_last_login(apps, schema_editor):
    connection = schema_editor.connection
    if "users" not in connection.introspection.table_names():
        return
    if "last_login" in _column_names(connection, "users"):
        return
    User = apps.get_model("accounts", "User")
    # On reprend le champ tel que le modele le definit, plutot que d'en
    # fabriquer un a la volee : un champ cree a la main n'est rattache a aucun
    # modele, et n'a donc pas le nom de colonne que add_field attend.
    schema_editor.add_field(User, User._meta.get_field("last_login"))


def tag_bcrypt_hashes(apps, schema_editor):
    """Etiquette les empreintes bcrypt brutes pour que Django les reconnaisse."""
    User = apps.get_model("accounts", "User")
    for user in User.objects.all().iterator():
        current = user.password or ""
        if current.startswith("$2"):
            user.password = BCRYPT_PREFIX + current
            user.save(update_fields=["password"])


def untag_bcrypt_hashes(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    for user in User.objects.all().iterator():
        current = user.password or ""
        if current.startswith(BCRYPT_PREFIX):
            user.password = current[len(BCRYPT_PREFIX):]
            user.save(update_fields=["password"])


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_last_login, migrations.RunPython.noop),
        migrations.RunPython(tag_bcrypt_hashes, untag_bcrypt_hashes),
    ]
