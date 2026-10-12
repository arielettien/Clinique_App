import uuid
from datetime import datetime

from sqlalchemy import Column, String, Date, DateTime, Boolean, Integer, JSON, ForeignKey, Text, UniqueConstraint

from database import Base


class Project(Base):
    """Un projet = un espace de suivi séparé (ex: 'Suivi Diabète',
    'Suivi Cancer', 'Nutrition Enfants'...). Chaque projet a ses propres
    patients et ses propres formulaires."""
    __tablename__ = "projects"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    # Fiche projet enrichie (page d'accueil) : date métier distincte de
    # created_at (qui reste l'horodatage technique réel d'insertion, jamais
    # modifié) — start_date est saisie/modifiable par l'utilisateur (le projet
    # a pu démarrer avant d'être créé dans l'application).
    start_date = Column(Date, nullable=True)
    ngo_name = Column(String(255), nullable=True)  # ONG de développement
    main_donor = Column(String(255), nullable=True)  # Bailleur principal
    cover_image_id = Column(String, ForeignKey("media.id"), nullable=True)


class Patient(Base):
    __tablename__ = "patients"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String, ForeignKey("projects.id"), nullable=True)
    first_name = Column(String(255), nullable=False)
    last_name = Column(String(255), nullable=False)
    date_of_birth = Column(Date, nullable=True)
    sex = Column(String(10), nullable=True)
    phone = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    # Code anonyme (ex: "PAT-2026-0042"), attribué à TOUT patient dès sa
    # création (admis en Suivi ou pas) — sert à identifier un patient dans un
    # document destiné à l'extérieur (ex: un bailleur) sans jamais y faire
    # apparaître son nom. Unique par projet, volontairement distinct du
    # numéro de dossier de suivi (préfixe différent) pour ne jamais les
    # confondre — un patient non admis en Suivi a un code mais pas de dossier.
    anonymous_code = Column(String(50), nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "anonymous_code", name="uq_patient_project_anon_code"),
    )


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String(255), unique=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=True)
    role = Column(String(50), default="admin")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    # Incrémenté à chaque déconnexion (et à chaque changement de mot de
    # passe) — intégré dans le jeton de session (voir security.py). Comparer
    # cette valeur à celle du jeton permet d'invalider immédiatement TOUTES
    # les sessions déjà ouvertes de ce compte, sans avoir besoin d'une liste
    # séparée de jetons révoqués.
    token_version = Column(Integer, default=0, nullable=False)


class Form(Base):
    """Un formulaire clinique : sa définition (sections/champs/formules)
    est stockée en JSON, ce qui permet d'en créer de nouveaux sans jamais
    toucher au code ni au schéma de base de données."""
    __tablename__ = "forms"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String, ForeignKey("projects.id"), nullable=True)
    name = Column(String(255), nullable=False)
    form_type = Column(String(50), default="survey")
    definition = Column(JSON, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    # Lien communautaire (point 7) : un formulaire peut être ouvert sans
    # compte via un lien + un code d'accès partagé. Seul le HASH du code est
    # stocké (jamais en clair) — comme un mot de passe. Régénérer le code
    # invalide immédiatement tous les appareils déjà débloqués, puisque
    # chaque requête re-vérifie contre ce hash.
    public_link_enabled = Column(Boolean, default=False)
    public_access_code_hash = Column(String, nullable=True)


class Submission(Base):
    """Une soumission = un formulaire rempli pour un patient donné,
    à un instant donné."""
    __tablename__ = "submissions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    form_id = Column(String, ForeignKey("forms.id"))
    patient_id = Column(String, ForeignKey("patients.id"))
    case_id = Column(String, ForeignKey("cases.id"), nullable=True)
    responses = Column(JSON, nullable=False)
    collected_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    # Jeton unique généré UNE FOIS par le navigateur à l'ouverture d'un formulaire
    # vierge, renvoyé avec la création — deux requêtes portant le même jeton
    # (double-clic, requête rejouée par le réseau) renvoient le même résultat
    # au lieu de créer un doublon. Nullable : une soumission créée sans jeton
    # (ancien client) reste possible, juste pas protégée contre ce cas précis.
    idempotency_key = Column(String(64), nullable=True, unique=True)


class Case(Base):
    """Un cas = un patient suivi dans le temps (Module Suivi). Rattaché au
    patient par FK (pas de champs démographiques dupliqués) — voir le
    dossier technique v0.19 pour le raisonnement."""
    __tablename__ = "cases"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    patient_id = Column(String, ForeignKey("patients.id"), nullable=False)
    case_number = Column(String(100), nullable=False)
    status = Column(String(50), default="active")  # active, closed, lost
    enrolled_at = Column(DateTime, default=datetime.utcnow)
    closed_at = Column(DateTime, nullable=True)
    closed_reason = Column(String(255), nullable=True)
    source_submission_id = Column(String, nullable=True)  # pas de FK stricte : évite une dépendance circulaire avec submissions.case_id
    created_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("project_id", "case_number", name="uq_case_project_number"),
    )


class Media(Base):
    """Un fichier envoyé depuis un formulaire (champ photo/audio/vidéo/fichier).
    Le contenu réel est sur disque (dossier uploads/), cette table ne garde que
    les métadonnées + le nom de fichier stocké (aléatoire, pas le nom d'origine,
    pour éviter tout risque de collision ou de traversée de chemin)."""
    __tablename__ = "media"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    original_filename = Column(String(255), nullable=False)
    stored_filename = Column(String(255), nullable=False)  # nom réel sur disque, dans uploads/
    content_type = Column(String(100), nullable=True)
    size_bytes = Column(String(20), nullable=True)
    field_type = Column(String(20), nullable=True)  # image / audio / video / file — pour l'affichage
    uploaded_by = Column(String, ForeignKey("users.id"), nullable=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow)


class AuditLog(Base):
    """Journal des actions sensibles — pas un journal exhaustif (pas de simple
    consultation de page, pas chaque soumission de formulaire, ce qui noierait
    l'information utile sous du bruit). Se limite à ce qui compte pour la
    sécurité et la responsabilité : connexions (réussies et échouées),
    gestion des comptes, suppressions difficiles à annuler, exports."""
    __tablename__ = "audit_logs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=True)  # nullable : connexion échouée, utilisateur pas toujours identifiable
    user_email = Column(String(255), nullable=True)  # conservé même si le compte est supprimé plus tard
    action = Column(String(50), nullable=False)  # login / login_failed / create / update / delete / export
    entity_type = Column(String(100), nullable=True)  # patient / submission / case / form / project / user
    entity_id = Column(String, nullable=True)
    project_id = Column(String, nullable=True)  # pour filtrer le journal par projet
    details = Column(Text, nullable=True)  # description libre, ex: "Formulaire 'Dépistage' supprimé"
    ip_address = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class PotentialDuplicate(Base):
    """Détecté quand un patient créé hors-ligne (donc sans avoir pu vérifier
    contre toute la base au moment de sa création) partage nom/prénom avec un
    patient déjà existant du même projet. Jamais fusionné automatiquement —
    seulement signalé pour qu'un admin tranche (voir /api/projects/{id}/potential-duplicates)."""
    __tablename__ = "potential_duplicates"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String, nullable=False)
    patient_id_a = Column(String, ForeignKey("patients.id"), nullable=False)
    patient_id_b = Column(String, ForeignKey("patients.id"), nullable=False)
    detected_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String(20), default="open")  # open / dismissed
