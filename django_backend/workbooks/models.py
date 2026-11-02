from django.db import models
from django.utils import timezone

from core.utils import new_uuid


class Workbook(models.Model):
    """Un classeur cree et modifie directement dans l'application (module
    Tableur).

    A ne pas confondre avec le module Suivi & Evaluation, qui stocke des
    fichiers Excel deposes depuis l'exterieur : ici, rien n'est importe —
    le classeur naît et vit dans l'application.

    Volontairement NON rattache a un projet : ces classeurs servent au
    pilotage general (budget, planning, indicateurs), qui depasse souvent le
    cadre d'un seul projet.

    Le contenu est stocke en JSON plutot qu'en lignes et colonnes de base de
    donnees : un tableur n'a pas de schema fixe, et le representer en tables
    reviendrait a reconstruire un moteur de feuille de calcul dans PostgreSQL
    pour aucun benefice.
    """

    id = models.CharField(primary_key=True, max_length=36, default=new_uuid, editable=False)
    title = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    # { "sheets": [ { "name", "cells": {"A1": {"v": ..., "f": ...}}, "merges": [...],
    #                 "colWidths": {...}, "rows": n, "cols": n } ] }
    content = models.JSONField(default=dict)

    created_by = models.ForeignKey(
        "accounts.User", null=True, blank=True,
        on_delete=models.SET_NULL, db_column="created_by", related_name="+",
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    updated_by = models.ForeignKey(
        "accounts.User", null=True, blank=True,
        on_delete=models.SET_NULL, db_column="updated_by", related_name="+",
    )

    class Meta:
        db_table = "workbooks"
        ordering = ["-updated_at"]

    def __str__(self):
        return self.title
