# Rapport d'avancement v12 — Clinique App (Lien communautaire / HTTPS)

Document de reprise, remplace la v11. À uploader seul dans une nouvelle conversation.

---

## 1. Bug actif

Aucun bug de code confirmé. Le point d'infrastructure ouvert dans la v11 (§3, certificat HTTPS) est
maintenant **résolu et confirmé fonctionnel** — voir §2 et §3 ci-dessous.

## 2. Ce qui a été traité cette session

### 2.1 Correctif §4 de la v11 : délai d'expiration sur la synchronisation communautaire

Les appels réseau de la synchronisation communautaire pouvaient rester bloqués très longtemps sur
"Synchronisation…" quand le serveur était injoignable (typiquement : téléphone passé sur ses données
mobiles au lieu du Wi-Fi local).

**Fichiers modifiés** :
- `offline_db.js` : ajout d'une constante `COMMUNITY_FETCH_TIMEOUT_MS` (8 secondes) et d'un message d'erreur
  clair, appliqués aux 3 appels réseau de `syncCommunityQueue` (upload média, création patient, création
  soumission).
- `community.html` : même délai de 8 secondes ajouté aux 5 appels réseau restants (vérification du code
  d'accès, envoi de photo/fichier, création patient, création soumission).

**Effet** : au-delà de 8 secondes sans réponse du serveur, message clair *« Connexion au serveur
introuvable — vérifie que tu es sur le même réseau Wi-Fi que l'ordinateur »* au lieu d'un blocage
indéfini. Le comportement existant (bascule en enregistrement local si le réseau manque) n'est pas changé,
juste borné dans le temps.

### 2.2 Correctif §3 de la v11 : certificat HTTPS auto-signé — **confirmé fonctionnel en conditions réelles**

Le Service Worker (mode hors-ligne) exige un "contexte sécurisé" (`https://` ou exactement `localhost`) —
une adresse IP locale en `http://` n'en bénéficiait jamais, empêchant tout téléphone connecté par IP de
rester utilisable une fois complètement hors-ligne.

**Fichiers ajoutés** :
- `backend/generate_cert.py` : génère un certificat HTTPS auto-signé (clé + certificat) pour une ou
  plusieurs adresses IP locales données en argument, plus `localhost`/`127.0.0.1`. Usage :
  `docker compose exec api python generate_cert.py <adresse_ip>`.
- `backend/docker-entrypoint.sh` : démarre l'API en HTTPS si `backend/certs/cert.pem`/`key.pem` existent,
  sinon reste en HTTP simple comme avant — pour ne jamais bloquer un premier démarrage avant qu'un
  certificat n'ait été généré.
- `backend/export_cert_der.py` : réexporte le certificat déjà généré au format DER (`cert.crt`), sans en
  créer un nouveau — utile pour tenter l'installation manuelle sur certains téléphones Android (voir §3).

**Fichiers modifiés** :
- `docker-compose.yml` : le service `api` utilise maintenant `sh docker-entrypoint.sh` comme commande de
  démarrage (au lieu d'un `uvicorn` direct en HTTP).
- `requirements.txt` : ajout de `cryptography` (nécessaire à la génération du certificat).
- `.env` : `COOKIE_SECURE` passé à `true` (confirmé une fois HTTPS actif et vérifié).
- `.env.example`, `CLAUDE.md` : documentation mise à jour.

**Confirmé fonctionnel par l'utilisateur, en conditions réelles** :
- Certificat généré (`docker compose exec api python generate_cert.py 192.168.137.166`), API redémarrée,
  connexion HTTPS vérifiée depuis le PC.
- **PC et iPhone** : avertissement de sécurité accepté une seule fois, fonctionnement hors-ligne complet
  confirmé (comme avant, mais maintenant aussi valable pour une adresse IP, pas seulement "localhost").
- Le réglage `chrome://flags` utilisé en v11 pour le cas HTTP **ne s'applique pas** au cas HTTPS (ce sont
  deux mécanismes différents : "origine non chiffrée traitée comme sécurisée" vs "certificat non reconnu
  comme fiable") — normal, pas un bug.

## 3. Nouvelle découverte : téléphones Android à interface simplifiée (Infinix/Tecno — XOS/HiOS)

**Constat** : sur un Infinix Note 40 (interface XOS), impossible d'installer le certificat comme
"Certificat CA" — que ce soit via Réglages → Sécurité → Chiffrement et identifiants, ou en ouvrant le
fichier directement depuis le gestionnaire de fichiers, Android redirige systématiquement vers l'écran
prévu pour une identité VPN (qui exige une clé privée, qu'un certificat CA n'a jamais besoin de fournir).
Testé avec le certificat au format PEM et au format DER (`export_cert_der.py`) — même résultat dans les
deux cas. Le téléphone a bien un verrouillage d'écran actif (empreinte), donc ce n'est pas la cause.

**Conclusion** : certaines interfaces Android simplifiées (confirmé : XOS d'Infinix ; probablement toute la
famille Transsion — Infinix/Tecno/itel, qui partagent la même base) ne proposent tout simplement pas de
chemin pour installer un certificat CA seul, quel que soit le format de fichier essayé.

**Solution qui fonctionne, confirmée par l'utilisateur** : utiliser **Firefox** (l'application classique,
pas Firefox Focus) au lieu de Chrome sur ces téléphones-là. Firefox permet d'ajouter une exception de
sécurité par site directement dans le navigateur ("Avancé" → "Accepter le risque et poursuivre"), sans
toucher au magasin de certificats du système — cette exception est mémorisée durablement par Firefox et
survit à la fermeture complète de l'application, ce que l'acceptation ponctuelle de Chrome ne permettait
pas sur ce modèle (fonctionnait en ligne, mais pas hors-ligne).

**Règle à retenir pour le déploiement terrain** :
- Téléphone Android "standard" (Pixel, Samsung récent, etc.) : Chrome + installation du certificat en
  "Certificat CA" via Réglages fonctionne normalement.
- Téléphone Android à interface simplifiée qui bloque sur "clé privée" quel que soit le chemin essayé
  (confirmé sur Infinix, à surveiller sur Tecno/itel) : basculer directement sur **Firefox**, sans perdre de
  temps à chercher un menu caché.
- iPhone : fonctionne nativement avec Safari, avertissement accepté une fois.

## 4. Fichiers actuels du projet

**Backend** : `models.py`, `main.py`, `form_validation.py`, `form_import_validation.py`, `formula_engine.py`,
`spreadsheet_io.py`, `security.py`, `database.py`, `create_admin.py`, `seed_form.py`,
**`generate_cert.py` (nouveau)**, **`export_cert_der.py` (nouveau)**, **`docker-entrypoint.sh` (nouveau)**,
migrations diverses (dont `migrate_add_public_link.py`, exécutée), `Dockerfile`, dossier `certs/` (contient
`cert.pem`/`key.pem`, généré localement, jamais à partager/exposer).

**Frontend** (`static/`) : `survey.html`, `follow_up.html`, `submission.html`, `case_detail.html`,
`community.html`, `history.html`, `form_builder.html`, `form_data.html`, `users.html`, `audit_log.html`,
`duplicates.html`, `project.html`, `project_hub.html`, `login.html`, `index.html`, `app_shell.js`,
`icons.js`, `offline_db.js`, `sw.js`, `manifest.json`, `dexie.min.js`, `style.css`.

**Racine du projet** : `docker-compose.yml` (modifié — commande de démarrage de l'API),
`requirements.txt` (modifié — ajout de `cryptography`), `.env` (`COOKIE_SECURE=true`), `.env.example`
(documentation mise à jour), `CLAUDE.md` (documentation mise à jour).

Rappel déploiement : `.py`/`.sh` → `docker compose restart api` (ou `up --build` si `requirements.txt` ou
`docker-compose.yml` a changé) ; nouvelle migration → `docker compose exec api python migrate_xxx.py` AVANT
le redémarrage. Fichiers statiques → hard refresh + remise à zéro du Service Worker en cas de doute.

## 5. Méthode à impérativement conserver pour la suite

- **Toujours régénérer/vérifier l'adresse IP avant de partager un lien** — le lien communautaire reprend
  l'adresse de la page au moment où on la consulte (`https://` maintenant, pas `http://`).
- **Si l'adresse IP du PC change** (autre réseau Wi-Fi, autre point d'accès mobile) : le certificat existant
  ne la couvrira plus. Relancer `generate_cert.py` avec la nouvelle adresse, `docker compose restart api`,
  puis réaccepter l'avertissement sur chaque appareil (nouveau certificat).
- **Une fois HTTPS actif, l'adresse `http://` ne répond plus du tout** (même port, TLS uniquement) — tout
  lien/favori `http://` doit être remplacé par `https://`.
- **Avant de faire basculer un téléphone qui a des données hors-ligne en attente vers une nouvelle
  adresse/un nouveau certificat** : synchroniser d'abord (le stockage hors-ligne est propre à chaque
  origine `http://.../https://...` — des données en attente sous l'ancienne adresse ne sont pas visibles
  depuis la nouvelle).
- **Le certificat est auto-signé** : chaque appareil doit accepter l'avertissement une seule fois
  (Chrome/Safari) — ou pour les téléphones Android à interface simplifiée qui bloquent sur l'installation
  du certificat CA (clé privée demandée à tort), utiliser Firefox à la place (voir §3).
- **`docker compose` doit être lancé depuis le dossier du projet** — sinon message "no configuration file
  provided".
- Après toute modification de `main.py` touchant à la base de données : exécuter la migration correspondante
  AVANT de redémarrer l'API, jamais l'inverse.
- Le statut "Restarting" affiché par `docker compose ps`/`restart` est transitoire — attendre quelques
  secondes avant de conclure à un problème.
- Toujours vérifier qu'un correctif serveur est bien actif via `https://ADRESSE:PORT/api/...` directement
  dans le navigateur, plutôt que de deviner depuis le comportement de l'interface.
- L'utilisateur (Ariel Ettien) débute techniquement — instructions pas-à-pas explicites, jamais de raccourci
  supposant une familiarité avec les outils techniques.
- Avant de coder un point nouveau ou ambigu, le discuter d'abord ; pour les demandes claires, proposer un
  choix par défaut raisonnable plutôt que de bloquer sur une question.
