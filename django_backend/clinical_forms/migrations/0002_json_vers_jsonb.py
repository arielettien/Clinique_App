"""Convertit la colonne JSON heritee au type attendu par Django.

La version precedente de l'application creait cette colonne avec le type
PostgreSQL "json". Django, lui, travaille en "jsonb". La difference n'est pas
cosmetique : avec "json", le pilote PostgreSQL decode lui-meme la valeur et
renvoie deja un dictionnaire, alors que Django s'attend a recevoir du texte a
decoder — toute lecture d'un formulaire echouait donc avec une erreur de type.

"jsonb" est par ailleurs le type recommande (stockage binaire, indexable).
La conversion preserve integralement le contenu.

Sans effet sur une base vierge (Django y cree deja la colonne en jsonb) et
sans effet sur SQLite, qui n'a pas ces types.
"""
from django.db import migrations

TABLE = "forms"
COLUMN = "definition"


def _current_type(connection, table, column):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = %s AND column_name = %s",
            [table, column],
        )
        row = cursor.fetchone()
    return row[0] if row else None


def to_jsonb(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return
    if _current_type(connection, TABLE, COLUMN) != "json":
        return
    with connection.cursor() as cursor:
        cursor.execute(
            f"ALTER TABLE {TABLE} ALTER COLUMN {COLUMN} TYPE jsonb USING {COLUMN}::jsonb"
        )


def to_json(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return
    if _current_type(connection, TABLE, COLUMN) != "jsonb":
        return
    with connection.cursor() as cursor:
        cursor.execute(
            f"ALTER TABLE {TABLE} ALTER COLUMN {COLUMN} TYPE json USING {COLUMN}::json"
        )


class Migration(migrations.Migration):

    dependencies = [
        ("clinical_forms", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(to_jsonb, to_json),
    ]
