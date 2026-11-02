---
name: clinique-ui
description: >
  Conception d'interface pour Clinique App — le système de design, les composants et
  les règles de composition qui font qu'un écran a l'air soigné et actuel plutôt que
  daté. Utilise ce skill DÈS QUE tu touches à l'apparence de l'application : créer une
  page, ajouter un écran ou un panneau, revoir une mise en page, ajouter des boutons ou
  une barre d'outils, un tableau, un formulaire, une fenêtre, un état vide, un message
  d'erreur, ou dès que l'utilisateur dit que quelque chose est moche, daté, tassé,
  illisible, confus, ou « pas beau ». Utilise-le aussi quand la demande semble purement
  fonctionnelle mais produira de l'interface visible, parce que c'est là que
  l'incohérence s'installe sans qu'on la voie venir.
---

# Interface de Clinique App

## Ce que tu construis, et pour qui

Clinique App est utilisée par une petite équipe d'ONG : des agents de santé sur le
terrain, sur téléphone, souvent dehors, parfois sans réseau — et des gestionnaires au
bureau, sur grand écran. Les données sont médicales : une erreur de lecture a des
conséquences réelles.

Deux choses en découlent, et elles ne sont pas en tension :

**Le calme vaut mieux que l'impact.** Ce n'est pas un site vitrine. Rien ne doit
attirer l'œil sans raison. Ce qui doit ressortir, c'est la donnée et l'action en cours.

**Soigné ≠ chargé.** Une interface a l'air actuelle grâce à l'espace, la hiérarchie et
la retenue — pas grâce aux dégradés, aux ombres portées ou aux animations. Le défaut le
plus courant ici n'est pas le manque d'effets, c'est le manque d'air.

## Contraintes techniques, non négociables

Elles viennent du mode hors-ligne et de `CLAUDE.md`, pas d'une préférence :

- **Pas de framework CSS ni JS, pas d'étape de compilation, pas de npm.** HTML, CSS et
  JavaScript écrits à la main.
- **Aucun CDN.** Tout doit fonctionner sans internet. Les icônes viennent de
  `icons.js` (jeu Lucide hébergé localement). Pas de `<link>` ni de `<script>` vers
  l'extérieur, hormis la police Google déjà en place.
- **Réutilise `style.css`** plutôt que d'écrire du style local. Le style propre à une
  page va dans son `<style>`, et uniquement ce qui lui est vraiment propre.
- **Commentaires en français**, et qui expliquent le *pourquoi* quand ce n'est pas
  évident — un bug passé, une contrainte du terrain.

## Les cinq défauts qui font « daté » ici

Ce sont les vrais, mesurés dans ce projet. Quand quelque chose a l'air vieillot, c'est
presque toujours l'un d'eux. Vérifie-les avant de chercher plus loin.

### 1. Trop de tailles de police, toutes trop petites

La feuille de style compte 22 tailles différentes, dont 14 sous `1rem`. C'est la
signature d'une interface d'administration des années 2010 : tout est dense et gris,
rien ne guide l'œil.

Tiens-toi à cette échelle :

| Usage | Taille | Graisse |
|---|---|---|
| Titre de page | `1.75rem` | 700 |
| Titre de section | `1.15rem` | 600 |
| Sous-titre / libellé de groupe | `0.95rem` | 600 |
| Texte courant | `0.9375rem` | 400 |
| Texte secondaire, métadonnées | `0.85rem` | 400 |
| Étiquette, badge | `0.75rem` | 600 |

Six tailles suffisent à toute l'application. Si tu en inventes une septième, c'est
presque toujours qu'un élément est au mauvais niveau de hiérarchie — corrige la
hiérarchie, pas la taille.

### 2. Tout est encadré, rien n'est posé

Chaque bloc a sa bordure grise. L'écran devient une grille de boîtes de même poids, et
l'œil ne sait pas où aller.

Sers-toi de l'élévation pour hiérarchiser :

- **Fond de page** (`--bg`) : rien dessus directement.
- **Carte posée** : `background: var(--surface)` + `box-shadow: var(--shadow-sm)`,
  **sans bordure**. C'est le cas par défaut.
- **Carte active ou survolée** : `box-shadow: var(--shadow-md)`.
- **Bordure** : réservée à ce qui doit être délimité strictement — champs de
  formulaire, tableaux, zones de dépôt de fichier.

Une bordure *et* une ombre sur le même élément, c'est presque toujours une de trop.

### 3. Les emojis servent d'icônes

`📝 📋 📊 🗑 ✏️ ⬇️` apparaissent dans les pages à côté du vrai jeu d'icônes. Les emojis
changent d'aspect selon le téléphone, ne s'alignent pas sur le texte, et ne prennent pas
la couleur courante. C'est le signal « bricolé » le plus visible de l'application.

Utilise `icon('nom')` depuis `icons.js`, ou `<span class="icon-slot" data-icon="nom">`
pour le HTML statique. Si l'icône manque au jeu, ajoute-la à `icons.js` (le contenu
interne d'un SVG Lucide) plutôt que de retomber sur un emoji.

Seule exception admise : un emoji unique et volontairement décoratif dans un état vide.

### 4. Les zones de clic sont trop petites

On trouve des boutons à `padding: 4px 10px`. Sur un téléphone tenu d'une main, dehors,
c'est raté une fois sur trois — et une erreur de saisie sur un dossier médical coûte
cher.

**44 × 44 px minimum** pour tout ce qui se touche. Sur une barre d'outils dense, garde
la hauteur et réduis plutôt le texte, ou regroupe.

### 5. Les barres d'outils grossissent sans jamais être rangées

Celle du tableur a dépassé vingt boutons alignés d'affilée. Au-delà de **sept**
éléments, regroupe par famille avec un séparateur (`.sep`), dans l'ordre où l'on s'en
sert : *agir* (annuler, enregistrer) → *mettre en forme* → *structurer* → *affichage*.
Au-delà de ~quinze, sors les réglages rares dans un panneau dépliable.

## Le système de design

Prends toujours les valeurs dans `style.css`, jamais en dur. Si tu écris `#2095F3`,
`16px` ou `0.87rem` dans une page, c'est une erreur : la variable existe.

**Couleurs** — `--primary` pour l'action principale (une seule par écran), `--secondary`
pour les actions neutres, `--success` pour la confirmation, `--sev-*` pour les alertes
par gravité, `--project-0..5` pour distinguer les projets. Les textes : `--ink`,
`--ink-muted`, `--ink-faint` (celle-ci est déjà réglée pour rester lisible — ne
l'éclaircis pas).

**Espacement** — `--space-1` (4px) à `--space-8` (64px). Toute valeur hors de cette
échelle est à corriger. Entre blocs de sens différent : `--space-5` ou plus. C'est de
là que vient l'essentiel de l'impression de soin.

**Rayons** — `--radius-sm` pour les contrôles, `--radius-md` pour les cartes,
`--radius-lg` pour les fenêtres.

**Mouvement** — `--transition-fast` pour un survol, `--transition-base` pour une
apparition. Rien d'autre. Pas d'animation d'entrée sur le contenu : sur un téléphone
d'entrée de gamme, ça rame, et ça retarde la lecture.

## Composer un écran

L'ordre compte plus que le détail de chaque bloc :

1. **Fil d'Ariane** (`.breadcrumb`) si on est en profondeur.
2. **Titre** en `1.75rem`, et juste en dessous **une phrase** qui dit à quoi sert
   l'écran. Cette phrase coûte une ligne et évite un appel téléphonique.
3. **L'action principale**, visible sans défiler. Une seule en `--primary`.
4. **Le contenu**.
5. **Les réglages rares**, plus bas ou repliés.

Deux règles de composition qui font le plus gros de l'écart entre « ça marche » et
« c'est soigné » :

**Laisse respirer.** Quand un écran paraît daté, le réflexe utile est d'augmenter
l'espace entre les groupes, pas d'ajouter des couleurs ou des bordures.

**Un seul point d'attention.** Si trois éléments sont en bleu vif, aucun ne ressort.
Choisis l'action que l'agent fait neuf fois sur dix, et laisse le reste en secondaire.

## États : ne jamais laisser un écran muet

Un écran vide sans explication donne l'impression que l'application est cassée. Chaque
zone qui peut être vide, lente ou en échec a ses trois états :

- **Vide** : `.empty-state`, une icône, ce qui manque, et le geste pour y remédier.
  « Aucun patient enregistré. Commence par en créer un. » — pas « Aucun résultat ».
- **En cours** : un texte sobre (« Chargement… »). Pas d'animation élaborée.
- **Erreur** : ce qui s'est passé, et quoi faire. « Impossible d'enregistrer le
  patient : ce numéro de téléphone est déjà utilisé par Awa Koné. » — jamais
  « Erreur 409 ».

Les messages s'adressent à un agent de santé, pas à un développeur. Tutoiement, phrases
courtes, pas de vocabulaire technique.

## Le terrain, pas seulement le bureau

- **Téléphone d'abord** pour les écrans de saisie : une seule colonne, champs pleine
  largeur, bouton de validation atteignable au pouce.
- **Hors-ligne** : si une page dépend du réseau, elle doit le dire et rester lisible
  sans lui. L'indicateur existe déjà (`#online-indicator`).
- **Soleil** : les contrastes faibles disparaissent dehors. Garde les textes sur
  `--ink` ou `--ink-muted`, jamais plus clair.
- **Impression** : les écrans qu'on imprime ont une règle à part — remets
  `html, body { height: auto !important; }` dans `@media print`, sinon une seconde page
  blanche apparaît (bug déjà rencontré ici).

## Avant de dire que c'est fini

Relis l'écran avec ces questions. Elles attrapent l'essentiel :

1. Ai-je inventé une taille de police, une couleur ou un espacement hors des échelles ?
2. Y a-t-il un emoji là où une icône devrait être ?
3. Un élément porte-t-il à la fois une bordure et une ombre ?
4. Le titre de page est-il nettement plus gros que le reste ?
5. Y a-t-il plus d'un bouton `--primary` visible ?
6. Tout ce qui se touche fait-il au moins 44 px ?
7. Les zones pouvant être vides ont-elles un message qui dit quoi faire ?
8. La page tient-elle sur un écran de téléphone sans défilement horizontal ?
9. Un message d'erreur parle-t-il technique plutôt que métier ?
10. Si la barre d'outils dépasse sept éléments, est-elle groupée ?

## Où regarder pour s'aligner

Avant d'écrire une nouvelle page, ouvre une page existante proche et reprends sa
structure — c'est plus sûr que de partir de zéro :

- `patients.html` — liste filtrable, tableau trié, fenêtre de détail.
- `project_hub.html` — cartes de navigation entre modules.
- `survey.html` — onglets, barre d'outils dense, tableau de données.
- `spreadsheets.html` — dépôt de fichier, aperçu, actions par ligne.
- `history.html` — page de lecture, sections, impression.

Et pour la coquille commune (barre latérale, bouton retour, indicateur hors-ligne),
appelle `initAppShell(projectId)` : ne réinvente jamais ces éléments.
