"""Limitation des tentatives repetees.

Utilise pour la connexion et pour le code d'acces du lien communautaire :
sans elle, rien ne freine un script qui essaierait des milliers de mots de
passe ou de codes a la vitesse du reseau.

Compteur en memoire du processus. C'est volontaire et suffisant ici :
l'application tourne en un seul processus (voir le fichier de demarrage), et
un redemarrage remet simplement les compteurs a zero — au pire, un attaquant
regagne quelques essais, ce qui ne change rien a l'ordre de grandeur de la
protection.
"""
import threading
from datetime import datetime, timedelta

from rest_framework.exceptions import Throttled


class AttemptLimiter:
    def __init__(self, max_attempts: int, window_minutes: int, lockout_minutes: int):
        self.max_attempts = max_attempts
        self.window = timedelta(minutes=window_minutes)
        self.lockout = timedelta(minutes=lockout_minutes)
        self._attempts: dict[str, list[datetime]] = {}
        self._lockouts: dict[str, datetime] = {}
        self._lock = threading.Lock()

    def check(self, key: str, message: str):
        """A appeler AVANT de verifier le mot de passe / le code. Leve une
        erreur 429 si cette cle est temporairement bloquee."""
        now = datetime.utcnow()
        with self._lock:
            locked_until = self._lockouts.get(key)
            if locked_until and locked_until > now:
                wait_min = max(1, int((locked_until - now).total_seconds() // 60) + 1)
                raise Throttled(detail=f"{message} Reessaie dans environ {wait_min} min.")

    def record_failure(self, key: str):
        now = datetime.utcnow()
        with self._lock:
            attempts = [t for t in self._attempts.get(key, []) if now - t < self.window]
            attempts.append(now)
            if len(attempts) >= self.max_attempts:
                self._lockouts[key] = now + self.lockout
                self._attempts.pop(key, None)
            else:
                self._attempts[key] = attempts

    def record_success(self, key: str):
        with self._lock:
            self._attempts.pop(key, None)
            self._lockouts.pop(key, None)


# Un peu plus tolerant que le lien public : une faute de frappe legitime d'un
# agent ne doit pas le bloquer pour la journee.
login_limiter = AttemptLimiter(max_attempts=10, window_minutes=15, lockout_minutes=15)

# Le lien communautaire n'a aucun compte a verrouiller : c'est la seule
# barriere entre un code devine et de vraies donnees patients.
public_code_limiter = AttemptLimiter(max_attempts=8, window_minutes=10, lockout_minutes=15)
