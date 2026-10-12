"""Exports et analyses construits a partir des soumissions d'un formulaire."""
from datetime import datetime, time
from io import BytesIO

from django.http import FileResponse
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from cases.models import Case
from clinical_forms.models import Form
from clinical_forms.services import fields_of, find_referral_field_id
from core.audit import log_audit
from core.spreadsheet_io import (
    DEFAULT_FIXED_COLUMNS,
    DEFAULT_FIXED_LABELS,
    build_data_export_bytes,
    build_patient_data_export_bytes,
    build_patient_export_bytes,
    slugify_py,
)
from core.utils import age_at
from submissions.models import Submission

MEDIA_FIELD_TYPES = ("image", "audio", "video", "file")


def _selected_columns(request):
    """Variables retenues pour l'export, telles que cochees dans l'ecran
    DONNEES. Renvoie (questions, colonnes d'identite), ou (None, None) si
    rien n'est precise — auquel cas tout est exporte, comme avant.

    Les deux listes sont transmises separement ("columns" pour les questions
    du formulaire, "identity" pour les colonnes d'identite) parce qu'un
    formulaire peut tres bien contenir une question nommee "sexe" ou "age",
    donc portant le meme nom qu'une colonne d'identite : avec une seule
    liste, decocher l'une ferait disparaitre l'autre.

    Permet de laisser de cote les colonnes inutiles a une analyse statistique
    (signatures, photos, nom du patient...) sans avoir a les supprimer
    ensuite dans le tableur.
    """
    questions = request.query_params.getlist("columns")
    identity = request.query_params.getlist("identity")
    if not questions and not identity:
        return None, None
    return set(questions), set(identity)


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _filtered_submissions(form_id, request):
    """Soumissions du formulaire, filtrees sur la periode demandee, de la plus
    ancienne a la plus recente."""
    query = Submission.objects.filter(form_id=form_id)
    start = _parse_date(request.query_params.get("start"))
    end = _parse_date(request.query_params.get("end"))
    if start:
        query = query.filter(collected_at__gte=datetime.combine(start, time.min))
    if end:
        query = query.filter(collected_at__lte=datetime.combine(end, time.max))
    return query.select_related("patient").order_by("collected_at")


def _file_response(content: bytes, media_type: str, filename: str):
    response = FileResponse(BytesIO(content), content_type=media_type)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def export_form_data(request, form_id):
    """Export "depistages" : UNE LIGNE PAR EVENEMENT."""
    export_format = request.query_params.get("format", "csv")
    if export_format not in ("csv", "xlsx"):
        return Response(
            {"detail": f"Format d'export non reconnu : '{export_format}' (csv ou xlsx attendu)."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    form = Form.objects.filter(pk=form_id).first()
    if not form:
        return Response({"detail": "Formulaire introuvable"}, status=status.HTTP_404_NOT_FOUND)

    submissions = list(_filtered_submissions(form_id, request))

    selected_questions, selected_identity = _selected_columns(request)
    fields = fields_of(form)
    if selected_questions is not None:
        fields = [f for f in fields if f.get("id") in selected_questions]
        fixed_columns = [c for c in DEFAULT_FIXED_COLUMNS if c in selected_identity]
        fixed_labels = {k: v for k, v in DEFAULT_FIXED_LABELS.items() if k in selected_identity}
    else:
        fixed_columns, fixed_labels = None, None

    field_ids = [f["id"] for f in fields if f.get("type") != "note"]
    # Champs photo/audio/video/fichier : la valeur brute est un identifiant de
    # fichier, illisible tel quel dans un tableur — on le transforme en adresse
    # complete, cliquable une fois connecte a l'application (le fichier reste
    # protege, jamais public).
    media_field_ids = {f["id"] for f in fields if f.get("type") in MEDIA_FIELD_TYPES}
    base_url = request.build_absolute_uri("/").rstrip("/")

    # Nombre de depistages de CE patient sur la periode exportee, et dates de
    # son premier/dernier — calcule une fois sur l'ensemble deja filtre, puis
    # repete sur chacune de ses lignes. Ces colonnes sont ici, et non dans
    # l'export "personnes" : suivre un historique dans le temps appartient a
    # l'export par evenement, "personnes" restant un instantane.
    per_patient_dates = {}
    for s in submissions:
        per_patient_dates.setdefault(s.patient_id, []).append(s.collected_at)

    rows = []
    for s in submissions:
        patient = s.patient
        patient_dates = per_patient_dates[s.patient_id]
        # Age et sexe sont les seules donnees patient incluses ici : exception
        # deliberee au principe "aucune donnee personnelle dans les exports",
        # car ces deux-la sont essentielles a l'analyse et non identifiantes.
        row = {
            "id_soumission": s.id,
            "patient": f"{patient.first_name} {patient.last_name}" if patient else "",
            # Code anonyme affiche COTE A COTE avec le nom, jamais a sa place :
            # c'est a l'utilisateur de retirer la colonne "patient" selon la
            # situation (avant un envoi a un bailleur, par exemple), en
            # connaissance de cause plutot que via une case "anonymiser".
            "code_anonyme": patient.anonymous_code if patient else None,
            "date": s.collected_at.strftime("%Y-%m-%d %H:%M"),
            "age": age_at(patient.date_of_birth, s.collected_at.date()) if patient else None,
            "sexe": patient.sex if patient else None,
            "nb_depistages_periode": len(patient_dates),
            "premier_depistage": min(patient_dates).strftime("%Y-%m-%d"),
            "dernier_depistage": max(patient_dates).strftime("%Y-%m-%d"),
        }
        for fid in field_ids:
            # None = question masquee par une condition d'affichage (non
            # applicable), a distinguer d'une reponse laissee vide alors que la
            # question etait bien posee. La conversion en "N/A" est faite plus
            # loin, pour couvrir aussi les champs a choix multiples eclates en
            # plusieurs colonnes.
            value = s.responses.get(fid)
            if value and fid in media_field_ids:
                value = f"{base_url}/api/media/{value}"
            row[fid] = value
        rows.append(row)

    content, media_type, ext = build_data_export_bytes(
        form.name, fields, rows, export_format, fixed_columns, fixed_labels)
    log_audit(request, user=request.user, action="export", entity_type="form",
              entity_id=form_id, project_id=form.project_id,
              details=f"Export ({export_format}) des donnees du formulaire "
                      f"'{form.name}' — {len(rows)} ligne(s)")
    return _file_response(content, media_type, f"{slugify_py(form.name)}.{ext}")


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def form_trend(request, form_id):
    """Depistages (soumissions) vs personnes distinctes, par mois — pour voir
    une tendance sans compter plusieurs fois la meme personne depistee
    plusieurs fois sur la periode."""
    form = Form.objects.filter(pk=form_id).first()
    if not form:
        return Response({"detail": "Formulaire introuvable"}, status=status.HTTP_404_NOT_FOUND)

    submissions = list(_filtered_submissions(form_id, request))

    buckets = {}
    for s in submissions:
        key = s.collected_at.strftime("%Y-%m")
        bucket = buckets.setdefault(key, {"submissions": 0, "patients": set()})
        bucket["submissions"] += 1
        bucket["patients"].add(s.patient_id)

    return Response({
        "total_submissions": len(submissions),
        "distinct_patients": len({s.patient_id for s in submissions}),
        "by_month": [
            {"period": k, "submissions": v["submissions"], "distinct_patients": len(v["patients"])}
            for k, v in sorted(buckets.items())
        ],
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def detect_status_change(request, form_id):
    """Repere les patients ayant eu, pour ce champ, une valeur hors des
    valeurs cibles AVANT d'en avoir une dedans plus tard (ex : negatif puis
    positif). Plusieurs valeurs cibles sont acceptees a la fois, pour ne pas
    rater une categorie proche ('positif' et 'suspect_cancer' comptent tous
    les deux comme preoccupants)."""
    form = Form.objects.filter(pk=form_id).first()
    if not form:
        return Response({"detail": "Formulaire introuvable"}, status=status.HTTP_404_NOT_FOUND)

    field = request.query_params.get("field")
    target_values = request.query_params.getlist("target_values")
    if field not in {f["id"] for f in fields_of(form)}:
        return Response({"detail": f"Le champ '{field}' n'existe pas dans ce formulaire."},
                        status=status.HTTP_422_UNPROCESSABLE_ENTITY)

    target_set = set(target_values)
    submissions = (Submission.objects.filter(form_id=form_id)
                   .select_related("patient").order_by("collected_at"))

    by_patient = {}
    for s in submissions:
        by_patient.setdefault(s.patient_id, []).append(s)

    results = []
    for patient_id, subs in by_patient.items():
        subs.sort(key=lambda s: s.collected_at)
        seen_other_value_at = None
        for s in subs:
            value = s.responses.get(field)
            if value is None or value == "":
                continue
            if str(value) not in target_set:
                if seen_other_value_at is None:
                    seen_other_value_at = s.collected_at
            elif seen_other_value_at is not None:
                patient = s.patient
                results.append({
                    "patient_id": patient_id,
                    "patient_name": (f"{patient.first_name} {patient.last_name}"
                                     if patient else "Patient supprime"),
                    "first_other_value_at": seen_other_value_at,
                    "target_value_at": s.collected_at,
                    "matched_value": str(value),
                })
                break

    export_format = request.query_params.get("format")
    if export_format in ("csv", "xlsx"):
        rows = [{
            "patient": r["patient_name"],
            "premiere_valeur_hors_cible_le": r["first_other_value_at"].strftime("%Y-%m-%d"),
            "devenu_valeur_cible_le": r["target_value_at"].strftime("%Y-%m-%d"),
            "valeur_declenchante": r["matched_value"],
        } for r in results]
        columns = ["patient", "premiere_valeur_hors_cible_le",
                   "devenu_valeur_cible_le", "valeur_declenchante"]
        content, media_type, ext = build_patient_export_bytes(rows, columns, export_format)
        return _file_response(content, media_type, f"detecteur_{slugify_py(field)}.{ext}")

    return Response({"field": field, "target_values": target_values, "matches": results})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def export_patients_data(request, form_id):
    """Export "personnes" : UNE LIGNE PAR PERSONNE (et non par depistage),
    sans nom ni telephone — pour qu'une personne depistee plusieurs fois ne
    soit pas comptee plusieurs fois dans un test statistique, ce qui fausserait
    une prevalence.

    La ligne porte aussi les REPONSES du formulaire, sans quoi cet export ne
    permettrait que de compter des personnes, jamais de les analyser. Une meme
    personne pouvant avoir repondu differemment a des depistages successifs, ce
    sont les reponses de sa soumission LA PLUS RECENTE qui sont retenues —
    coherent avec l'age, calcule a cette meme date.

    Le nombre de depistages et les dates premiere/derniere ne sont
    volontairement PAS ici : ils appartiennent a l'export "depistages", plus
    adapte pour suivre un historique dans le temps.
    """
    form = Form.objects.filter(pk=form_id).first()
    if not form:
        return Response({"detail": "Formulaire introuvable"}, status=status.HTTP_404_NOT_FOUND)

    export_format = request.query_params.get("format", "csv")
    status_field = request.query_params.get("status_field")
    status_target_values = request.query_params.getlist("status_target_values")
    target_set = set(status_target_values) if status_target_values else None

    submissions = list(_filtered_submissions(form_id, request))

    selected_questions, selected_identity = _selected_columns(request)
    fields = fields_of(form)
    if selected_questions is not None:
        fields = [f for f in fields if f.get("id") in selected_questions]

    by_patient = {}
    for s in submissions:
        by_patient.setdefault(s.patient_id, []).append(s)

    rows = []
    for patient_id, subs in by_patient.items():
        subs.sort(key=lambda s: s.collected_at)
        patient = subs[-1].patient
        last_sub = subs[-1]
        row = {
            "identifiant": patient_id,
            "sexe": (patient.sex if patient else "") or "",
            "age": age_at(patient.date_of_birth, last_sub.collected_at) if patient else "",
        }
        if status_field and target_set:
            last_value = last_sub.responses.get(status_field)
            row["dernier_resultat_connu"] = "N/A" if last_value is None else (last_value or "")
            row["a_ete_positif_au_moins_une_fois"] = 1 if any(
                str(s.responses.get(status_field)) in target_set
                for s in subs if s.responses.get(status_field) not in (None, "")
            ) else 0
        # Reponses de la soumission la plus recente, sous leurs identifiants de
        # champ d'origine : l'eclatement en colonnes est fait ensuite, comme
        # pour l'export "depistages".
        row.update(last_sub.responses)
        rows.append(row)

    fixed_columns = ["identifiant", "sexe", "age"]
    fixed_labels = {"identifiant": "Identifiant", "sexe": "Sexe", "age": "Age"}
    if selected_identity is not None:
        # "identifiant" est toujours conserve : sans lui, chaque ligne devient
        # anonyme au point d'etre inutilisable (impossible de recouper deux
        # exports, ni de retrouver une personne pour verifier une valeur).
        fixed_columns = [c for c in fixed_columns if c == "identifiant" or c in selected_identity]
        fixed_labels = {k: v for k, v in fixed_labels.items() if k in fixed_columns}
    if status_field and target_set:
        fixed_columns += ["dernier_resultat_connu", "a_ete_positif_au_moins_une_fois"]
        fixed_labels["dernier_resultat_connu"] = "Dernier resultat connu"
        fixed_labels["a_ete_positif_au_moins_une_fois"] = "A ete positif au moins une fois"

    content, media_type, ext = build_patient_data_export_bytes(
        fields, rows, fixed_columns, fixed_labels, export_format)
    return _file_response(content, media_type, f"{slugify_py(form.name)}_personnes.{ext}")


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def referral_candidates(request, form_id):
    """Soumissions ou le champ S/N-S vaut "Oui" mais dont le patient n'a
    toujours aucun dossier de suivi actif — filet de securite pour rattraper
    les cas ou la proposition affichee juste apres l'enregistrement aurait ete
    ignoree ou fermee par erreur."""
    form = Form.objects.filter(pk=form_id).first()
    if not form:
        return Response({"detail": "Formulaire introuvable"}, status=status.HTTP_404_NOT_FOUND)

    field_id = find_referral_field_id(form.definition)
    if not field_id:
        return Response([])

    followed_patient_ids = set(
        Case.objects.filter(project_id=form.project_id, status="active")
        .values_list("patient_id", flat=True)
    )

    result = []
    for s in Submission.objects.filter(form_id=form_id).select_related("patient"):
        if not s.responses.get(field_id):
            continue
        if s.patient_id in followed_patient_ids or not s.patient:
            continue
        result.append({
            "submission_id": s.id,
            "patient_id": s.patient.id,
            "patient_name": f"{s.patient.first_name} {s.patient.last_name}",
            "collected_at": s.collected_at,
        })
    return Response(result)
