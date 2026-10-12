"""
Génère un certificat HTTPS auto-signé pour que le Service Worker (mode
hors-ligne) fonctionne aussi quand l'application est ouverte via l'adresse IP
locale de l'ordinateur (ex: http://192.168.137.166:8000), pas seulement via
"localhost" — les navigateurs n'activent le Service Worker que dans un
"contexte sécurisé" (https://, ou l'exception spéciale "localhost"). Une
adresse IP en http:// ne bénéficie d'aucune exception, ce qui empêchait tout
téléphone connecté par IP de rester utilisable une fois complètement
hors-ligne (voir le rapport d'avancement v11, §3).

Ce certificat est auto-signé (pas délivré par une autorité reconnue comme
Let's Encrypt) : chaque navigateur qui s'y connecte pour la première fois
affiche un avertissement "connexion non sécurisée" à accepter une seule fois
par appareil. C'est une limite connue et acceptée, pas une erreur de
configuration — voir la procédure d'acceptation dans le rapport.

Utilisation (le conteneur "api" doit déjà être en train de tourner, dans un
AUTRE terminal que celui où "docker compose up" tourne) :

    docker compose exec api python generate_cert.py 192.168.137.166

Remplace 192.168.137.166 par l'adresse IP actuelle de l'ordinateur sur le
réseau Wi-Fi utilisé sur le terrain (visible via `ipconfig` dans une invite
PowerShell, ligne "Adresse IPv4" sous l'adaptateur Wi-Fi utilisé). Tu peux
indiquer plusieurs adresses à la suite si besoin, séparées par un espace.

Important : si cette adresse IP change plus tard (autre réseau Wi-Fi, autre
point d'accès mobile...), le certificat existant ne la couvrira plus. Il
faudra alors relancer ce script avec la nouvelle adresse, PUIS redémarrer
l'API (`docker compose restart api`) — et accepter à nouveau l'avertissement
du navigateur sur chaque téléphone, puisqu'il s'agit techniquement d'un
nouveau certificat.

Écrit les fichiers dans backend/certs/ (cert.pem, key.pem). Le démarrage de
l'API (voir docker-entrypoint.sh) détecte automatiquement leur présence :
s'ils existent, l'API démarre en HTTPS ; sinon elle continue de démarrer en
HTTP simple comme avant, pour ne jamais bloquer un premier démarrage avant
que ce script ait pu être exécuté.
"""

import ipaddress
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

CERTS_DIR = Path(__file__).parent / "certs"


def main():
    ip_args = sys.argv[1:]
    if not ip_args:
        print(
            "Utilisation : docker compose exec api python generate_cert.py <adresse_ip> [autre_ip ...]\n"
            "Exemple      : docker compose exec api python generate_cert.py 192.168.137.166\n"
            "(trouve l'adresse IP actuelle avec `ipconfig` dans PowerShell, sous l'adaptateur Wi-Fi utilisé)"
        )
        sys.exit(1)

    # localhost/127.0.0.1 toujours inclus, pour continuer à fonctionner depuis
    # l'ordinateur lui-même sans rien reconfigurer.
    san_entries = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    for ip_str in ip_args:
        try:
            san_entries.append(x509.IPAddress(ipaddress.ip_address(ip_str)))
        except ValueError:
            print(f"'{ip_str}' n'est pas une adresse IP valide — ignorée.")

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Clinique App (certificat local)")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=825))  # 825 jours : durée max acceptée par Safari/Chrome pour un certificat
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .sign(key, hashes.SHA256())
    )

    CERTS_DIR.mkdir(exist_ok=True)
    (CERTS_DIR / "key.pem").write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    (CERTS_DIR / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    print(f"Certificat généré dans {CERTS_DIR}/ pour : localhost, 127.0.0.1, {', '.join(ip_args)}")
    print("Prochaine étape : docker compose restart api")
    print("Puis ouvre https://<une-des-adresses-ci-dessus>:8000 dans le navigateur et accepte l'avertissement.")


if __name__ == "__main__":
    main()
