"""Genere un certificat HTTPS auto-signe.

Pourquoi c'est necessaire : le mode hors-ligne repose sur un Service Worker,
que les navigateurs n'activent que dans un "contexte securise" (https://, ou
l'exception speciale "localhost"). Une adresse IP en http:// ne beneficie
d'aucune exception — sans certificat, tout telephone connecte par l'adresse IP
locale du serveur perd le mode hors-ligne.

Ce certificat est auto-signe (pas delivre par une autorite reconnue) : chaque
navigateur qui s'y connecte pour la premiere fois affiche un avertissement
"connexion non securisee", a accepter une seule fois par appareil. C'est une
limite connue et acceptee, pas une erreur de configuration.

Utilisation (le conteneur doit deja tourner, dans un AUTRE terminal) :

    docker compose exec api python manage.py generate_cert 192.168.1.50

Remplace l'adresse par celle du serveur sur le reseau local. Plusieurs
adresses peuvent etre indiquees a la suite.

Important : si cette adresse change plus tard (autre reseau, autre point
d'acces), le certificat ne la couvrira plus. Il faut alors relancer cette
commande avec la nouvelle adresse, PUIS redemarrer le service — et accepter a
nouveau l'avertissement sur chaque appareil, puisqu'il s'agit techniquement
d'un nouveau certificat.
"""
import ipaddress
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from django.conf import settings
from django.core.management.base import BaseCommand

# 825 jours : duree maximale acceptee par Safari et Chrome pour un certificat.
CERT_VALIDITY_DAYS = 825


class Command(BaseCommand):
    help = "Genere un certificat HTTPS auto-signe pour les adresses IP locales indiquees."

    def add_arguments(self, parser):
        parser.add_argument("adresses", nargs="+",
                            help="Adresses IP locales du serveur (ex: 192.168.1.50)")

    def handle(self, *args, **options):
        certs_dir = Path(settings.BASE_DIR) / "certs"
        ip_args = options["adresses"]

        # localhost et 127.0.0.1 sont toujours inclus, pour continuer a
        # fonctionner depuis le serveur lui-meme sans rien reconfigurer.
        san_entries = [
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        ]
        valid_ips = []
        for ip_str in ip_args:
            try:
                san_entries.append(x509.IPAddress(ipaddress.ip_address(ip_str)))
                valid_ips.append(ip_str)
            except ValueError:
                self.stdout.write(self.style.WARNING(
                    f"'{ip_str}' n'est pas une adresse IP valide — ignoree."))

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "Clinique App (certificat local)")
        ])
        now = datetime.now(timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=CERT_VALIDITY_DAYS))
            .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
            .sign(key, hashes.SHA256())
        )

        certs_dir.mkdir(exist_ok=True)
        (certs_dir / "key.pem").write_bytes(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))
        (certs_dir / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))

        self.stdout.write(self.style.SUCCESS(
            f"Certificat genere dans {certs_dir}/ pour : localhost, 127.0.0.1, "
            f"{', '.join(valid_ips)}"))
        self.stdout.write("Prochaine etape : docker compose restart api")
        self.stdout.write("Puis ouvre https://<une-des-adresses-ci-dessus>:8000 et accepte l'avertissement.")
