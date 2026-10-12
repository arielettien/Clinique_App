# Clinique App — v0.1 (Dossiers patients)

Ceci est la toute première brique du logiciel de ta clinique : une petite application qui permet d'ajouter et de lister des patients. On construira le reste (connexion sécurisée, vrais formulaires, alertes, exports...) par-dessus, étape par étape.

Ce guide suppose que tu n'as **jamais utilisé Docker**. Suis les étapes dans l'ordre, une par une.

---

## Étape 1 — Installer Docker Desktop (une seule fois)

Docker permet de faire tourner la base de données et l'application sans que tu aies besoin d'installer Python, PostgreSQL, etc. toi-même.

1. Va sur **https://www.docker.com/products/docker-desktop/**
2. Clique sur le bouton de téléchargement pour **Windows**.
3. Une fois le fichier téléchargé, double-clique dessus pour lancer l'installation.
4. Laisse les options par défaut (notamment "Use WSL 2 instead of Hyper-V" si la question apparaît — c'est le choix recommandé).
5. À la fin de l'installation, **redémarre l'ordinateur** si Windows te le demande.
6. Après le redémarrage, lance **Docker Desktop** (cherche-le dans le menu Démarrer).
7. Attends que l'icône de baleine 🐳 en bas à droite de l'écran (barre des tâches) arrête de s'animer — cela veut dire que Docker est prêt.

**Pour vérifier que ça a marché :**
Ouvre le menu Démarrer, tape `powershell`, ouvre "Windows PowerShell", puis tape :

```
docker --version
```

Tu devrais voir une ligne du genre `Docker version 27.x.x`. Si tu vois un message d'erreur, dis-le-moi avant de continuer — c'est le point où ça bloque le plus souvent (par exemple si la virtualisation n'est pas activée dans le BIOS).

---

## Étape 2 — Placer le projet sur ton ordinateur

1. Télécharge et **dézippe** le fichier `clinique-app.zip` que je t'ai fourni.
2. Place le dossier `clinique-app` à un endroit simple, par exemple directement dans `C:\clinique-app` (évite les dossiers avec des espaces ou des accents dans le chemin, ça évite des soucis).

---

## Étape 3 — Lancer l'application

1. Ouvre le dossier `clinique-app` dans l'Explorateur Windows.
2. Fais **Maj + clic droit** dans un espace vide du dossier, puis choisis **"Ouvrir la fenêtre PowerShell ici"** (ou "Ouvrir dans le terminal" selon ta version de Windows).
3. Dans la fenêtre qui s'ouvre, tape exactement :

```
docker compose up --build
```

4. Appuie sur Entrée. La première fois, ça va télécharger des éléments et prendre 1 à 3 minutes — c'est normal. Tu vas voir défiler beaucoup de texte.
5. Quand ça s'arrête de défiler et que tu vois une ligne ressemblant à `Uvicorn running on http://0.0.0.0:8000`, c'est prêt.

**Laisse cette fenêtre PowerShell ouverte** — c'est elle qui fait tourner l'application. Si tu la fermes, l'application s'arrête.

---

## Étape 4 — Ouvrir l'application

Ouvre ton navigateur (Chrome, Edge...) et va à l'adresse :

```
http://localhost:8000
```

Tu devrais voir une page avec un formulaire pour ajouter un patient et un tableau en dessous. Essaie d'ajouter un patient test pour vérifier que tout fonctionne.

---

## Étape 5 — Arrêter l'application

Quand tu as fini pour aujourd'hui :

1. Retourne dans la fenêtre PowerShell.
2. Appuie sur **Ctrl + C** pour arrêter.
3. Tape ensuite `docker compose down` si tu veux libérer les ressources complètement (les données restent enregistrées, rien n'est perdu).

La prochaine fois, il suffira de refaire l'étape 3 (mais ce sera plus rapide, plus besoin de tout retélécharger).

---

## Et après ?

Cette v0.1 est volontairement minimale : pas encore de connexion sécurisée, pas encore de vrais formulaires cliniques, une seule "organisation" implicite. C'est le point de départ sur lequel on va construire, dans l'ordre logique du planning qu'on a défini ensemble :

- Ajouter l'authentification (comptes utilisateurs, mots de passe)
- Remplacer ce formulaire simple par le vrai moteur de formulaires (validation, logique conditionnelle, calculs)
- Ajouter le mode hors-ligne
- Ajouter les exports (Excel, SPSS...)
- Préparer la connexion au serveur de la clinique

Reviens vers moi dès que tu as une page qui fonctionne (ou si quelque chose bloque à une étape) et on passe à la suite.
