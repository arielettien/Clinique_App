# Protocole de déploiement — Clinique App sur serveur local

Guide pas à pas pour installer l'application sur le serveur de l'ONG, avec
reprise des données déjà saisies (patients, formulaires, photos, signatures).

**Contexte de ce déploiement :** réseau local uniquement (pas d'accès depuis
internet), 4 à 5 utilisateurs, serveur allumé en permanence.

**Durée estimée :** 2 à 3 heures au total, réparties sur autant de séances que
tu veux — chaque étape se termine proprement et peut être reprise plus tard.

---

## ⚠️ Règle d'or

**Ne supprime RIEN sur le PC Windows tant que le serveur n'est pas validé.**
Tant que tout n'est pas vérifié à l'étape 11, l'installation actuelle reste ta
sauvegarde de secours. Si quelque chose se passe mal, tu peux continuer à
travailler sur le PC Windows comme avant, sans rien avoir perdu.

---

## Matériel et informations nécessaires

**Matériel**
- La machine qui servira de serveur (mini-PC ou PC de bureau, 4 Go de RAM minimum, SSD)
- Un onduleur pour cette machine (**indispensable** — voir la note en fin de document)
- Une clé USB d'au moins 8 Go (pour installer Ubuntu)
- Un écran, un clavier et une souris (seulement le temps de l'installation)
- Une connexion internet sur le serveur **pendant l'installation uniquement**

**Informations à préparer et à noter** (une fiche à remplir est fournie en fin de document)
- Le nom et le mot de passe Wi-Fi/réseau du centre
- L'accès à l'interface de la box/du routeur (pour l'adresse IP fixe)
- La plage d'adresses IP du réseau local (souvent `192.168.1.x` ou `192.168.0.x`)

---

## Étape 1 — Sauvegarder les données actuelles (sur le PC Windows)

**Objectif :** récupérer tout ce qui a déjà été saisi, pour le remettre sur le serveur.

1. Démarre Docker Desktop et attends qu'il soit complètement lancé.

2. Ouvre PowerShell dans le dossier du projet et lance l'application si elle
   ne tourne pas déjà :
   ```powershell
   cd "C:\Users\ARIEL ETTIEN\Desktop\clinique-app\clinique-app"
   docker compose up -d
   ```

3. Lance le script de préparation :
   ```powershell
   .\preparer_migration.ps1
   ```

   > Si PowerShell refuse d'exécuter le script ("l'exécution de scripts est
   > désactivée"), lance d'abord :
   > `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`
   > puis relance le script.

4. Le script crée un fichier `migration_clinique_AAAA-MM-JJ_HHMM.sql` et
   affiche la liste de ce qu'il faut transférer. **Note le nom exact de ce
   fichier**, tu en auras besoin à l'étape 9.

**✅ Vérification :** le script affiche « Termine » et le fichier `.sql` est
bien présent dans le dossier du projet.

---

## Étape 2 — Installer Ubuntu Server sur la machine serveur

**Objectif :** installer le système d'exploitation.

> ⚠️ Cette étape **efface tout le contenu du disque** de la machine serveur.
> Vérifie qu'elle ne contient rien d'important avant de commencer.

1. **Télécharger Ubuntu Server** (depuis ton PC Windows) :
   rends-toi sur <https://ubuntu.com/download/server> et télécharge la version
   **24.04 LTS**. Le fichier fait environ 2,5 Go.

2. **Créer la clé USB d'installation** :
   - Télécharge et lance **Rufus** (<https://rufus.ie>)
   - Sélectionne ta clé USB, puis le fichier Ubuntu téléchargé
   - Clique sur « Démarrer » et attends la fin

3. **Démarrer la machine serveur sur la clé USB** :
   branche la clé, allume la machine et appuie immédiatement et de façon
   répétée sur la touche du menu de démarrage (`F12`, `F11`, `F2`, `Échap` ou
   `Suppr` selon la marque), puis choisis la clé USB dans la liste.

4. **Répondre aux questions de l'installateur** :

   | Question | Réponse |
   |---|---|
   | Language | English (ou Français) |
   | Keyboard | **French (AZERTY)** si ton clavier est AZERTY — important pour le mot de passe |
   | Type of install | Ubuntu Server (pas « minimized ») |
   | Network | Laisser tel quel (on fixera l'IP à l'étape 4) |
   | Proxy | Laisser vide |
   | Mirror | Laisser la valeur proposée |
   | Storage | « Use an entire disk » puis confirmer (`Continue`) |
   | Your name | Ton nom |
   | Server name | `clinique-serveur` |
   | Username | `ong` (retiens-le, tu l'utiliseras tout le temps) |
   | Password | Un mot de passe solide — **note-le immédiatement** |
   | Ubuntu Pro | « Skip for now » |
   | **Install OpenSSH server** | ☑️ **COCHER** (indispensable pour administrer à distance) |
   | Featured snaps | N'en cocher aucun |

5. L'installation prend 10 à 20 minutes. Quand l'écran affiche
   « Reboot Now », **retire la clé USB** puis valide.

**✅ Vérification :** la machine redémarre et affiche un écran de connexion en
texte, du type `clinique-serveur login:`.

---

## Étape 3 — Première connexion et mises à jour

1. Connecte-toi directement sur la machine (nom d'utilisateur `ong`, puis ton
   mot de passe — **il ne s'affiche pas pendant la frappe, c'est normal**).

2. Mets le système à jour :
   ```bash
   sudo apt update
   sudo apt upgrade -y
   ```
   (`sudo` redemande ton mot de passe la première fois.)

3. Règle le fuseau horaire :
   ```bash
   sudo timedatectl set-timezone Africa/Abidjan
   timedatectl
   ```
   > Remplace `Africa/Abidjan` par ton fuseau si besoin. La liste complète
   > s'affiche avec `timedatectl list-timezones`.

4. Note l'adresse IP actuelle de la machine :
   ```bash
   ip -br a
   ```
   Tu verras une ligne du type `enp1s0  UP  192.168.1.37/24`.
   **Note les deux informations** : le nom de la carte réseau (`enp1s0` ici)
   et l'adresse IP (`192.168.1.37` ici).

**✅ Vérification :** `timedatectl` affiche la bonne heure locale.

---

## Étape 4 — Donner une adresse IP fixe au serveur

**Pourquoi :** sans adresse fixe, l'adresse du serveur peut changer après une
coupure de courant — ce qui casserait à la fois le certificat HTTPS et tous les
raccourcis enregistrés sur les téléphones.

Choisis **une** des deux méthodes.

### Méthode A — Par la box/le routeur (recommandée, plus simple)

1. Récupère l'adresse MAC du serveur :
   ```bash
   ip -br link
   ```
   (Format `aa:bb:cc:dd:ee:ff`.)

2. Depuis un navigateur sur ton PC, connecte-toi à l'interface de la box
   (souvent `192.168.1.1`), cherche la section « DHCP » → « Baux statiques » /
   « Réservation d'adresse », et associe l'adresse MAC du serveur à une adresse
   IP fixe choisie hors de la plage automatique (exemple : `192.168.1.50`).

3. Redémarre le serveur : `sudo reboot`, puis revérifie avec `ip -br a`.

### Méthode B — Directement sur le serveur

1. Ouvre le fichier de configuration réseau :
   ```bash
   sudo nano /etc/netplan/50-cloud-init.yaml
   ```

2. Remplace tout le contenu par ceci, **en adaptant** le nom de la carte réseau
   (étape 3), l'adresse choisie et l'adresse de la box :
   ```yaml
   network:
     version: 2
     ethernets:
       enp1s0:
         dhcp4: no
         addresses: [192.168.1.50/24]
         routes:
           - to: default
             via: 192.168.1.1
         nameservers:
           addresses: [8.8.8.8, 1.1.1.1]
   ```
   Enregistre avec `Ctrl+O` puis `Entrée`, quitte avec `Ctrl+X`.

3. Applique :
   ```bash
   sudo chmod 600 /etc/netplan/50-cloud-init.yaml
   sudo netplan apply
   ip -br a
   ```

**✅ Vérification :** `ip -br a` affiche bien l'adresse fixe choisie, et
`ping -c 3 ubuntu.com` fonctionne (internet toujours accessible).

À partir d'ici, tu peux débrancher l'écran et le clavier du serveur, et
continuer **depuis ton PC Windows** dans PowerShell :
```powershell
ssh ong@192.168.1.50
```

---

## Étape 5 — Activer le pare-feu

**Objectif :** n'autoriser les connexions que depuis le réseau local, jamais
depuis internet.

> ⚠️ **Autorise SSH AVANT d'activer le pare-feu**, sinon tu te coupes toi-même
> l'accès à distance à la machine.

Adapte `192.168.1.0/24` à ton réseau (si tes adresses sont en `192.168.0.x`,
écris `192.168.0.0/24`) :

```bash
sudo ufw allow from 192.168.1.0/24 to any port 22 proto tcp
sudo ufw allow from 192.168.1.0/24 to any port 8000 proto tcp
sudo ufw enable
sudo ufw status verbose
```

**✅ Vérification :** `sudo ufw status verbose` affiche « Status: active » et
les deux règles, limitées à ton réseau local.

---

## Étape 6 — Installer Docker

```bash
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Autorise ton utilisateur à lancer Docker sans `sudo`, et active le démarrage
automatique :
```bash
sudo usermod -aG docker $USER
sudo systemctl enable docker
```

**Déconnecte-toi puis reconnecte-toi** (`exit`, puis `ssh ong@192.168.1.50`) —
sans ça, la ligne `usermod` ne prend pas effet.

**✅ Vérification :**
```bash
docker compose version
docker run hello-world
```
La deuxième commande doit afficher « Hello from Docker! » **sans** avoir besoin
de `sudo`.

---

## Étape 7 — Transférer l'application sur le serveur

Choisis **une** des deux méthodes.

### Méthode A — Par le réseau (recommandée)

Depuis PowerShell **sur ton PC Windows** (pas dans la session SSH) :
```powershell
cd "C:\Users\ARIEL ETTIEN\Desktop"
scp -r "clinique-app/clinique-app" ong@192.168.1.50:/home/ong/
```
> Les barres obliques sont bien dans ce sens (`/`), même sous Windows : c'est
> `scp` qui les attend ainsi.
Le transfert peut prendre plusieurs minutes selon le volume de photos.

### Méthode B — Par clé USB

Copie le dossier du projet sur une clé USB depuis Windows (**vérifie que le
fichier caché `.env` est bien copié**), puis sur le serveur :
```bash
lsblk                                  # repérer la clé (ex : sdb1)
sudo mkdir -p /media/usb
sudo mount /dev/sdb1 /media/usb
cp -r /media/usb/clinique-app ~/
sudo umount /media/usb
```
> Si le montage échoue avec une clé formatée en exFAT :
> `sudo apt install -y exfatprogs` puis réessaie.

### Vérifier que tout est bien arrivé

```bash
cd ~/clinique-app
ls -la .env
ls backend/uploads | wc -l
ls *.sql
```

**✅ Vérification :** les trois commandes répondent sans erreur — `.env` existe,
le nombre de fichiers médias correspond à ce qu'annonçait le script de l'étape 1,
et le fichier `.sql` est présent.

> Si `.env` est absent, c'est le piège classique : Windows masque les fichiers
> commençant par un point. Recopie-le seul, explicitement.

---

## Étape 8 — Régler la configuration pour la production

```bash
cd ~/clinique-app
nano .env
```

Modifie ces deux lignes (laisse le reste **strictement identique**, en
particulier `SECRET_KEY`, `POSTGRES_PASSWORD` et
`BACKUP_ENCRYPTION_PASSPHRASE`) :

```
COOKIE_SECURE=false
UVICORN_RELOAD=false
```

> `COOKIE_SECURE` restera à `false` jusqu'à l'étape 10, le temps de mettre le
> HTTPS en place. `UVICORN_RELOAD=false` désactive le rechargement automatique
> du code, inutile sur un serveur en production.

Enregistre (`Ctrl+O`, `Entrée`) et quitte (`Ctrl+X`).

Démarre **uniquement la base de données** pour l'instant :
```bash
docker compose up -d db
docker compose ps
```

**✅ Vérification :** la ligne `db` affiche `Up ... (healthy)`. Si elle affiche
`starting`, attends 15 secondes et refais `docker compose ps`.

---

## Étape 9 — Restaurer les données existantes

**Objectif :** réinjecter tous les patients, dossiers et formulaires déjà saisis.

Remplace le nom de fichier par celui noté à l'étape 1 :
```bash
cd ~/clinique-app
docker compose cp migration_clinique_2026-09-30_1430.sql db:/tmp/migration.sql
docker compose exec -T db psql -U clinic_user -d clinic_db -f /tmp/migration.sql
```

Des lignes `CREATE TABLE`, `COPY`, `ALTER TABLE` défilent — c'est normal.

Démarre ensuite toute l'application (la construction des images prend 3 à 10
minutes la première fois, connexion internet nécessaire) :
```bash
docker compose up -d --build
docker compose ps
```

**✅ Vérification :** les quatre services (`db`, `api`, `backup`,
`backup-encrypt`) sont `Up`, et le compte des patients correspond à ce que tu
avais :
```bash
docker compose exec -T db psql -U clinic_user -d clinic_db -c "SELECT COUNT(*) FROM patients;"
```

---

## Étape 10 — Activer le HTTPS

**Pourquoi :** sans HTTPS, le mode hors-ligne sur les téléphones ne s'active
jamais (le navigateur l'interdit sur une adresse IP en `http://`).

Génère le certificat pour l'adresse fixe du serveur :
```bash
docker compose exec api python generate_cert.py 192.168.1.50
docker compose restart api
docker compose logs --tail=5 api
```

**✅ Vérification :** les logs affichent
« Certificat trouvé dans certs/ : lancement en HTTPS. »

Active ensuite le cookie sécurisé :
```bash
nano .env          # mettre COOKIE_SECURE=true
docker compose up -d
```

---

## Étape 11 — Vérifications finales

À faire dans l'ordre. **Ne considère le déploiement terminé que si tout passe.**

**Depuis un PC du réseau local**
1. Ouvre `https://192.168.1.50:8000` (bien `https`, avec le `s`)
2. Un avertissement « connexion non privée » s'affiche → clique sur
   « Paramètres avancés » puis « Continuer » (normal avec un certificat
   auto-signé, à accepter une fois par appareil)
3. Connecte-toi avec ton compte habituel → **les comptes existants fonctionnent**
4. Ouvre la liste des patients → ils sont tous là
5. Ouvre la fiche d'un patient qui a une **photo ou une signature** → elle
   s'affiche (cela valide le transfert du dossier `uploads/`)
6. Lance une impression de fiche → le contenu est correct

**Depuis un téléphone sur le Wi-Fi du centre**
7. Même adresse, accepte l'avertissement
8. Connecte-toi, puis coupe le Wi-Fi → l'application reste utilisable
   (mode hors-ligne) ; rallume le Wi-Fi et vérifie la synchronisation
9. Teste le lien communautaire

**Sauvegardes**
10. Déclenche une sauvegarde manuelle puis vérifie qu'elle est bien chiffrée :
    ```bash
    docker compose exec backup /backup.sh
    docker compose restart backup-encrypt
    sleep 10
    ls -l backups/daily/
    ```
    **✅ Vérification :** tu vois un fichier `.sql.gz.gpg` — et **aucun**
    fichier `.sql.gz` non chiffré.

---

## Étape 12 — Test de coupure de courant

C'est le test qui compte vraiment pour un serveur.

```bash
sudo reboot
```

Attends 1 à 2 minutes, puis rouvre `https://192.168.1.50:8000` depuis un PC.

**✅ Vérification :** l'application est de nouveau accessible **sans que tu aies
tapé la moindre commande**. C'est le comportement attendu : les conteneurs sont
configurés pour redémarrer automatiquement.

Si ça fonctionne, tu peux aussi faire le test réel : couper l'alimentation
(avec l'onduleur branché), rallumer, et vérifier de la même façon.

---

## ⚡ L'onduleur : le point à ne pas négliger

Une coupure de courant pendant que la base de données écrit sur le disque peut
**corrompre l'ensemble de la base**. Les sauvegardes permettraient de repartir,
mais tout ce qui a été saisi depuis la dernière sauvegarde serait perdu.

Un onduleur de 650 à 1000 VA suffit largement pour un mini-PC. Branche dessus
**le serveur et la box internet** (sans la box, le serveur reste allumé mais
devient injoignable).

---

## Routine après le déploiement

| Fréquence | Action |
|---|---|
| **Chaque semaine** | Copier le dossier `backups/` sur un disque externe ou une clé USB, conservée **hors du bureau** (protège contre vol/incendie) |
| **Chaque mois** | `sudo apt update && sudo apt upgrade -y` puis `sudo reboot` |
| **Chaque mois** | Vérifier l'espace disque : `df -h` (la ligne `/` ne doit pas dépasser 80 %) |
| **Une fois** | Ranger une copie du mot de passe `BACKUP_ENCRYPTION_PASSPHRASE` dans un endroit sûr, **hors du serveur** — sans lui, les sauvegardes chiffrées sont définitivement illisibles |

---

## En cas de problème

| Symptôme | Cause probable et solution |
|---|---|
| `permission denied` sur une commande `docker` | Tu ne t'es pas déconnecté/reconnecté après l'étape 6. Fais `exit` puis reconnecte-toi en SSH. |
| L'application n'est pas joignable depuis un autre appareil | Vérifie `docker compose ps` (tout doit être `Up`), puis `sudo ufw status` (le port 8000 doit être autorisé), puis que l'appareil est bien sur le même réseau. |
| « connexion non privée » dans le navigateur | Normal, certificat auto-signé. Accepter une fois par appareil. |
| Le mode hors-ligne ne s'active pas sur téléphone | L'adresse doit être en `https://`, pas `http://`. Revois l'étape 10. |
| Un service ne démarre pas | `docker compose logs <nom_du_service>` (ex : `docker compose logs api`) affiche la raison. |
| Impossible de se connecter en SSH | Vérifie que « OpenSSH server » a bien été coché à l'étape 2, et que la règle de pare-feu du port 22 existe. |
| Les photos/signatures ne s'affichent pas | Le dossier `backend/uploads/` n'a pas été transféré (étape 7). Recopie-le. |

**En dernier recours :** tant que tu n'as rien supprimé, le PC Windows
fonctionne toujours comme avant. Tu peux y retourner et reprendre le
déploiement plus tard, sans aucune perte.

---

## Fiche récapitulative — à remplir et à conserver

```
Adresse du serveur (IP fixe) : ____________________________
Adresse complète de l'app    : https://______________:8000

Nom d'utilisateur Ubuntu     : ____________________________
Mot de passe Ubuntu          : (conservé dans : ___________)

Commande de connexion        : ssh ______@_________________
Dossier de l'application     : /home/______/clinique-app

Date du déploiement          : ____ / ____ / ________
Emplacement de la copie du
fichier .env (hors serveur)  : ____________________________
Emplacement du disque de
sauvegarde externe           : ____________________________
```

### Commandes utiles au quotidien

```bash
cd ~/clinique-app

docker compose ps                  # état des services
docker compose logs --tail=50 api  # journaux de l'application
docker compose restart api         # redémarrer l'application
docker compose up -d               # tout démarrer
df -h                              # espace disque restant
```
