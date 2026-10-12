"""
Convertit le certificat déjà généré (backend/certs/cert.pem) en format DER
(cert.crt) — certains téléphones Android le reconnaissent plus fiablement
comme un "certificat CA" pur (aucune clé privée requise) que le fichier PEM
d'origine, qui a pu, sur certains modèles, être redirigé à tort vers l'écran
"certificat + clé privée" (destiné à un tout autre usage : identité VPN).

Ce script NE régénère PAS le certificat — il ne fait que le réexporter dans
un autre format. Les appareils qui ont déjà accepté le certificat existant
(PC, iPhone, autres téléphones Android) ne sont donc pas affectés.

Utilisation (après avoir déjà généré un certificat avec generate_cert.py) :

    docker compose exec api python export_cert_der.py

Transfère ensuite backend/certs/cert.crt (celui-ci, pas cert.pem) sur le
téléphone Android qui bloque, et réessaie l'installation via Réglages →
Sécurité → Chiffrement et identifiants → Installer un certificat →
Certificat CA.
"""

from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization

CERTS_DIR = Path(__file__).parent / "certs"


def main():
    pem_path = CERTS_DIR / "cert.pem"
    if not pem_path.exists():
        print(f"Aucun certificat trouvé ({pem_path}) — lance d'abord generate_cert.py.")
        return
    cert = x509.load_pem_x509_certificate(pem_path.read_bytes())
    der_path = CERTS_DIR / "cert.crt"
    der_path.write_bytes(cert.public_bytes(serialization.Encoding.DER))
    print(f"Écrit : {der_path} (format DER, à installer comme 'Certificat CA' sur Android)")


if __name__ == "__main__":
    main()
