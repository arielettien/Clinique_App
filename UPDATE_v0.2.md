# Clinique App — Mise à jour v0.2 : connexion sécurisée

Cette version ajoute une page de connexion. Les dossiers patients ne sont plus accessibles sans être connecté.

**Rassure-toi : ton patient test (Papi Djo) ne sera pas perdu.** Les données sont stockées dans un espace séparé du dossier de fichiers (un "volume" Docker), donc remplacer les fichiers ci-dessous ne touche pas à la base de données.

---

## Étape 1 — Arrêter l'application actuelle

1. Retourne dans la fenêtre PowerShell où `docker compose up --build` tourne encore.
2. Appuie sur **Ctrl + C** pour l'arrêter.
3. Tape ensuite (⚠️ **sans** `-v`, pour garder les données) :

```
docker compose down
```

---

## Étape 2 — Remplacer les fichiers du projet

1. Télécharge et dézippe le nouveau fichier `clinique-app.zip` que je te fournis.
2. Remplace **entièrement** le contenu de ton dossier `C:\Users\ARIEL ETTIEN\Desktop\clinique-app\clinique-app` par celui du nouveau zip (tu peux supprimer l'ancien dossier `backend` et le fichier `docker-compose.yml`, puis copier les nouveaux à la place).

---

## Étape 3 — Relancer (avec reconstruction)

On a ajouté de nouvelles bibliothèques Python (pour gérer les mots de passe et les connexions), donc il faut reconstruire l'image :

```
docker compose up --build
```

Attends de revoir la ligne `Uvicorn running on http://0.0.0.0:8000`. **Laisse cette fenêtre ouverte**, comme avant.

---

## Étape 4 — Créer ton compte administrateur (une seule fois)

Cette étape se fait dans un **deuxième terminal**, pendant que le premier continue de tourner.

1. Clique sur le **`+`** en haut de la fenêtre du terminal pour ouvrir un nouvel onglet (ou ouvre une nouvelle fenêtre PowerShell).
2. Dans ce nouvel onglet, va dans le même dossier :

```
cd "C:\Users\ARIEL ETTIEN\Desktop\clinique-app\clinique-app"
```

3. Tape :

```
docker compose exec api python create_admin.py
```

4. Réponds aux questions posées :
   - **Email de connexion** : ce que tu veux, par exemple `admin@clinique.local`
   - **Nom complet** : ton nom
   - **Mot de passe** : au moins 8 caractères (rien ne s'affiche en tapant, c'est normal, comme pour WSL tout à l'heure)
   - **Confirme le mot de passe** : retape le même

Tu dois voir : `Compte créé avec succès : ...`

---

## Étape 5 — Se connecter

1. Ouvre (ou recharge) `http://localhost:8000` dans ton navigateur.
2. Tu devrais maintenant arriver sur une **page de connexion**.
3. Entre l'email et le mot de passe que tu viens de créer.
4. Tu devrais retrouver la liste des patients (Papi Djo compris), avec ton nom affiché en haut à droite et un bouton "Se déconnecter".

---

## Et après ?

Prochaine étape recommandée : remplacer le formulaire simple actuel par le vrai moteur de formulaires cliniques (avec validation, logique conditionnelle, champs calculés) — la vraie brique centrale du projet. On pourra aussi ajouter d'autres comptes utilisateurs (pour ton équipe) une fois que celui-ci fonctionne bien.

Dis-moi comment se passe la connexion, ou envoie une capture si quelque chose coince.
