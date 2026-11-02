// Moteur de calcul du module Tableur.
//
// Écrit à la main (analyseur lexical + analyse descendante récursive),
// volontairement SANS eval() — même principe que formula_engine.py côté
// serveur : une formule saisie par un utilisateur ne doit jamais pouvoir
// exécuter du code dans le navigateur de quelqu'un d'autre.
//
// Les noms de fonctions existent en français ET en anglais : les tableurs de
// l'équipe viennent d'Excel en français (SOMME, MOYENNE), mais la
// documentation et les habitudes de certains utilisent l'anglais.

const ERREUR_REF = '#REF!';
const ERREUR_DIV = '#DIV/0!';
const ERREUR_VALEUR = '#VALEUR!';
const ERREUR_NOM = '#NOM?';
const ERREUR_CYCLE = '#CYCLE!';
const TOUTES_ERREURS = [ERREUR_REF, ERREUR_DIV, ERREUR_VALEUR, ERREUR_NOM, ERREUR_CYCLE];

/* ───────────── Références de cellules ───────────── */

// "A" -> 0, "B" -> 1, ... "Z" -> 25, "AA" -> 26
function lettresVersIndex(lettres) {
  let n = 0;
  for (const c of lettres.toUpperCase()) n = n * 26 + (c.charCodeAt(0) - 64);
  return n - 1;
}

function indexVersLettres(index) {
  let s = '';
  let n = index + 1;
  while (n > 0) {
    const reste = (n - 1) % 26;
    s = String.fromCharCode(65 + reste) + s;
    n = Math.floor((n - 1) / 26);
  }
  return s;
}

function refVersCoord(ref) {
  const m = /^\$?([A-Za-z]+)\$?(\d+)$/.exec(ref);
  if (!m) return null;
  return { col: lettresVersIndex(m[1]), ligne: parseInt(m[2], 10) - 1 };
}

function coordVersRef(ligne, col) {
  return indexVersLettres(col) + (ligne + 1);
}

/* ───────────── Analyse lexicale ───────────── */

function decouper(formule) {
  const jetons = [];
  let i = 0;
  while (i < formule.length) {
    const c = formule[i];
    if (c === ' ') { i++; continue; }

    if (/[0-9]/.test(c) || (c === '.' && /[0-9]/.test(formule[i + 1] || ''))) {
      let j = i;
      while (j < formule.length && /[0-9.]/.test(formule[j])) j++;
      jetons.push({ type: 'nombre', valeur: parseFloat(formule.slice(i, j)) });
      i = j;
      continue;
    }

    if (c === '"') {
      let j = i + 1;
      let texte = '';
      while (j < formule.length && formule[j] !== '"') { texte += formule[j]; j++; }
      jetons.push({ type: 'texte', valeur: texte });
      i = j + 1;
      continue;
    }

    if (/[A-Za-z_$]/.test(c)) {
      let j = i;
      while (j < formule.length && /[A-Za-z0-9_$.]/.test(formule[j])) j++;
      const mot = formule.slice(i, j);
      // Une plage ("A1:B10") se reconnaît ici, avant de décider s'il s'agit
      // d'une référence simple ou d'un nom de fonction.
      if (formule[j] === ':' && refVersCoord(mot)) {
        let k = j + 1;
        while (k < formule.length && /[A-Za-z0-9$]/.test(formule[k])) k++;
        const fin = formule.slice(j + 1, k);
        if (refVersCoord(fin)) {
          jetons.push({ type: 'plage', debut: mot, fin });
          i = k;
          continue;
        }
      }
      if (formule[j] === '(') jetons.push({ type: 'fonction', valeur: mot.toUpperCase() });
      else if (refVersCoord(mot)) jetons.push({ type: 'ref', valeur: mot });
      else jetons.push({ type: 'nom', valeur: mot.toUpperCase() });
      i = j;
      continue;
    }

    if ('+-*/^(),;%'.includes(c)) { jetons.push({ type: c }); i++; continue; }

    if ('<>='.includes(c)) {
      const deux = formule.slice(i, i + 2);
      if (['<=', '>=', '<>'].includes(deux)) { jetons.push({ type: 'comp', valeur: deux }); i += 2; }
      else { jetons.push({ type: 'comp', valeur: c }); i++; }
      continue;
    }

    if (c === '&') { jetons.push({ type: '&' }); i++; continue; }

    throw new Error(ERREUR_VALEUR);
  }
  return jetons;
}

/* ───────────── Analyse syntaxique ───────────── */

function analyser(jetons) {
  let pos = 0;
  const courant = () => jetons[pos];
  const avaler = (type) => {
    if (!courant() || courant().type !== type) throw new Error(ERREUR_VALEUR);
    return jetons[pos++];
  };

  function comparaison() {
    let gauche = concatenation();
    while (courant() && courant().type === 'comp') {
      const op = jetons[pos++].valeur;
      gauche = { type: 'binaire', op, gauche, droite: concatenation() };
    }
    return gauche;
  }

  function concatenation() {
    let gauche = somme();
    while (courant() && courant().type === '&') {
      pos++;
      gauche = { type: 'binaire', op: '&', gauche, droite: somme() };
    }
    return gauche;
  }

  function somme() {
    let gauche = produit();
    while (courant() && (courant().type === '+' || courant().type === '-')) {
      const op = jetons[pos++].type;
      gauche = { type: 'binaire', op, gauche, droite: produit() };
    }
    return gauche;
  }

  function produit() {
    let gauche = puissance();
    while (courant() && (courant().type === '*' || courant().type === '/')) {
      const op = jetons[pos++].type;
      gauche = { type: 'binaire', op, gauche, droite: puissance() };
    }
    return gauche;
  }

  function puissance() {
    const gauche = unaire();
    if (courant() && courant().type === '^') {
      pos++;
      return { type: 'binaire', op: '^', gauche, droite: puissance() };
    }
    return gauche;
  }

  function unaire() {
    if (courant() && (courant().type === '-' || courant().type === '+')) {
      const op = jetons[pos++].type;
      return { type: 'unaire', op, operande: unaire() };
    }
    return pourcentage();
  }

  function pourcentage() {
    const valeur = primaire();
    if (courant() && courant().type === '%') { pos++; return { type: 'pourcent', operande: valeur }; }
    return valeur;
  }

  function primaire() {
    const j = courant();
    if (!j) throw new Error(ERREUR_VALEUR);

    if (j.type === 'nombre') { pos++; return { type: 'nombre', valeur: j.valeur }; }
    if (j.type === 'texte') { pos++; return { type: 'texte', valeur: j.valeur }; }
    if (j.type === 'ref') { pos++; return { type: 'ref', valeur: j.valeur }; }
    if (j.type === 'plage') { pos++; return { type: 'plage', debut: j.debut, fin: j.fin }; }

    if (j.type === 'nom') {
      pos++;
      if (j.valeur === 'VRAI' || j.valeur === 'TRUE') return { type: 'booleen', valeur: true };
      if (j.valeur === 'FAUX' || j.valeur === 'FALSE') return { type: 'booleen', valeur: false };
      throw new Error(ERREUR_NOM);
    }

    if (j.type === 'fonction') {
      pos++;
      avaler('(');
      const args = [];
      if (courant() && courant().type !== ')') {
        args.push(comparaison());
        // Excel en français sépare les arguments par ";", en anglais par ","
        while (courant() && (courant().type === ';' || courant().type === ',')) {
          pos++;
          args.push(comparaison());
        }
      }
      avaler(')');
      return { type: 'appel', nom: j.valeur, args };
    }

    if (j.type === '(') {
      pos++;
      const expr = comparaison();
      avaler(')');
      return expr;
    }

    throw new Error(ERREUR_VALEUR);
  }

  const arbre = comparaison();
  if (pos < jetons.length) throw new Error(ERREUR_VALEUR);
  return arbre;
}

/* ───────────── Évaluation ───────────── */

function versNombre(v) {
  if (v === null || v === undefined || v === '') return 0;
  if (typeof v === 'number') return v;
  if (typeof v === 'boolean') return v ? 1 : 0;
  if (TOUTES_ERREURS.includes(v)) throw new Error(v);
  // Accepte la virgule décimale : c'est ce que tape un utilisateur francophone.
  const n = parseFloat(String(v).replace(',', '.'));
  if (isNaN(n)) throw new Error(ERREUR_VALEUR);
  return n;
}

function estVide(v) {
  return v === null || v === undefined || v === '';
}

// Noms français et anglais pointent vers la même implémentation.
const FONCTIONS = {
  SOMME: (vals) => vals.reduce((t, v) => t + versNombre(v), 0),
  MOYENNE: (vals) => {
    const nombres = vals.filter(v => !estVide(v)).map(versNombre);
    if (nombres.length === 0) throw new Error(ERREUR_DIV);
    return nombres.reduce((a, b) => a + b, 0) / nombres.length;
  },
  MIN: (vals) => {
    const n = vals.filter(v => !estVide(v)).map(versNombre);
    return n.length ? Math.min(...n) : 0;
  },
  MAX: (vals) => {
    const n = vals.filter(v => !estVide(v)).map(versNombre);
    return n.length ? Math.max(...n) : 0;
  },
  NB: (vals) => vals.filter(v => !estVide(v) && !isNaN(parseFloat(String(v).replace(',', '.')))).length,
  NBVAL: (vals) => vals.filter(v => !estVide(v)).length,
  ARRONDI: (vals) => {
    const f = Math.pow(10, vals.length > 1 ? versNombre(vals[1]) : 0);
    return Math.round(versNombre(vals[0]) * f) / f;
  },
  ABS: (vals) => Math.abs(versNombre(vals[0])),
  RACINE: (vals) => {
    const n = versNombre(vals[0]);
    if (n < 0) throw new Error(ERREUR_VALEUR);
    return Math.sqrt(n);
  },
  ENT: (vals) => Math.floor(versNombre(vals[0])),
  PUISSANCE: (vals) => Math.pow(versNombre(vals[0]), versNombre(vals[1])),
  MEDIANE: (vals) => {
    const n = vals.filter(v => !estVide(v)).map(versNombre).sort((a, b) => a - b);
    if (!n.length) throw new Error(ERREUR_DIV);
    const m = Math.floor(n.length / 2);
    return n.length % 2 ? n[m] : (n[m - 1] + n[m]) / 2;
  },
};
const ALIAS = {
  SUM: 'SOMME', AVERAGE: 'MOYENNE', COUNT: 'NB', COUNTA: 'NBVAL',
  ROUND: 'ARRONDI', SQRT: 'RACINE', INT: 'ENT', POWER: 'PUISSANCE', MEDIAN: 'MEDIANE',
};

/**
 * Évalue une formule.
 * @param {string} formule - sans le "=" initial
 * @param {(ref:string)=>any} lireCellule - valeur CALCULÉE d'une cellule
 */
function evaluerFormule(formule, lireCellule) {
  const arbre = analyser(decouper(formule));

  function valeursDePlage(noeud) {
    const d = refVersCoord(noeud.debut);
    const f = refVersCoord(noeud.fin);
    if (!d || !f) throw new Error(ERREUR_REF);
    const valeurs = [];
    for (let l = Math.min(d.ligne, f.ligne); l <= Math.max(d.ligne, f.ligne); l++) {
      for (let c = Math.min(d.col, f.col); c <= Math.max(d.col, f.col); c++) {
        valeurs.push(lireCellule(coordVersRef(l, c)));
      }
    }
    return valeurs;
  }

  function evaluer(n) {
    switch (n.type) {
      case 'nombre': return n.valeur;
      case 'texte': return n.valeur;
      case 'booleen': return n.valeur;
      case 'ref': {
        const v = lireCellule(n.valeur.replace(/\$/g, '').toUpperCase());
        if (TOUTES_ERREURS.includes(v)) throw new Error(v);
        return v;
      }
      case 'plage': throw new Error(ERREUR_VALEUR); // une plage n'a de sens que dans une fonction
      case 'unaire': {
        const v = versNombre(evaluer(n.operande));
        return n.op === '-' ? -v : v;
      }
      case 'pourcent': return versNombre(evaluer(n.operande)) / 100;
      case 'binaire': {
        if (n.op === '&') return String(afficher(evaluer(n.gauche))) + String(afficher(evaluer(n.droite)));
        if (['=', '<>', '<', '>', '<=', '>='].includes(n.op)) {
          const g = evaluer(n.gauche);
          const d = evaluer(n.droite);
          const comparables = (typeof g === 'string' || typeof d === 'string')
            ? [String(g), String(d)] : [versNombre(g), versNombre(d)];
          const [a, b] = comparables;
          switch (n.op) {
            case '=': return a === b;
            case '<>': return a !== b;
            case '<': return a < b;
            case '>': return a > b;
            case '<=': return a <= b;
            case '>=': return a >= b;
          }
        }
        const g = versNombre(evaluer(n.gauche));
        const d = versNombre(evaluer(n.droite));
        switch (n.op) {
          case '+': return g + d;
          case '-': return g - d;
          case '*': return g * d;
          case '/':
            if (d === 0) throw new Error(ERREUR_DIV);
            return g / d;
          case '^': return Math.pow(g, d);
        }
        throw new Error(ERREUR_VALEUR);
      }
      case 'appel': {
        const nom = ALIAS[n.nom] || n.nom;
        // SI/IF est à part : ses branches ne doivent PAS être évaluées toutes
        // les deux (sinon =SI(B1=0;"";A1/B1) renverrait une division par zéro
        // alors que c'est précisément ce qu'on cherche à éviter).
        if (nom === 'SI' || nom === 'IF') {
          const condition = evaluer(n.args[0]);
          const vrai = typeof condition === 'boolean' ? condition : versNombre(condition) !== 0;
          if (vrai) return n.args.length > 1 ? evaluer(n.args[1]) : true;
          return n.args.length > 2 ? evaluer(n.args[2]) : false;
        }
        const fn = FONCTIONS[nom];
        if (!fn) throw new Error(ERREUR_NOM);
        const valeurs = [];
        for (const a of n.args) {
          if (a.type === 'plage') valeurs.push(...valeursDePlage(a));
          else valeurs.push(evaluer(a));
        }
        return fn(valeurs);
      }
    }
    throw new Error(ERREUR_VALEUR);
  }

  return evaluer(arbre);
}

/** Références citées par une formule — sert à détecter les cycles. */
function referencesDe(formule) {
  const refs = new Set();
  let jetons;
  try { jetons = decouper(formule); } catch (e) { return refs; }
  for (const j of jetons) {
    if (j.type === 'ref') refs.add(j.valeur.replace(/\$/g, '').toUpperCase());
    else if (j.type === 'plage') {
      const d = refVersCoord(j.debut);
      const f = refVersCoord(j.fin);
      if (!d || !f) continue;
      for (let l = Math.min(d.ligne, f.ligne); l <= Math.max(d.ligne, f.ligne); l++) {
        for (let c = Math.min(d.col, f.col); c <= Math.max(d.col, f.col); c++) {
          refs.add(coordVersRef(l, c));
        }
      }
    }
  }
  return refs;
}

/** Mise en forme d'une valeur calculée pour l'affichage dans une cellule. */
function afficher(v) {
  if (v === null || v === undefined) return '';
  if (typeof v === 'boolean') return v ? 'VRAI' : 'FAUX';
  if (typeof v === 'number') {
    if (!isFinite(v)) return ERREUR_DIV;
    // Coupe les décimales parasites du calcul en virgule flottante
    // (0.1 + 0.2 doit afficher 0,3 et non 0,30000000000000004).
    const arrondi = Math.round(v * 1e10) / 1e10;
    return String(arrondi).replace('.', ',');
  }
  return String(v);
}

window.TableurEngine = {
  evaluerFormule, referencesDe, afficher,
  refVersCoord, coordVersRef, indexVersLettres, lettresVersIndex,
  ERREUR_CYCLE, ERREUR_VALEUR, TOUTES_ERREURS,
};

/* ═══════════ Transformation des références ═══════════
 *
 * Réécrit les références d'une formule sans toucher au reste du texte
 * (espaces, chaînes entre guillemets, noms de fonctions). C'est la brique
 * commune à deux besoins qui semblent différents mais sont le même :
 *
 *   - insérer/supprimer une ligne : toutes les références situées après le
 *     point d'insertion se décalent ;
 *   - recopier une formule vers le bas : les références RELATIVES se
 *     décalent, les références absolues ($A$1) restent fixes.
 *
 * Sans ça, insérer une ligne au milieu d'un budget fausse silencieusement
 * tous les totaux — le pire défaut possible pour un tableur.
 */

// Repère les références ("A1", "$B$7") en ignorant ce qui est entre
// guillemets et ce qui est suivi d'une parenthèse (un nom de fonction).
function transformerFormule(formule, transformer) {
  let resultat = '';
  let i = 0;
  while (i < formule.length) {
    const c = formule[i];

    if (c === '"') {                       // chaîne : recopiée telle quelle
      let j = i + 1;
      while (j < formule.length && formule[j] !== '"') j++;
      resultat += formule.slice(i, Math.min(j + 1, formule.length));
      i = j + 1;
      continue;
    }

    if (/[A-Za-z$]/.test(c)) {
      let j = i;
      while (j < formule.length && /[A-Za-z0-9$]/.test(formule[j])) j++;
      const mot = formule.slice(i, j);
      const estFonction = formule[j] === '(';
      const m = estFonction ? null : /^(\$?)([A-Za-z]+)(\$?)(\d+)$/.exec(mot);
      if (m) {
        const [, dollarCol, lettres, dollarLigne, chiffres] = m;
        const transforme = transformer({
          col: lettresVersIndex(lettres),
          ligne: parseInt(chiffres, 10) - 1,
          colAbsolue: dollarCol === '$',
          ligneAbsolue: dollarLigne === '$',
        });
        if (!transforme) resultat += ERREUR_REF;
        else resultat += dollarCol + indexVersLettres(transforme.col)
                       + dollarLigne + (transforme.ligne + 1);
      } else {
        resultat += mot;
      }
      i = j;
      continue;
    }

    resultat += c;
    i++;
  }
  return resultat;
}

/**
 * Décalage dû à une insertion/suppression de ligne ou de colonne.
 * Le caractère absolu ($) ne protège PAS d'un décalage : dans Excel aussi,
 * insérer une ligne au-dessus de $A$5 en fait $A$6 — ce qui compte, c'est
 * que la formule continue de désigner la MÊME cellule qu'avant.
 */
function decalerReferences(formule, axe, depuis, delta) {
  return transformerFormule(formule, (r) => {
    const position = axe === 'ligne' ? r.ligne : r.col;
    if (delta < 0 && position >= depuis && position < depuis - delta) return null;  // cible supprimée
    if (position < depuis) return { col: r.col, ligne: r.ligne };
    return axe === 'ligne'
      ? { col: r.col, ligne: r.ligne + delta }
      : { col: r.col + delta, ligne: r.ligne };
  });
}

/**
 * Déplacement dû à une recopie : seules les références relatives suivent.
 * C'est ce qui permet de tirer "=A1*B1" vers le bas pour obtenir
 * "=A2*B2", tout en gardant "=$A$1" pointé sur une constante.
 */
function translaterReferences(formule, dLigne, dCol, maxLigne, maxCol) {
  return transformerFormule(formule, (r) => {
    const ligne = r.ligneAbsolue ? r.ligne : r.ligne + dLigne;
    const col = r.colAbsolue ? r.col : r.col + dCol;
    if (ligne < 0 || col < 0 || ligne > maxLigne || col > maxCol) return null;
    return { col, ligne };
  });
}

/* ═══════════ Formats d'affichage ═══════════ */

const FORMATS = {
  standard: { libelle: 'Standard' },
  nombre0: { libelle: 'Nombre (0 déc.)', decimales: 0 },
  nombre2: { libelle: 'Nombre (2 déc.)', decimales: 2 },
  monnaie: { libelle: 'Monnaie (FCFA)', decimales: 0, suffixe: ' FCFA' },
  pourcent: { libelle: 'Pourcentage', decimales: 1, facteur: 100, suffixe: ' %' },
};

// Sépare les milliers par une espace insécable : la convention française,
// et ça évite qu'un budget à sept chiffres devienne illisible.
function separerMilliers(texte) {
  const [entier, decimal] = texte.split(',');
  const signe = entier.startsWith('-') ? '-' : '';
  const chiffres = signe ? entier.slice(1) : entier;
  const groupe = chiffres.replace(/\B(?=(\d{3})+(?!\d))/g, '\u00a0');
  return signe + groupe + (decimal !== undefined ? ',' + decimal : '');
}

function afficherAvecFormat(valeur, formatNom) {
  const format = FORMATS[formatNom];
  if (!format || formatNom === 'standard' || typeof valeur !== 'number') {
    return afficher(valeur);
  }
  if (!isFinite(valeur)) return ERREUR_DIV;
  const n = valeur * (format.facteur || 1);
  const texte = n.toFixed(format.decimales).replace('.', ',');
  return separerMilliers(texte) + (format.suffixe || '');
}

window.TableurEngine.transformerFormule = transformerFormule;
window.TableurEngine.decalerReferences = decalerReferences;
window.TableurEngine.translaterReferences = translaterReferences;
window.TableurEngine.afficherAvecFormat = afficherAvecFormat;
window.TableurEngine.FORMATS = FORMATS;

/* ═══════════ Opérations sur une feuille ═══════════
 *
 * Ces fonctions ne touchent PAS au DOM : elles transforment un objet
 * feuille. C'est ce qui permet de les vérifier automatiquement, alors que
 * la partie affichage ne se teste qu'à l'œil.
 */

/**
 * Insère (delta > 0) ou supprime (delta < 0) une ligne ou une colonne.
 * Déplace les cellules, les fusions et les largeurs de colonnes, ET
 * réécrit les formules pour qu'elles désignent toujours les mêmes cellules.
 */
function decalerContenuFeuille(feuille, axe, depuis, delta) {
  const nouvelles = {};
  for (const [ref, cell] of Object.entries(feuille.cells || {})) {
    const p = refVersCoord(ref);
    if (!p) continue;
    let { ligne, col } = p;
    if (axe === 'ligne') {
      if (ligne === depuis && delta < 0) continue;   // ligne supprimée
      if (ligne >= depuis) ligne += delta;
    } else {
      if (col === depuis && delta < 0) continue;     // colonne supprimée
      if (col >= depuis) col += delta;
    }
    if (ligne < 0 || col < 0) continue;
    const copie = Object.assign({}, cell);
    if (typeof copie.v === 'string' && copie.v.startsWith('=')) {
      copie.v = '=' + decalerReferences(copie.v.slice(1), axe, depuis, delta);
    }
    nouvelles[coordVersRef(ligne, col)] = copie;
  }
  feuille.cells = nouvelles;

  feuille.merges = (feuille.merges || []).map(m => {
    const m2 = Object.assign({}, m);
    if (axe === 'ligne' && m.l >= depuis) m2.l += delta;
    if (axe === 'colonne' && m.c >= depuis) m2.c += delta;
    return m2;
  }).filter(m => m.l >= 0 && m.c >= 0);

  if (axe === 'colonne') {
    const largeurs = {};
    for (const [c, w] of Object.entries(feuille.colWidths || {})) {
      const n = Number(c);
      if (n === depuis && delta < 0) continue;
      largeurs[n >= depuis ? n + delta : n] = w;
    }
    feuille.colWidths = largeurs;
  } else {
    // Les hauteurs suivent leur ligne : sans ça, une ligne haute resterait
    // en place pendant que son contenu se décale d'un cran.
    const hauteurs = {};
    for (const [l, h] of Object.entries(feuille.rowHeights || {})) {
      const n = Number(l);
      if (n === depuis && delta < 0) continue;
      hauteurs[n >= depuis ? n + delta : n] = h;
    }
    feuille.rowHeights = hauteurs;
  }

  if (axe === 'ligne') feuille.rows = Math.max(1, (feuille.rows || 1) + delta);
  else feuille.cols = Math.max(1, (feuille.cols || 1) + delta);

  return feuille;
}

/**
 * Recopie un bloc source sur toute une zone cible, en décalant les
 * références relatives des formules. La source elle-même n'est pas touchée.
 */
function recopierBloc(feuille, source, cible) {
  const hauteur = source.l2 - source.l1 + 1;
  const largeur = source.c2 - source.c1 + 1;
  const maxLigne = (feuille.rows || 1) - 1;
  const maxCol = (feuille.cols || 1) - 1;

  for (let l = cible.l1; l <= cible.l2; l++) {
    for (let c = cible.c1; c <= cible.c2; c++) {
      if (l <= source.l2 && c <= source.c2 && l >= source.l1 && c >= source.c1) continue;
      if (l > maxLigne || c > maxCol) continue;
      const lSource = source.l1 + ((l - source.l1) % hauteur);
      const cSource = source.c1 + ((c - source.c1) % largeur);
      const origine = feuille.cells[coordVersRef(lSource, cSource)];
      const ref = coordVersRef(l, c);
      if (!origine) { delete feuille.cells[ref]; continue; }

      let valeur = origine.v;
      if (typeof valeur === 'string' && valeur.startsWith('=')) {
        valeur = '=' + translaterReferences(valeur.slice(1), l - lSource, c - cSource, maxLigne, maxCol);
      }
      feuille.cells[ref] = origine.s
        ? { v: valeur, s: Object.assign({}, origine.s) }
        : { v: valeur };
    }
  }
  return feuille;
}

window.TableurEngine.decalerContenuFeuille = decalerContenuFeuille;
window.TableurEngine.recopierBloc = recopierBloc;
