# Description à donner à Stitch

Stitch comprend mieux l'anglais. **Copie le bloc anglais** ci-dessous, la version
française est là pour que tu saches ce que tu colles.

---

## 1. Le prompt principal (à copier tel quel)

```
Internal clinical data-collection dashboard for a small health NGO in Côte d'Ivoire.
Not a marketing site, not a consumer app: a working tool, used every day, for medical records.

USERS AND CONTEXT
- Field health workers on Android phones, outdoors in bright sunlight, often with no network.
- Office managers on 1440px desktop screens.
- Interface language is French.

SCREENS I NEED
1. Project list: cards with a cover photo, a search bar, one primary "create" action.
2. Case dashboard: breadcrumb, page title, 4 KPI counters (active / overdue / alerts / closed),
   a data table of patient records (code, name, age, last visit, status badge), alert banners.
3. Patient list: filter bar, sortable table, row actions, detail panel.
4. Data-entry form: long form, many fields, one column, large touch targets, sticky submit button.

VISUAL DIRECTION
- White background. Structure carried by thin blue outlines rather than shadows.
- Deep navy headers (table headers, section titles) for a strong, readable top edge.
- One pure blue accent for the single primary action per screen.
- Status colours kept conventional and separate from the accent: green active, orange overdue,
  red alert. Colour is medical information, not decoration.
- Calm and data-first. Nothing should attract the eye without a reason.

HARD REQUIREMENTS
- Text contrast at least 4.5:1 everywhere. It must stay readable in direct sunlight.
- Every touch target at least 44x44px.
- No hover-only interaction: a phone has no hover.
- Legible on a 390px-wide phone with no horizontal scrolling.
- No emoji used as icons: outline SVG icons only, single consistent stroke weight.

AVOID
- Neumorphism, soft embossed cards, heavy gradients, glassmorphism.
- Dense admin look with 12px type and 8px padding.
- Purple/pink AI gradients.
- More than one bright call-to-action per screen.
```

---

## 2. La même chose en français, pour ta compréhension

Tableau de bord interne de collecte de données cliniques, pour une petite ONG de santé
en Côte d'Ivoire. Ce n'est ni un site vitrine ni une application grand public : c'est un
outil de travail quotidien, qui porte des dossiers médicaux.

**Qui s'en sert** — des agents de santé sur téléphone Android, dehors en plein soleil,
souvent sans réseau ; et des gestionnaires au bureau sur grand écran. Interface en français.

**Les écrans demandés** — liste de projets ; tableau de bord de suivi (fil d'Ariane, titre,
4 compteurs, tableau de dossiers, bandeaux d'alerte) ; liste de patients filtrable ;
formulaire de saisie long en une colonne.

**La direction visuelle** — fond blanc, structure portée par des traits bleus fins plutôt
que par des ombres, en-têtes bleu profond, un seul bleu d'accent par écran, couleurs de
statut conventionnelles et distinctes de l'accent.

**Les exigences non négociables** — contraste minimum 4,5:1, cibles tactiles de 44 px,
aucune interaction au survol seul, lisible sur 390 px sans défilement horizontal,
icônes SVG et jamais d'emoji.

**À éviter** — neumorphisme, dégradés lourds, look administratif dense en 12 px,
dégradés violet/rose, plus d'un bouton vif par écran.

---

## 3. Comment t'en servir

1. Colle le bloc anglais dans Stitch, génère.
2. Demande des variantes : ajoute à la fin `more editorial`, `more compact`,
   `warmer`, `higher contrast` — une variante par essai, pour pouvoir comparer.
3. Garde celle qui te plaît. Exporte le **`DESIGN.md`**, ou fais une capture.
4. Donne-le moi : j'en tire les couleurs et les règles et je les traduis dans une
   couche CSS, comme `style_blanc_bleu.css`.

⚠️ Ne mets jamais de vraies données patient dans Stitch. Décris la structure, pas le contenu.

---

## 4. Pourquoi ne pas reprendre son code directement

Stitch sort du Tailwind, du React, des dépendances chargées par CDN. Le projet
l'interdit (`CLAUDE.md`) : pas de framework, pas d'étape de compilation, pas de npm,
et surtout aucun CDN — sinon le mode hors ligne tombe, et c'est lui qui permet de
travailler sans réseau sur le terrain.

Stitch sert donc à **décider de l'apparence**, pas à fournir le code.
