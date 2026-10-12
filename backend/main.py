"""
Clinique App — v0.7
--------------------
Ajout par rapport à la v0.6 :
- Export des données recueillies (CSV / Excel) par formulaire
- Export/import de la STRUCTURE d'un formulaire (notre propre format tableur),
  pour dupliquer/sauvegarder/construire un formulaire hors ligne
"""

from datetime import date, datetime, timedelta
from typing import Optional, List, Dict, Any
import os
import re
import secrets
import threading
import uuid

from fastapi import FastAPI, HTTPException, Depends, Request, Response, Query, UploadFile, File, Header
from fastapi import Form as FastAPIFormField
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, FileResponse
import mimetypes
from pydantic import BaseModel, Field
from sqlalchemy import select, or_
from sqlalchemy.exc import IntegrityError
from io import BytesIO
import pandas as pd

from database import engine, SessionLocal, Base
from models import Patient, User, Form, Submission, Project, Case, Media, AuditLog, PotentialDuplicate
from security import (
    verify_password,
    hash_password,
    create_access_token,
    decode_access_token,
    ACCESS_TOKEN_EXPIRE_HOURS,
)
from form_validation import validate_and_process_submission
from form_import_validation import validate_form_definition
from spreadsheet_io import (
    build_template_bytes,
    parse_spreadsheet_to_sections,
    build_data_export_bytes,
    build_patient_export_bytes,
    build_patient_data_export_bytes,
    slugify_py,
)

app = FastAPI(title="Clinique — v0.7")


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)


@app.get("/health")
def health():
    return {"status": "ok"}


# --- Authentification ---

class LoginRequest(BaseModel):
    email: str
    password: str


def get_current_user(request: Request) -> User:
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=401, detail="Non authentifié")

    payload = decode_access_token(token)
    user_id = payload.get("sub") if payload else None
    if not user_id:
        raise HTTPException(status_code=401, detail="Session invalide ou expirée")

    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if not user or not user.is_active:
            raise HTTPException(status_code=401, detail="Compte introuvable ou désactivé")
        # Un jeton émis AVANT la dernière déconnexion/réinitialisation de mot
        # de passe de ce compte (token_version différent) ne doit plus jamais
        # être accepté, même s'il n'a pas encore techniquement expiré.
        if payload.get("tv", 0) != user.token_version:
            raise HTTPException(status_code=401, detail="Session invalide ou expirée")
        return user
    finally:
        db.close()


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """À utiliser en plus de get_current_user sur les actions sensibles
    (suppression de projet, gestion des comptes) — jusqu'ici le rôle
    n'était jamais vérifié nulle part, n'importe quel compte pouvait tout
    faire."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Action réservée aux administrateurs.")
    return current_user


def log_audit(db, request: Request = None, user: User = None, user_email: str = None,
               action: str = "", entity_type: str = None, entity_id: str = None,
               project_id: str = None, details: str = None):
    """Enregistre une action sensible dans le journal d'audit. Volontairement
    limité aux connexions, à la gestion des comptes, aux suppressions et aux
    exports — pas à chaque consultation de page ni chaque soumission de
    formulaire, ce qui noierait l'information utile sous du bruit.
    N'importe quelle erreur ici est avalée : le journal d'audit ne doit
    jamais faire échouer l'action réelle qu'il est censé simplement noter."""
    try:
        entry = AuditLog(
            user_id=user.id if user else None,
            user_email=user_email or (user.email if user else None),
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            project_id=project_id,
            details=details,
            ip_address=(request.client.host if request and request.client else None),
        )
        db.add(entry)
        db.commit()
    except Exception as e:
        print(f"[audit] échec de journalisation (ignoré) : {e}")


# Protection contre les mots de passe devinés en boucle — mêmes seuils que le
# lien communautaire (voir verify_public_code), mais par (IP, email) : un peu
# plus tolérant qu'un lien public puisqu'une faute de frappe légitime doit
# rester possible sans bloquer l'utilisateur pour la journée.
_LOGIN_MAX_ATTEMPTS = 10
_LOGIN_WINDOW = timedelta(minutes=15)
_LOGIN_LOCKOUT = timedelta(minutes=15)
_login_attempts: Dict[str, List[datetime]] = {}
_login_lockouts: Dict[str, datetime] = {}
_login_lock = threading.Lock()


@app.post("/api/auth/login")
def login(payload: LoginRequest, request: Request, response: Response):
    db = SessionLocal()
    try:
        client_ip = request.client.host if request.client else "inconnu"
        login_key = f"{client_ip}:{payload.email}"
        now = datetime.utcnow()
        with _login_lock:
            locked_until = _login_lockouts.get(login_key)
            if locked_until and locked_until > now:
                wait_min = max(1, int((locked_until - now).total_seconds() // 60) + 1)
                raise HTTPException(
                    status_code=429,
                    detail=f"Trop de tentatives de connexion. Réessaie dans environ {wait_min} min.",
                )

        user = db.query(User).filter(User.email == payload.email).first()
        if not user or not verify_password(payload.password, user.hashed_password):
            with _login_lock:
                attempts = [t for t in _login_attempts.get(login_key, []) if now - t < _LOGIN_WINDOW]
                attempts.append(now)
                if len(attempts) >= _LOGIN_MAX_ATTEMPTS:
                    _login_lockouts[login_key] = now + _LOGIN_LOCKOUT
                    _login_attempts.pop(login_key, None)
                else:
                    _login_attempts[login_key] = attempts
            log_audit(db, request, user_email=payload.email, action="login_failed",
                      details="Email ou mot de passe incorrect")
            raise HTTPException(status_code=401, detail="Email ou mot de passe incorrect")
        with _login_lock:
            _login_attempts.pop(login_key, None)
            _login_lockouts.pop(login_key, None)
        if not user.is_active:
            log_audit(db, request, user=user, action="login_failed", details="Compte désactivé")
            raise HTTPException(status_code=401, detail="Compte désactivé")

        token = create_access_token(user.id, user.token_version)
        # secure=True uniquement une fois l'application derrière HTTPS — en
        # HTTP simple (ex: localhost pendant le développement), le navigateur
        # refuse de renvoyer un cookie "secure" et la connexion casserait.
        cookie_secure = os.environ.get("COOKIE_SECURE", "false").lower() == "true"
        response.set_cookie(
            key="access_token",
            value=token,
            httponly=True,
            samesite="lax",
            secure=cookie_secure,
            max_age=ACCESS_TOKEN_EXPIRE_HOURS * 3600,
        )
        log_audit(db, request, user=user, action="login")
        return {"full_name": user.full_name, "role": user.role}
    finally:
        db.close()


@app.post("/api/auth/logout")
def logout(response: Response, current_user: User = Depends(get_current_user)):
    response.delete_cookie("access_token")
    # Incrémenter token_version invalide immédiatement ce jeton (et tout autre
    # jeton déjà émis pour ce compte, ex: une autre session ouverte ailleurs)
    # côté serveur — sans ça, un jeton copié avant la déconnexion restait
    # valable jusqu'à 12h de plus.
    db = SessionLocal()
    try:
        user = db.get(User, current_user.id)
        if user:
            user.token_version += 1
            db.commit()
    finally:
        db.close()
    return {"status": "ok"}


@app.get("/api/auth/me")
def me(current_user: User = Depends(get_current_user)):
    return {
        "email": current_user.email,
        "full_name": current_user.full_name,
        "role": current_user.role,
    }


# --- Gestion des comptes utilisateurs (réservé aux administrateurs) ---

class UserCreate(BaseModel):
    email: str
    full_name: str
    password: str = Field(min_length=8)
    role: str = "operator"  # "admin" ou "operator"


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    password: Optional[str] = Field(default=None, min_length=8)  # pour réinitialiser un mot de passe oublié


def _serialize_user(u: User) -> dict:
    return {
        "id": u.id, "email": u.email, "full_name": u.full_name,
        "role": u.role, "is_active": u.is_active, "created_at": u.created_at,
    }


@app.get("/api/users")
def list_users(current_user: User = Depends(require_admin)):
    db = SessionLocal()
    try:
        users = db.query(User).order_by(User.created_at.asc()).all()
        return [_serialize_user(u) for u in users]
    finally:
        db.close()


@app.post("/api/users", status_code=201)
def create_user(payload: UserCreate, request: Request, current_user: User = Depends(require_admin)):
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == payload.email).first()
        if existing:
            raise HTTPException(status_code=409, detail="Un compte existe déjà avec cet email.")
        if payload.role not in ("admin", "operator"):
            raise HTTPException(status_code=422, detail="Rôle invalide (admin ou operator).")
        user = User(
            email=payload.email, full_name=payload.full_name,
            hashed_password=hash_password(payload.password), role=payload.role,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        log_audit(db, request, user=current_user, action="create", entity_type="user",
                  entity_id=user.id, details=f"Compte créé : {user.email} ({user.role})")
        return _serialize_user(user)
    finally:
        db.close()


@app.put("/api/users/{user_id}")
def update_user(user_id: str, payload: UserUpdate, request: Request, current_user: User = Depends(require_admin)):
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Compte introuvable.")
        changes = []
        if payload.full_name is not None:
            user.full_name = payload.full_name
        if payload.role is not None:
            if payload.role not in ("admin", "operator"):
                raise HTTPException(status_code=422, detail="Rôle invalide (admin ou operator).")
            if user.id == current_user.id and payload.role != "admin":
                raise HTTPException(status_code=400, detail="Impossible de retirer ton propre statut admin.")
            if payload.role != user.role:
                changes.append(f"rôle -> {payload.role}")
            user.role = payload.role
        if payload.is_active is not None:
            if user.id == current_user.id and not payload.is_active:
                raise HTTPException(status_code=400, detail="Impossible de désactiver ton propre compte.")
            if payload.is_active != user.is_active:
                changes.append("réactivé" if payload.is_active else "désactivé")
            user.is_active = payload.is_active
        if payload.password:
            user.hashed_password = hash_password(payload.password)
            # Invalide toute session déjà ouverte avec l'ancien mot de passe
            # (utile en particulier si ce changement fait suite à un compte
            # compromis) — sans ça, une session déjà ouverte resterait valable
            # jusqu'à 12h malgré le nouveau mot de passe.
            user.token_version += 1
            changes.append("mot de passe réinitialisé")
        db.commit()
        db.refresh(user)
        if changes:
            log_audit(db, request, user=current_user, action="update", entity_type="user",
                      entity_id=user.id, details=f"{user.email} : {', '.join(changes)}")
        return _serialize_user(user)
    finally:
        db.close()


@app.delete("/api/users/{user_id}")
def delete_user(user_id: str, request: Request, current_user: User = Depends(require_admin)):
    """Suppression définitive, autorisée seulement si ce compte n'a jamais
    créé la moindre donnée (soumission/cas) — sinon, la suppression casserait
    la traçabilité de qui a fait quoi. Un compte déjà utilisé doit être
    désactivé plutôt que supprimé."""
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Compte introuvable.")
        if user.id == current_user.id:
            raise HTTPException(status_code=400, detail="Impossible de supprimer ton propre compte.")

        created_counts = {
            "soumission(s)": db.query(Submission).filter(Submission.created_by == user_id).count(),
            "cas": db.query(Case).filter(Case.created_by == user_id).count(),
        }
        total_created = sum(created_counts.values())
        if total_created > 0:
            details = ", ".join(f"{v} {k}" for k, v in created_counts.items() if v > 0)
            raise HTTPException(
                status_code=409,
                detail=f"Ce compte a déjà créé des données ({details}) — impossible de le supprimer sans perdre la traçabilité. Désactive-le plutôt.",
            )

        deleted_email = user.email
        db.delete(user)
        db.commit()
        log_audit(db, request, user=current_user, action="delete", entity_type="user",
                  entity_id=user_id, details=f"Compte supprimé : {deleted_email}")
        return {"status": "ok"}
    finally:
        db.close()


# --- Schémas & endpoints Projets ---

class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    start_date: Optional[date] = None
    ngo_name: Optional[str] = None
    main_donor: Optional[str] = None
    cover_image_id: Optional[str] = None


class ProjectOut(ProjectCreate):
    id: str
    created_at: datetime

    class Config:
        from_attributes = True


@app.get("/api/projects", response_model=List[ProjectOut])
def list_projects(current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        return db.query(Project).order_by(Project.created_at.desc()).all()
    finally:
        db.close()


@app.get("/api/projects/{project_id}/stats")
def get_project_stats(project_id: str, current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        patient_count = db.query(Patient).filter(Patient.project_id == project_id).count()
        form_count = db.query(Form).filter(Form.project_id == project_id, Form.is_active == True).count()  # noqa: E712
        return {"patient_count": patient_count, "form_count": form_count}
    finally:
        db.close()


@app.post("/api/projects", response_model=ProjectOut, status_code=201)
def create_project(payload: ProjectCreate, current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        project = Project(**payload.model_dump())
        db.add(project)
        db.commit()
        db.refresh(project)
        return project
    finally:
        db.close()


@app.get("/api/projects/{project_id}", response_model=ProjectOut)
def get_project(project_id: str, current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Projet introuvable")
        return project
    finally:
        db.close()


@app.put("/api/projects/{project_id}", response_model=ProjectOut)
def update_project(project_id: str, payload: ProjectCreate, current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Projet introuvable")
        project.name = payload.name
        project.description = payload.description
        project.start_date = payload.start_date
        project.ngo_name = payload.ngo_name
        project.main_donor = payload.main_donor
        project.cover_image_id = payload.cover_image_id
        db.commit()
        db.refresh(project)
        return project
    finally:
        db.close()


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: str, request: Request, current_user: User = Depends(require_admin)):
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Projet introuvable")

        # Pas de cascade SQL définie sur ces clés étrangères : on supprime
        # nous-mêmes, dans l'ordre, tout ce qui dépend du projet avant le
        # projet lui-même (soumissions -> formulaires, cas, puis patients).
        form_ids = [f.id for f in db.query(Form).filter(Form.project_id == project_id).all()]
        if form_ids:
            db.query(Submission).filter(Submission.form_id.in_(form_ids)).delete(synchronize_session=False)
            db.query(Form).filter(Form.project_id == project_id).delete(synchronize_session=False)
        db.query(Case).filter(Case.project_id == project_id).delete(synchronize_session=False)
        db.query(Patient).filter(Patient.project_id == project_id).delete(synchronize_session=False)

        project_name = project.name
        db.delete(project)
        db.commit()
        log_audit(db, request, user=current_user, action="delete", entity_type="project",
                  entity_id=project_id, project_id=project_id, details=f"Projet supprimé : {project_name}")
        return {"status": "ok"}
    finally:
        db.close()


# --- Schémas & endpoints Patients (désormais rattachés à un projet) ---

class PatientCreate(BaseModel):
    project_id: str
    first_name: str
    last_name: str
    date_of_birth: Optional[date] = None
    sex: Optional[str] = None
    phone: Optional[str] = None
    id: Optional[str] = None  # fourni si créé hors-ligne (UUID généré dans le navigateur) — voir create_patient


class PatientOut(PatientCreate):
    id: str
    created_at: datetime
    anonymous_code: Optional[str] = None

    class Config:
        from_attributes = True


def _generate_patient_code(db, project_id: str, reference_date: Optional[datetime] = None) -> str:
    # Même principe que _generate_case_number (voir plus bas) : se base sur le
    # code le plus haut RÉELLEMENT utilisé cette année pour ce projet, pas sur
    # un simple comptage de patients — un comptage se trompe dès qu'un patient
    # a été supprimé.
    year = (reference_date or datetime.utcnow()).year
    prefix = f"PAT-{year}-"
    existing = db.query(Patient.anonymous_code).filter(
        Patient.project_id == project_id,
        Patient.anonymous_code.like(f"{prefix}%"),
    ).all()
    max_seq = 0
    for (code,) in existing:
        if code:
            suffix = code[len(prefix):]
            if suffix.isdigit():
                max_seq = max(max_seq, int(suffix))
    return f"{prefix}{max_seq + 1:04d}"


def _assign_patient_code(db, patient: Patient):
    """Attribue un code anonyme à un patient qui n'en a pas encore — appelé
    juste après la création. Boucle de retry (comme pour les numéros de
    dossier) car deux créations simultanées dans le même projet pourraient
    lire le même "code le plus haut" avant que l'une des deux n'ait validé."""
    if patient.anonymous_code or not patient.project_id:
        return
    for _attempt in range(5):
        patient.anonymous_code = _generate_patient_code(db, patient.project_id)
        try:
            db.commit()
            # Indispensable : après un commit, SQLAlchemy "expire" tous les
            # attributs de l'objet (ils seront rechargés depuis la base à la
            # prochaine lecture) — sans ce refresh immédiat, cette prochaine
            # lecture arrive une fois la session déjà fermée (juste avant que
            # FastAPI ne sérialise la réponse), ce qui plante silencieusement
            # la réponse au navigateur alors que le patient est bel et bien
            # enregistré en base (d'où la confusion : "échec" côté écran,
            # succès côté données).
            db.refresh(patient)
            return
        except IntegrityError:
            db.rollback()
            continue
    # Après 5 échecs (extrêmement improbable) : le patient reste sans code,
    # rien de bloquant — il pourra en recevoir un plus tard (voir migration).


@app.get("/api/patients", response_model=List[PatientOut])
def list_patients(
    response: Response,
    project_id: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    page: Optional[int] = Query(default=None, ge=1),
    page_size: Optional[int] = Query(default=None, ge=1, le=500),
    current_user: User = Depends(get_current_user),
):
    db = SessionLocal()
    try:
        query = select(Patient).order_by(Patient.created_at.desc())
        if project_id:
            query = query.where(Patient.project_id == project_id)
        if search:
            # Chaque mot tapé doit se retrouver dans le prénom OU le nom
            # (ex: "yao b" -> first_name~yao OU last_name~yao, ET first_name~b OU last_name~b).
            for term in search.strip().split():
                like = f"%{term}%"
                query = query.where(or_(Patient.first_name.ilike(like), Patient.last_name.ilike(like)))
            query = query.limit(8)
            return db.execute(query).scalars().all()

        # Pagination optionnelle : si ni page ni page_size ne sont fournis,
        # comportement inchangé (tout renvoyer) pour ne rien casser côté
        # appelants existants. Le total est renvoyé en en-tête plutôt que
        # dans le corps, pour garder une réponse "liste simple" compatible.
        if page or page_size:
            page = page or 1
            page_size = page_size or 50
            total_count = len(db.execute(query).scalars().all())
            response.headers["X-Total-Count"] = str(total_count)
            query = query.limit(page_size).offset((page - 1) * page_size)

        return db.execute(query).scalars().all()
    finally:
        db.close()


def _find_matching_patient_by_name(db, project_id, first_name, last_name):
    """Cherche un patient déjà existant du même projet avec exactement le
    même prénom+nom (insensible casse/espaces) — utilisé pour avertir AVANT
    de créer un doublon en ligne (voir create_patient, ?force=true pour
    passer outre l'avertissement une fois confirmé par l'utilisateur)."""
    fn = (first_name or "").strip().lower()
    ln = (last_name or "").strip().lower()
    if not fn or not ln:
        return None
    for other in db.query(Patient).filter(Patient.project_id == project_id).all():
        if other.first_name.strip().lower() == fn and other.last_name.strip().lower() == ln:
            return other
    return None


def _flag_potential_duplicates(db, new_patient):
    """Cherche d'autres patients du même projet avec le même prénom+nom
    (comparaison insensible à la casse/aux espaces). Ne fusionne jamais rien
    automatiquement — se contente de signaler la paire pour qu'un admin
    tranche. Appelé uniquement pour un patient créé hors-ligne (voir
    create_patient) — la création en ligne, elle, avertit AVANT création via
    _find_matching_patient_by_name, plutôt que de signaler après coup."""
    fn = new_patient.first_name.strip().lower()
    ln = new_patient.last_name.strip().lower()
    others = (
        db.query(Patient)
        .filter(Patient.project_id == new_patient.project_id, Patient.id != new_patient.id)
        .all()
    )
    for other in others:
        if other.first_name.strip().lower() == fn and other.last_name.strip().lower() == ln:
            # Évite de signaler deux fois la même paire (dans un sens ou l'autre)
            existing = (
                db.query(PotentialDuplicate)
                .filter(
                    PotentialDuplicate.project_id == new_patient.project_id,
                    PotentialDuplicate.status == "open",
                )
                .filter(
                    ((PotentialDuplicate.patient_id_a == new_patient.id) & (PotentialDuplicate.patient_id_b == other.id))
                    | ((PotentialDuplicate.patient_id_a == other.id) & (PotentialDuplicate.patient_id_b == new_patient.id))
                )
                .first()
            )
            if not existing:
                db.add(PotentialDuplicate(project_id=new_patient.project_id, patient_id_a=new_patient.id, patient_id_b=other.id))
    db.commit()


def _check_phone_unique(db, project_id, phone, exclude_patient_id=None):
    """Vérifie qu'aucun AUTRE patient de ce même projet n'a déjà ce numéro de
    téléphone. Champ vide : jamais comparé (plusieurs patients peuvent
    légitimement ne pas avoir de téléphone renseigné). Comparaison en
    ignorant espaces/tirets/points, pour absorber les variantes de saisie
    (ex : "07 12 34 56 78" vs "0712345678")."""
    if not phone or not phone.strip():
        return None
    norm_phone = re.sub(r"[\s\-.]", "", phone)
    query = db.query(Patient).filter(Patient.project_id == project_id)
    if exclude_patient_id:
        query = query.filter(Patient.id != exclude_patient_id)
    for other in query.all():
        if other.phone and re.sub(r"[\s\-.]", "", other.phone) == norm_phone:
            return f"Le numéro de téléphone « {phone} » est déjà utilisé par {other.first_name} {other.last_name} dans ce projet."
    return None


@app.post("/api/patients", response_model=PatientOut, status_code=201)
def create_patient(payload: PatientCreate, force: bool = Query(default=False), current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        # Un identifiant fourni signifie que ce patient a été créé hors-ligne
        # (voir 3.5) — si une synchronisation précédente avait déjà réussi
        # mais que la confirmation n'était jamais arrivée jusqu'au navigateur
        # (coupure au mauvais moment), on ne recrée pas un doublon : on
        # renvoie simplement la fiche déjà existante.
        if payload.id:
            existing = db.get(Patient, payload.id)
            if existing:
                return existing

        phone_conflict = _check_phone_unique(db, payload.project_id, payload.phone)
        if phone_conflict:
            raise HTTPException(status_code=409, detail=phone_conflict)

        # Avertit AVANT de créer un doublon en ligne (l'agent a pu ne pas
        # remarquer/utiliser la recherche) — ?force=true après confirmation
        # explicite de l'utilisateur passe outre. Ne s'applique pas à une
        # création hors-ligne (payload.id) : celle-ci est déjà signalée après
        # coup par _flag_potential_duplicates, recherche impossible sans réseau.
        if not payload.id and not force:
            existing_match = _find_matching_patient_by_name(db, payload.project_id, payload.first_name, payload.last_name)
            if existing_match:
                raise HTTPException(status_code=409, detail={
                    "msg": (
                        f"Un patient nommé {existing_match.first_name} {existing_match.last_name} existe déjà "
                        f"dans ce projet (créé le {existing_match.created_at.strftime('%d/%m/%Y')})."
                    ),
                    "existing_patient_id": existing_match.id,
                    "existing_patient_name": f"{existing_match.first_name} {existing_match.last_name}",
                })

        data = payload.model_dump()
        if not data.get("id"):
            data.pop("id", None)
        patient = Patient(**data)
        db.add(patient)
        db.commit()
        db.refresh(patient)
        _assign_patient_code(db, patient)
        # Un patient créé hors-ligne n'a pas pu être vérifié contre la base
        # complète au moment de sa saisie (recherche impossible sans réseau)
        # — on vérifie donc maintenant, après coup, et on signale plutôt que
        # de fusionner automatiquement (risque de mélanger deux vrais patients).
        if payload.id:
            _flag_potential_duplicates(db, patient)
            db.refresh(patient)
        return patient
    finally:
        db.close()


@app.get("/api/patients/{patient_id}", response_model=PatientOut)
def get_patient(patient_id: str, current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        patient = db.get(Patient, patient_id)
        if not patient:
            raise HTTPException(status_code=404, detail="Patient introuvable")
        return patient
    finally:
        db.close()


class PatientUpdate(BaseModel):
    first_name: str
    last_name: str
    date_of_birth: Optional[date] = None
    sex: Optional[str] = None
    phone: Optional[str] = None


@app.put("/api/patients/{patient_id}", response_model=PatientOut)
def update_patient(patient_id: str, payload: PatientUpdate, current_user: User = Depends(get_current_user)):
    """Met à jour les infos d'identité d'un patient existant — utilisé
    notamment quand on réutilise une fiche retrouvée par la recherche
    (téléphone qui a changé, faute d'orthographe corrigée, etc.)."""
    db = SessionLocal()
    try:
        patient = db.get(Patient, patient_id)
        if not patient:
            raise HTTPException(status_code=404, detail="Patient introuvable")

        phone_conflict = _check_phone_unique(db, patient.project_id, payload.phone, exclude_patient_id=patient_id)
        if phone_conflict:
            raise HTTPException(status_code=409, detail=phone_conflict)

        patient.first_name = payload.first_name
        patient.last_name = payload.last_name
        patient.date_of_birth = payload.date_of_birth
        patient.sex = payload.sex
        patient.phone = payload.phone
        db.commit()
        db.refresh(patient)
        return patient
    finally:
        db.close()


@app.delete("/api/patients/{patient_id}")
def delete_patient(patient_id: str, request: Request, current_user: User = Depends(get_current_user)):
    """Supprime un patient seulement s'il n'a aucune soumission ni aucun cas
    de suivi rattaché — sinon on perdrait de vraies données cliniques.
    Couvre le cas d'une fiche créée par erreur (doublon, mauvais clic)."""
    db = SessionLocal()
    try:
        patient = db.get(Patient, patient_id)
        if not patient:
            raise HTTPException(status_code=404, detail="Patient introuvable")
        submission_count = db.query(Submission).filter(Submission.patient_id == patient_id).count()
        case_count = db.query(Case).filter(Case.patient_id == patient_id).count()
        if submission_count > 0 or case_count > 0:
            raise HTTPException(
                status_code=409,
                detail=f"Ce patient a {submission_count} soumission(s) et {case_count} cas de suivi — impossible de le supprimer sans perdre des données.",
            )
        patient_name = f"{patient.first_name} {patient.last_name}"
        patient_project_id = patient.project_id
        db.delete(patient)
        db.commit()
        log_audit(db, request, user=current_user, action="delete", entity_type="patient",
                  entity_id=patient_id, project_id=patient_project_id, details=f"Patient supprimé : {patient_name}")
        return {"status": "ok"}
    finally:
        db.close()


@app.get("/api/projects/{project_id}/potential-duplicates")
def list_potential_duplicates(project_id: str, current_user: User = Depends(get_current_user)):
    """Paires de patients signalées comme possiblement identiques (même
    prénom+nom), détectées après la synchronisation d'un patient créé
    hors-ligne. Jamais fusionné automatiquement — à trancher manuellement."""
    db = SessionLocal()
    try:
        rows = (
            db.query(PotentialDuplicate)
            .filter(PotentialDuplicate.project_id == project_id, PotentialDuplicate.status == "open")
            .order_by(PotentialDuplicate.detected_at.desc())
            .all()
        )
        result = []
        for r in rows:
            pa = db.get(Patient, r.patient_id_a)
            pb = db.get(Patient, r.patient_id_b)
            if not pa or not pb:
                continue
            result.append({
                "id": r.id,
                "detected_at": r.detected_at,
                "patient_a": {"id": pa.id, "first_name": pa.first_name, "last_name": pa.last_name, "date_of_birth": pa.date_of_birth, "phone": pa.phone, "created_at": pa.created_at},
                "patient_b": {"id": pb.id, "first_name": pb.first_name, "last_name": pb.last_name, "date_of_birth": pb.date_of_birth, "phone": pb.phone, "created_at": pb.created_at},
            })
        return result
    finally:
        db.close()


@app.post("/api/potential-duplicates/{dup_id}/dismiss")
def dismiss_potential_duplicate(dup_id: str, request: Request, current_user: User = Depends(get_current_user)):
    """Marque une paire comme vérifiée (pas un vrai doublon, ou déjà traitée
    manuellement) — ne supprime ni ne fusionne aucun patient, se contente de
    la retirer de la liste à traiter."""
    db = SessionLocal()
    try:
        dup = db.get(PotentialDuplicate, dup_id)
        if not dup:
            raise HTTPException(status_code=404, detail="Signalement introuvable")
        dup.status = "dismissed"
        db.commit()
        log_audit(db, request, user=current_user, action="update", entity_type="potential_duplicate",
                  entity_id=dup_id, project_id=dup.project_id, details="Doublon potentiel traité/écarté")
        return {"status": "ok"}
    finally:
        db.close()


# --- Schémas & endpoints Formulaires (création/édition désormais possibles) ---

class FormCreate(BaseModel):
    project_id: str
    name: str
    form_type: str = "survey"
    definition: Dict[str, Any]


class FormUpdate(BaseModel):
    name: str
    form_type: Optional[str] = None
    definition: Dict[str, Any]


@app.get("/api/forms")
def list_forms(
    project_id: Optional[str] = Query(default=None),
    current_user: User = Depends(get_current_user),
):
    db = SessionLocal()
    try:
        query = db.query(Form).filter(Form.is_active == True)  # noqa: E712
        if project_id:
            query = query.filter(Form.project_id == project_id)
        forms = query.all()
        return [
            {"id": f.id, "name": f.name, "form_type": f.form_type, "project_id": f.project_id}
            for f in forms
        ]
    finally:
        db.close()


@app.get("/api/forms/{form_id}")
def get_form(form_id: str, current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        form = db.get(Form, form_id)
        if not form:
            raise HTTPException(status_code=404, detail="Formulaire introuvable")
        return {
            "id": form.id,
            "name": form.name,
            "form_type": form.form_type,
            "project_id": form.project_id,
            "definition": form.definition,
            "public_link_enabled": bool(form.public_link_enabled),
        }
    finally:
        db.close()


@app.post("/api/forms", status_code=201)
def create_form(payload: FormCreate, current_user: User = Depends(require_admin)):
    db = SessionLocal()
    try:
        form = Form(
            project_id=payload.project_id,
            name=payload.name,
            form_type=payload.form_type,
            definition=payload.definition,
        )
        db.add(form)
        db.commit()
        db.refresh(form)
        return {"id": form.id, "name": form.name}
    finally:
        db.close()


@app.put("/api/forms/{form_id}")
def update_form(form_id: str, payload: FormUpdate, current_user: User = Depends(require_admin)):
    db = SessionLocal()
    try:
        form = db.get(Form, form_id)
        if not form:
            raise HTTPException(status_code=404, detail="Formulaire introuvable")
        form.name = payload.name
        if payload.form_type:
            form.form_type = payload.form_type
        form.definition = payload.definition
        db.commit()
        return {"id": form.id, "name": form.name}
    finally:
        db.close()


def _generate_access_code(length: int = 8) -> str:
    """Code court, lisible sur un clavier de téléphone : majuscules + chiffres
    seulement, sans caractères ambigus (pas de 0/O ni de 1/I/l)."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


class PublicLinkToggle(BaseModel):
    enabled: bool


@app.get("/api/forms/{form_id}/public-link")
def get_public_link_status(form_id: str, current_user: User = Depends(require_admin)):
    """État actuel du lien communautaire (point 7) — jamais le code lui-même
    (on ne stocke que son hash, comme un mot de passe) : seulement s'il est
    activé et si un code a déjà été généré au moins une fois."""
    db = SessionLocal()
    try:
        form = db.get(Form, form_id)
        if not form:
            raise HTTPException(status_code=404, detail="Formulaire introuvable")
        return {"enabled": bool(form.public_link_enabled), "has_code": bool(form.public_access_code_hash)}
    finally:
        db.close()


@app.put("/api/forms/{form_id}/public-link")
def toggle_public_link(form_id: str, payload: PublicLinkToggle, request: Request, current_user: User = Depends(require_admin)):
    """Active/désactive le lien communautaire pour ce formulaire. À
    l'activation, si aucun code n'existe encore, un premier code est généré
    et renvoyé EN CLAIR une seule fois (impossible à récupérer ensuite — voir
    /regenerate pour en obtenir un nouveau)."""
    db = SessionLocal()
    try:
        form = db.get(Form, form_id)
        if not form:
            raise HTTPException(status_code=404, detail="Formulaire introuvable")
        form.public_link_enabled = payload.enabled
        plain_code = None
        if payload.enabled and not form.public_access_code_hash:
            plain_code = _generate_access_code()
            form.public_access_code_hash = hash_password(plain_code)
        db.commit()
        log_audit(db, request, user=current_user, action="update", entity_type="form",
                  entity_id=form_id, project_id=form.project_id,
                  details=f"Lien communautaire {'activé' if payload.enabled else 'désactivé'} pour « {form.name} »")
        return {"enabled": bool(form.public_link_enabled), "has_code": bool(form.public_access_code_hash), "code": plain_code}
    finally:
        db.close()


@app.post("/api/forms/{form_id}/public-link/regenerate")
def regenerate_public_link_code(form_id: str, request: Request, current_user: User = Depends(require_admin)):
    """Régénère le code d'accès — invalide IMMÉDIATEMENT tous les appareils
    déjà débloqués avec l'ancien code, puisque chaque requête publique
    revérifie contre le hash actuel. Utile si le code a fui ou qu'un agent
    quitte l'équipe."""
    db = SessionLocal()
    try:
        form = db.get(Form, form_id)
        if not form:
            raise HTTPException(status_code=404, detail="Formulaire introuvable")
        plain_code = _generate_access_code()
        form.public_access_code_hash = hash_password(plain_code)
        db.commit()
        log_audit(db, request, user=current_user, action="update", entity_type="form",
                  entity_id=form_id, project_id=form.project_id,
                  details=f"Code du lien communautaire régénéré pour « {form.name} »")
        return {"enabled": bool(form.public_link_enabled), "has_code": True, "code": plain_code}
    finally:
        db.close()


def _fields_of(form: Form) -> list:
    """Tous les champs du formulaire, toutes sections/groupes confondus, à
    plat — pratique pour construire les colonnes d'export sans se soucier
    des sections."""
    fields = []
    for section in form.definition.get("sections") or []:
        fields.extend(section.get("fields", []))
    return fields


@app.get("/api/forms/{form_id}/export-template")
def export_form_template(form_id: str, current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        form = db.get(Form, form_id)
        if not form:
            raise HTTPException(status_code=404, detail="Formulaire introuvable")
        content = build_template_bytes(
            form.name,
            form.definition.get("sections") or [],
            form.definition.get("choice_lists"),
        )
        filename = f"{slugify_py(form.name)}_structure.xlsx"
        return StreamingResponse(
            BytesIO(content),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    finally:
        db.close()


@app.post("/api/forms/import", status_code=201)
async def import_form(
    project_id: str = FastAPIFormField(...),
    name: str = FastAPIFormField(...),
    file: UploadFile = File(...),
    current_user: User = Depends(require_admin),
):
    content = await file.read()
    try:
        sections, choice_lists = parse_spreadsheet_to_sections(content, file.filename or "formulaire.xlsx")
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Impossible de lire le fichier : {e}")

    total_fields = sum(len(s.get("fields", [])) for s in sections)
    if total_fields == 0:
        raise HTTPException(
            status_code=422,
            detail="Aucune question valide trouvée (vérifie que la colonne 'label' est bien remplie).",
        )

    definition = {"sections": sections}
    if choice_lists:
        definition["choice_lists"] = choice_lists

    structure_errors = validate_form_definition(definition)
    if structure_errors:
        raise HTTPException(status_code=422, detail=" | ".join(structure_errors))

    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Projet introuvable")

        form = Form(project_id=project_id, name=name, form_type="survey", definition=definition)
        db.add(form)
        db.commit()
        db.refresh(form)
        return {"id": form.id, "name": form.name, "questions_imported": total_fields}
    finally:
        db.close()


@app.get("/api/forms/{form_id}/export")
def export_form_data(
    request: Request,
    form_id: str,
    format: str = Query(default="csv"),
    start: Optional[date] = Query(default=None),
    end: Optional[date] = Query(default=None),
    current_user: User = Depends(get_current_user),
):
    if format not in ("csv", "xlsx"):
        raise HTTPException(status_code=400, detail=f"Format d'export non reconnu : '{format}' (csv ou xlsx attendu).")
    db = SessionLocal()
    try:
        form = db.get(Form, form_id)
        if not form:
            raise HTTPException(status_code=404, detail="Formulaire introuvable")

        query = db.query(Submission).filter(Submission.form_id == form_id)
        if start:
            query = query.filter(Submission.collected_at >= datetime.combine(start, datetime.min.time()))
        if end:
            query = query.filter(Submission.collected_at <= datetime.combine(end, datetime.max.time()))
        submissions = query.order_by(Submission.collected_at.asc()).all()

        fields = _fields_of(form)
        field_ids = [f["id"] for f in fields if f.get("type") != "note"]
        # Champs photo/audio/vidéo/fichier : la valeur brute est un identifiant
        # de fichier (Media.id), illisible tel quel dans un tableur — on le
        # transforme en adresse complète, cliquable une fois connecté à l'app
        # (le fichier reste protégé, jamais public).
        media_field_ids = {f["id"] for f in fields if f.get("type") in ("image", "audio", "video", "file")}
        base_url = str(request.base_url).rstrip("/")

        # Nombre de dépistages de CE patient sur la période exportée, et dates
        # de son premier/dernier — calculé une fois sur l'ensemble déjà filtré
        # par start/end, puis répété sur chacune de ses lignes (une par
        # dépistage). Anciennement dans l'export "personnes" : déplacé ici,
        # qui est le bon endroit pour suivre un historique dans le temps —
        # "personnes" reste un instantané, pas un historique.
        per_patient_dates: Dict[str, list] = {}
        for s in submissions:
            per_patient_dates.setdefault(s.patient_id, []).append(s.collected_at)

        rows = []
        for s in submissions:
            patient = db.get(Patient, s.patient_id)
            patient_dates = per_patient_dates[s.patient_id]
            # Âge (à la date de cette soumission précise, pas "aujourd'hui")
            # et sexe : seules données patient incluses dans cet export — une
            # exception délibérée au principe général "aucune donnée
            # personnelle du patient dans les exports" (anonymat + inutile à
            # l'analyse), car ces deux-là sont au contraire essentielles à
            # l'analyse statistique et ne sont pas identifiantes.
            row = {
                "id_soumission": s.id,
                "patient": f"{patient.first_name} {patient.last_name}" if patient else "",
                # Code anonyme affiché CÔTE À CÔTE avec le nom (pas à sa place) :
                # à l'utilisateur de retirer la colonne "patient" lui-même selon
                # la situation (ex: avant l'envoi à un bailleur), en connaissance
                # de cause plutôt que par une case "anonymiser" automatique.
                "code_anonyme": patient.anonymous_code if patient else None,
                "date": s.collected_at.strftime("%Y-%m-%d %H:%M"),
                "age": _age_at(patient.date_of_birth, s.collected_at.date()) if patient else None,
                "sexe": patient.sex if patient else None,
                "nb_depistages_periode": len(patient_dates),
                "premier_depistage": min(patient_dates).strftime("%Y-%m-%d"),
                "dernier_depistage": max(patient_dates).strftime("%Y-%m-%d"),
            }
            for fid in field_ids:
                # None = question masquée par une condition d'affichage (non applicable),
                # à distinguer d'une réponse laissée vide alors qu'elle était posée.
                # La conversion en "N/A" est faite plus loin (build_data_export_bytes),
                # pour que ça marche aussi bien sur les champs simples que sur les
                # champs "choix multiples" (éclatés en plusieurs colonnes).
                value = s.responses.get(fid)
                if value and fid in media_field_ids:
                    value = f"{base_url}/api/media/{value}"
                row[fid] = value
            rows.append(row)

        content, media_type, ext = build_data_export_bytes(form.name, fields, rows, format)
        filename = f"{slugify_py(form.name)}.{ext}"
        log_audit(db, request, user=current_user, action="export", entity_type="form",
                  entity_id=form_id, project_id=form.project_id,
                  details=f"Export ({format}) des données du formulaire '{form.name}' — {len(rows)} ligne(s)")
        return StreamingResponse(
            BytesIO(content),
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    finally:
        db.close()


@app.get("/api/forms/{form_id}/trend")
def get_form_trend(
    form_id: str,
    start: Optional[date] = Query(default=None),
    end: Optional[date] = Query(default=None),
    current_user: User = Depends(get_current_user),
):
    """Dépistages (soumissions) vs personnes distinctes, par mois — pour voir
    une tendance sans compter la même personne plusieurs fois si elle a été
    dépistée plus d'une fois sur la période."""
    db = SessionLocal()
    try:
        form = db.get(Form, form_id)
        if not form:
            raise HTTPException(status_code=404, detail="Formulaire introuvable")

        query = db.query(Submission).filter(Submission.form_id == form_id)
        if start:
            query = query.filter(Submission.collected_at >= datetime.combine(start, datetime.min.time()))
        if end:
            query = query.filter(Submission.collected_at <= datetime.combine(end, datetime.max.time()))
        submissions = query.order_by(Submission.collected_at.asc()).all()

        buckets: Dict[str, Dict[str, Any]] = {}
        for s in submissions:
            key = s.collected_at.strftime("%Y-%m")
            bucket = buckets.setdefault(key, {"submissions": 0, "patients": set()})
            bucket["submissions"] += 1
            bucket["patients"].add(s.patient_id)

        overall_patients = {s.patient_id for s in submissions}

        return {
            "total_submissions": len(submissions),
            "distinct_patients": len(overall_patients),
            "by_month": [
                {"period": k, "submissions": v["submissions"], "distinct_patients": len(v["patients"])}
                for k, v in sorted(buckets.items())
            ],
        }
    finally:
        db.close()


@app.get("/api/forms/{form_id}/status-change")
def detect_status_change(
    form_id: str,
    field: str = Query(...),
    target_values: List[str] = Query(...),
    format: Optional[str] = Query(default=None),
    current_user: User = Depends(get_current_user),
):
    """Repère les patients ayant eu, pour ce champ, une valeur hors de
    `target_values` avant d'en avoir une dedans plus tard (ex : négatif puis
    positif OU suspect_cancer) — plusieurs valeurs cibles à la fois, pour ne
    pas rater une catégorie proche (ex : 'positif' et 'suspect_cancer' comptent
    tous les deux comme préoccupants)."""
    db = SessionLocal()
    try:
        form = db.get(Form, form_id)
        if not form:
            raise HTTPException(status_code=404, detail="Formulaire introuvable")

        valid_field_ids = {f["id"] for f in _fields_of(form)}
        if field not in valid_field_ids:
            raise HTTPException(status_code=422, detail=f"Le champ '{field}' n'existe pas dans ce formulaire.")

        target_set = set(target_values)
        submissions = (
            db.query(Submission)
            .filter(Submission.form_id == form_id)
            .order_by(Submission.collected_at.asc())
            .all()
        )

        by_patient: Dict[str, list] = {}
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
                    patient = db.get(Patient, patient_id)
                    results.append({
                        "patient_id": patient_id,
                        "patient_name": f"{patient.first_name} {patient.last_name}" if patient else "Patient supprimé",
                        "first_other_value_at": seen_other_value_at,
                        "target_value_at": s.collected_at,
                        "matched_value": str(value),
                    })
                    break

        if format in ("csv", "xlsx"):
            rows = [{
                "patient": r["patient_name"],
                "premiere_valeur_hors_cible_le": r["first_other_value_at"].strftime("%Y-%m-%d"),
                "devenu_valeur_cible_le": r["target_value_at"].strftime("%Y-%m-%d"),
                "valeur_declenchante": r["matched_value"],
            } for r in results]
            columns = ["patient", "premiere_valeur_hors_cible_le", "devenu_valeur_cible_le", "valeur_declenchante"]
            content, media_type, ext = build_patient_export_bytes(rows, columns, format)
            filename = f"detecteur_{slugify_py(field)}.{ext}"
            return StreamingResponse(
                BytesIO(content), media_type=media_type,
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

        return {"field": field, "target_values": target_values, "matches": results}
    finally:
        db.close()


def _age_at(dob, reference_date) -> Optional[int]:
    if not dob:
        return None
    return reference_date.year - dob.year - ((reference_date.month, reference_date.day) < (dob.month, dob.day))


@app.get("/api/forms/{form_id}/export-patients")
def export_patients_data(
    form_id: str,
    format: str = Query(default="csv"),
    start: Optional[date] = Query(default=None),
    end: Optional[date] = Query(default=None),
    status_field: Optional[str] = Query(default=None),
    status_target_values: Optional[List[str]] = Query(default=None),
    current_user: User = Depends(get_current_user),
):
    """Export anonymisé pour analyse statistique/épidémiologique (SPSS, Epi
    Info...) : UNE LIGNE PAR PERSONNE (pas par dépistage), sans nom ni
    téléphone, pour éviter qu'une personne dépistée plusieurs fois ne soit
    comptée plusieurs fois dans un test statistique.

    Cette ligne porte aussi les RÉPONSES du formulaire — sinon cet export ne
    permettait que de compter des personnes, jamais de les analyser. Comme
    une même personne peut avoir répondu différemment à des dépistages
    successifs, ce sont les réponses de sa soumission LA PLUS RÉCENTE qui
    sont retenues (cohérent avec l'âge, déjà calculé à cette même date). Le
    nombre de dépistages et les dates première/dernière sont volontairement
    PAS ici — voir l'export "dépistages" (une ligne par événement), qui les
    porte désormais, plus adapté pour suivre l'historique dans le temps."""
    db = SessionLocal()
    try:
        form = db.get(Form, form_id)
        if not form:
            raise HTTPException(status_code=404, detail="Formulaire introuvable")

        query = db.query(Submission).filter(Submission.form_id == form_id)
        if start:
            query = query.filter(Submission.collected_at >= datetime.combine(start, datetime.min.time()))
        if end:
            query = query.filter(Submission.collected_at <= datetime.combine(end, datetime.max.time()))
        submissions = query.order_by(Submission.collected_at.asc()).all()

        target_set = set(status_target_values) if status_target_values else None
        fields = _fields_of(form)

        by_patient: Dict[str, list] = {}
        for s in submissions:
            by_patient.setdefault(s.patient_id, []).append(s)

        rows = []
        for patient_id, subs in by_patient.items():
            subs.sort(key=lambda s: s.collected_at)
            patient = db.get(Patient, patient_id)
            last_sub = subs[-1]
            row = {
                "identifiant": patient_id,
                "sexe": (patient.sex if patient else "") or "",
                "age": _age_at(patient.date_of_birth, last_sub.collected_at) if patient else "",
            }
            if status_field and target_set:
                last_value = last_sub.responses.get(status_field)
                row["dernier_resultat_connu"] = "N/A" if last_value is None else (last_value if last_value != "" else "")
                ever_target = any(
                    str(s.responses.get(status_field)) in target_set
                    for s in subs
                    if s.responses.get(status_field) not in (None, "")
                )
                row["a_ete_positif_au_moins_une_fois"] = 1 if ever_target else 0
            # Réponses de la soumission la plus récente, sous leurs field_id
            # d'origine — c'est _expand_row_and_columns (via
            # build_patient_data_export_bytes) qui les éclate ensuite en
            # colonnes, exactement comme pour l'export "dépistages".
            row.update(last_sub.responses)
            rows.append(row)

        fixed_columns = ["identifiant", "sexe", "age"]
        fixed_labels = {"identifiant": "Identifiant", "sexe": "Sexe", "age": "Âge"}
        if status_field and target_set:
            fixed_columns += ["dernier_resultat_connu", "a_ete_positif_au_moins_une_fois"]
            fixed_labels["dernier_resultat_connu"] = "Dernier résultat connu"
            fixed_labels["a_ete_positif_au_moins_une_fois"] = "A été positif au moins une fois"

        content, media_type, ext = build_patient_data_export_bytes(fields, rows, fixed_columns, fixed_labels, format)
        filename = f"{slugify_py(form.name)}_personnes.{ext}"
        return StreamingResponse(
            BytesIO(content),
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    finally:
        db.close()


# --- Endpoints soumissions (consultations remplies) ---

class SubmissionCreate(BaseModel):
    form_id: str
    patient_id: str
    case_id: Optional[str] = None
    responses: Dict[str, Any]
    collected_at: Optional[datetime] = None
    idempotency_key: Optional[str] = None  # voir Submission.idempotency_key


def _find_referral_field_id(form_definition):
    """Cherche le champ S/N-S (Suivi/Non-Suivi) dans la définition d'un
    formulaire — un seul par formulaire (imposé côté créateur de formulaire,
    voir form_builder.html)."""
    for section in (form_definition or {}).get("sections", []):
        for f in section.get("fields", []):
            if f.get("type") == "boolean" and f.get("triggers_referral"):
                return f["id"]
    return None


def _check_referral_suggestion(db, form, patient_id, processed_responses):
    """Si ce formulaire a un champ S/N-S et que sa réponse est "Oui", et que
    ce patient n'a pas déjà un dossier de suivi actif dans ce projet,
    renvoie de quoi proposer d'en créer un. Ne crée jamais rien lui-même —
    se contente de signaler, la décision reste entièrement manuelle."""
    field_id = _find_referral_field_id(form.definition)
    if not field_id or not processed_responses.get(field_id):
        return None
    existing_case = (
        db.query(Case)
        .filter(Case.patient_id == patient_id, Case.project_id == form.project_id, Case.status == "active")
        .first()
    )
    if existing_case:
        return None
    return {"suggest_referral": True, "patient_id": patient_id}


@app.get("/api/forms/{form_id}/referral-candidates")
def list_referral_candidates(form_id: str, current_user: User = Depends(get_current_user)):
    """Soumissions où le champ S/N-S vaut "Oui" mais dont le patient n'a
    toujours aucun dossier de suivi actif — le filet de sécurité pour
    rattraper les cas où la proposition affichée juste après l'enregistrement
    aurait été ignorée ou fermée par erreur."""
    db = SessionLocal()
    try:
        form = db.get(Form, form_id)
        if not form:
            raise HTTPException(status_code=404, detail="Formulaire introuvable")
        field_id = _find_referral_field_id(form.definition)
        if not field_id:
            return []
        submissions = db.query(Submission).filter(Submission.form_id == form_id).all()
        result = []
        for s in submissions:
            if not s.responses.get(field_id):
                continue
            existing_case = (
                db.query(Case)
                .filter(Case.patient_id == s.patient_id, Case.project_id == form.project_id, Case.status == "active")
                .first()
            )
            if existing_case:
                continue
            patient = db.get(Patient, s.patient_id)
            if not patient:
                continue
            result.append({
                "submission_id": s.id,
                "patient_id": patient.id,
                "patient_name": f"{patient.first_name} {patient.last_name}",
                "collected_at": s.collected_at,
            })
        return result
    finally:
        db.close()


def _check_unique_fields(db, form, responses, exclude_submission_id=None):
    """Vérifie qu'aucune AUTRE soumission de ce même formulaire ne porte déjà
    la même valeur pour un champ marqué "valeur unique" dans le constructeur
    (ex : numéro de téléphone, numéro de fiche). Comparaison insensible à la
    casse/aux espaces. Champ vide : jamais comparé (plusieurs soumissions
    peuvent légitimement laisser ce champ non renseigné). Portée volontaire :
    au sein de CE formulaire seulement, pas de tous les formulaires du projet."""
    errors = []
    unique_fields = [
        f for f in _fields_of(form)
        if isinstance(f.get("validation"), dict) and f["validation"].get("unique") and f.get("type") != "note"
    ]
    if not unique_fields:
        return errors

    others = db.query(Submission).filter(Submission.form_id == form.id).all()
    for field in unique_fields:
        fid = field["id"]
        value = responses.get(fid)
        if value is None or str(value).strip() == "":
            continue
        norm_value = str(value).strip().lower()
        for other in others:
            if exclude_submission_id and other.id == exclude_submission_id:
                continue
            other_value = (other.responses or {}).get(fid)
            if other_value is None or str(other_value).strip() == "":
                continue
            if str(other_value).strip().lower() == norm_value:
                label = field.get("label", fid)
                errors.append(f"{label} : la valeur « {value} » est déjà utilisée par une autre soumission de ce formulaire.")
                break
    return errors


def _check_age_consistency(form, responses, patient, reference_date):
    """Si un champ numérique du formulaire est marqué "comparer à la date de
    naissance", vérifie qu'il correspond (± 1 an, pour absorber les
    approximations d'anniversaire) à l'âge réel du patient à la date de la
    visite. Sans ce contrôle, un âge saisi à la main dans le formulaire peut
    silencieusement contredire la date de naissance déjà connue du patient
    (remontée terrain)."""
    errors = []
    if not patient or not patient.date_of_birth:
        return errors
    real_age = _age_at(patient.date_of_birth, reference_date)
    if real_age is None:
        return errors
    for field in _fields_of(form):
        if not (isinstance(field.get("validation"), dict) and field["validation"].get("check_against_dob")):
            continue
        fid = field["id"]
        value = responses.get(fid)
        if value is None or str(value).strip() == "":
            continue
        try:
            entered_age = float(value)
        except (TypeError, ValueError):
            continue  # déjà signalé ailleurs comme "doit être un nombre"
        if abs(entered_age - real_age) > 1:
            label = field.get("label", fid)
            errors.append(
                f"{label} ({value} ans) ne correspond pas à la date de naissance du patient "
                f"(âge réel à la date de cette visite : {real_age} ans). Vérifie la date de naissance ou corrige cet âge."
            )
    return errors


@app.post("/api/submissions", status_code=201)
def create_submission(payload: SubmissionCreate, current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        # Idempotence : si ce jeton a déjà servi (double-clic, requête rejouée
        # par le réseau...), on renvoie le résultat déjà enregistré au lieu
        # d'en créer un second.
        if payload.idempotency_key:
            existing = db.query(Submission).filter(Submission.idempotency_key == payload.idempotency_key).first()
            if existing:
                return {
                    "id": existing.id,
                    "responses": existing.responses,
                    "collected_at": existing.collected_at,
                    "alerts": [],
                    "already_recorded": True,
                }

        form = db.get(Form, payload.form_id)
        if not form:
            raise HTTPException(status_code=404, detail="Formulaire introuvable")
        patient = db.get(Patient, payload.patient_id)
        if not patient:
            raise HTTPException(status_code=404, detail="Patient introuvable")
        if patient.project_id != form.project_id:
            raise HTTPException(
                status_code=409,
                detail="Ce patient et ce formulaire n'appartiennent pas au même projet.",
            )
        case = None
        if payload.case_id:
            case = db.get(Case, payload.case_id)
            if not case:
                raise HTTPException(status_code=404, detail="Cas de suivi introuvable")
            if case.project_id != form.project_id:
                raise HTTPException(
                    status_code=409,
                    detail="Ce cas de suivi n'appartient pas au même projet que le formulaire.",
                )

        processed_responses, errors, alerts = validate_and_process_submission(
            form.definition, payload.responses
        )
        if errors:
            raise HTTPException(status_code=422, detail=" | ".join(errors))

        unique_errors = _check_unique_fields(db, form, processed_responses)
        if unique_errors:
            raise HTTPException(status_code=422, detail=" | ".join(unique_errors))

        age_errors = _check_age_consistency(form, processed_responses, patient, payload.collected_at or datetime.utcnow())
        if age_errors:
            raise HTTPException(status_code=422, detail=" | ".join(age_errors))

        submission = Submission(
            form_id=form.id,
            patient_id=patient.id,
            case_id=payload.case_id,
            responses=processed_responses,
            collected_at=payload.collected_at or datetime.utcnow(),
            created_by=current_user.id,
            idempotency_key=payload.idempotency_key,
        )
        db.add(submission)
        try:
            db.commit()
        except IntegrityError:
            # Course rare : une requête jumelle a inséré le même jeton entre
            # notre vérification ci-dessus et cet instant -> on renvoie la
            # sienne plutôt que de faire échouer l'enregistrement.
            db.rollback()
            existing = db.query(Submission).filter(Submission.idempotency_key == payload.idempotency_key).first()
            if existing:
                return {
                    "id": existing.id, "responses": existing.responses,
                    "collected_at": existing.collected_at, "alerts": [], "already_recorded": True,
                }
            raise
        db.refresh(submission)
        referral = _check_referral_suggestion(db, form, patient.id, processed_responses)
        return {
            "id": submission.id,
            "responses": submission.responses,
            "collected_at": submission.collected_at,
            "alerts": alerts,
            "referral": referral,
        }
    finally:
        db.close()


@app.get("/api/submissions/{submission_id}")
def get_submission(submission_id: str, current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        submission = db.get(Submission, submission_id)
        if not submission:
            raise HTTPException(status_code=404, detail="Soumission introuvable")
        form = db.get(Form, submission.form_id)
        patient = db.get(Patient, submission.patient_id)
        return {
            "id": submission.id,
            "form_id": submission.form_id,
            "form_name": form.name if form else None,
            "form_definition": form.definition if form else None,
            "patient_id": submission.patient_id,
            "patient_name": f"{patient.first_name} {patient.last_name}" if patient else None,
            "responses": submission.responses,
            "collected_at": submission.collected_at,
            "updated_at": submission.updated_at,
        }
    finally:
        db.close()


class SubmissionUpdate(BaseModel):
    responses: Dict[str, Any]
    collected_at: Optional[datetime] = None
    expected_updated_at: Optional[datetime] = None  # concurrence : valeur vue au chargement


@app.put("/api/submissions/{submission_id}")
def update_submission(submission_id: str, payload: SubmissionUpdate, current_user: User = Depends(get_current_user)):
    """Corrige les réponses d'une soumission déjà enregistrée (erreur de
    saisie de l'opérateur) — ne crée jamais un nouvel enregistrement, modifie
    celui-ci en place. Pour une nouvelle visite, voir POST /api/submissions."""
    db = SessionLocal()
    try:
        submission = db.get(Submission, submission_id)
        if not submission:
            raise HTTPException(status_code=404, detail="Soumission introuvable")

        if payload.expected_updated_at and submission.updated_at:
            # Marge de 1s : évite les faux conflits dus à l'arrondi des microsecondes
            # entre ce que le navigateur a reçu en JSON et ce que la base a stocké.
            if submission.updated_at != payload.expected_updated_at:
                raise HTTPException(
                    status_code=409,
                    detail="Quelqu'un d'autre a modifié cette soumission entre-temps. Recharge la page pour voir la version actuelle avant de corriger à nouveau.",
                )

        form = db.get(Form, submission.form_id)
        if not form:
            raise HTTPException(status_code=404, detail="Formulaire introuvable")

        processed_responses, errors, alerts = validate_and_process_submission(
            form.definition, payload.responses
        )
        if errors:
            raise HTTPException(status_code=422, detail=" | ".join(errors))

        unique_errors = _check_unique_fields(db, form, processed_responses, exclude_submission_id=submission_id)
        if unique_errors:
            raise HTTPException(status_code=422, detail=" | ".join(unique_errors))

        patient = db.get(Patient, submission.patient_id)
        age_errors = _check_age_consistency(form, processed_responses, patient, payload.collected_at or submission.collected_at)
        if age_errors:
            raise HTTPException(status_code=422, detail=" | ".join(age_errors))

        submission.responses = processed_responses
        if payload.collected_at:
            submission.collected_at = payload.collected_at
        submission.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(submission)
        referral = _check_referral_suggestion(db, form, submission.patient_id, processed_responses)
        return {
            "id": submission.id,
            "responses": submission.responses,
            "collected_at": submission.collected_at,
            "alerts": alerts,
            "referral": referral,
        }
    finally:
        db.close()


@app.delete("/api/submissions/{submission_id}")
def delete_submission(submission_id: str, request: Request, current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        submission = db.get(Submission, submission_id)
        if not submission:
            raise HTTPException(status_code=404, detail="Soumission introuvable")
        form = db.get(Form, submission.form_id)
        db.delete(submission)
        db.commit()
        log_audit(db, request, user=current_user, action="delete", entity_type="submission",
                  entity_id=submission_id, project_id=(form.project_id if form else None),
                  details=f"Soumission supprimée (formulaire : {form.name if form else '—'})")
        return {"status": "ok"}
    finally:
        db.close()


@app.get("/api/patients/{patient_id}/submissions")
def list_patient_submissions(patient_id: str, current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        patient = db.get(Patient, patient_id)
        if not patient:
            raise HTTPException(status_code=404, detail="Patient introuvable")

        submissions = (
            db.query(Submission)
            .filter(Submission.patient_id == patient_id)
            .order_by(Submission.collected_at.desc())
            .all()
        )

        result = []
        for s in submissions:
            form = db.get(Form, s.form_id)
            field_types = {}
            if form:
                for section in (form.definition or {}).get("sections", []):
                    for f in section.get("fields", []):
                        field_types[f["id"]] = f.get("type")
            result.append({
                "id": s.id,
                "form_name": form.name if form else "Formulaire supprimé",
                "form_type": form.form_type if form else None,
                "responses": s.responses,
                "collected_at": s.collected_at,
                # collected_at n'a qu'une date choisie par l'agent (le champ
                # "Date de cet événement" du formulaire n'a pas d'heure) —
                # toujours minuit par défaut. created_at, lui, est l'horodatage
                # technique réel de l'enregistrement, utilisé par l'écran
                # (history.html) pour afficher une heure qui corresponde à la
                # réalité, plutôt que ce minuit de convention.
                "created_at": s.created_at,
                "field_types": field_types,
            })
        return result
    finally:
        db.close()


@app.get("/api/forms/{form_id}/submissions")
def list_form_submissions(
    form_id: str,
    start: Optional[date] = Query(default=None),
    end: Optional[date] = Query(default=None),
    page: Optional[int] = Query(default=None, ge=1),
    page_size: Optional[int] = Query(default=None, ge=1, le=500),
    current_user: User = Depends(get_current_user),
):
    """Vue agrégée façon Kobo : toutes les réponses reçues pour CE formulaire,
    tous patients confondus — utile pour analyser les données d'un coup d'œil."""
    db = SessionLocal()
    try:
        form = db.get(Form, form_id)
        if not form:
            raise HTTPException(status_code=404, detail="Formulaire introuvable")

        query = db.query(Submission).filter(Submission.form_id == form_id)
        if start:
            query = query.filter(Submission.collected_at >= datetime.combine(start, datetime.min.time()))
        if end:
            query = query.filter(Submission.collected_at <= datetime.combine(end, datetime.max.time()))
        query = query.order_by(Submission.collected_at.desc())

        # Pagination optionnelle : si ni page ni page_size ne sont fournis,
        # comportement inchangé (tout renvoyer) — n'importe quel appelant
        # existant continue de fonctionner sans modification.
        total_count = None
        if page or page_size:
            total_count = query.count()
            page = page or 1
            page_size = page_size or 100
            query = query.limit(page_size).offset((page - 1) * page_size)

        submissions = query.all()

        # Statut d'admission en Suivi (point 9) — seulement si ce formulaire a
        # un champ S/N-S : "admitted" (dossier actif déjà créé), "pending"
        # (S/N-S = Oui mais toujours aucun dossier — "plus tard" a été
        # cliqué), "not_admitted" (S/N-S = Non ou vide). None si le
        # formulaire n'a pas de champ S/N-S du tout (colonne non pertinente).
        referral_field_id = _find_referral_field_id(form.definition)

        result = []
        for s in submissions:
            patient = db.get(Patient, s.patient_id)
            referral_status = None
            if referral_field_id:
                if s.responses.get(referral_field_id):
                    existing_case = (
                        db.query(Case)
                        .filter(Case.patient_id == s.patient_id, Case.project_id == form.project_id, Case.status == "active")
                        .first()
                    )
                    referral_status = "admitted" if existing_case else "pending"
                else:
                    referral_status = "not_admitted"
            result.append({
                "id": s.id,
                "patient_name": f"{patient.first_name} {patient.last_name}" if patient else "Patient supprimé",
                "patient_id": s.patient_id,
                "responses": s.responses,
                "collected_at": s.collected_at,
                "referral_status": referral_status,
            })
        response_body = {
            "form": {"id": form.id, "name": form.name, "project_id": form.project_id, "definition": form.definition},
            "submissions": result,
            "has_referral_field": referral_field_id is not None,
        }
        if total_count is not None:
            response_body["total"] = total_count
        return response_body
    finally:
        db.close()


# --- Médias (champs photo / audio / vidéo / fichier, façon Kobo) ---

UPLOADS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
os.makedirs(UPLOADS_DIR, exist_ok=True)

# Limite volontairement généreuse mais pas illimitée : évite qu'un fichier
# gigantesque (ou une erreur d'appareil) ne remplisse le disque du serveur.
MAX_UPLOAD_SIZE_BYTES = 25 * 1024 * 1024  # 25 Mo

ALLOWED_FIELD_TYPES_BY_CONTENT = {
    "image": ("image/",),
    "audio": ("audio/",),
    "video": ("video/",),
    "file": None,  # type générique : tout type de fichier accepté
    # Une signature est un dessin transformé en PNG côté navigateur (voir
    # submission.html) — techniquement une image, mais distinguée de "image"
    # pour que ce soit clair, dans les métadonnées du fichier, que ceci vient
    # d'un pavé de signature plutôt qu'une photo importée.
    "signature": ("image/",),
}


@app.post("/api/media/upload")
async def upload_media(
    file: UploadFile = File(...),
    field_type: str = FastAPIFormField(default="file"),
    current_user: User = Depends(get_current_user),
):
    content = await file.read()
    return await _store_uploaded_media(content, file.filename, file.content_type, field_type, uploaded_by=current_user.id)


async def _store_uploaded_media(content: bytes, filename: Optional[str], content_type: Optional[str], field_type: str, uploaded_by: Optional[str]):
    if field_type not in ALLOWED_FIELD_TYPES_BY_CONTENT:
        raise HTTPException(status_code=400, detail=f"Type de champ média inconnu : '{field_type}'.")

    if len(content) > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(status_code=413, detail=f"Fichier trop volumineux (max {MAX_UPLOAD_SIZE_BYTES // (1024*1024)} Mo).")
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Fichier vide.")

    expected_prefixes = ALLOWED_FIELD_TYPES_BY_CONTENT[field_type]
    content_type = content_type or "application/octet-stream"
    if expected_prefixes and not any(content_type.startswith(p) for p in expected_prefixes):
        raise HTTPException(
            status_code=400,
            detail=f"Ce champ attend un fichier de type {field_type} ; reçu : {content_type}.",
        )
    # Ces types déclarés sont refusés même pour le champ générique "file" : un
    # SVG ou un HTML peut contenir du JavaScript qui s'exécuterait dans le
    # navigateur d'un soignant/admin s'il ouvrait ce fichier — aucun usage
    # légitime de ces types n'existe dans cette appli.
    if any(content_type.startswith(p) for p in ("image/svg", "text/html", "application/xhtml", "application/x-shockwave")):
        raise HTTPException(status_code=400, detail=f"Type de fichier non autorisé : {content_type}.")

    ext = os.path.splitext(filename or "")[1][:10]  # extension d'origine, tronquée par sécurité
    stored_filename = f"{uuid.uuid4()}{ext}"
    dest_path = os.path.join(UPLOADS_DIR, stored_filename)
    with open(dest_path, "wb") as f:
        f.write(content)

    db = SessionLocal()
    try:
        media = Media(
            original_filename=(filename or "fichier")[:255],
            stored_filename=stored_filename,
            content_type=content_type,
            size_bytes=str(len(content)),
            field_type=field_type,
            uploaded_by=uploaded_by,
        )
        db.add(media)
        db.commit()
        db.refresh(media)
        return {
            "id": media.id,
            "original_filename": media.original_filename,
            "content_type": media.content_type,
            "size_bytes": len(content),
        }
    finally:
        db.close()


# Seuls ces types sont réaffichés "en ligne" (directement dans le
# navigateur) — tout le reste force un téléchargement (Content-Disposition:
# attachment) plutôt qu'un affichage direct, pour qu'un type de fichier
# inattendu (même correctement déclaré) ne puisse jamais s'exécuter comme
# une page dans l'origine de l'application.
SAFE_INLINE_CONTENT_TYPES = {
    "image/jpeg", "image/png", "image/gif", "image/webp",
    "audio/mpeg", "audio/wav", "audio/ogg", "audio/mp4", "audio/webm",
    "video/mp4", "video/webm", "video/ogg",
}


@app.get("/api/media/{media_id}")
def get_media(media_id: str, current_user: User = Depends(get_current_user)):
    """Diffuse le fichier — jamais accessible sans être connecté (ce sont
    potentiellement des photos/documents de patients)."""
    db = SessionLocal()
    try:
        media = db.get(Media, media_id)
        if not media:
            raise HTTPException(status_code=404, detail="Fichier introuvable.")
        path = os.path.join(UPLOADS_DIR, media.stored_filename)
        if not os.path.isfile(path):
            raise HTTPException(status_code=404, detail="Fichier introuvable sur le disque.")
        content_type = media.content_type or "application/octet-stream"
        disposition = "inline" if content_type in SAFE_INLINE_CONTENT_TYPES else "attachment"
        # Le nom d'origine est fourni par la personne qui a envoyé le fichier
        # — jamais fait confiance tel quel dans un en-tête HTTP (guillemets/
        # retours à la ligne pourraient sinon manipuler l'en-tête).
        safe_filename = (media.original_filename or "fichier").replace('"', "'").replace("\r", "").replace("\n", "")
        return StreamingResponse(
            open(path, "rb"),
            media_type=content_type,
            headers={
                "Content-Disposition": f'{disposition}; filename="{safe_filename}"',
                "X-Content-Type-Options": "nosniff",
            },
        )
    finally:
        db.close()


# --- Schémas & endpoints Cas de suivi (Module Suivi) ---

def _find_next_visit_field(definition: dict) -> Optional[str]:
    """Cherche le champ (unique par formulaire) marqué comme 'date de
    prochaine visite' — voir le réglage ajouté dans form_builder.html sur
    les champs de type 'date'."""
    for section in (definition or {}).get("sections", []):
        for field in section.get("fields", []):
            if field.get("next_visit_marker"):
                return field["id"]
    return None


def _serialize_case(db, case: Case) -> dict:
    patient = db.get(Patient, case.patient_id)
    last_submission = (
        db.query(Submission)
        .filter(Submission.case_id == case.id)
        .order_by(Submission.collected_at.desc())
        .first()
    )
    visit_count = db.query(Submission).filter(Submission.case_id == case.id).count()

    next_visit_at = None
    is_overdue = False
    if last_submission:
        form = db.get(Form, last_submission.form_id)
        if form:
            marker_field_id = _find_next_visit_field(form.definition)
            if marker_field_id:
                raw_value = last_submission.responses.get(marker_field_id)
                if raw_value:
                    try:
                        next_visit_at = date.fromisoformat(str(raw_value))
                        is_overdue = case.status == "active" and next_visit_at < datetime.utcnow().date()
                    except ValueError:
                        next_visit_at = None

    return {
        "id": case.id,
        "project_id": case.project_id,
        "patient_id": case.patient_id,
        "patient_name": f"{patient.first_name} {patient.last_name}" if patient else "Patient supprimé",
        "patient_sex": patient.sex if patient else None,
        "patient_date_of_birth": patient.date_of_birth if patient else None,
        "patient_anonymous_code": patient.anonymous_code if patient else None,
        "case_number": case.case_number,
        "status": case.status,
        "enrolled_at": case.enrolled_at,
        "closed_at": case.closed_at,
        "closed_reason": case.closed_reason,
        "last_visit_at": last_submission.collected_at if last_submission else None,
        "visit_count": visit_count,
        "next_visit_at": next_visit_at,
        "is_overdue": is_overdue,
        "updated_at": case.updated_at,
    }


def _generate_case_number(db, project_id: str, reference_date: Optional[datetime] = None) -> str:
    # Se base sur le numéro le plus haut RÉELLEMENT utilisé cette année pour ce
    # projet, pas sur un simple comptage de dossiers existants : un comptage se
    # trompe dès qu'un dossier a déjà été supprimé (le compte diminue mais les
    # numéros déjà attribués aux dossiers restants, eux, ne bougent pas), ce qui
    # régénérait alors un numéro déjà pris — et comme rien n'est enregistré à
    # chaque tentative ratée, les 5 essais de la boucle d'appel régénéraient
    # exactement le même numéro cassé, donc échouaient à chaque fois.
    year = (reference_date or datetime.utcnow()).year
    prefix = f"CAS-{year}-"
    existing = db.query(Case.case_number).filter(
        Case.project_id == project_id,
        Case.case_number.like(f"{prefix}%"),
    ).all()
    max_seq = 0
    for (number,) in existing:
        suffix = number[len(prefix):]
        if suffix.isdigit():
            max_seq = max(max_seq, int(suffix))
    return f"{prefix}{max_seq + 1:04d}"


class CaseCreate(BaseModel):
    project_id: str
    patient_id: str
    case_number: Optional[str] = None
    source_submission_id: Optional[str] = None
    enrolled_at: Optional[datetime] = None  # pour la saisie rétroactive (rattrapage de dossiers papier) ; vide = maintenant
    id: Optional[str] = None  # fourni si créé hors-ligne (UUID généré dans le navigateur)


@app.get("/api/projects/{project_id}/cases")
def list_cases(
    project_id: str,
    status: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    overdue: Optional[bool] = Query(default=None),
    sort: Optional[str] = Query(default=None),  # "activity" = trie par dernière activité (visite ou inscription)
    current_user: User = Depends(get_current_user),
):
    db = SessionLocal()
    try:
        query = db.query(Case).filter(Case.project_id == project_id)
        if status:
            query = query.filter(Case.status == status)
        cases = query.order_by(Case.enrolled_at.desc()).all()

        results = [_serialize_case(db, c) for c in cases]
        if search:
            term = search.strip().lower()
            results = [r for r in results if term in r["patient_name"].lower() or term in r["case_number"].lower()]
        if overdue is not None:
            results = [r for r in results if r["is_overdue"] == overdue]
        if sort == "activity":
            results.sort(key=lambda r: r["last_visit_at"] or r["enrolled_at"], reverse=True)
        return results
    finally:
        db.close()


@app.get("/api/projects/{project_id}/cases/export")
def export_cases(
    project_id: str,
    request: Request,
    format: str = Query(default="csv"),
    status: Optional[str] = Query(default=None),
    current_user: User = Depends(get_current_user),
):
    """Export de la liste des dossiers de suivi (pas de leurs visites, déjà
    exportables par formulaire — ici, une ligne par CAS : identité, statut,
    dates, motif de clôture)."""
    if format not in ("csv", "xlsx"):
        raise HTTPException(status_code=400, detail=f"Format d'export non reconnu : '{format}' (csv ou xlsx attendu).")
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Projet introuvable")

        query = db.query(Case).filter(Case.project_id == project_id)
        if status:
            query = query.filter(Case.status == status)
        cases = query.order_by(Case.enrolled_at.asc()).all()

        rows = []
        for c in cases:
            serialized = _serialize_case(db, c)
            # Âge à aujourd'hui (pas à une date de visite précise, contrairement
            # à l'export des données de formulaire) — un dossier n'a pas une
            # date unique, et ça reste cohérent avec l'âge affiché à l'écran
            # dans la liste des dossiers et la fiche d'un dossier.
            age_value = _age_at(serialized["patient_date_of_birth"], datetime.utcnow().date())
            rows.append({
                "numero_dossier": serialized["case_number"],
                "patient": serialized["patient_name"],
                "code_anonyme": serialized["patient_anonymous_code"] or "",
                "age": age_value if age_value is not None else "",
                "sexe": serialized["patient_sex"] or "",
                "statut": serialized["status"],
                "inscrit_le": serialized["enrolled_at"].strftime("%Y-%m-%d") if serialized["enrolled_at"] else "",
                "derniere_visite": serialized["last_visit_at"].strftime("%Y-%m-%d") if serialized["last_visit_at"] else "",
                "nb_visites": serialized["visit_count"],
                "cloture_le": serialized["closed_at"].strftime("%Y-%m-%d") if serialized["closed_at"] else "",
                "motif_cloture": serialized["closed_reason"] or "",
            })

        columns = ["numero_dossier", "patient", "code_anonyme", "age", "sexe", "statut", "inscrit_le", "derniere_visite", "nb_visites", "cloture_le", "motif_cloture"]
        df = pd.DataFrame(rows, columns=columns)
        if format == "xlsx":
            buffer = BytesIO()
            with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                df.to_excel(writer, index=False, sheet_name="Dossiers")
            buffer.seek(0)
            content, media_type, ext = buffer.read(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
        else:
            content, media_type, ext = ("\ufeff" + df.to_csv(index=False, sep=";")).encode("utf-8"), "text/csv", "csv"

        filename = f"{slugify_py(project.name)}_dossiers.{ext}"
        log_audit(db, request, user=current_user, action="export", entity_type="case_list",
                  entity_id=project_id, project_id=project_id, details=f"Export de la liste des dossiers ({format}) — {len(rows)} ligne(s)")
        return StreamingResponse(BytesIO(content), media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    finally:
        db.close()


@app.get("/api/projects/{project_id}/export")
def export_project_summary(
    project_id: str,
    request: Request,
    format: str = Query(default="csv"),
    current_user: User = Depends(get_current_user),
):
    """Export d'une fiche résumé du projet (identité + compteurs) — pour un
    export complet des données recueillies, utiliser l'export par formulaire
    (Enquête) et l'export des dossiers (Suivi), déjà disponibles séparément."""
    if format not in ("csv", "xlsx"):
        raise HTTPException(status_code=400, detail=f"Format d'export non reconnu : '{format}' (csv ou xlsx attendu).")
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Projet introuvable")

        patient_count = db.query(Patient).filter(Patient.project_id == project_id).count()
        form_ids = [f.id for f in db.query(Form).filter(Form.project_id == project_id).all()]
        submission_count = db.query(Submission).filter(Submission.form_id.in_(form_ids)).count() if form_ids else 0
        case_active = db.query(Case).filter(Case.project_id == project_id, Case.status == "active").count()
        case_closed = db.query(Case).filter(Case.project_id == project_id, Case.status == "closed").count()
        case_lost = db.query(Case).filter(Case.project_id == project_id, Case.status == "lost").count()

        rows = [{
            "nom_projet": project.name,
            "description": project.description or "",
            "cree_le": project.created_at.strftime("%Y-%m-%d") if project.created_at else "",
            "nb_formulaires": len(form_ids),
            "nb_patients": patient_count,
            "nb_soumissions": submission_count,
            "cas_actifs": case_active,
            "cas_clotures": case_closed,
            "cas_perdus_de_vue": case_lost,
        }]
        columns = list(rows[0].keys())
        df = pd.DataFrame(rows, columns=columns)
        if format == "xlsx":
            buffer = BytesIO()
            with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                df.to_excel(writer, index=False, sheet_name="Résumé")
            buffer.seek(0)
            content, media_type, ext = buffer.read(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
        else:
            content, media_type, ext = ("\ufeff" + df.to_csv(index=False, sep=";")).encode("utf-8"), "text/csv", "csv"

        filename = f"{slugify_py(project.name)}_resume.{ext}"
        log_audit(db, request, user=current_user, action="export", entity_type="project_summary",
                  entity_id=project_id, project_id=project_id, details=f"Export résumé du projet ({format})")
        return StreamingResponse(BytesIO(content), media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    finally:
        db.close()


@app.get("/api/projects/{project_id}/cases/stats")
def get_case_stats(project_id: str, current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        active_cases = db.query(Case).filter(Case.project_id == project_id, Case.status == "active").all()
        active = len(active_cases)
        overdue = sum(1 for c in active_cases if _serialize_case(db, c)["is_overdue"])
        closed = db.query(Case).filter(Case.project_id == project_id, Case.status == "closed").count()
        lost = db.query(Case).filter(Case.project_id == project_id, Case.status == "lost").count()
        month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        closed_this_month = (
            db.query(Case)
            .filter(Case.project_id == project_id, Case.status.in_(["closed", "lost"]), Case.closed_at >= month_start)
            .count()
        )
        return {
            "active": active,
            "overdue": overdue,
            "closed": closed,
            "lost": lost,
            "closed_this_month": closed_this_month,
            "total": active + closed + lost,
        }
    finally:
        db.close()


@app.post("/api/cases", status_code=201)
def create_case(
    payload: CaseCreate,
    force: bool = Query(default=False),
    current_user: User = Depends(get_current_user),
):
    db = SessionLocal()
    try:
        project = db.get(Project, payload.project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Projet introuvable")
        patient = db.get(Patient, payload.patient_id)
        if not patient:
            raise HTTPException(status_code=404, detail="Patient introuvable")
        if patient.project_id != payload.project_id:
            raise HTTPException(
                status_code=409,
                detail="Ce patient n'appartient pas au projet indiqué.",
            )

        # Cas créé hors-ligne, synchronisation éventuellement rejouée (coupure
        # juste après un premier succès) — on ne recrée pas de doublon, on
        # renvoie simplement le dossier déjà existant avec ce même identifiant.
        if payload.id:
            existing = db.get(Case, payload.id)
            if existing:
                return _serialize_case(db, existing)

        if not force:
            existing_active = (
                db.query(Case)
                .filter(
                    Case.patient_id == payload.patient_id,
                    Case.project_id == payload.project_id,
                    Case.status == "active",
                )
                .first()
            )
            if existing_active:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "msg": f"Ce patient a déjà un dossier actif : {existing_active.case_number}.",
                        "existing_case_id": existing_active.id,
                        "existing_case_number": existing_active.case_number,
                    },
                )

        enrolled_at = payload.enrolled_at or datetime.utcnow()

        # La contrainte d'unicité (project_id, case_number) transforme une
        # collision silencieuse (deux créations simultanées lisant le même
        # compte avant que l'une des deux ne valide) en erreur rattrapable —
        # on régénère alors un nouveau numéro et on réessaie.
        for attempt in range(5):
            case = Case(
                id=payload.id or str(uuid.uuid4()),
                project_id=payload.project_id,
                patient_id=payload.patient_id,
                case_number=payload.case_number or _generate_case_number(db, payload.project_id, enrolled_at),
                source_submission_id=payload.source_submission_id,
                enrolled_at=enrolled_at,
                created_by=current_user.id,
            )
            db.add(case)
            try:
                db.commit()
                break
            except IntegrityError:
                db.rollback()
                if payload.case_number:
                    # Le numéro a été explicitement demandé par l'appelant :
                    # on ne le régénère pas à sa place, on signale le conflit.
                    raise HTTPException(status_code=409, detail="Ce numéro de dossier existe déjà dans ce projet.")
                continue
        else:
            raise HTTPException(status_code=409, detail="Impossible d'attribuer un numéro de dossier unique, réessaie.")

        db.refresh(case)
        return _serialize_case(db, case)
    finally:
        db.close()


@app.get("/api/cases/{case_id}")
def get_case(case_id: str, current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        case = db.get(Case, case_id)
        if not case:
            raise HTTPException(status_code=404, detail="Cas introuvable")
        return _serialize_case(db, case)
    finally:
        db.close()


class CaseUpdate(BaseModel):
    case_number: Optional[str] = None
    enrolled_at: Optional[datetime] = None
    expected_updated_at: Optional[datetime] = None  # concurrence : valeur vue au chargement


@app.put("/api/cases/{case_id}")
def update_case(case_id: str, payload: CaseUpdate, current_user: User = Depends(get_current_user)):
    """Corrige le numéro de dossier ou la date d'inscription. Le patient
    rattaché n'est volontairement pas modifiable ici — supprimer et
    recréer le cas plutôt que de risquer de mélanger deux historiques."""
    db = SessionLocal()
    try:
        case = db.get(Case, case_id)
        if not case:
            raise HTTPException(status_code=404, detail="Cas introuvable")
        if payload.expected_updated_at and case.updated_at:
            if case.updated_at != payload.expected_updated_at:
                raise HTTPException(
                    status_code=409,
                    detail="Quelqu'un d'autre a modifié ce dossier entre-temps. Recharge la page avant de corriger à nouveau.",
                )
        if payload.case_number:
            case.case_number = payload.case_number
        if payload.enrolled_at:
            case.enrolled_at = payload.enrolled_at
        case.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(case)
        return _serialize_case(db, case)
    finally:
        db.close()


@app.delete("/api/cases/{case_id}")
def delete_case(case_id: str, request: Request, current_user: User = Depends(get_current_user)):
    """Suppression autorisée uniquement si le cas n'a aucune visite
    rattachée (ex : doublon créé par erreur). Sinon, orienter vers la
    clôture pour ne jamais perdre de données de suivi réelles."""
    db = SessionLocal()
    try:
        case = db.get(Case, case_id)
        if not case:
            raise HTTPException(status_code=404, detail="Cas introuvable")
        visit_count = db.query(Submission).filter(Submission.case_id == case_id).count()
        if visit_count > 0:
            raise HTTPException(
                status_code=409,
                detail=f"Ce dossier a {visit_count} visite(s) enregistrée(s) — impossible de le supprimer. Clôture-le plutôt.",
            )
        case_number = case.case_number
        case_project_id = case.project_id
        db.delete(case)
        db.commit()
        log_audit(db, request, user=current_user, action="delete", entity_type="case",
                  entity_id=case_id, project_id=case_project_id, details=f"Dossier supprimé : {case_number}")
        return {"status": "ok"}
    finally:
        db.close()


class CaseClose(BaseModel):
    reason: str
    status: Optional[str] = "closed"  # "closed" ou "lost" (perdu de vue)
    closed_at: Optional[datetime] = None  # pour la clôture rétroactive ; vide = maintenant


@app.post("/api/cases/{case_id}/close")
def close_case(case_id: str, payload: CaseClose, current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        case = db.get(Case, case_id)
        if not case:
            raise HTTPException(status_code=404, detail="Cas introuvable")
        case.status = payload.status if payload.status in ("closed", "lost") else "closed"
        case.closed_at = payload.closed_at or datetime.utcnow()
        case.closed_reason = payload.reason
        case.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(case)
        return _serialize_case(db, case)
    finally:
        db.close()


@app.post("/api/cases/{case_id}/reopen")
def reopen_case(case_id: str, current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        case = db.get(Case, case_id)
        if not case:
            raise HTTPException(status_code=404, detail="Cas introuvable")
        case.status = "active"
        case.closed_at = None
        case.closed_reason = None
        case.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(case)
        return _serialize_case(db, case)
    finally:
        db.close()


@app.get("/api/cases/{case_id}/timeline")
def get_case_timeline(case_id: str, current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        case = db.get(Case, case_id)
        if not case:
            raise HTTPException(status_code=404, detail="Cas introuvable")

        events = [{
            "type": "registration",
            "at": case.enrolled_at,
            "title": "Inscription au suivi",
            "description": f"Dossier {case.case_number} ouvert",
        }]

        submissions = (
            db.query(Submission)
            .filter(Submission.case_id == case_id)
            .order_by(Submission.collected_at.asc())
            .all()
        )
        for s in submissions:
            form = db.get(Form, s.form_id)
            field_types = {}
            if form:
                for section in (form.definition or {}).get("sections", []):
                    for f in section.get("fields", []):
                        field_types[f["id"]] = f.get("type")
            events.append({
                "type": "follow_up",
                "at": s.collected_at,
                "title": form.name if form else "Visite",
                "submission_id": s.id,
                "form_id": s.form_id,
                "responses": s.responses,
                "field_types": field_types,
            })

        if case.status in ("closed", "lost") and case.closed_at:
            events.append({
                "type": "closure",
                "at": case.closed_at,
                "title": "Clôture du dossier" if case.status == "closed" else "Perdu de vue",
                "description": case.closed_reason or "",
            })

        # L'inscription précède TOUJOURS toute visite de ce dossier (une
        # soumission ne peut exister avec ce case_id qu'après la création du
        # cas) — mais une visite saisie le même jour via un simple sélecteur
        # de date est enregistrée à minuit (00:00), donc numériquement AVANT
        # l'heure réelle de création du dossier dans l'après-midi. On force
        # donc l'inscription en première position plutôt que de se fier
        # uniquement à la comparaison brute des horodatages.
        events.sort(key=lambda e: (e["type"] != "registration", e["at"]))
        return {"case": _serialize_case(db, case), "events": events}
    finally:
        db.close()


@app.get("/api/patients/{patient_id}/cases")
def list_patient_cases(patient_id: str, current_user: User = Depends(get_current_user)):
    """Utilisé notamment par history.html pour savoir si ce patient a déjà
    un dossier de suivi (et proposer 'Voir le dossier' plutôt que 'Créer')."""
    db = SessionLocal()
    try:
        cases = db.query(Case).filter(Case.patient_id == patient_id).order_by(Case.enrolled_at.desc()).all()
        return [_serialize_case(db, c) for c in cases]
    finally:
        db.close()


@app.delete("/api/forms/{form_id}")
def delete_form(form_id: str, request: Request, current_user: User = Depends(require_admin)):
    """Supprime un formulaire seulement s'il n'a aucune soumission ; sinon
    l'archive (is_active=False) pour ne jamais perdre de données déjà
    recueillies."""
    db = SessionLocal()
    try:
        form = db.get(Form, form_id)
        if not form:
            raise HTTPException(status_code=404, detail="Formulaire introuvable")
        submission_count = db.query(Submission).filter(Submission.form_id == form_id).count()
        if submission_count > 0:
            form.is_active = False
            db.commit()
            log_audit(db, request, user=current_user, action="update", entity_type="form",
                      entity_id=form_id, project_id=form.project_id,
                      details=f"Formulaire '{form.name}' archivé (avait {submission_count} soumission(s))")
            return {
                "status": "archived",
                "message": f"{submission_count} soumission(s) existent pour ce formulaire : il a été archivé plutôt que supprimé.",
            }
        form_name = form.name
        form_project_id = form.project_id
        db.delete(form)
        db.commit()
        log_audit(db, request, user=current_user, action="delete", entity_type="form",
                  entity_id=form_id, project_id=form_project_id, details=f"Formulaire supprimé : {form_name}")
        return {"status": "deleted"}
    finally:
        db.close()


@app.get("/api/audit-logs")
def list_audit_logs(
    project_id: Optional[str] = Query(default=None),
    action: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(require_admin),
):
    """Consultation du journal d'audit — réservée aux admins. Volontairement
    limité aux actions sensibles (voir AuditLog dans models.py), pas un
    journal exhaustif de toute activité."""
    db = SessionLocal()
    try:
        query = db.query(AuditLog).order_by(AuditLog.created_at.desc())
        if project_id:
            query = query.filter(AuditLog.project_id == project_id)
        if action:
            query = query.filter(AuditLog.action == action)
        total = query.count()
        entries = query.limit(page_size).offset((page - 1) * page_size).all()
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "entries": [{
                "id": e.id,
                "user_email": e.user_email,
                "action": e.action,
                "entity_type": e.entity_type,
                "entity_id": e.entity_id,
                "project_id": e.project_id,
                "details": e.details,
                "ip_address": e.ip_address,
                "created_at": e.created_at,
            } for e in entries],
        }
    finally:
        db.close()


# --- Lien communautaire (point 7) : accès à UN formulaire précis, sans
# compte, via un lien + un code d'accès partagé (façon Kobo). Chaque route
# ci-dessous revérifie le code à chaque appel (pas de session séparée à
# révoquer) : régénérer le code invalide donc tous les appareils déjà
# débloqués, immédiatement et sans action supplémentaire. Toujours scopé à UN
# SEUL formulaire/projet — jamais d'accès à autre chose avec ce code. ---

# Protection contre les essais de code en boucle : ce lien n'a pas de compte
# à verrouiller, donc rien ne freinait naturellement un script essayant des
# codes au hasard. Compteur en mémoire (process unique, --reload non utilisé
# en usage normal) par (IP, formulaire) — au-delà de quelques échecs, on
# bloque temporairement sans même revérifier le code.
_PUBLIC_CODE_MAX_ATTEMPTS = 8
_PUBLIC_CODE_WINDOW = timedelta(minutes=10)
_PUBLIC_CODE_LOCKOUT = timedelta(minutes=15)
_public_code_attempts: Dict[str, List[datetime]] = {}
_public_code_lockouts: Dict[str, datetime] = {}
_public_code_lock = threading.Lock()


def verify_public_code(form_id: str, request: Request, x_community_code: str = Header(default="")) -> Form:
    db = SessionLocal()
    try:
        form = db.get(Form, form_id)
        if not form or not form.public_link_enabled or not form.public_access_code_hash:
            raise HTTPException(status_code=404, detail="Lien communautaire introuvable ou désactivé.")

        client_ip = request.client.host if request.client else "inconnu"
        key = f"{client_ip}:{form_id}"
        now = datetime.utcnow()
        with _public_code_lock:
            locked_until = _public_code_lockouts.get(key)
            if locked_until and locked_until > now:
                wait_min = max(1, int((locked_until - now).total_seconds() // 60) + 1)
                raise HTTPException(
                    status_code=429,
                    detail=f"Trop de tentatives avec un code incorrect. Réessaie dans environ {wait_min} min.",
                )

        if not x_community_code or not verify_password(x_community_code, form.public_access_code_hash):
            with _public_code_lock:
                attempts = [t for t in _public_code_attempts.get(key, []) if now - t < _PUBLIC_CODE_WINDOW]
                attempts.append(now)
                if len(attempts) >= _PUBLIC_CODE_MAX_ATTEMPTS:
                    _public_code_lockouts[key] = now + _PUBLIC_CODE_LOCKOUT
                    _public_code_attempts.pop(key, None)
                else:
                    _public_code_attempts[key] = attempts
            raise HTTPException(status_code=403, detail="Code d'accès incorrect.")

        with _public_code_lock:
            _public_code_attempts.pop(key, None)
            _public_code_lockouts.pop(key, None)
        return form
    finally:
        db.close()


@app.get("/api/public/forms/{form_id}")
def get_form_public(form: Form = Depends(verify_public_code)):
    return {"id": form.id, "name": form.name, "project_id": form.project_id, "definition": form.definition}


class PublicPatientCreate(BaseModel):
    first_name: str
    last_name: str
    date_of_birth: Optional[date] = None
    sex: Optional[str] = None
    phone: Optional[str] = None
    id: Optional[str] = None  # fourni si créé hors-ligne sur le téléphone de l'agent


@app.post("/api/public/forms/{form_id}/patients", response_model=PatientOut, status_code=201)
def create_patient_public(payload: PublicPatientCreate, form: Form = Depends(verify_public_code)):
    db = SessionLocal()
    try:
        if payload.id:
            existing = db.get(Patient, payload.id)
            if existing:
                return existing

        phone_conflict = _check_phone_unique(db, form.project_id, payload.phone)
        if phone_conflict:
            raise HTTPException(status_code=409, detail=phone_conflict)

        data = payload.model_dump()
        data["project_id"] = form.project_id  # jamais celui envoyé par le client : toujours celui du formulaire
        if not data.get("id"):
            data.pop("id", None)
        patient = Patient(**data)
        db.add(patient)
        db.commit()
        db.refresh(patient)
        _assign_patient_code(db, patient)
        if payload.id:
            _flag_potential_duplicates(db, patient)
            db.refresh(patient)
        log_audit(db, user_email="Lien communautaire", action="create", entity_type="patient",
                  entity_id=patient.id, project_id=form.project_id,
                  details=f"Patient créé via lien communautaire (formulaire « {form.name} ») : {patient.first_name} {patient.last_name}")
        # log_audit fait son propre commit interne, qui "expire" à nouveau les
        # attributs de patient (même raison que le commentaire dans
        # _assign_patient_code) — sans ce refresh final, la réponse plante
        # silencieusement une fois sur deux alors que le patient est déjà
        # bien enregistré.
        db.refresh(patient)
        return patient
    finally:
        db.close()


class PublicSubmissionCreate(BaseModel):
    patient_id: str
    responses: Dict[str, Any]
    collected_at: Optional[datetime] = None
    idempotency_key: Optional[str] = None


@app.post("/api/public/forms/{form_id}/submissions", status_code=201)
def create_submission_public(payload: PublicSubmissionCreate, form: Form = Depends(verify_public_code)):
    db = SessionLocal()
    try:
        if payload.idempotency_key:
            existing = db.query(Submission).filter(Submission.idempotency_key == payload.idempotency_key).first()
            if existing:
                return {
                    "id": existing.id, "responses": existing.responses,
                    "collected_at": existing.collected_at, "alerts": [], "already_recorded": True,
                }

        patient = db.get(Patient, payload.patient_id)
        if not patient:
            raise HTTPException(status_code=404, detail="Patient introuvable")
        if patient.project_id != form.project_id:
            # Ne devrait jamais arriver via ce parcours (le patient est
            # toujours créé via /patients ci-dessus, avec le même project_id),
            # sauf identifiant bricolé à la main — filet de sécurité.
            raise HTTPException(status_code=409, detail="Ce patient n'appartient pas au projet de ce formulaire.")

        processed_responses, errors, alerts = validate_and_process_submission(form.definition, payload.responses)
        if errors:
            raise HTTPException(status_code=422, detail=" | ".join(errors))

        unique_errors = _check_unique_fields(db, form, processed_responses)
        if unique_errors:
            raise HTTPException(status_code=422, detail=" | ".join(unique_errors))

        age_errors = _check_age_consistency(form, processed_responses, patient, payload.collected_at or datetime.utcnow())
        if age_errors:
            raise HTTPException(status_code=422, detail=" | ".join(age_errors))

        # Pas de case_id (le lien communautaire ne fait que de l'Enquête, pas
        # de Suivi) et pas de created_by (pas de compte) — la traçabilité
        # passe par le journal d'audit ci-dessous à la place.
        submission = Submission(
            form_id=form.id, patient_id=patient.id, case_id=None,
            responses=processed_responses, collected_at=payload.collected_at or datetime.utcnow(),
            created_by=None, idempotency_key=payload.idempotency_key,
        )
        db.add(submission)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = db.query(Submission).filter(Submission.idempotency_key == payload.idempotency_key).first()
            if existing:
                return {
                    "id": existing.id, "responses": existing.responses,
                    "collected_at": existing.collected_at, "alerts": [], "already_recorded": True,
                }
            raise
        db.refresh(submission)
        log_audit(db, user_email="Lien communautaire", action="create", entity_type="submission",
                  entity_id=submission.id, project_id=form.project_id,
                  details=f"Soumission créée via lien communautaire (formulaire « {form.name} »)")
        return {"id": submission.id, "responses": submission.responses, "collected_at": submission.collected_at, "alerts": alerts}
    finally:
        db.close()


@app.post("/api/public/forms/{form_id}/media/upload")
async def upload_media_public(
    file: UploadFile = File(...),
    field_type: str = FastAPIFormField(default="file"),
    form: Form = Depends(verify_public_code),
):
    content = await file.read()
    return await _store_uploaded_media(content, file.filename, file.content_type, field_type, uploaded_by=None)





# Sert les pages web. Doit rester APRES les routes /api/... pour ne pas les masquer.
app.mount("/", StaticFiles(directory="static", html=True), name="static")
