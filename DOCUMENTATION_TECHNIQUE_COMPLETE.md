# Documentation technique complète — Clinique App

**But de ce document.** Permettre de comprendre et, si besoin, de reconstruire fonctionnellement
l'application "Clinique App" sans avoir accès au code source original — en fournissant le modèle de
données, la logique métier de chaque endpoint, le moteur de règles, l'architecture hors-ligne, le design
system, et l'infrastructure de déploiement.

**Limite honnête à connaître avant de s'en servir.** Ce document décrit le comportement du code avec le
plus de précision possible (jusqu'à inclure le code source complet des modules les plus courts et les plus
sensibles — moteur de formules, validation, modèle de données, sécurité). Pour les fichiers volumineux
(`main.py`, `spreadsheet_io.py`, les pages HTML), le contenu ci-dessous est une **spécification très
détaillée extraite du code**, pas le code lui-même — une reformulation, même soignée, peut perdre une
nuance qu'un fichier source ne perd jamais. **La méthode la plus fiable pour reprendre ce projet trait pour
trait reste de conserver le code source réel** (sauvegarde du dossier complet, ou idéalement un suivi Git —
ce projet n'a pas encore de dépôt Git à la date de rédaction). Ce document est complémentaire à cette
sauvegarde, pas un substitut.

Date de rédaction : 22 juillet 2026. Version de l'application couverte : v0.7 (bandeau visible dans
`main.py`), avec les fonctionnalités "Lien communautaire" (point 7) et HTTPS auto-signé déjà en place.

---

## Table des matières

1. [Vue d'ensemble du projet](#1-vue-densemble-du-projet)
2. [Architecture générale et arborescence](#2-architecture-générale-et-arborescence)
3. [Modèle de données (`models.py`, `database.py`) — code complet](#3-modèle-de-données)
4. [Sécurité (`security.py`) — code complet](#4-sécurité)
5. [Moteur de formules (`formula_engine.py`) — code complet](#5-moteur-de-formules)
6. [Validation de soumission (`form_validation.py`) — code complet](#6-validation-de-soumission)
7. [Validation d'import de formulaire (`form_import_validation.py`) — code complet](#7-validation-dimport-de-formulaire)
8. [Spécification complète de l'API (`main.py`)](#8-spécification-complète-de-lapi-mainpy)
9. [Import/export Excel (`spreadsheet_io.py`)](#9-importexport-excel-spreadsheet_iopy)
10. [Frontend — scripts partagés (code complet)](#10-frontend--scripts-partagés)
11. [Frontend — pages de l'application](#11-frontend--pages-de-lapplication)
12. [Design system (`style.css`)](#12-design-system-stylecss)
13. [Infrastructure : Docker, variables d'environnement, HTTPS](#13-infrastructure)
14. [Migrations de base de données](#14-migrations-de-base-de-données)
15. [Scripts utilitaires](#15-scripts-utilitaires)
16. [Décisions et limites opérationnelles importantes](#16-décisions-et-limites-opérationnelles-importantes)
17. [Recette de reconstruction pas à pas](#17-recette-de-reconstruction-pas-à-pas)

---

## 1. Vue d'ensemble du projet

**Clinique App** est une application de collecte de données cliniques, en français, pour une clinique
suivant des patients dans le cadre de un ou plusieurs "projets" (ex: "Suivi Diabète", "Nutrition Enfants").
Deux modules fonctionnels distincts partagent la même base de patients :

- **Module Enquête** (inspiré de KoboToolbox) : formulaires transversaux, un dépistage/une observation à la
  fois, pas de notion de suivi dans le temps.
- **Module Suivi** (inspiré de CommCare) : "dossiers" (`Case`) — un patient inscrit dans un projet, avec une
  timeline de visites (soumissions liées au dossier), un statut (actif/clôturé/perdu de vue), une date de
  prochaine visite calculée.

Fonctionnalités transverses : formulaires 100% pilotés par une définition JSON (pas de code à écrire pour
créer un nouveau formulaire), calculs et logique conditionnelle via un mini-moteur de formules maison (sans
`eval`), mode hors-ligne complet (Service Worker + IndexedDB), export Excel/CSV (structure de formulaire ET
données), accès public à un formulaire précis via un lien + code d'accès partagé ("lien communautaire"),
journal d'audit restreint aux actions sensibles, HTTPS auto-signé pour permettre le hors-ligne sur des
téléphones accédant par adresse IP locale.

**Stack.** Backend : FastAPI (Python), SQLAlchemy, PostgreSQL. Frontend : HTML/CSS/JS statique, sans
framework ni bundler. Déploiement : Docker Compose (3 services : `db`, `api`, `backup`). Pas de suite de
tests. Pas de dépôt Git à ce jour (pas de suivi de version).

---

## 2. Architecture générale et arborescence

```
clinique-app/
├── docker-compose.yml
├── .env                          # secrets réels (jamais versionné) : SECRET_KEY, POSTGRES_PASSWORD, COOKIE_SECURE
├── .env.example                  # modèle documenté, valeurs à remplir
├── README.md, UPDATE_v0.2.md     # guides d'installation pas-à-pas en français (utilisateur non technique)
├── backups/                      # sauvegardes quotidiennes .sql.gz (30j glissants), montées depuis le service "backup"
└── backend/
    ├── Dockerfile
    ├── docker-entrypoint.sh      # bascule HTTP/HTTPS selon présence d'un certificat
    ├── requirements.txt
    ├── main.py                   # TOUTES les routes API (~2500 lignes, un seul fichier)
    ├── models.py                 # modèles SQLAlchemy
    ├── database.py               # engine/session
    ├── security.py               # hash mot de passe, JWT
    ├── formula_engine.py         # mini-langage de formules (calculs, conditions, alertes)
    ├── form_validation.py        # revalidation serveur d'une soumission
    ├── form_import_validation.py # validation structurelle d'un formulaire importé
    ├── spreadsheet_io.py         # import/export Excel/CSV
    ├── generate_cert.py          # génère un certificat HTTPS auto-signé
    ├── export_cert_der.py        # réexporte le certificat existant au format DER
    ├── create_admin.py           # crée le premier compte admin
    ├── seed_form.py              # insère un formulaire de démonstration
    ├── migrate_add_*.py          # scripts de migration ponctuels (un par évolution de schéma)
    ├── certs/                    # cert.pem / key.pem (générés localement, jamais à exposer)
    ├── uploads/                  # fichiers médias uploadés (noms aléatoires, jamais le nom d'origine)
    └── static/                   # frontend statique servi tel quel par FastAPI
        ├── app_shell.js, sw.js, offline_db.js, dexie.min.js, icons.js, style.css, manifest.json
        └── *.html                # une page par écran (voir §11)
```

**Principe central du backend** : tout le code API vit dans **un seul fichier** `main.py`, organisé par
ressource (auth, users, projects, patients, forms, submissions, media, cases, audit log, public/communautaire),
grep-able via `@app.`. Chaque route ouvre et referme sa propre session SQLAlchemy (`SessionLocal()` /
`finally: db.close()`) — jamais de session partagée entre une dépendance (`get_current_user`,
`verify_public_code`) et la route qui l'utilise.

**Principe central des formulaires** : `Form.definition` est un blob JSON (sections → champs), interprété à
la fois par le frontend (rendu, calculs/conditions en direct) et le backend (revalidation, recalcul serveur
faisant autorité). Créer un nouveau type de formulaire ne nécessite jamais de migration de schéma ni de code
— seulement une nouvelle valeur de `definition`.

**Principe central du hors-ligne** : le Service Worker (`sw.js`) ne met en cache QUE les fichiers de
l'application (HTML/CSS/JS) en stratégie "cache d'abord, mise à jour en arrière-plan" ; les données
métier (patients, formulaires, cas) passent exclusivement par IndexedDB (`offline_db.js`, via Dexie), gérées
explicitement par le code de l'application — jamais par un cache HTTP générique. Une soumission faite
hors-ligne est mise en file d'attente localement avec un identifiant d'idempotence, envoyée dès que le
réseau revient (bouton manuel de synchronisation en complément, car les événements navigateur
`online`/`offline` ne sont pas fiables à 100%, en particulier sous throttling réseau de DevTools).

---

## 3. Modèle de données

### `backend/database.py` (code complet)

```python
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://clinic_user:dev_password_a_changer@localhost:5432/clinic_db",
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()
```

### `backend/models.py` (code complet)

```python
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Date, DateTime, Boolean, JSON, ForeignKey, Text, UniqueConstraint

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


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String(255), unique=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=True)
    role = Column(String(50), default="admin")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


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
```

**Relations clés** : `Project` 1—N `Patient`/`Form`/`Case`. `Form.definition` porte tout le schéma du
formulaire. `Case` est FK-only vers `Patient` (pas de duplication démographique). `Submission` est
optionnellement liée à un `Case` (visite de suivi) ou pas (Enquête simple). Toutes les clés primaires sont
des `str` UUID générés en Python (`uuid.uuid4()`), pas des entiers auto-incrémentés — ce qui permet à un
client (navigateur hors-ligne) de générer lui-même un identifiant définitif avant même d'avoir pu contacter
le serveur.

**Pas d'Alembic.** `Base.metadata.create_all()` (appelé au démarrage de l'API) crée les tables absentes mais
n'altère jamais une table existante. Toute évolution de schéma sur une table déjà en production nécessite un
script `migrate_add_*.py` dédié (voir §14).

---

## 4. Sécurité

### `backend/security.py` (code complet)

```python
import os
from datetime import datetime, timedelta, timezone

import jwt
from passlib.context import CryptContext

SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY or SECRET_KEY == "dev_secret_key_a_changer":
    raise RuntimeError(
        "SECRET_KEY absente ou laissée à la valeur par défaut du dépôt. "
        "Génère une clé aléatoire (python3 -c \"import secrets; print(secrets.token_urlsafe(48))\") "
        "et fournis-la via la variable d'environnement SECRET_KEY (fichier .env, jamais versionné) "
        "avant de démarrer le serveur. Avec la valeur par défaut, n'importe qui la connaissant peut "
        "forger une session valide pour n'importe quel compte."
    )
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 12

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return pwd_context.verify(password, hashed)


def create_access_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    payload = {"sub": user_id, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except jwt.PyJWTError:
        return None
```

**Points clés** : `hash_password`/`verify_password` utilisés à la fois pour les mots de passe utilisateurs
ET pour le code d'accès du lien communautaire (même mécanisme, un code d'accès est traité comme un mot de
passe). Session = cookie JWT `httponly`, `samesite=lax`, `secure` piloté par la variable d'environnement
`COOKIE_SECURE` (doit être `false` en HTTP simple, `true` une fois derrière un vrai HTTPS — voir §13). Durée
de session : 12h. Le module refuse de s'importer (donc l'API refuse de démarrer) si `SECRET_KEY` est absente
ou laissée à la valeur par défaut du dépôt — protection contre un déploiement non sécurisé par oubli.

---

## 5. Moteur de formules

Mini-langage d'expression, **sans `eval`/`exec`**, utilisé pour trois usages dans la définition JSON d'un
formulaire : champs `calculated` (résultat numérique/texte), conditions `relevant` (affichage
conditionnel), conditions `alert_if` (déclenchement d'alerte informative, jamais bloquante). Il existe
**deux implémentations totalement indépendantes mais fonctionnellement identiques** : celle-ci en Python
(côté serveur, qui fait autorité) et une réimplémentation en JavaScript (dupliquée dans `survey.html`,
`community.html` et `form_builder.html` — voir §10/§11) pour le calcul en direct dans le navigateur, y
compris hors-ligne.

### `backend/formula_engine.py` (code complet)

```python
"""
Moteur de formules sécurisé — v3.

Ajoute les fonctions les plus utilisées dans les vrais formulaires Kobo/XLSForm :
- if(condition, valeur_si_vrai, valeur_si_faux)  → variables dérivées, scores, tranches
- selected(champ, 'valeur')                       → "cette réponse a-t-elle été cochée
                                                       dans un choix multiples ?"
- coalesce(a, b, ...)                             → première valeur non vide

Contrairement aux fonctions mathématiques (pow, sqrt, abs, round), ces trois
fonctions ne doivent PAS convertir leurs arguments en nombre — if() doit pouvoir
renvoyer du texte, selected() doit recevoir une liste telle quelle. Elles sont
donc classées à part (GENERIC_FUNCTIONS) et reçoivent leurs arguments "bruts".

Sert à la fois pour :
- les champs "calculated" (résultat numérique ou texte, ex : IMC, tranche d'âge)
- les conditions d'affichage "relevant" (résultat vrai/faux, logique conditionnelle)
- les conditions d'alerte "alert_if" (résultat vrai/faux)

Toujours sans eval()/exec() : grammaire explicite, fonctions en liste blanche.
"""

import re
import math

NUMERIC_FUNCTIONS = {
    "pow": lambda a, b: math.pow(a, b),
    "sqrt": lambda a: math.sqrt(a),
    "abs": lambda a: abs(a),
    "round": lambda a, n=0: round(a, int(n)),
}


def _fn_if(condition, true_value, false_value):
    return true_value if _truthy(condition) else false_value


def _fn_selected(value, target):
    """Reproduit selected() de XLSForm : vrai si `target` fait partie des
    réponses cochées dans un champ "choix multiples" (une liste), ou si
    `value` est directement égal à `target` pour un champ à choix unique."""
    if isinstance(value, list):
        return str(target) in [str(v) for v in value]
    if value is None:
        return False
    return str(value) == str(target)


def _fn_coalesce(*args):
    for a in args:
        if a is not None and a != "" and not (isinstance(a, list) and len(a) == 0):
            return a
    return args[-1] if args else None


GENERIC_FUNCTIONS = {
    "if": _fn_if,
    "selected": _fn_selected,
    "coalesce": _fn_coalesce,
}

_TOKEN_PATTERN = re.compile(
    r"""\s*(?:
        (\d+\.?\d*)
        |([a-zA-Z_][a-zA-Z0-9_]*)
        |'([^']*)'
        |"([^"]*)"
        |(==|!=|<=|>=)
        |([+\-*/(),<>])
    )""",
    re.VERBOSE,
)


class FormulaError(Exception):
    pass


def _tokenize(expr: str):
    tokens = []
    pos = 0
    while pos < len(expr):
        match = _TOKEN_PATTERN.match(expr, pos)
        if not match or match.end() == pos:
            raise FormulaError(f"Caractère non autorisé près de : '{expr[pos:pos + 10]}'")
        number, ident, str1, str2, cmp2, op1 = match.groups()
        if number is not None:
            tokens.append(("number", float(number)))
        elif ident is not None:
            tokens.append(("ident", ident))
        elif str1 is not None:
            tokens.append(("string", str1))
        elif str2 is not None:
            tokens.append(("string", str2))
        elif cmp2 is not None:
            tokens.append(("op", cmp2))
        elif op1 is not None:
            tokens.append(("op", op1))
        pos = match.end()
    return tokens


def _to_number(v):
    if v is None:
        return float("nan")
    if isinstance(v, bool):
        raise FormulaError("Impossible d'utiliser une valeur oui/non dans un calcul numérique")
    try:
        return float(v)
    except (TypeError, ValueError):
        raise FormulaError(f"Valeur non numérique utilisée dans un calcul : '{v}'")


def _try_to_number(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _truthy(v):
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip() != ""
    return bool(v)


def _compare(op, left, right):
    # Un champ pas encore rempli (valeur manquante) ne peut jamais rendre vraie
    # une comparaison d'ordre (< > <= >=) — on ne peut pas dire si "vide" est
    # plus petit ou plus grand qu'un nombre. Seules l'égalité et la différence
    # restent définies dans ce cas (une valeur manquante n'égale jamais une
    # vraie valeur).
    if left is None or right is None:
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        return False

    l_num = _try_to_number(left)
    r_num = _try_to_number(right)
    if l_num is not None and r_num is not None:
        l, r = l_num, r_num
    else:
        l, r = str(left), str(right)
    if op == "==":
        return l == r
    if op == "!=":
        return l != r
    if op == "<":
        return l < r
    if op == ">":
        return l > r
    if op == "<=":
        return l <= r
    if op == ">=":
        return l >= r
    raise FormulaError(f"Opérateur inconnu : {op}")


class _Parser:
    def __init__(self, tokens, values):
        self.tokens = tokens
        self.pos = 0
        self.values = values

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def advance(self):
        t = self.peek()
        self.pos += 1
        return t

    def _resolve_ident(self, name):
        if name == "true":
            return True
        if name == "false":
            return False
        if name not in self.values:
            raise FormulaError(f"Champ inconnu dans la formule : {name}")
        v = self.values[name]
        if v == "":
            return None
        return v

    def parse_primary(self):
        t = self.peek()
        if t is None:
            raise FormulaError("Formule incomplète")

        if t[0] == "number":
            self.advance()
            return t[1]

        if t[0] == "string":
            self.advance()
            return t[1]

        if t[0] == "ident":
            if t[1] in ("and", "or", "not"):
                raise FormulaError(f"'{t[1]}' n'est pas attendu à cet endroit de la formule")
            self.advance()
            nxt = self.peek()
            if nxt and nxt[1] == "(":
                self.advance()
                args = []
                if self.peek() and self.peek()[1] != ")":
                    args.append(self.parse_or())
                    while self.peek() and self.peek()[1] == ",":
                        self.advance()
                        args.append(self.parse_or())
                if not self.peek() or self.peek()[1] != ")":
                    raise FormulaError("Parenthèse manquante")
                self.advance()
                if t[1] in NUMERIC_FUNCTIONS:
                    return NUMERIC_FUNCTIONS[t[1]](*[_to_number(a) for a in args])
                if t[1] in GENERIC_FUNCTIONS:
                    return GENERIC_FUNCTIONS[t[1]](*args)
                raise FormulaError(f"Fonction non autorisée : {t[1]}")
            return self._resolve_ident(t[1])

        if t[1] == "(":
            self.advance()
            v = self.parse_or()
            if not self.peek() or self.peek()[1] != ")":
                raise FormulaError("Parenthèse manquante")
            self.advance()
            return v

        if t[1] == "-":
            self.advance()
            return -_to_number(self.parse_primary())

        raise FormulaError("Formule invalide")

    def parse_mul_div(self):
        v = self.parse_primary()
        while self.peek() and self.peek()[1] in ("*", "/"):
            op = self.advance()[1]
            rhs = self.parse_primary()
            v = _to_number(v) * _to_number(rhs) if op == "*" else _to_number(v) / _to_number(rhs)
        return v

    def parse_add_sub(self):
        v = self.parse_mul_div()
        while self.peek() and self.peek()[1] in ("+", "-"):
            op = self.advance()[1]
            rhs = self.parse_mul_div()
            v = _to_number(v) + _to_number(rhs) if op == "+" else _to_number(v) - _to_number(rhs)
        return v

    def parse_comparison(self):
        v = self.parse_add_sub()
        if self.peek() and self.peek()[1] in ("==", "!=", "<", ">", "<=", ">="):
            op = self.advance()[1]
            rhs = self.parse_add_sub()
            return _compare(op, v, rhs)
        return v

    def parse_not(self):
        if self.peek() and self.peek()[0] == "ident" and self.peek()[1] == "not":
            self.advance()
            return not _truthy(self.parse_not())
        return self.parse_comparison()

    def parse_and(self):
        v = self.parse_not()
        while self.peek() and self.peek()[0] == "ident" and self.peek()[1] == "and":
            self.advance()
            rhs = self.parse_not()
            v = _truthy(v) and _truthy(rhs)
        return v

    def parse_or(self):
        v = self.parse_and()
        while self.peek() and self.peek()[0] == "ident" and self.peek()[1] == "or":
            self.advance()
            rhs = self.parse_and()
            v = _truthy(v) or _truthy(rhs)
        return v


def evaluate_formula(formula: str, values: dict):
    """Évalue une formule et renvoie sa valeur (nombre, texte ou booléen selon le cas)."""
    tokens = _tokenize(formula)
    parser = _Parser(tokens, values)
    result = parser.parse_or()
    if parser.pos < len(tokens):
        raise FormulaError("Formule invalide (caractères en trop)")
    return result


def evaluate_condition(condition: str, values: dict, default: bool = True) -> bool:
    """Évalue une condition (relevant / alert_if) et renvoie un booléen.
    En cas d'erreur dans la formule (faute de frappe, champ inconnu...), renvoie
    `default` plutôt que de faire planter tout l'enregistrement du formulaire."""
    if not condition or not condition.strip():
        return True
    try:
        return _truthy(evaluate_formula(condition, values))
    except FormulaError:
        return default


def validate_formula_syntax(expr: str):
    """Vérifie qu'une formule/condition est syntaxiquement valide, sans l'évaluer
    contre de vraies données (utile pour valider un formulaire importé avant de
    l'enregistrer). Renvoie None si tout va bien, sinon un message d'erreur."""
    if not expr or not expr.strip():
        return None
    try:
        tokens = _tokenize(expr)
        fake_values = {}
        for t in tokens:
            if (t[0] == "ident" and t[1] not in ("and", "or", "not", "true", "false")
                    and t[1] not in NUMERIC_FUNCTIONS and t[1] not in GENERIC_FUNCTIONS):
                fake_values[t[1]] = "1"
        evaluate_formula(expr, fake_values)
        return None
    except FormulaError as e:
        return str(e)
    except Exception as e:
        return f"Erreur inattendue : {e}"
```

**Grammaire (priorité croissante)** : `or` → `and` → `not` → comparaison (`== != < > <= >=`) → `+ -` →
`* /` → primaire (nombre, chaîne, identifiant/appel de fonction, parenthèses, moins unaire).
**Fonctions numériques** (arguments coercés en `float`) : `pow`, `sqrt`, `abs`, `round`. **Fonctions
génériques** (arguments bruts) : `if`, `selected`, `coalesce`. Une valeur manquante (`None`) ne peut jamais
rendre vraie une comparaison d'ordre, seulement `==`/`!=`. `round()` suit l'arrondi Python (« au pair »).

**Réimplémentation JavaScript identique** (dans `survey.html`, `community.html`, `form_builder.html`) : même
tokenizer, même grammaire, mêmes fonctions, y compris `pythonRound()` qui reproduit fidèlement l'arrondi «
au pair » de Python (calcul par chaîne décimale + `BigInt`, pas par simple `Math.round`), et `truthy()` qui
traite `NaN` comme vrai (`v !== 0`) pour garantir l'identité stricte avec la sémantique Python
(`nan != 0 == True`) — commentaire explicite dans le code sur ce point, cité pour garantir que le calcul
fait hors-ligne dans le navigateur donne exactement le même résultat que le recalcul serveur.

---

## 6. Validation de soumission

### `backend/form_validation.py` (code complet)

```python
"""
Vérifie une soumission de formulaire par rapport à sa définition :
- champs obligatoires présents (sauf si masqués par une condition d'affichage)
- bornes min/max, et un format (expression régulière) pour les champs texte
- recalcule les champs "calculated" côté serveur (ne fait jamais confiance
  au calcul déjà fait dans le navigateur), en les traitant PAR ORDRE DE
  DÉPENDANCES (un calcul peut référencer un autre calcul défini plus bas
  dans le formulaire — corrige un bug où l'ordre du document seul faisait
  échouer ce genre de calcul)
- applique la logique conditionnelle ("relevant") : au niveau d'un champ,
  et au niveau d'une SECTION ENTIÈRE (comme un begin_group conditionnel de
  Kobo) — si la section n'est pas pertinente, tous ses champs sont ignorés
  et effacés d'un coup
- calcule les alertes ("alert_if") : informatives, elles ne bloquent jamais
  l'enregistrement, contrairement aux erreurs de validation

Point important : les champs de SAISIE non envoyés par le navigateur (jamais
rendus, ou décochés) sont explicitement pré-remplis à None avant d'évaluer
les conditions — sinon une condition qui les référence échoue silencieusement
et retombe sur son défaut, ce qui pouvait à tort rendre visible/obligatoire
un champ qui aurait dû rester masqué. Les champs CALCULÉS ne sont volontairement
PAS pré-remplis : leur absence temporaire (tant qu'ils n'ont pas encore été
évalués) est ce qui permet à la boucle de dépendances de savoir qu'il faut
réessayer plus tard, plutôt que de produire silencieusement un résultat vide.
"""

import re

from formula_engine import evaluate_formula, evaluate_condition, FormulaError


def _is_empty(value) -> bool:
    """Un champ est considéré vide si : rien, chaîne vide, ou liste vide
    (ce dernier cas concerne les questions "choix multiples" où aucune case
    n'a été cochée)."""
    if value is None:
        return True
    if isinstance(value, (str, list)):
        return len(value) == 0
    return False


def _clear_section_fields(section: dict, processed: dict):
    """Efface la valeur de tous les champs d'une section (utilisé quand la
    section entière n'est pas pertinente)."""
    for field in section.get("fields", []):
        processed[field["id"]] = None


def _input_field_ids(form_definition: dict):
    """Identifiants des champs de SAISIE (non calculés) uniquement — voir la
    note du docstring de module : seuls ceux-ci sont pré-remplis à None."""
    ids = []
    for section in form_definition.get("sections", []):
        for field in section.get("fields", []):
            if "id" in field and field.get("type") != "calculated":
                ids.append(field["id"])
    return ids


def validate_and_process_submission(form_definition: dict, responses: dict):
    # Les champs de saisie jamais envoyés deviennent explicitement vides,
    # AVANT d'évaluer la moindre condition (corrige un champ conditionnel
    # bloquant à tort une soumission valide).
    processed = {fid: None for fid in _input_field_ids(form_definition)}
    processed.update(responses)

    errors = []
    alerts = []

    # --- Phase 1 : pertinence (sections puis champs), avant tout calcul ------
    irrelevant_fields = set()
    for section in form_definition.get("sections", []):
        sec_relevant = section.get("relevant")
        if sec_relevant and not evaluate_condition(sec_relevant, processed, default=True):
            _clear_section_fields(section, processed)
            for f in section.get("fields", []):
                irrelevant_fields.add(f["id"])
            continue
        for field in section.get("fields", []):
            rel = field.get("relevant")
            if rel and not evaluate_condition(rel, processed, default=True):
                processed[field["id"]] = None
                irrelevant_fields.add(field["id"])

    # --- Phase 2 : calculs par ordre de dépendances ---------------------------
    # Passes répétées jusqu'à stabilisation : un calcul qui échoue parce qu'il
    # dépend d'un autre calcul pas encore fait est simplement réessayé au tour
    # suivant, jusqu'à ce que plus aucun ne progresse (dépendance circulaire ou
    # champ réellement inconnu — alors signalé comme erreur, pas de boucle infinie).
    calc_fields = []
    for section in form_definition.get("sections", []):
        for field in section.get("fields", []):
            if field.get("type") == "calculated" and field["id"] not in irrelevant_fields:
                calc_fields.append(field)

    pending = {f["id"]: f for f in calc_fields}
    while pending:
        progressed = False
        for fid in list(pending.keys()):
            field = pending[fid]
            formula = field.get("formula", "")
            try:
                processed[fid] = evaluate_formula(formula, processed)
                del pending[fid]
                progressed = True
            except FormulaError:
                continue  # dépend probablement d'un calcul pas encore fait -> réessai
            except ZeroDivisionError:
                errors.append(f"{field.get('label', fid)} : division par zéro dans le calcul.")
                del pending[fid]
                progressed = True
            except Exception as e:  # jamais de 500 silencieux sur un calcul cassé
                errors.append(f"{field.get('label', fid)} : erreur de calcul ({e}).")
                del pending[fid]
                progressed = True
        if not progressed:
            for fid, field in pending.items():
                try:
                    evaluate_formula(field.get("formula", ""), processed)
                except Exception as e:
                    errors.append(f"{field.get('label', fid)} : erreur de calcul ({e}).")
            break

    # --- Phase 3 : validation des champs de saisie + alertes -----------------
    for section in form_definition.get("sections", []):
        for field in section.get("fields", []):
            field_id = field["id"]
            if field_id in irrelevant_fields:
                continue
            field_type = field.get("type")
            label = field.get("label", field_id)

            if field_type != "calculated":
                value = processed.get(field_id)
                if field.get("required") and _is_empty(value):
                    errors.append(f"{label} est obligatoire.")
                elif not _is_empty(value):
                    if field_type == "boolean":
                        processed[field_id] = (str(value).lower() == "true")
                    elif field_type == "select_multiple":
                        processed[field_id] = value if isinstance(value, list) else [value]
                    elif field_type in ("number", "decimal"):
                        validation = field.get("validation", {})
                        try:
                            num_value = float(value)
                            processed[field_id] = num_value
                            if "min" in validation and num_value < validation["min"]:
                                errors.append(f"{label} doit être supérieur ou égal à {validation['min']}.")
                            if "max" in validation and num_value > validation["max"]:
                                errors.append(f"{label} doit être inférieur ou égal à {validation['max']}.")
                        except (TypeError, ValueError):
                            errors.append(f"{label} doit être un nombre.")
                    elif field_type == "text":
                        validation = field.get("validation", {})
                        pattern = validation.get("pattern")
                        if pattern:
                            try:
                                if not re.fullmatch(pattern, str(value)):
                                    errors.append(
                                        validation.get("pattern_message")
                                        or f"{label} : le format n'est pas valide."
                                    )
                            except re.error:
                                # Un motif invalide ne doit jamais faire planter une soumission —
                                # il aurait dû être détecté à l'import/l'enregistrement du formulaire.
                                pass

            alert_condition = field.get("alert_if")
            if alert_condition and evaluate_condition(alert_condition, processed, default=False):
                alerts.append({
                    "field_id": field_id,
                    "message": field.get("alert_message") or f"Alerte sur « {label} »",
                    "severity": field.get("alert_severity", "medium"),
                })

    return processed, errors, alerts
```

**Remarque** : cette fonction ne gère PAS `validation.unique` ni `validation.check_against_dob` — ces deux
vérifications (unicité au sein du formulaire, cohérence âge/date de naissance) sont faites séparément dans
`main.py` (`_check_unique_fields`, `_check_age_consistency` — voir §8.6), **après** l'appel à
`validate_and_process_submission`, car elles nécessitent un accès à la base de données (comparer à d'autres
soumissions, ou au patient) que ce module n'a pas.

---

## 7. Validation d'import de formulaire

### `backend/form_import_validation.py` (code complet)

```python
"""
Vérifie qu'un fichier de formulaire importé (généré par une IA, ou écrit à la
main) respecte la structure attendue, avant de l'enregistrer en base.

Format attendu (voir aussi le guide d'import) :
{
  "choice_lists": {
    "oui_non": [{"value": "oui", "label": "Oui"}, {"value": "non", "label": "Non"}]
  },
  "sections": [
    {
      "id": "...", "title": "...",
      "relevant": "...",   (optionnel : condition d'affichage de la SECTION entière)
      "fields": [
        {
          "id": "identifiant_technique", "type": "text|number|decimal|date|
                select_one|select_multiple|boolean|calculated|note",
          "label": "Texte affiché",
          "hint": "Texte d'aide affiché sous la question (optionnel)",
          "required": true,
          "validation": {"min": 0, "max": 100, "pattern": "regex", "pattern_message": "..."},
          "options": [{"value": "...", "label": "..."}],
          "list_name": "oui_non",   (alternative à "options" : référence une liste partagée)
          "formula": "...",
          "relevant": "...",
          "alert_if": "...", "alert_message": "...", "alert_severity": "medium"
        }
      ]
    }
  ]
}
"""

import re

from formula_engine import validate_formula_syntax

ALLOWED_FIELD_TYPES = {
    "text", "number", "decimal", "date", "select_one", "select_multiple",
    "boolean", "calculated", "note",
}
ALLOWED_SEVERITIES = {"low", "medium", "high", "critical"}


def _validate_choice_lists(choice_lists):
    errors = []
    if choice_lists is None:
        return errors, {}
    if not isinstance(choice_lists, dict):
        return ["\"choice_lists\" doit être un objet (nom de liste -> liste de choix)."], {}

    for list_name, options in choice_lists.items():
        if not isinstance(options, list) or len(options) == 0:
            errors.append(f"Liste de choix '{list_name}' : doit contenir au moins une option.")
            continue
        for opt in options:
            if not isinstance(opt, dict) or "value" not in opt or "label" not in opt:
                errors.append(f"Liste de choix '{list_name}' : chaque option doit avoir 'value' et 'label'.")
                break
    return errors, choice_lists


def validate_form_definition(definition):
    errors = []

    if not isinstance(definition, dict):
        return ["Le fichier doit contenir un objet JSON (pas une liste ou un texte brut)."]

    sections = definition.get("sections")
    if not isinstance(sections, list) or len(sections) == 0:
        return ["Le fichier doit contenir au moins une section : \"sections\": [ ... ]."]

    choice_list_errors, choice_lists = _validate_choice_lists(definition.get("choice_lists"))
    errors.extend(choice_list_errors)

    seen_ids = set()

    for s_idx, section in enumerate(sections):
        if not isinstance(section, dict):
            errors.append(f"Section {s_idx + 1} : doit être un objet.")
            continue

        section_relevant = section.get("relevant")
        if section_relevant:
            err = validate_formula_syntax(section_relevant)
            if err:
                errors.append(f"Section {s_idx + 1} ('{section.get('title', '')}') : condition invalide ({err}).")

        fields = section.get("fields")
        if not isinstance(fields, list):
            errors.append(f"Section {s_idx + 1} : \"fields\" doit être une liste.")
            continue

        for f_idx, field in enumerate(fields):
            if not isinstance(field, dict):
                errors.append(f"Section {s_idx + 1}, champ {f_idx + 1} : doit être un objet.")
                continue

            field_id = field.get("id")
            field_type = field.get("type")

            if not field_id or not isinstance(field_id, str):
                errors.append(f"Section {s_idx + 1}, champ {f_idx + 1} : \"id\" manquant ou invalide.")
                continue
            if field_id in seen_ids:
                errors.append(f"Identifiant de champ en double : '{field_id}'.")
            seen_ids.add(field_id)

            if field_type not in ALLOWED_FIELD_TYPES:
                errors.append(
                    f"Champ '{field_id}' : type inconnu '{field_type}' "
                    f"(types valides : {', '.join(sorted(ALLOWED_FIELD_TYPES))})."
                )
                continue

            if not field.get("label"):
                errors.append(f"Champ '{field_id}' : \"label\" manquant.")

            if field_type in ("select_one", "select_multiple"):
                list_name = field.get("list_name")
                if list_name:
                    if list_name not in choice_lists:
                        errors.append(
                            f"Champ '{field_id}' : référence la liste '{list_name}', "
                            f"absente de \"choice_lists\"."
                        )
                else:
                    options = field.get("options")
                    if not isinstance(options, list) or len(options) == 0:
                        errors.append(f"Champ '{field_id}' : options manquantes (ou \"list_name\") pour un champ à choix.")
                    else:
                        for opt in options:
                            if not isinstance(opt, dict) or "value" not in opt or "label" not in opt:
                                errors.append(f"Champ '{field_id}' : chaque option doit avoir 'value' et 'label'.")
                                break

            if field_type == "calculated":
                formula = field.get("formula")
                if not formula:
                    errors.append(f"Champ '{field_id}' : formule manquante pour un champ calculé.")
                else:
                    err = validate_formula_syntax(formula)
                    if err:
                        errors.append(f"Champ '{field_id}' : formule invalide ({err}).")

            for cond_key in ("relevant", "alert_if"):
                cond = field.get(cond_key)
                if cond:
                    err = validate_formula_syntax(cond)
                    if err:
                        errors.append(f"Champ '{field_id}' : condition '{cond_key}' invalide ({err}).")

            severity = field.get("alert_severity")
            if severity and severity not in ALLOWED_SEVERITIES:
                errors.append(
                    f"Champ '{field_id}' : sévérité d'alerte inconnue '{severity}' "
                    f"(valeurs valides : {', '.join(sorted(ALLOWED_SEVERITIES))})."
                )

            validation = field.get("validation")
            if isinstance(validation, dict) and validation.get("pattern"):
                try:
                    re.compile(validation["pattern"])
                except re.error as e:
                    errors.append(f"Champ '{field_id}' : expression régulière invalide ({e}).")

    return errors
```

**Note** : le docstring de module montre `ALLOWED_FIELD_TYPES` sans les types média (`image`/`audio`/
`video`/`file`), alors que `form_builder.html`/`submission.html`/`community.html` les gèrent bel et bien.
Ceci signifie qu'un formulaire IMPORTÉ via fichier (spreadsheet ou JSON collé) contenant un champ média
serait rejeté par cette validation — les champs média ne sont ajoutables qu'à la main via le constructeur
visuel (`form_builder.html`), pas via l'import.

---

## 8. Spécification complète de l'API (`main.py`)

### 8.0 Configuration globale

```python
app = FastAPI(title="Clinique — v0.7")
```

- **Startup** (`@app.on_event("startup")`) : `Base.metadata.create_all(bind=engine)` — crée les tables
  manquantes, n'altère jamais les tables existantes.
- **`GET /health`** — aucune auth. Renvoie `{"status": "ok"}`.
- **Montage statique** : `app.mount("/", StaticFiles(directory="static", html=True), name="static")` — placé
  **impérativement en dernier** dans le fichier, après toutes les routes `/api/...` (sinon il masquerait ces
  dernières, un mount `/` captant tout ce qui n'a pas matché avant).
- `MAX_UPLOAD_SIZE_BYTES = 25 * 1024 * 1024` (25 Mo). `UPLOADS_DIR` créé avec `os.makedirs(exist_ok=True)`
  au chargement.

### 8.1 Authentification (`/api/auth/...`)

```python
class LoginRequest(BaseModel):
    email: str
    password: str
```

**`get_current_user(request) -> User`** : lit le cookie `access_token` → `401` si absent. Décode le JWT →
`401 "Session invalide ou expirée"` si invalide. Charge le `User`, `401 "Compte introuvable ou désactivé"`
si absent ou `is_active` falsy.

**`require_admin(current_user = Depends(get_current_user))`** : `403 "Action réservée aux administrateurs."`
si `role != "admin"`.

**`log_audit(db, request, user, user_email, action, entity_type, entity_id, project_id, details)`** :
construit une `AuditLog` (avec `ip_address = request.client.host` si disponible), `commit()`. **Avale toute
exception** (`except Exception: print(...)`, ne relance jamais) — le journal ne doit jamais faire échouer
l'action réelle. Portée volontairement étroite (voir liste exhaustive en §8.10).

- **`POST /api/auth/login`** : vérifie email/mot de passe (`log_audit(action="login_failed")` si échec),
  vérifie `is_active`, crée le token, pose le cookie (`secure` = `COOKIE_SECURE` env, `httponly`,
  `samesite=lax`, `max_age=12h`), `log_audit(action="login")`, renvoie `{full_name, role}`.
- **`POST /api/auth/logout`** : aucune auth requise, `delete_cookie`, `{"status":"ok"}`.
- **`GET /api/auth/me`** — auth requise. Renvoie `{email, full_name, role}`.

### 8.2 Utilisateurs (`/api/users`) — réservé admin

```python
class UserCreate(BaseModel):
    email: str; full_name: str; password: str; role: str = "operator"
class UserUpdate(BaseModel):
    full_name: Optional[str] = None; role: Optional[str] = None
    is_active: Optional[bool] = None; password: Optional[str] = None
```

- **`GET /api/users`** : tous, triés `created_at asc`. Jamais `hashed_password` dans la sortie.
- **`POST /api/users`** (201) : `409` si email pris ; `422` si rôle hors `{admin, operator}` ; `log_audit`
  `create`/`user`.
- **`PUT /api/users/{id}`** : `404` si absent. Garde-fous : impossible de retirer son propre rôle admin
  (`400`), impossible de désactiver son propre compte (`400`). `log_audit` seulement si un champ a réellement
  changé.
- **`DELETE /api/users/{id}`** : `400` si soi-même ; `409` si le compte a déjà créé des soumissions/cas
  (compte exact affiché) — « désactive-le plutôt » ; sinon suppression réelle + `log_audit`.

### 8.3 Projets (`/api/projects`)

```python
class ProjectCreate(BaseModel): name: str; description: Optional[str] = None
class ProjectOut(ProjectCreate): id: str; created_at: datetime
```

- **`GET /api/projects`** (auth simple) : tous, `created_at desc`.
- **`GET /api/projects/{id}/stats`** : `{patient_count, form_count}` (`form_count` sur formulaires actifs
  uniquement).
- **`POST /api/projects`** (auth simple, PAS admin) : création directe, aucun log (flux normal).
- **`PUT /api/projects/{id}`** : met à jour name/description.
- **`DELETE /api/projects/{id}`** (admin) : cascade manuelle dans l'ordre `Submission` (via `form_id` du
  projet) → `Form` → `Case` → `Patient` → `Project`. `log_audit` `delete`/`project`.

### 8.4 Patients (`/api/patients`)

```python
class PatientCreate(BaseModel):
    project_id: str; first_name: str; last_name: str
    date_of_birth: Optional[date] = None; sex: Optional[str] = None
    phone: Optional[str] = None; id: Optional[str] = None   # id fourni si créé hors-ligne
class PatientUpdate(BaseModel):
    first_name: str; last_name: str
    date_of_birth: Optional[date] = None; sex: Optional[str] = None; phone: Optional[str] = None
```

- **`GET /api/patients`** : params `project_id`, `search`, `page`, `page_size`.
  - Mode **recherche** : chaque mot doit matcher `first_name ILIKE` OU `last_name ILIKE` (mots enchaînés en
    ET, prénom/nom en OU par mot) ; limité à 8 résultats, ignore la pagination.
  - Mode **pagination** (si `page`/`page_size` fourni sans `search`) : total calculé en Python (`len()`,
    pas un `COUNT` SQL), exposé via header `X-Total-Count`.
  - Sinon : tout, non paginé (compat historique).
- **`_flag_potential_duplicates`** : compare `first_name`/`last_name` (lower/strip) exacts au sein du même
  projet, crée une `PotentialDuplicate` si pas déjà signalée dans les deux sens — appelé uniquement pour les
  patients créés **hors-ligne** (id fourni par le client).
- **`_check_phone_unique`** : normalise en retirant espaces/tirets/points, compare au sein du projet (hors
  soi-même en édition) ; téléphone vide jamais en conflit.
- **`POST /api/patients`** (201) : si `id` fourni et déjà existant → renvoie l'existant tel quel (idempotence
  offline). Sinon vérifie téléphone (`409` si conflit), crée, et si créé hors-ligne appelle
  `_flag_potential_duplicates`. Aucun log d'audit (flux normal).
- **`PUT /api/patients/{id}`** : revérifie téléphone (excluant soi-même), met à jour l'identité.
- **`DELETE /api/patients/{id}`** (auth simple) : `409` si soumissions ou cas existent (compte exact) ;
  sinon suppression + `log_audit`.
- **`GET /api/projects/{id}/potential-duplicates`** : paires `open`, ignore silencieusement une paire dont
  un patient a été supprimé depuis.
- **`POST /api/potential-duplicates/{id}/dismiss`** : `status="dismissed"` (jamais de fusion), `log_audit`.

### 8.5 Formulaires (`/api/forms`)

```python
class FormCreate(BaseModel): project_id: str; name: str; form_type: str = "survey"; definition: Dict[str, Any]
class FormUpdate(BaseModel): name: str; form_type: Optional[str] = None; definition: Dict[str, Any]
class PublicLinkToggle(BaseModel): enabled: bool
```

- **`GET /api/forms`** : filtré `is_active=True`, `project_id` optionnel. Liste allégée `[{id, name,
  form_type, project_id}]` (jamais la définition complète).
- **`GET /api/forms/{id}`** : définition complète + `public_link_enabled`.
- **`POST /api/forms`** (admin) : crée, renvoie `{id, name}`.
- **`PUT /api/forms/{id}`** (admin) : `definition` toujours remplacée intégralement.
- **`DELETE /api/forms/{id}`** (admin) : si des soumissions existent → **archive** (`is_active=False`) au
  lieu de supprimer (`log_audit update/form`, réponse `{"status":"archived", ...}`). Sinon suppression
  réelle (`log_audit delete/form`, `{"status":"deleted"}`).

**Lien communautaire — administration :**
- `_generate_access_code(length=8)` : alphabet `ABCDEFGHJKLMNPQRSTUVWXYZ23456789` (sans `0/O/1/I/L`, anti-
  ambiguïté clavier), `secrets.choice` (cryptographiquement sûr).
- **`GET /api/forms/{id}/public-link`** (admin) : `{enabled, has_code}` — jamais le code ni le hash.
- **`PUT /api/forms/{id}/public-link`** (admin, body `PublicLinkToggle`) : active/désactive ; si activation
  et pas encore de hash, génère un premier code (`code` renvoyé **une seule fois** en clair). `log_audit
  update/form`.
- **`POST /api/forms/{id}/public-link/regenerate`** (admin) : génère systématiquement un nouveau code,
  invalidant instantanément tous les appareils déjà débloqués (chaque appel public revérifie le hash
  courant). `log_audit update/form`.

**Export/import de structure :**
- **`GET /api/forms/{id}/export-template`** : `build_template_bytes` → xlsx, `Content-Disposition:
  attachment; filename="{slug}_structure.xlsx"`.
- **`POST /api/forms/import`** (201, admin, multipart `project_id`/`name`/`file`) : `parse_spreadsheet_to_
  sections` (erreur → `422`), `422` si 0 champ trouvé, `validate_form_definition` (`422` si erreurs), `404`
  si projet introuvable, crée le `Form`, renvoie `{id, name, questions_imported}`.

**Export des données recueillies :**
- **`GET /api/forms/{id}/export`** : `format` (csv/xlsx), `start`/`end` (bornes inclusives journée
  complète). Valeur `None` = masqué par une condition (distinct de vide volontaire) ; champs média
  transformés en URL `/api/media/{id}` (reste protégé par auth). `build_data_export_bytes`. `log_audit
  export/form`.
- **`GET /api/forms/{id}/trend`** : regroupe par mois, `{total_submissions, distinct_patients, by_month:
  [{period, submissions, distinct_patients}]}` — compte les personnes distinctes pour ne pas surestimer une
  tendance si une même personne est dépistée plusieurs fois.
- **`GET /api/forms/{id}/status-change`** (`field`, `target_values[]` répétable) : détecte, par patient
  (soumissions triées chronologiquement), le premier basculement d'une valeur *hors*-cible vers une valeur
  *dans* `target_values` — un seul match par patient (le premier observé). Export ou JSON selon `format`.
- **`_age_at(dob, reference_date)`** : âge en années révolues.
- **`GET /api/forms/{id}/export-patients`** : une ligne par **personne** (pas par dépistage), anonymisé
  (identifiant = `patient_id` brut, pas de nom), âge à la dernière soumission de la période,
  `nb_depistages_periode`, et si `status_field`/`status_target_values` fournis : `dernier_resultat_connu` +
  `a_ete_positif_au_moins_une_fois` (agrégé sur **toutes** les soumissions, pas seulement la dernière).

### 8.6 Soumissions (`/api/submissions`)

```python
class SubmissionCreate(BaseModel):
    form_id: str; patient_id: str; case_id: Optional[str] = None
    responses: Dict[str, Any]; collected_at: Optional[datetime] = None
    idempotency_key: Optional[str] = None
class SubmissionUpdate(BaseModel):
    responses: Dict[str, Any]; collected_at: Optional[datetime] = None
    expected_updated_at: Optional[datetime] = None
```

**Mécanisme "référence pour suivi" (S/N-S)** :
- `_find_referral_field_id` : cherche un champ `boolean` marqué `triggers_referral` (un seul par formulaire,
  premier trouvé si plusieurs — non revérifié ici).
- `_check_referral_suggestion` : si réponse truthy ET pas de `Case` `active` déjà existant pour ce
  `(patient_id, project_id)` → `{"suggest_referral": True, "patient_id": ...}`. **Ne crée jamais de `Case`**
  — signale seulement.

**`POST /api/submissions`** (201) :
1. Idempotence : `idempotency_key` déjà présente → renvoie l'existante (`already_recorded: True`, pas de
   clé `referral`).
2. `404` si form/patient introuvables ; `409` si projets incohérents (patient/formulaire) ; `409` si
   `case_id` fourni mais projet incohérent.
3. `validate_and_process_submission` → `422` si erreurs.
4. `_check_unique_fields` (champs `validation.unique`, portée = **ce formulaire seulement**, comparaison
   insensible casse/espaces) → `422` si conflit.
5. `_check_age_consistency` (champs `validation.check_against_dob`, tolérance **±1 an**) → `422` si
   incohérence.
6. Crée la `Submission`. `commit()` protégé par `try/except IntegrityError` : en cas de course sur
   `idempotency_key` (contrainte unique violée par une requête jumelle), `rollback()` puis renvoie
   l'existante.
7. Sinon calcule `_check_referral_suggestion`, renvoie `{id, responses, collected_at, alerts, referral}`.

**`GET /api/submissions/{id}`** : `{id, form_id, form_name, form_definition, patient_id, patient_name,
responses, collected_at, updated_at}` (`form_name`/`patient_name` = `None` si supprimés).

**`PUT /api/submissions/{id}`** : **verrouillage optimiste** — `expected_updated_at` comparé (`!=` strict)
à `submission.updated_at` → `409` si divergence (« quelqu'un d'autre a modifié cette soumission… »).
Revalide entièrement, met à jour `updated_at = utcnow()`.

**`DELETE /api/submissions/{id}`** (auth simple) : `log_audit delete/submission`.

**`GET /api/patients/{id}/submissions`** : toutes, `desc`, avec `field_types` (map id→type, tiré de la
définition **actuelle** — pas historique).

**`GET /api/forms/{id}/submissions`** : vue agrégée. Calcule `referral_status` par soumission (seulement si
champ référence présent) : `not_admitted` (réponse falsy), `admitted` (truthy + cas actif existant),
`pending` (truthy + pas de cas actif). Pagination optionnelle.

### 8.7 Médias (`/api/media`)

```python
ALLOWED_FIELD_TYPES_BY_CONTENT = {"image": ("image/",), "audio": ("audio/",), "video": ("video/",), "file": None}
```

**`_store_uploaded_media`** : `field_type` doit être connu (`400` sinon) ; taille `>25 Mo` → `413`, `0`
octet → `400` ; type de contenu doit matcher le préfixe attendu (sauf `file`, tout accepté) → `400` sinon.
**Nom stocké = `{uuid4()}{ext}`** (jamais le nom original — anti path-traversal/collision) ; nom original
gardé en métadonnée seulement.

**`GET /api/media/{id}`** : `404` si enregistrement absent OU fichier disparu du disque. Toujours protégé
par auth (jamais public, même pour un média venant du lien communautaire).

### 8.8 Cas de suivi (`/api/cases`, `/api/projects/{id}/cases`)

```python
class CaseCreate(BaseModel):
    project_id: str; patient_id: str; case_number: Optional[str] = None
    source_submission_id: Optional[str] = None; enrolled_at: Optional[datetime] = None; id: Optional[str] = None
class CaseUpdate(BaseModel):
    case_number: Optional[str] = None; enrolled_at: Optional[datetime] = None
    expected_updated_at: Optional[datetime] = None
class CaseClose(BaseModel):
    reason: str; status: Optional[str] = "closed"; closed_at: Optional[datetime] = None
```

- `_find_next_visit_field` : champ `date` unique marqué `next_visit_marker`.
- `_serialize_case` (sérialiseur central) : charge patient (ou « Patient supprimé »), `last_submission`,
  `visit_count`, et si un `next_visit_marker` existe et qu'une dernière soumission en porte la valeur :
  `next_visit_at` (parse `date.fromisoformat`, invalide → `None` silencieux) + `is_overdue` (actif ET
  `next_visit_at < aujourd'hui`).
- `_generate_case_number` : `f"CAS-{année}-{count+1:04d}"` — **non atomique**, retry géré à la création.
- **`GET /api/projects/{id}/cases`** : filtre SQL sur `status` seulement ; `search`/`overdue`/`sort=activity`
  faits **en mémoire** après sérialisation complète.
- **`POST /api/cases`** (201, `?force=false`) : idempotence par `id` fourni ; sans `force`, `409` structuré
  (`{msg, existing_case_id, existing_case_number}`) si un cas actif existe déjà pour ce patient ; **boucle
  de retry jusqu'à 5 tentatives** sur `IntegrityError` de la contrainte `(project_id, case_number)` (sauf si
  `case_number` était explicitement fourni par l'appelant → `409` direct sans retry).
- **`PUT /api/cases/{id}`** : `patient_id` **jamais modifiable** ici ; même verrouillage optimiste que les
  soumissions.
- **`DELETE /api/cases/{id}`** : `409` si au moins une visite existe (« clôture-le plutôt »).
- **`POST /api/cases/{id}/close`** : `status` forcé à `closed` si valeur invalide (pas d'erreur levée) ;
  `closed_at` rétroactif possible.
- **`POST /api/cases/{id}/reopen`** : remet `active`, efface `closed_at`/`closed_reason`.
- **`GET /api/cases/{id}/timeline`** : fusionne 3 types d'événements triés par date : `registration`
  (inscription), `follow_up` (une entrée par soumission liée), `closure` (si clôturé).
- **`GET /api/patients/{id}/cases`** : tous les cas du patient, `desc`.
- **`GET /api/projects/{id}/cases/export`** / **`GET /api/projects/{id}/export`** / **`GET
  /api/projects/{id}/cases/stats`** : exports/stats construits directement via `pandas.DataFrame` (pas via
  `spreadsheet_io.py`), CSV avec BOM UTF-8 + séparateur `;` (convention Excel FR).

### 8.9 Endpoints "logique métier moins évidente" — récapitulatif

| Endpoint | Ce qu'il calcule |
|---|---|
| `GET /api/forms/{id}/referral-candidates` | Filet de sécurité : soumissions "Oui" au champ S/N-S mais patient toujours sans cas actif (suggestion ratée/ignorée). |
| `GET /api/forms/{id}/trend` | Soumissions ET personnes distinctes par mois — évite de surestimer une tendance. |
| `GET /api/forms/{id}/status-change` | Détecteur de bascule chronologique hors-cible → cible sur un champ donné, un match par patient. |
| `GET /api/forms/{id}/export-patients` | Export dédoublonné par personne (anonymisé) pour analyse statistique externe. |
| `GET /api/cases/{id}/timeline` | Fusion chronologique inscription + visites + clôture. |

Aucun scoring pondéré nulle part — toute la "logique métier" se résume à du regroupement chronologique par
patient, des comparaisons ensemblistes, et des agrégats simples (comptages, distinct-count).

### 8.10 Journal d'audit (`/api/audit-logs`)

**`GET /api/audit-logs`** (admin) : `project_id`/`action`/`page`/`page_size`, `total` = vrai `COUNT` SQL
(contrairement à la pagination patients).

**Liste exhaustive `action`/`entity_type` produits par le code** : `login`/`login_failed` (sans entity) ;
`create`/`update`/`delete` sur `user` ; `delete` sur `project` ; `delete` sur `patient` ; `update` sur
`potential_duplicate` ; `update`/`delete` sur `form` ; `export` sur `form`/`case_list`/`project_summary` ;
`delete` sur `submission` ; `delete` sur `case` ; `create` sur `patient` et `create` sur `submission`
(**uniquement** via le lien communautaire, `user_email="Lien communautaire"` — seule voie de création de
patient/soumission qui soit loggée, précisément parce qu'il n'y a pas de compte à qui l'attribuer).

### 8.11 Lien communautaire / accès public (`/api/public/forms/{id}/...`)

Accès à **un seul formulaire précis**, sans compte, via une URL + un code d'accès partagé. Chaque route
revérifie le code **à chaque appel** (pas de session révocable séparément) — régénérer le code invalide
instantanément tous les appareils débloqués.

**`verify_public_code(form_id, x_community_code: Header)`** (dépendance FastAPI partagée) :
1. `404 "Lien communautaire introuvable ou désactivé."` si formulaire absent, ou `public_link_enabled`
   falsy, ou pas de hash — **même statut** que "n'existe pas" (ne révèle pas qu'un formulaire désactivé
   existe).
2. `403 "Code d'accès incorrect."` si header absent ou `verify_password` échoue.
3. Renvoie le `Form` (réutilisé comme dépendance injectée).

- **`GET /api/public/forms/{id}`** : `{id, name, project_id, definition}`.
- **`POST /api/public/forms/{id}/patients`** (201) : body sans `project_id` (jamais fourni par le client) —
  **`data["project_id"] = form.project_id` écrase explicitement** toute valeur qu'un client bricolé aurait
  injectée. `log_audit(user_email="Lien communautaire", ...)`.
- **`POST /api/public/forms/{id}/submissions`** (201) : body **sans `case_id`** (le lien communautaire ne
  fait que de l'Enquête, jamais de Suivi). Même logique d'idempotence/validation que la voie authentifiée.
  `created_by=None`. `log_audit`. Réponse **sans `referral`** (pas de Suivi ici).
- **`POST /api/public/forms/{id}/media/upload`** : identique à la voie authentifiée, `uploaded_by=None`.

### 8.12 Idempotency_key — mécanique transverse

Champ optionnel sur `SubmissionCreate`/`PublicSubmissionCreate`, généré côté client (clé de l'`outbox`
IndexedDB). Contrainte unique en base. Flux identique dans les deux routes de création de soumission :
recherche préalable → court-circuite tout si trouvée ; sinon crée, et en cas de course au `commit()`
(`IntegrityError`), `rollback()` + relit l'existante. Couvre : double-clic, requête réseau rejouée, rejeu de
la file `outbox` offline. Idempotence par `id` fourni côté client (plus simple, sans race condition gérée
explicitement) pour `Patient`/`Case`. Pas d'équivalent pour `PUT` — celles-ci utilisent le verrouillage
optimiste (`expected_updated_at`), un mécanisme différent.

---

## 9. Import/export Excel (`spreadsheet_io.py`)

### Vue d'ensemble

Deux familles de fonctionnalités, toutes deux basées sur `pandas`/`openpyxl` : (1) export/import de la
**structure** d'un formulaire vers/depuis un fichier tableur (format propre à l'app, inspiré de XLSForm/Kobo
sans en être un — un fichier exporté depuis Kobo.org n'est pas importable tel quel) ; (2) export des
**données recueillies** pour analyse statistique externe (Epi Info, SPSS...). La feuille `Choices` (listes
réutilisables) n'existe qu'au format Excel — un CSV se limite aux options en ligne.

```python
TEMPLATE_COLUMNS = [
    "id", "type", "label", "hint", "required", "min", "max", "pattern", "pattern_message",
    "options", "list_name", "formula", "relevant", "alert_if", "alert_message", "alert_severity",
]
CHOICES_COLUMNS = ["list_name", "value", "label"]
```

### `slugify_py(text) -> str`

1. `unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()` — supprime accents/
   diacritiques.
2. `.lower()`.
3. `re.sub(r"[^a-z0-9]+", "_", s).strip("_")`.
4. Vide → `"champ"`.
5. Commence par un chiffre → préfixe `"q_"`.

### Export de structure — `field_to_row(field)` / `build_template_bytes(form_name, sections, choice_lists)`

Chaque champ devient une ligne : `required` → `"oui"`/`""` ; options inline → `"val:label|val:label"` (colonne
`options` forcée à vide si `list_name` renseigné — mutuellement exclusifs en sortie même si le JSON avait les
deux). Structure du classeur : ligne `begin_group` (convention XLSForm réutilisée) → une ligne par champ →
ligne `end_group`, répété par section ; feuille séparée `Choices`. `form_name` est reçu mais **n'est utilisé
dans aucun calcul** de cette fonction (probablement utilisé par l'appelant pour le nom du fichier téléchargé).

### Import de structure — `parse_spreadsheet_to_sections(content, filename)`

- CSV : `pd.read_csv(sep=None, engine="python")` (délimiteur auto-détecté, tolère `,` ou `;`), pas de
  `choice_lists` (toujours `{}`).
- Excel : feuille `Structure` si présente sinon la première feuille ; `choice_lists` via `_parse_choices_
  sheet` (feuille `Choices`, `{}` si absente/erreur).
- `_clean(value)` : normalise `None`/`NaN`/espaces en `""`.
- `row_to_field(row, used_ids)` : `label` vide → `None` (ligne ignorée). `id` = colonne `id` sinon
  `slugify_py(label)`, **dédupliqué** par suffixe `_2`, `_3`... via un `used_ids` partagé sur tout le
  fichier. `required` reconnaît `oui/true/1/yes/vrai` (insensible casse) sinon absent. `min`/`max` : `float()`,
  ignorés silencieusement si non convertibles. `options` parsée `"v:l|v:l"` (split sur le **premier** `:`
  seulement) **seulement si `list_name` vide** (exclusivité mutuelle).
- Repérage de `begin_group`/`end_group` pour reconstruire les sections (titre = `label` du `begin_group`,
  défaut `"Section {n}"` ; id = `slugify_py(titre)`, défaut `"section_{n}"` ; `relevant` de section = colonne
  `relevant` du `begin_group`). Sans marqueurs → section unique par défaut `{"id":"main", "title":
  "Questions"}`. Jamais de résultat vide (retombe sur la section par défaut).

### Export de données — `_expand_row_and_columns(fields, rows)` / `build_data_export_bytes(...)`

Transforme chaque champ `select_multiple` en **plusieurs colonnes binaires 0/1** (une par option, format
`{field_id}_{valeur_option}`) — directement exploitable statistiquement (chi², régression logistique...).
Noms de colonnes **déduplicés** (`unique_name`) en cas de collision entre une colonne générée et un champ du
formulaire portant le même nom. **Convention `"N/A"`** : une valeur `None` explicitement présente dans la
soumission (champ masqué par une condition, pas juste laissé vide) devient la chaîne `"N/A"` dans l'export
(pour toutes les colonnes binaires d'un `select_multiple` masqué, ou pour un champ simple masqué) — distingue
"non applicable" de "case non cochée"/"réponse vide". Champs `type == "note"` toujours ignorés (pas de
colonne). Sortie xlsx : feuilles `Données` + `Dictionnaire` (libellés des colonnes, hors les 3 colonnes fixes
`id_soumission`/`patient`/`date`). Sortie CSV : séparateur `;`, préfixe BOM UTF-8 (`﻿`) pour compat
Excel FR.

### `build_patient_export_bytes(rows, columns, fmt)`

Plus simple : `rows`/`columns` déjà en forme finale (fournis par l'appelant), pas de dictionnaire de données.
xlsx → feuille `Personnes` ; CSV → même convention `;`/BOM.

---

## 10. Frontend — scripts partagés

### `backend/static/app_shell.js` (code complet)

```javascript
// Injecte la barre latérale (sidebar) commune à toutes les pages internes.
// Chaque page doit avoir un <div id="app-sidebar-slot"></div> et appeler
// initAppShell(currentProjectId) — currentProjectId peut être null.

// Charge icons.js (icônes Lucide hébergées localement, pas de CDN — l'app doit
// pouvoir fonctionner hors ligne) une seule fois, avant que la sidebar en ait besoin.
// Remplit tout élément [data-icon="nom"] avec l'icône Lucide correspondante —
// pour le HTML statique déjà présent dans la page (le HTML généré
// dynamiquement en JS appelle icon('nom') directement dans son template).
function renderIconSlots(root) {
  if (!window.icon) return;
  (root || document).querySelectorAll('[data-icon]').forEach(el => {
    if (!el.dataset.rendered) { el.innerHTML = icon(el.dataset.icon); el.dataset.rendered = '1'; }
  });
}

function loadIconsScript() {
  if (window.icon) return Promise.resolve();
  return new Promise((resolve) => {
    const s = document.createElement('script');
    s.src = '/icons.js';
    s.onload = resolve;
    s.onerror = resolve; // on continue même si ça échoue, avec un repli silencieux
    document.head.appendChild(s);
  });
}

// Charge Dexie puis offline_db.js, dans l'ordre (le second dépend du premier).
function loadScript(src) {
  return new Promise((resolve) => {
    const s = document.createElement('script');
    s.src = src;
    s.onload = resolve;
    s.onerror = resolve;
    document.head.appendChild(s);
  });
}
async function loadOfflineScripts() {
  if (window.__offlineScriptsLoaded) return;
  window.__offlineScriptsLoaded = true;
  await loadScript('/dexie.min.js');
  await loadScript('/offline_db.js');
  // Ouvre la base tout de suite ici, avec un délai de sécurité : si un autre
  // onglet de l'application reste ouvert avec une version plus ancienne du
  // code, l'ouverture peut rester bloquée indéfiniment (mécanisme normal
  // d'IndexedDB) — sans ce délai, la page resterait vide sans aucune
  // explication. Mieux vaut continuer sans le stockage hors-ligne que de
  // bloquer complètement l'affichage de la page.
  try {
    await Promise.race([
      offlineDb.open(),
      new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), 4000)),
    ]);
  } catch (e) {
    console.warn("Stockage local indisponible pour l'instant (peut-être un autre onglet de l'application resté ouvert avec une ancienne version — ferme-le puis recharge). L'application continue sans le cache hors-ligne.", e);
    window.__offlineDbUnavailable = true;
  }
}

// Enregistre le Service Worker (permet à l'app de s'ouvrir hors-ligne) — une
// seule fois, silencieusement ; un navigateur qui ne le supporte pas (rare)
// continue de fonctionner normalement, juste sans le bénéfice hors-ligne.
function registerServiceWorker() {
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(e => console.warn('Service Worker non enregistré :', e));
  }
}

const PROJECT_COLORS = [
  'var(--project-0)', 'var(--project-1)', 'var(--project-2)',
  'var(--project-3)', 'var(--project-4)', 'var(--project-5)',
];

function projectColor(projectId) {
  if (!projectId) return PROJECT_COLORS[0];
  let hash = 0;
  for (let i = 0; i < projectId.length; i++) {
    hash = (hash * 31 + projectId.charCodeAt(i)) >>> 0;
  }
  return PROJECT_COLORS[hash % PROJECT_COLORS.length];
}

// Construit un message d'erreur lisible à partir d'une réponse API en échec.
// FastAPI renvoie tantôt une chaîne (nos propres HTTPException(detail="...")),
// tantôt un tableau d'erreurs de validation Pydantic (ex: champ requis manquant :
// [{loc:["body","patient_id"], msg:"Field required", type:"missing"}]). Sans ce
// helper, afficher `data.detail` tel quel dans un textContent produit
// littéralement la chaîne "[object Object]".
function extractApiError(data, fallback) {
  const d = data && data.detail;
  if (!d) return fallback;
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) {
    return d.map(item => {
      if (typeof item === 'string') return item;
      if (item && typeof item === 'object') {
        const loc = Array.isArray(item.loc) ? item.loc.filter(p => p !== 'body').join('.') : '';
        return loc ? `${loc} : ${item.msg || ''}` : (item.msg || JSON.stringify(item));
      }
      return String(item);
    }).join(' | ');
  }
  if (typeof d === 'object') return d.msg || JSON.stringify(d);
  return String(d);
}

async function initAppShell(activeProjectId) {
  await loadIconsScript();
  await loadOfflineScripts();
  registerServiceWorker();

  let meRes;
  try {
    meRes = await fetch('/api/auth/me', { credentials: 'same-origin' });
  } catch (e) {
    // Hors-ligne : pas de redirection vers /login.html (qui échouerait aussi) —
    // on affiche juste l'indicateur "hors ligne" et on laisse la page gérer
    // elle-même ce qu'elle peut montrer depuis le cache local.
    const slot = document.getElementById('app-sidebar-slot');
    if (slot) slot.innerHTML = `<div class="brand">Clinique</div><div id="online-indicator" class="online-indicator is-offline">Hors ligne</div>`;
    return null;
  }
  if (!meRes.ok) {
    window.location.href = '/login.html';
    return null;
  }
  const user = await meRes.json();

  const projectsRes = await fetch('/api/projects', { credentials: 'same-origin' });
  const projects = projectsRes.ok ? await projectsRes.json() : [];

  const slot = document.getElementById('app-sidebar-slot');
  if (slot) {
    const ic = window.icon || (() => '');
    const projectLinks = projects.map(p => {
      const isActive = p.id === activeProjectId;
      return `
        <a class="nav-link ${isActive ? 'active' : ''}" href="/project_hub.html?project_id=${p.id}">
          <span class="project-dot" style="background:${projectColor(p.id)}"></span>
          ${p.name}
        </a>`;
    }).join('');

    slot.innerHTML = `
      <a class="brand" href="/">${ic('stethoscope')} Clinique</a>
      <div id="online-indicator" class="online-indicator"></div>
      <nav>
        <a class="nav-link ${!activeProjectId ? 'active' : ''}" href="/">${ic('folder')} Tous les projets</a>
        <div class="nav-section-label">Projets</div>
        ${projectLinks || '<div style="font-size:0.8rem;color:var(--ink-faint);padding:4px 10px;">Aucun projet</div>'}
        ${user.role === 'admin' ? `
        <div class="nav-section-label" style="margin-top:var(--space-4);">Administration</div>
        <a class="nav-link" href="/users.html">${ic('user')} Gérer les utilisateurs</a>
        <a class="nav-link" href="/audit_log.html">${ic('clock')} Journal d'activité</a>
        ` : ''}
      </nav>
      <div class="user-box">
        <div class="user-name">${user.full_name || user.email}</div>
        <button id="shell-logout-btn" type="button">${ic('log-out')} Se déconnecter</button>
      </div>
    `;
    if (window.updateOnlineIndicator) updateOnlineIndicator();

    document.getElementById('shell-logout-btn').addEventListener('click', async () => {
      await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' });
      window.location.href = '/login.html';
    });
  }

  // Met en cache ce projet pour une consultation hors-ligne plus tard —
  // silencieux, ne bloque jamais l'affichage de la page en cours.
  if (activeProjectId && window.cacheProjectForOffline) {
    cacheProjectForOffline(activeProjectId);
  }

  return user;
}
```

### `backend/static/sw.js` (code complet)

```javascript
// Service Worker — permet à l'application de s'ouvrir et de rester utilisable
// sans connexion. Deux stratégies distinctes, volontairement différentes :
//
// 1. Fichiers de l'application (HTML/CSS/JS/icônes) : "cache d'abord" — on les
//    a déjà vus, pas besoin du réseau pour les réafficher. Mis à jour en
//    arrière-plan à chaque visite en ligne, sans jamais faire attendre l'agent.
// 2. Appels à l'API (/api/...) : jamais mis en cache ici — les données
//    (patients, cas, soumissions) passent par IndexedDB (voir offline_db.js),
//    gérées explicitement par l'application, pas par un cache HTTP générique
//    qui masquerait la fraîcheur réelle des données.

const CACHE_NAME = 'clinique-app-shell-v1';
const APP_SHELL_FILES = [
  '/', '/login.html', '/style.css', '/app_shell.js', '/icons.js', '/dexie.min.js', '/offline_db.js',
  '/index.html', '/project_hub.html', '/survey.html', '/follow_up.html',
  '/case_detail.html', '/history.html', '/submission.html', '/form_data.html',
  '/form_builder.html', '/users.html', '/audit_log.html', '/community.html',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      // addAll échouerait entièrement si UN SEUL fichier manque (ex: une page
      // pas encore créée) — on ajoute donc individuellement, en ignorant les
      // échecs isolés, pour ne jamais bloquer l'installation du reste.
      return Promise.all(
        APP_SHELL_FILES.map((url) => cache.add(url).catch((e) => console.warn('SW: échec cache', url, e)))
      );
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(names.filter((n) => n !== CACHE_NAME).map((n) => caches.delete(n)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Jamais de mise en cache HTTP générique pour l'API — les données passent
  // par IndexedDB, gérées explicitement (voir offline_db.js).
  if (url.pathname.startsWith('/api/')) return;

  if (event.request.method !== 'GET') return;

  event.respondWith(
    // ignoreSearch : nos pages utilisent des paramètres (?project_id=...,
    // ?form_id=...) qui ne changent jamais LE FICHIER servi, seulement ce
    // qu'il affiche ensuite en JS — sans ça, une adresse avec paramètres ne
    // correspond jamais à l'entrée mise en cache (juste "/follow_up.html"),
    // et la page entière échoue à s'ouvrir hors-ligne.
    caches.match(event.request, { ignoreSearch: true }).then((cached) => {
      const network = fetch(event.request)
        .then((response) => {
          if (response && response.status === 200) {
            const copy = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
          }
          return response;
        })
        .catch(() => cached); // hors-ligne : on retombe sur la version déjà en cache
      return cached || network;
    })
  );
});
```

### `backend/static/offline_db.js` — structure complète (Dexie/IndexedDB)

Fichier de ~730 lignes. Schéma Dexie, par versions successives (chaque version ajoute des tables sans
jamais en retirer, migration Dexie automatique) :

```javascript
const offlineDb = new Dexie('clinique_offline');
// v1
offlineDb.version(1).stores({
  forms: '&id, project_id', patients: '&id, project_id', cases: '&id, project_id, patient_id',
  meta: '&key',
});
// v2 : file d'attente de soumissions hors-ligne
offlineDb.version(2).stores({ /* + */ outbox: '++id, &idempotency_key, form_id, status' });
// v6 (fusionne v3/v4/v5) : patients/cas créés hors-ligne, actions différées, timelines, médias
offlineDb.version(6).stores({
  forms: '&id, project_id', patients: '&id, project_id', cases: '&id, project_id, patient_id',
  meta: '&key', outbox: '++id, &idempotency_key, form_id, status',
  pending_patients: '&id, project_id, status', pending_cases: '&id, project_id, patient_id, status',
  pending_actions: '++id, case_id, status', case_timelines: '&case_id', pending_media: '&id, status',
});
// v7 : lien communautaire — file ENTIÈREMENT séparée, jamais mélangée à celle ci-dessus
offlineDb.version(7).stores({
  community_forms: '&form_id', community_pending_patients: '&id, form_id, status',
  community_outbox: '++id, &idempotency_key, form_id, status', community_pending_media: '&id, status',
});
```

**Fonctions principales (module principal, hors lien communautaire)** :

- `queueOfflineSubmission({formId, patientId, caseId, responses, collectedAt, idempotencyKey})` : ajoute à
  `outbox`, `status: 'pending'`.
- `countPendingSubmissions()`, `getPendingSubmissions()`.
- `isOnline` (variable module, mise à jour par les événements `online`/`offline`) + `updateOnlineIndicator()`
  : met à jour le texte/classe de l'indicateur sidebar, affiche/masque un bouton "Synchroniser maintenant"
  (créé dynamiquement) si des éléments sont en attente — nécessaire car `navigator.onLine`/l'événement
  `online` ne sont pas fiables (notamment sous throttling DevTools).
- `checkServerReachable()` : `fetch('/health', {cache:'no-store', signal: AbortSignal.timeout(4000)})` — vrai
  ping serveur, pas juste "le Wi-Fi est branché".
- `cacheProjectForOffline(projectId)` : si en ligne, récupère en parallèle projet/formulaires(résumé)/
  patients/cas actifs, PUIS récupère le détail complet de chaque formulaire (la liste ne contient pas la
  définition) et la timeline de chaque cas actif individuellement, puis remplace **uniquement** les entrées
  de ce projet dans `forms`/`patients`/`cases`/`case_timelines` (jamais toute la base — ne détruit pas le
  cache d'un autre projet), enregistre `last_cached_{projectId}` et `project_info_{projectId}` dans `meta`.
  Tout échec est avalé silencieusement (juste "pas à jour", jamais bloquant).
- `getCachedProjectInfo`, `getLastCachedAt`, `getCachedPatients/Cases/Forms(projectId)`,
  `getCachedPatientById/CaseById(id)`, `getCachedCaseTimeline(caseId)`.
- `generateOfflineId()` : `crypto.randomUUID()` ou repli `'off-' + Date.now() + random`.
- `createPatientOffline({...})` / `createCaseOffline({...})` : génèrent un id définitif tout de suite,
  écrivent à la fois dans la table de consultation (`patients`/`cases`, visible immédiatement) ET dans la
  file d'attente (`pending_patients`/`pending_cases`).
- `queueCloseCaseOffline` / `queueReopenCaseOffline` / `queueUpdateCaseOffline` / `queueDeleteCaseOffline` :
  mémorisent l'action dans `pending_actions` (avec son `type`) ET reflètent tout de suite le changement dans
  le cache local `cases` (l'écran ne reste jamais bloqué en attendant le réseau) — sauf suppression, qui
  retire tout de suite l'entrée du cache local.
- `storeMediaOffline(blob, fieldType, filename, contentType)` : stocke le **fichier lui-même** (Blob, pas
  une référence) dans `pending_media`, id local préfixé `'local:'`.
- `syncPendingData()` : envoie tout, **dans l'ordre des dépendances** — patients → cas → actions (close/
  reopen/update/delete) → soumissions (en résolvant d'abord chaque média `'local:...'` référencé via
  `syncOneMediaFile`, avant d'envoyer la soumission qui le référence). S'arrête (`break`) à la première
  erreur réseau franche (pas juste un 4xx) pour ne pas insister en boucle hors-ligne ; se relance
  automatiquement 1,5s après l'événement `online`.
- Garde-fou multi-onglets : `offlineDb.on('versionchange', () => offlineDb.close())` +
  `offlineDb.on('blocked', ...)` — évite qu'un onglet resté ouvert avec un ancien code bloque indéfiniment
  l'ouverture de la base après une mise à jour.

**Fonctions "lien communautaire"** (préfixées `community*`/`Community`, **jamais mélangées** aux tables
principales) : `cacheCommunityForm(form)`, `getCachedCommunityForm(formId)`,
`queueCommunityPatientOffline`, `storeCommunityMediaOffline`, `getCommunityOfflineMediaObjectUrl`,
`queueCommunitySubmissionOffline`, `countCommunityPending(formId)`, et `syncCommunityQueue(formId, code)` —
même principe général que la file principale, mais chaque appel réseau porte le code d'accès (`X-Community-
Code`), et chaque appel a un délai d'expiration explicite `COMMUNITY_FETCH_TIMEOUT_MS = 8000` (ajouté pour
échouer clairement en quelques secondes plutôt que de bloquer indéfiniment quand le serveur est injoignable
— ex: téléphone passé sur ses données mobiles au lieu du Wi-Fi local), avec un message d'erreur uniforme
`communityNetworkErrorMessage()` = *"Connexion au serveur introuvable — vérifie que tu es sur le même
réseau Wi-Fi que l'ordinateur."*.

### `backend/static/community.html` — page publique (lien communautaire)

Page autonome, sans sidebar/compte, pensée mobile d'abord. Deux écrans : **gate** (saisie du code d'accès,
avec code mémorisé dans `localStorage` par `form_id` pour les visites suivantes, y compris pour retrouver le
formulaire en cache si complètement hors-ligne) et **form** (formulaire rendu dynamiquement).

Réimplémente un **troisième exemplaire** du moteur de formules JS (même grammaire que `formula_engine.py` —
voir §5), utilisé pour `recomputeCalculatedFields`/`recomputeConditions`/alertes en direct. Champs média :
aperçu local immédiat (`URL.createObjectURL`), upload direct si en ligne, sinon `storeCommunityMediaOffline`.
Soumission : crée le patient (`POST /api/public/forms/{id}/patients`, avec repli
`queueCommunityPatientOffline` si hors-ligne) puis la soumission (`POST .../submissions`, repli
`queueCommunitySubmissionOffline`), jeton d'idempotence généré à l'ouverture et régénéré après chaque envoi.
Tous les appels réseau ont le timeout de 8s décrit ci-dessus. Enregistre son propre Service Worker au
démarrage (même `sw.js` que le reste de l'app, `community.html` fait partie de `APP_SHELL_FILES`).

---

## 11. Frontend — pages de l'application

*(Résumé fonctionnel de chaque page ; `community.html`, `app_shell.js`, `sw.js`, `offline_db.js` déjà
couverts en détail au §10.)*

### `index.html` — Liste des projets
Page d'accueil (`start_url` du manifeste PWA). Grille de cartes projet (`GET /api/projects`, stats par
projet via `GET /api/projects/{id}/stats` — chargées **séquentiellement**, pas en parallèle) + formulaire de
création (`POST /api/projects`). Pas de repli hors-ligne.

### `project_hub.html` — Hub d'un projet
Page de routage entre les deux modules : carte "Enquête" (`survey.html`) et carte "Suivi" (`follow_up.html`).
`GET /api/projects/{id}` seulement.

### `project.html` — Redirection (page obsolète)
Fichier fantôme conservé pour rediriger les anciens favoris vers `project_hub.html` (`window.location.
replace`). Aucun appel API.

### `survey.html` — Module Enquête (cœur applicatif)
4 onglets : **SOMMAIRE** (stats + mini-graphique en barres des soumissions sur 7 jours, calculé en excluant
les formulaires `form_type !== 'survey'` pour éviter un double comptage avec le Suivi), **FORMULAIRE**
(liste, création, import Excel via `POST /api/forms/import`, suppression avec archivage automatique côté
serveur si des données existent), **DONNÉES** (le plus riche : filtre de période, tableau avec filtres de
colonnes façon Excel, export CSV/xlsx, bloc Tendance, **détecteur de changement de statut** — interface pour
`GET /api/forms/{id}/status-change`), **PARAMÈTRES** (édition projet, export résumé, zone dangereuse
suppression). Repli hors-ligne sur `getCachedForms`/`getCachedProjectInfo` pour les listes ; **aucun** repli
pour l'onglet DONNÉES (soumissions jamais mises en cache pour consultation).

### `follow_up.html` — Module Suivi (tableau de bord + gestion des dossiers)
4 onglets : **TABLEAU DE BORD** (6 stat-box, cas récemment actifs, alerte doublons, formulaire "Nouveau
dossier patient" avec recherche/création de patient + `POST /api/cases`, gestion du conflit 409 "déjà un
dossier actif" via confirmation + `?force=true`, repli hors-ligne complet via `createPatientOffline`/
`createCaseOffline`), **CAS ACTIFS**/**CAS CLÔTURÉS** (filtres de colonnes façon Excel, modale de clôture
partagée, suppression groupée avec repli `queueDeleteCaseOffline`), **FORMULAIRES** (filtré sur `form_type`
`follow_up`/`closure`). Repli hors-ligne systématique via `getCachedCases` (note : le cache ne contient QUE
les cas **actifs** — les compteurs clôturés/perdus de vue restent à `—` hors-ligne plutôt qu'un faux zéro).

### `case_detail.html` — Fiche/timeline d'un dossier
En-tête patient + actions (nouvelle visite, modifier, clôturer/réouvrir, supprimer si 0 visite). Timeline
triée du plus récent au plus ancien (3 types d'événements, rendu distinct par type). Édition avec
verrouillage optimiste (`expected_updated_at`, `409` → rechargement auto après 1,8s). Repli hors-ligne
complet sur toutes les actions d'écriture.

### `history.html` — Historique complet d'un patient
Transverse : dossiers de suivi du patient (avec transfert possible vers le Module Suivi) + toutes ses
soumissions Enquête. Suppression du patient proposée seulement si aucune donnée rattachée. Pas de repli
hors-ligne.

### `submission.html` — Formulaire de saisie (la page la plus centrale)
4 modes selon les query params : Enquête (`form_id` seul), Consultation (`patient_id`+`form_id`), Suivi/
visite (`case_id`+`form_id`), édition (`submission_id`). Rend dynamiquement la `definition` JSON, réimplémente
le moteur de formules JS (voir §5), gère l'upload média avec repli hors-ligne (fichier stocké en Blob local),
jeton d'idempotence, verrouillage optimiste en édition, mise à jour de la fiche patient avant soumission
finale, et affiche la **suggestion de transfert vers Suivi** (`referral.suggest_referral`) après une
soumission Enquête. Intégration hors-ligne la plus poussée de toute l'app ; seule la correction d'une
soumission existante n'a aucun repli offline.

### `form_data.html` — Vue tabulaire des soumissions (variante autonome)
Équivalent de l'onglet DONNÉES de `survey.html` mais sans pagination/période — tout l'historique d'un coup,
colonnes dérivées de la **définition** du formulaire (stables même si des réponses ont des champs vides),
pas des clés de la première réponse. Bannière de candidats au transfert Suivi (`referral-candidates`). Pas
de repli hors-ligne.

### `form_builder.html` — Créateur/éditeur de formulaire (admin uniquement)
Interface visuelle façon Kobo/XLSForm, jamais de JSON écrit à la main. Modèle interne riche (sections/
questions/listes de choix réutilisables avec `uid` internes), génération d'identifiants techniques stable
par slugification (sauf si fixé manuellement), réglages spécifiques par type de champ (unique, vérification
d'âge, marqueur de prochaine visite — un seul par formulaire, marqueur S/N-S — un seul par formulaire),
blocs de condition (`relevant`/`alert_if`) construits visuellement puis convertis en chaîne de formule. Mode
Aperçu interactif (troisième réimplémentation du moteur de formules JS). Panneau de gestion du lien
communautaire (activation, code affiché une seule fois, régénération). Pas de repli hors-ligne — page
purement administrative.

### `users.html` — Gestion des comptes (admin)
CRUD utilisateurs. Protections "je ne peux pas m'auto-modifier/désactiver/supprimer" faites côté client (en
plus du serveur qui les impose réellement).

### `audit_log.html` — Journal d'activité (admin)
Consultation paginée, filtre par type d'action, badges colorés, libellés français des types d'entité.

### `duplicates.html` — Doublons potentiels de patients
Liste les paires détectées après synchronisation offline. Jamais de fusion automatique, bouton "écarter"
uniquement.

### `login.html` — Page de connexion
Seule page sans `app_shell.js`/sidebar. Formulaire minimal, pas d'intégration hors-ligne (structurellement
impossible).

### `manifest.json` (contenu exact)

```json
{
  "name": "Clinique App",
  "short_name": "Clinique",
  "description": "Enquête et suivi clinique — Module Enquête (KoboToolbox-like) et Module Suivi (CommCare-like)",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#F3F5F4",
  "theme_color": "#2095F3",
  "icons": [
    { "src": "/icon-192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "/icon-512.png", "sizes": "512x512", "type": "image/png" }
  ]
}
```

---

## 12. Design system (`style.css`)

### Variables CSS (`:root`)

**Couleurs de base** : `--bg: #F3F5F4`, `--surface: #FFFFFF`, `--surface-muted: #EEF2F1`, `--ink: #1C2B2E`,
`--ink-muted: #5B6B6E`, `--ink-faint: #6B7A7C` (corrigé pour contraste WCAG AA — 4,1:1), `--border: #E2E8E6`,
`--border-strong: #CBD5D3`.

**Action principale** : `--primary: #2095F3`, `--primary-dark: #0D7BD6`, `--primary-soft: #E7F3FE`.
**Action secondaire** (slate, volontairement pas teal) : `--secondary: #475569`, `--secondary-dark: #334155`,
`--secondary-soft: #F1F5F9`. **Succès** : `--success: #15803D`, `--success-dark: #14532D`,
`--success-soft: #ECFDF5`.

**Sévérités d'alerte** (conventionnelles, reconnaissables d'un coup d'œil) :
- Low : `--sev-low-bg:#EFF8FF`, `--sev-low-fg:#0369A1`, `--sev-low-border:#BAE6FD`
- Medium : `--sev-medium-bg:#FFFBEB`, `--sev-medium-fg:#92400E`, `--sev-medium-border:#FDE68A`
- High : `--sev-high-bg:#FFF1F2`, `--sev-high-fg:#9F1239`, `--sev-high-border:#FECDD3`
- Critical : `--sev-critical-bg:#FEF2F2`, `--sev-critical-fg:#B91C1C`, `--sev-critical-border:#FCA5A5`

**Palette d'accent par projet** (façon onglets de dossiers médicaux, via `projectColor(id)`) :
`--project-0:#0F766E` (teal), `--project-1:#B45309` (orange brûlé), `--project-2:#7C3AED` (violet, Module
Suivi), `--project-3:#BE185D` (magenta), `--project-4:#1D4ED8` (bleu profond), `--project-5:#15803D` (vert).

**Espacement** (échelle unique, toute valeur hors échelle "à corriger") : `--space-1:4px` … `--space-8:64px`
(doublement approximatif à chaque cran : 4/8/12/16/24/32/48/64).

**Rayons/ombres** : `--radius-sm:8px`, `--radius-md:12px`, `--radius-lg:16px`,
`--shadow-sm:0 1px 2px rgba(28,43,46,.06)`, `--shadow-md:0 4px 20px rgba(28,43,46,.09)`,
`--transition-fast:150ms ease`, `--transition-base:200ms ease`.

**Typographie** (une seule famille partout, Inter) : `--font-display`/`--font-body:
'Inter', system-ui, -apple-system, sans-serif`, `--font-mono: 'IBM Plex Mono', 'Courier New', monospace`.

### Composants principaux
`.app-shell`/`.app-sidebar` (240px fixe, sticky)/`.app-main` ; `.online-indicator` (pastille + états
`.is-online`/`.is-offline`/`.has-pending`) ; `.nav-link`/`.nav-section-label` ; `.card`/`.card-clickable` ;
`.btn` (40px, variantes `-primary`/`-secondary`/`-ghost`/`-success`/`-danger`/`-archive`, `.btn-sm` = 32px) ;
champs de formulaire unifiés (focus = anneau `--primary-soft`) ; `table.data-table` (en-têtes majuscules,
lignes zébrées) ; `.alert-banner` (4 variantes de sévérité) ; `.empty-state` ; `.module-card` (hub projet) ;
`.counter-grid`/`.counter-card` (dashboard Suivi, teintes actif/retard/alerte/clôturé) ; `.timeline`
(puces colorées par type d'événement) ; `.tab-bar`/`.tab-btn`/`.tab-panel` (soulignement + fade-in 0,25s) ;
`.stats-grid`/`.stat-box` ; mini-graphique en barres (`.chart-box`/`.mini-bar`, tooltip au survol) ;
`.danger-zone` (bordure/fond rouges).

**Responsive** : un seul point de rupture à `max-width: 760px` (sidebar → pleine largeur, grilles 4/2 → 1/2
colonnes). **Pas de dark mode** (aucune règle `prefers-color-scheme`/`data-theme`).

---

## 13. Infrastructure

### `docker-compose.yml` (contenu actuel complet)

```yaml
services:
  db:
    image: postgres:15-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: clinic_db
      POSTGRES_USER: clinic_user
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?Défini POSTGRES_PASSWORD dans un fichier .env (voir .env.example)}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U clinic_user -d clinic_db"]
      interval: 5s
      timeout: 5s
      retries: 5

  api:
    build: ./backend
    restart: unless-stopped
    environment:
      DATABASE_URL: postgresql://clinic_user:${POSTGRES_PASSWORD:?...}@db:5432/clinic_db
      SECRET_KEY: ${SECRET_KEY:?Défini SECRET_KEY dans un fichier .env (voir .env.example)}
      COOKIE_SECURE: ${COOKIE_SECURE:-false}
    ports:
      - "8000:8000"
    depends_on:
      db:
        condition: service_healthy
    volumes:
      - ./backend:/app
    command: sh docker-entrypoint.sh

  backup:
    image: prodrigestivill/postgres-backup-local
    restart: unless-stopped
    environment:
      POSTGRES_HOST: db
      POSTGRES_DB: clinic_db
      POSTGRES_USER: clinic_user
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?...}
      SCHEDULE: "@daily"
      BACKUP_KEEP_DAYS: 30
      BACKUP_KEEP_WEEKS: 8
      BACKUP_KEEP_MONTHS: 6
    volumes:
      - ./backups:/backups
    depends_on:
      db:
        condition: service_healthy

volumes:
  postgres_data:
```

### `backend/Dockerfile`

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```
(Le `command:` de `docker-compose.yml` remplace ce `CMD` en usage normal — `docker-entrypoint.sh` gère le
choix HTTP/HTTPS.)

### `backend/requirements.txt`

```
fastapi==0.115.0
uvicorn[standard]==0.32.0
sqlalchemy==2.0.36
psycopg2-binary==2.9.10
pydantic==2.9.2
passlib==1.7.4
bcrypt==4.0.1
pyjwt==2.9.0
pandas==2.2.3
openpyxl==3.1.5
python-multipart==0.0.12
cryptography==43.0.3
```

### Variables d'environnement (`.env`, jamais versionné)

| Variable | Rôle | Contrainte |
|---|---|---|
| `SECRET_KEY` | Signature JWT | Obligatoire, refuse de démarrer si absente ou = valeur par défaut du dépôt |
| `POSTGRES_PASSWORD` | Mot de passe PostgreSQL | Obligatoire |
| `COOKIE_SECURE` | Flag `Secure` du cookie de session | `false` en HTTP simple ; `true` une fois HTTPS confirmé actif |

### HTTPS auto-signé (pour le mode hors-ligne sur téléphones par IP locale)

**Problème résolu** : le Service Worker exige un "contexte sécurisé" (`https://` ou exactement
`localhost`) — une adresse IP locale en `http://` n'en bénéficie jamais, empêchant tout hors-ligne réel sur
un téléphone connecté par IP.

**`backend/generate_cert.py`** : génère `certs/cert.pem`/`certs/key.pem` (clé RSA 2048, certificat auto-
signé, validité 825 jours, SAN = `localhost` + `127.0.0.1` + les IP passées en argument). Usage :
`docker compose exec api python generate_cert.py <ip-locale> [<autre-ip> ...]`.

**`backend/export_cert_der.py`** : réexporte le certificat déjà généré au format DER (`cert.crt`), sans
créer de nouveau couple clé/certificat — utile pour tenter l'installation manuelle sur certains téléphones
Android (n'affecte pas les appareils ayant déjà accepté le certificat existant).

**`backend/docker-entrypoint.sh`** :
```sh
#!/bin/sh
if [ -f /app/certs/cert.pem ] && [ -f /app/certs/key.pem ]; then
  echo "[démarrage] Certificat trouvé dans certs/ : lancement en HTTPS."
  exec uvicorn main:app --host 0.0.0.0 --port 8000 --reload \
    --ssl-certfile /app/certs/cert.pem --ssl-keyfile /app/certs/key.pem
else
  echo "[démarrage] Aucun certificat dans certs/ (voir generate_cert.py) : lancement en HTTP simple."
  exec uvicorn main:app --host 0.0.0.0 --port 8000 --reload
fi
```
Bascule automatique HTTP↔HTTPS selon la présence du certificat — un premier démarrage sans certificat
fonctionne toujours en HTTP, jamais bloqué.

**Procédure de mise en place** :
```
docker compose up --build
docker compose exec api python generate_cert.py 192.168.137.166   # adresse IP locale actuelle
docker compose restart api
# ouvrir https://192.168.137.166:8000, accepter l'avertissement (certificat auto-signé)
# une fois confirmé : COOKIE_SECURE=true dans .env, puis docker compose restart api
```

**Limites/comportements connus (confirmés en usage réel)** :
- Une fois HTTPS actif, `http://` sur le même port **ne répond plus du tout** (TLS uniquement) — tout lien/
  favori doit être mis à jour en `https://`.
- Si l'adresse IP locale change (autre réseau Wi-Fi/point d'accès), le certificat ne la couvre plus :
  regénérer + `docker compose restart api` + réaccepter l'avertissement sur chaque appareil (nouveau
  certificat = nouvelle exception à accepter).
- Le stockage hors-ligne (IndexedDB) est **propre à chaque origine** (`http://...`/`https://...` sont deux
  origines distinctes) — toujours synchroniser un appareil AVANT de le faire basculer sur une nouvelle
  adresse/un nouveau certificat, sous peine de données en attente orphelines.
- **PC et iPhone (Safari)** : acceptent l'avertissement de certificat auto-signé et activent normalement le
  Service Worker ensuite.
- **Android à interface simplifiée** (confirmé : Infinix Note 40 / XOS ; probablement toute la famille
  Transsion — Infinix/Tecno/itel) : l'installation du certificat comme "Certificat CA" via les réglages (ou
  via le gestionnaire de fichiers) redirige systématiquement vers l'écran prévu pour une identité VPN
  (demande une clé privée qu'un certificat CA n'a jamais besoin d'avoir) — quel que soit le format de
  fichier essayé (PEM ou DER). Le réglage `chrome://flags/#unsafely-treat-insecure-origin-as-secure` (qui
  fonctionne pour une origine `http://` non chiffrée) **ne s'applique pas** à ce cas (mécanisme différent :
  origine non chiffrée traitée comme sécurisée, vs. certificat non reconnu comme fiable). **Solution qui
  fonctionne** : utiliser **Firefox** (l'app classique, pas Firefox Focus) sur ces téléphones — Firefox
  permet d'ajouter une exception de sécurité par site directement dans le navigateur ("Avancé" → "Accepter
  le risque et poursuivre"), sans toucher au magasin de certificats du système, et cette exception survit à
  la fermeture complète de l'application (contrairement à Chrome sur ce même appareil, qui fonctionnait en
  ligne mais jamais hors-ligne).

---

## 14. Migrations de base de données

**Principe** : pas d'Alembic. `Base.metadata.create_all()` (au démarrage) crée les tables absentes mais
n'altère jamais une table existante. Chaque évolution de schéma sur une table déjà en production a son
script `migrate_add_*.py` dédié, idempotent (`ADD COLUMN IF NOT EXISTS` via `sqlalchemy.text()`), à exécuter
manuellement une fois : `docker compose exec api python migrate_xxx.py`.

| Script | Ce qu'il fait |
|---|---|
| `migrate_add_projects.py` | Crée la table `projects` ; ajoute `project_id` à `patients`/`forms` ; crée un projet "Général" par défaut et y range tout ce qui existait avant (patients/formulaires orphelins). |
| `migrate_add_cases.py` | Crée la table `cases` ; ajoute `case_id` à `submissions`. |
| `migrate_add_idempotency_key.py` | Ajoute `submissions.idempotency_key` + contrainte unique (autorise plusieurs `NULL`, comportement standard Postgres). |
| `migrate_add_case_number_unique.py` | Ajoute la contrainte unique `(project_id, case_number)` sur `cases` — vérifie D'ABORD qu'aucun doublon n'existe déjà (sinon les affiche et s'arrête sans rien modifier, pour arbitrage manuel avant de forcer la contrainte). |
| `migrate_add_public_link.py` | Ajoute `forms.public_link_enabled` et `forms.public_access_code_hash` (lien communautaire). |
| `migrate_add_submission_updated_at.py` | Ajoute `submissions.updated_at`, initialisé à `created_at` pour les lignes existantes (nécessaire au verrouillage optimiste). |

**Ordre d'exécution recommandé pour partir de zéro** (une base neuve, schéma final directement via
`create_all()`, n'a en réalité besoin d'AUCUNE de ces migrations — elles ne servent qu'à faire évoluer une
base déjà peuplée) : `migrate_add_projects.py` → `migrate_add_cases.py` → `migrate_add_idempotency_key.py` →
`migrate_add_case_number_unique.py` → `migrate_add_public_link.py` → `migrate_add_submission_updated_at.py`.

---

## 15. Scripts utilitaires

### `backend/create_admin.py`
Interactif (`input()`/`getpass.getpass()`) : demande email/nom/mot de passe (≥8 caractères, confirmation),
crée un `User(role="admin")`. Refuse si l'email existe déjà. Usage : `docker compose exec api python
create_admin.py`.

### `backend/seed_form.py`
Insère un formulaire de démonstration "Consultation de routine" (poids, taille, IMC calculé via
`poids / pow(taille/100, 2)`, température, tension) — idempotent (ne recrée rien si le nom existe déjà).

---

## 16. Décisions et limites opérationnelles importantes

- **Formulaires pilotés par JSON** : ajouter un nouveau formulaire ne nécessite jamais de migration de
  schéma ni de code — juste une nouvelle valeur de `Form.definition`.
- **Aucun `eval`/`exec` nulle part** : le moteur de formules est un vrai petit interpréteur (tokenizer +
  parseur récursif descendant), dupliqué à l'identique en Python (serveur, fait autorité) et en JavaScript
  (client, calcul en direct y compris hors-ligne) — trois copies JS existent (`survey.html`,
  `community.html`, `form_builder.html`), toujours synchronisées manuellement entre elles et avec la version
  Python.
- **Audit log volontairement restreint** : uniquement connexions, gestion de comptes, suppressions, exports
  — jamais les lectures ni les créations en flux normal (sauf via le lien communautaire, seule voie de
  création sans compte, donc tracée par exception).
- **Idempotence par clé côté soumissions**, par identifiant côté patients/cas (deux mécanismes différents)
  — tous deux pensés pour la réplication de la file hors-ligne.
- **Verrouillage optimiste** (`expected_updated_at`) sur `PUT submissions`/`PUT cases` — différent de
  l'idempotence, détecte une modification concurrente plutôt qu'un doublon de création.
- **Suppression vs archivage** : un formulaire avec des réponses est archivé, jamais supprimé ; un compte
  utilisateur ayant déjà créé des données est bloqué en suppression (à désactiver plutôt) ; un patient avec
  des données rattachées ne peut pas être supprimé ; un dossier de suivi avec des visites ne peut être que
  clôturé, jamais supprimé.
- **Le hors-ligne exige un contexte sécurisé** (`https://` ou exactement `localhost`) — voir §13 pour la
  solution HTTPS auto-signée et ses limites par plateforme (Android/Chrome vs Firefox).
- **Une adresse IP locale n'est jamais joignable depuis les données mobiles d'un opérateur** — la
  synchronisation depuis le terrain suppose d'être reconnecté au même réseau Wi-Fi que le serveur, tant
  qu'aucun hébergement public n'est en place.
- **Pas de dépôt Git** à la date de rédaction — recommandation forte : en initialiser un (`git init`) pour
  obtenir un historique de version réel, complément indispensable à ce document pour toute reprise fidèle du
  projet.

---

## 17. Recette de reconstruction pas à pas

Pour repartir d'un dossier vide et reconstruire un environnement équivalent :

1. Recréer l'arborescence décrite en §2.
2. Copier/recréer intégralement les fichiers listés en code complet dans ce document (§3 à §7, §10) —
   ce sont des copies fidèles du code source.
3. Réimplémenter `main.py` à partir de la spécification exhaustive du §8 (idéalement en gardant le fichier
   original comme référence si disponible par ailleurs — voir l'avertissement en tête de document).
4. Réimplémenter `spreadsheet_io.py` à partir du §9.
5. Reconstruire les pages HTML/CSS restantes à partir du §11 et du design system du §12.
6. Mettre en place `docker-compose.yml`, `Dockerfile`, `requirements.txt` tels que donnés en §13.
7. Créer `.env` à partir des variables du §13 (générer `SECRET_KEY`/`POSTGRES_PASSWORD` aléatoires,
   `COOKIE_SECURE=false` au départ).
8. `docker compose up --build`.
9. `docker compose exec api python create_admin.py` — créer le premier compte.
10. (Optionnel) `docker compose exec api python seed_form.py` — formulaire de démonstration.
11. (Pour le hors-ligne sur téléphones par IP locale) suivre la procédure HTTPS du §13.
12. Se connecter sur `http://localhost:8000` (ou `https://...` après l'étape 11) et vérifier le
    fonctionnement de bout en bout : créer un projet, un formulaire (via `form_builder.html`), un patient,
    une soumission, un dossier de suivi.
