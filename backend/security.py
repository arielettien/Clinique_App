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


def create_access_token(user_id: str, token_version: int = 0) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    # "tv" (token_version) est comparé à la valeur actuelle de l'utilisateur en
    # base à chaque requête (voir get_current_user dans main.py) — incrémenter
    # cette valeur (déconnexion, réinitialisation de mot de passe) invalide
    # instantanément tous les jetons déjà émis pour ce compte, sans avoir
    # besoin d'une liste séparée de jetons révoqués.
    payload = {"sub": user_id, "tv": token_version, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str):
    """Renvoie le contenu du jeton (dict avec au moins "sub"), ou None s'il
    est invalide/expiré/mal signé."""
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None
