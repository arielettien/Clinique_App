# Importer un questionnaire dans Clinique App

Ce guide explique comment importer la **structure** d'un questionnaire (ses questions),
sans développeur. Il ne concerne pas l'import des réponses déjà collectées — voir la
dernière section.

Où : page **Enquête** d'un projet → bouton **Importer**.

## 1. Les fichiers acceptés

L'application reconnaît le format toute seule. Trois cas :

| Tu as… | Ce qu'il faut savoir |
|---|---|
| **Un export Kobo Toolbox** (feuilles `survey`, `choices`, `settings`) | Importe-le tel quel. L'application fait la conversion. |
| **Le modèle de l'application** (feuille `Structure`, colonnes `id`, `type`, `label`…) | C'est le format natif. Pour l'obtenir : ouvre un formulaire existant → **Télécharger la structure**, puis modifie-le dans Excel. |
| **Un tableau simple** avec des en-têtes en français (`Identifiant`, `Type`, `Libellé`, `Obligatoire`…) | Accepté aussi. La seule colonne indispensable est le **libellé** de la question. |

Un fichier CSV fonctionne pour les deux derniers cas, mais sans listes de choix
réutilisables (un CSV n'a qu'une seule feuille).

## 2. Ce que l'import fait avec un fichier Kobo

Kobo et Clinique App ne parlent pas tout à fait la même langue. L'import traduit, et
**te dit tout ce qu'il a adapté** dans la fenêtre qui s'affiche après l'import.
Lis cette liste : elle s'adresse à toi.

Ce qui est traduit sans que tu aies rien à faire :

- `label::Français (fr)` → le libellé de la question
- `select_one oui_non` → une question à choix unique, reliée à la liste `oui_non`
- `${age} = 'oui'` → `age == 'oui'` (la syntaxe des conditions)
- `${age} >= 15 and ${age} <= 80` → un minimum et un maximum sur la question
- les lignes techniques `start`, `end`, `today`, `deviceid`… → retirées (ce ne sont
  pas des questions)

Ce qui demande un coup d'œil de ta part après l'import :

- **Un `calculate` sans libellé** reçoit un libellé fabriqué (`calc_age_cat` →
  « Calc age cat »). Renomme-le dans le constructeur si tu le gardes. Ces champs
  calculés font souvent double emploi avec le tableau de bord, qui recalcule tout
  depuis les réponses : tu peux les supprimer.
- **Une condition d'affichage que l'application ne sait pas traduire** (par exemple
  `count-selected(...)`) est **retirée** : la question est alors toujours visible.
  C'est volontaire — une question affichée à tort se saute, une question cachée à
  tort fait perdre une donnée sans que personne ne le voie. Rétablis la condition à
  la main dans le constructeur si nécessaire.
- **Une contrainte complexe** (autre qu'un simple « entre A et B ») est signalée, pas
  traduite. À reporter à la main.

## 3. Quand l'import est refusé

Le message d'erreur décrit **ce que l'application a vu** : le format reconnu, les
feuilles, les colonnes trouvées, et ce qui manque. Lis-le avant de chercher ailleurs.

Les trois causes fréquentes :

| Le message dit… | Cause | Solution |
|---|---|---|
| « Colonnes trouvées : Nom, Prénom, Téléphone, … » et « Il manque la colonne du libellé » | C'est un **fichier de réponses** (une ligne par patient), pas une structure | Ce n'est pas le bon fichier. Il faut la structure du questionnaire, pas les données. |
| « Colonnes trouvées : aucune » | La feuille lue est vide, ou les en-têtes ne sont pas sur la première ligne | Supprime les lignes de titre au-dessus des en-têtes. |
| « Feuille lue : Feuil1 » alors que ta structure est sur une autre feuille | Sans feuille nommée `Structure` ou `survey`, l'application lit la **première** feuille | Renomme la bonne feuille `Structure`, ou mets-la en premier. |

## 4. Juste après l'import

Trois réglages que le fichier ne peut pas transporter, à faire dans le constructeur :

1. **Le type du formulaire.** L'import crée toujours une *Enquête*. Si le questionnaire
   suit les mêmes patients dans le temps (visites, suivi à 1 an…), passe-le en
   **Suivi — visite**. Le choix se fait dans le constructeur, en haut.
2. **La date de prochaine visite.** Si le questionnaire a une question « date du
   prochain rendez-vous », ouvre-la et coche **« Utiliser comme date de prochaine
   visite »**. Sans ça, aucun patient ne sera jamais signalé en retard dans Suivi.
3. **Les avertissements** de la fenêtre d'import, un par un.

## 5. Une étude déjà réalisée, avec ses réponses

L'import de formulaire n'importe **que la structure**. Pour une étude terminée
ailleurs (Google Forms, Kobo…) dont tu as l'Excel des **réponses** — une ligne par
personne, une colonne par question — il y a un autre chemin, qui crée tout d'un
coup : le questionnaire, les patients et les fiches.

Où : page **Enquête** → **Importer une étude déjà remplie**.

Ça se passe en trois étapes, et rien n'est enregistré avant la troisième :

1. **Le fichier.** Donne un nom à l'étude (ce sera le nom du formulaire) et choisis
   l'Excel. Si le fichier contient deux questionnaires côte à côte (colonnes en
   double avec un suffixe comme `Q2`), coupe-le d'abord en deux fichiers dans
   Excel : un fichier = un questionnaire.
2. **Vérifier.** L'application montre chaque colonne avec ce qu'elle a deviné :
   - le **rôle** : les colonnes d'identité (prénom, nom, code patient, téléphone,
     sexe, date ou année de naissance, date de collecte) deviennent le patient ;
     tout le reste est une **question** ; *ignorer* écarte une colonne (adresse
     e-mail, horodatage automatique, colonne en double) ;
   - le **type** de chaque question, deviné d'après les valeurs : dates, nombres,
     choix unique quand les mêmes réponses reviennent, choix multiples pour les
     grilles à cases.
   Corrige ce qui est faux dans les listes déroulantes. Puis **Vérifier sans rien
   enregistrer** : tu obtiens le rapport exact de ce qui *serait* créé.
3. **Importer pour de bon.** Même rapport, cette fois réel, avec des liens vers les
   données et les patients.

Ce qu'il faut savoir :

- **Une ligne refusée n'arrête pas les autres.** Elle est listée avec son numéro de
  ligne Excel et la raison. Corrige-la dans le fichier et relance le même import :
  les lignes déjà passées ne seront **jamais** créées en double.
- **Une personne vue plusieurs fois** (même code patient, ou même nom et téléphone)
  = un seul patient avec plusieurs fiches. Une personne déjà présente dans le projet
  est réutilisée, pas dupliquée.
- **Sans nom de patient**, chaque patient s'appelle « Patient » suivi de son code.
  C'est volontaire et visible : à toi de décider si tu complètes ensuite.
- **Une année de naissance seule** devient le 1er juillet de cette année — l'âge
  est juste à six mois près, sans effet sur des tranches d'âge de cinq ans.
- Le formulaire créé est de type **Enquête**. S'il s'agit en réalité d'un suivi dans
  le temps, change le type dans le constructeur après l'import.
- L'import est inscrit au **journal d'activité**, avec les chiffres.

## Pour un développeur

Le code est dans `django_backend/core/form_import_formats.py`, appelé par
`/api/forms/import`. Pour ajouter un nouveau format : étendre `detecter()` et
`ALIAS`, puis produire des lignes aux colonnes du modèle pour `rows_to_sections()`.
Les tests sont dans `core/tests.py`, classe `ImportStructureTests` — ajoute un cas
par format.
