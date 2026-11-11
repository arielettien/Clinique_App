"""Import d'une étude déjà réalisée (questionnaire + réponses) — côté HTTP.

Deux étapes, sans état côté serveur :
  1. POST /api/etudes/analyser  : le fichier → une proposition (rôle et type
     de chaque colonne), que l'agent corrige à l'écran.
  2. POST /api/etudes/importer  : le fichier à nouveau + la proposition
     corrigée. Avec a_blanc=1, tout est fait puis annulé : on rend le rapport
     exact de ce qui AURAIT été créé. Sans, on écrit pour de bon.

Renvoyer le fichier deux fois coûte une seconde sur le réseau local, et
évite de stocker un fichier de données patients « en attente » sur le
serveur.

Chaque fiche repasse par validate_and_process_submission, comme une saisie
à la main : le fichier n'est jamais cru sur parole. Une ligne refusée est
signalée avec son numéro Excel et ignorée ; les autres passent. Relancer le
même fichier ne crée jamais de doublon (clé d'idempotence par ligne), donc
corriger le fichier puis réimporter est le chemin normal, pas un risque.
"""
import json

from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from clinical_forms.models import Form
from core.audit import log_audit
from core.etude_import import (ROLES, TYPES_QUESTION, cle_idempotence, construire_definition,
                               analyser, lire_tableau, preparer_ligne)
from core.form_import_validation import validate_form_definition
from core.form_validation import validate_and_process_submission
from core.permissions import IsAdmin
from patients.models import Patient
from patients.services import assign_patient_code
from projects.models import Project
from submissions.models import Submission
from submissions.services import check_age_consistency

MAX_ERREURS_RAPPORT = 100


@api_view(["POST"])
@permission_classes([IsAdmin])
def analyser_etude(request):
    uploaded = request.FILES.get("file")
    if not uploaded:
        return Response({"detail": "Aucun fichier reçu."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        proposition = analyser(uploaded.read(), uploaded.name or "etude.xlsx")
    except Exception as e:  # noqa: BLE001 — message remonte tel quel a l'agent
        return Response({"detail": f"Impossible de lire le fichier : {e}"},
                        status=status.HTTP_422_UNPROCESSABLE_ENTITY)
    return Response(proposition)


def _colonnes_validees(brut: str) -> list:
    """Ne garde de la proposition renvoyée par le navigateur que ce que le
    serveur accepte de croire : rôle, type, libellé, options. Les valeurs,
    elles, sont relues dans le fichier."""
    try:
        colonnes = json.loads(brut or "[]")
    except json.JSONDecodeError:
        raise ValueError("La description des colonnes est illisible.")
    if not isinstance(colonnes, list) or not colonnes:
        raise ValueError("Aucune colonne décrite.")
    ids = set()
    for c in colonnes:
        if c.get("role") not in ROLES:
            raise ValueError(f"Rôle inconnu « {c.get('role')} » pour « {c.get('entete')} ».")
        if c["role"] == "question":
            if c.get("type") not in TYPES_QUESTION:
                raise ValueError(f"Type inconnu « {c.get('type')} » pour « {c.get('entete')} ».")
            if not c.get("id") or c["id"] in ids:
                raise ValueError(f"Identifiant manquant ou en double pour « {c.get('entete')} ».")
            ids.add(c["id"])
        if not isinstance(c.get("sources"), list) or not c["sources"]:
            raise ValueError(f"Colonne source manquante pour « {c.get('entete')} ».")
    return colonnes


def _obtenir_patient(project_id, infos: dict, cache: dict, rapport: dict) -> Patient:
    """Un patient par personne, pas par ligne : la même personne vue deux
    fois dans le fichier (même code, ou même nom + téléphone) donne deux
    fiches sur un seul patient. Et si elle existe déjà en base — import
    précédent, saisie à la main — on la réutilise plutôt que de la dupliquer."""
    cle = infos["code"].lower() if infos["code"] else (
        infos["prenom"].lower(), infos["nom"].lower(), infos["telephone"], str(infos["date_naissance"]))
    if cle in cache:
        return cache[cle]

    candidats = Patient.objects.filter(project_id=project_id,
                                       first_name__iexact=infos["prenom"],
                                       last_name__iexact=infos["nom"])
    existant = None
    for p in candidats:
        if infos["telephone"] and (p.phone or "") == infos["telephone"]:
            existant = p
            break
        if not infos["telephone"] and infos["date_naissance"] and p.date_of_birth == infos["date_naissance"]:
            existant = p
            break
        if not infos["telephone"] and not infos["date_naissance"]:
            existant = p
            break
    if existant:
        rapport["patients_existants"] += 1
        cache[cle] = existant
        return existant

    patient = Patient(project_id=project_id, first_name=infos["prenom"], last_name=infos["nom"],
                      date_of_birth=infos["date_naissance"], sex=infos["sexe"],
                      phone=infos["telephone"] or None)
    patient.save(force_insert=True)
    assign_patient_code(patient)
    rapport["patients_crees"] += 1
    cache[cle] = patient
    return patient


@api_view(["POST"])
@permission_classes([IsAdmin])
def importer_etude(request):
    uploaded = request.FILES.get("file")
    project_id = request.data.get("project_id")
    nom = (request.data.get("nom") or "").strip()
    a_blanc = str(request.data.get("a_blanc") or "").lower() in ("1", "true", "oui")

    if not uploaded:
        return Response({"detail": "Aucun fichier reçu."}, status=status.HTTP_400_BAD_REQUEST)
    if not Project.objects.filter(pk=project_id).exists():
        return Response({"detail": "Projet introuvable"}, status=status.HTTP_404_NOT_FOUND)
    nom_fichier = uploaded.name or "etude.xlsx"
    if not nom:
        nom = nom_fichier.rsplit(".", 1)[0]
    try:
        colonnes = _colonnes_validees(request.data.get("colonnes"))
        entetes, lignes = lire_tableau(uploaded.read(), nom_fichier)
    except Exception as e:  # noqa: BLE001
        return Response({"detail": str(e)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

    manquantes = [s for c in colonnes for s in c["sources"] if s not in entetes]
    if manquantes:
        return Response({"detail": "Le fichier ne correspond plus à l'analyse (colonnes absentes : "
                                   + ", ".join(manquantes[:5]) + "). Relance l'analyse."},
                        status=status.HTTP_422_UNPROCESSABLE_ENTITY)

    definition = construire_definition(nom, colonnes, nom_fichier)
    erreurs_structure = validate_form_definition(definition)
    if erreurs_structure:
        return Response({"detail": " | ".join(erreurs_structure)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

    # Jamais deux formulaires pour une même étude : si le nom existe déjà dans
    # ce projet, on complète ce formulaire-là — à condition que ses questions
    # soient les mêmes, sinon les anciennes fiches et les nouvelles ne
    # parleraient pas de la même chose.
    existant = Form.objects.filter(project_id=project_id, is_active=True, name=nom).first()
    if existant:
        ids_existants = {f["id"] for s in (existant.definition or {}).get("sections", []) for f in s.get("fields", [])}
        ids_nouveaux = {f["id"] for f in definition["sections"][0]["fields"]}
        if ids_existants != ids_nouveaux:
            return Response({"detail": f"Un formulaire « {nom} » existe déjà dans ce projet avec des questions "
                                       f"différentes. Donne un autre nom à cette étude."},
                            status=status.HTTP_409_CONFLICT)
        definition = existant.definition

    rapport = {"a_blanc": a_blanc, "lignes": len(lignes), "fiches": 0, "deja_enregistrees": 0,
               "patients_crees": 0, "patients_existants": 0, "erreurs": [], "avertissements": [],
               "formulaire": None}

    with transaction.atomic():
        form = existant or Form.objects.create(project_id=project_id, name=nom,
                                               form_type="survey", definition=definition)
        cache_patients: dict = {}
        sans_date = 0
        for numero, ligne in enumerate(lignes, start=2):      # ligne 1 = les en-têtes, comme dans Excel
            prep = preparer_ligne(ligne, colonnes, numero)
            rapport["avertissements"].extend(prep["avertissements"])
            cle = cle_idempotence(project_id, nom, prep["signature"])
            if Submission.objects.filter(idempotency_key=cle).exists():
                rapport["deja_enregistrees"] += 1
                continue

            processed, erreurs, _alertes = validate_and_process_submission(definition, prep["responses"])
            if erreurs:
                if len(rapport["erreurs"]) < MAX_ERREURS_RAPPORT:
                    rapport["erreurs"].append({"ligne": numero, "message": " | ".join(erreurs)})
                continue

            patient = _obtenir_patient(project_id, prep["patient"], cache_patients, rapport)
            collected_at = prep["collected_at"]
            if collected_at is None:
                sans_date += 1
            erreurs_age = check_age_consistency(form, processed, patient, collected_at)
            if erreurs_age:
                if len(rapport["erreurs"]) < MAX_ERREURS_RAPPORT:
                    rapport["erreurs"].append({"ligne": numero, "message": " | ".join(erreurs_age)})
                continue

            fiche = Submission(form=form, patient=patient, responses=processed,
                               created_by=request.user, idempotency_key=cle)
            if collected_at is not None:
                fiche.collected_at = collected_at
            fiche.save(force_insert=True)
            rapport["fiches"] += 1

        if sans_date:
            rapport["avertissements"].append(f"{sans_date} fiche(s) sans date de collecte, datées du jour de l'import.")
        rapport["formulaire"] = {"id": form.id, "nom": form.name,
                                 "questions": len(definition["sections"][0]["fields"]),
                                 "existant": bool(existant)}
        if a_blanc:
            transaction.set_rollback(True)

    rapport["avertissements"] = list(dict.fromkeys(rapport["avertissements"]))
    if not a_blanc:
        log_audit(request, user=request.user, action="create", entity_type="study",
                  entity_id=rapport["formulaire"]["id"], project_id=project_id,
                  details=(f"Import de l'étude « {nom} » depuis {nom_fichier} : "
                           f"{rapport['patients_crees']} patient(s) créé(s), {rapport['patients_existants']} "
                           f"réutilisé(s), {rapport['fiches']} fiche(s), {len(rapport['erreurs'])} ligne(s) "
                           f"refusée(s), {rapport['deja_enregistrees']} déjà enregistrée(s)."))
    return Response(rapport)
