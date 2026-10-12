// ─── Jeton CSRF ───────────────────────────────────────────────────────────
// Le serveur exige un jeton anti-CSRF sur toute requête qui modifie des
// données (POST/PUT/DELETE) : sans lui, elles seraient toutes refusées.
// Plutôt que de l'ajouter à la main dans la cinquantaine d'appels répartis
// dans les pages, on l'attache ici une fois pour toutes, en enveloppant
// fetch(). Les requêtes de lecture (GET) et celles vers d'autres domaines ne
// sont pas touchées.
(function attachCsrfToken() {
  function readCookie(name) {
    // Lecture par découpage plutôt que par expression régulière : plus lisible,
    // et sans risque d'échappement mal interprété.
    const parts = document.cookie ? document.cookie.split(';') : [];
    for (const part of parts) {
      const [key, ...rest] = part.trim().split('=');
      if (key === name) return decodeURIComponent(rest.join('='));
    }
    return null;
  }

  const SAFE_METHODS = ['GET', 'HEAD', 'OPTIONS', 'TRACE'];
  const originalFetch = window.fetch.bind(window);

  window.fetch = function (resource, options) {
    const opts = options || {};
    const method = (opts.method || 'GET').toUpperCase();
    const url = typeof resource === 'string' ? resource : (resource && resource.url) || '';
    // Même origine uniquement : une adresse absolue vers un autre site ne doit
    // jamais recevoir notre jeton.
    const sameOrigin = !/^https?:\/\//i.test(url) || url.startsWith(window.location.origin);

    if (!SAFE_METHODS.includes(method) && sameOrigin) {
      const token = readCookie('csrftoken');
      if (token) {
        const headers = new Headers(opts.headers || {});
        if (!headers.has('X-CSRFToken')) headers.set('X-CSRFToken', token);
        return originalFetch(resource, Object.assign({}, opts, { headers }));
      }
    }
    return originalFetch(resource, opts);
  };
})();

// Injecte la barre latérale (sidebar) commune à toutes les pages internes.
// Chaque page doit avoir un <div id="app-sidebar-slot"></div> et appeler
// initAppShell(currentProjectId) — currentProjectId peut être null.

// Charge icons.js (icônes Lucide hébergées localement, pas de CDN — l'app doit
// pouvoir fonctionner hors ligne) une seule fois, avant que la sidebar en ait besoin.
// Remplit tout élément [data-icon="nom"] avec l'icône Lucide correspondante —
// pour le HTML statique déjà présent dans la page (le HTML généré
// dynamiquement en JS appelle icon('nom') directement dans son template).
function renderIconSlots(root) {
  if (!window.icon) return;
  (root || document).querySelectorAll('[data-icon]').forEach(el => {
    if (!el.dataset.rendered) { el.innerHTML = icon(el.dataset.icon); el.dataset.rendered = '1'; }
  });
}

function loadIconsScript() {
  if (window.icon) return Promise.resolve();
  return new Promise((resolve) => {
    const s = document.createElement('script');
    s.src = '/icons.js';
    s.onload = resolve;
    s.onerror = resolve; // on continue même si ça échoue, avec un repli silencieux
    document.head.appendChild(s);
  });
}

// Charge Dexie puis offline_db.js, dans l'ordre (le second dépend du premier).
function loadScript(src) {
  return new Promise((resolve) => {
    const s = document.createElement('script');
    s.src = src;
    s.onload = resolve;
    s.onerror = resolve;
    document.head.appendChild(s);
  });
}
async function loadOfflineScripts() {
  if (window.__offlineScriptsLoaded) return;
  window.__offlineScriptsLoaded = true;
  await loadScript('/dexie.min.js');
  await loadScript('/offline_db.js');
  // Ouvre la base tout de suite ici, avec un délai de sécurité : si un autre
  // onglet de l'application reste ouvert avec une version plus ancienne du
  // code, l'ouverture peut rester bloquée indéfiniment (mécanisme normal
  // d'IndexedDB) — sans ce délai, la page resterait vide sans aucune
  // explication. Mieux vaut continuer sans le stockage hors-ligne que de
  // bloquer complètement l'affichage de la page.
  try {
    await Promise.race([
      offlineDb.open(),
      new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), 4000)),
    ]);
  } catch (e) {
    console.warn("Stockage local indisponible pour l'instant (peut-être un autre onglet de l'application resté ouvert avec une ancienne version — ferme-le puis recharge). L'application continue sans le cache hors-ligne.", e);
    window.__offlineDbUnavailable = true;
  }
}

// Enregistre le Service Worker (permet à l'app de s'ouvrir hors-ligne) — une
// seule fois, silencieusement ; un navigateur qui ne le supporte pas (rare)
// continue de fonctionner normalement, juste sans le bénéfice hors-ligne.
function registerServiceWorker() {
  if ('serviceWorker' in navigator) {
    // .update() force une vérification immédiate de sw.js (en contournant le
    // cache du navigateur pour CE fichier précis) au lieu d'attendre le
    // prochain contrôle automatique du navigateur — sur certains navigateurs
    // (Firefox notamment), ce contrôle spontané peut être espacé de plusieurs
    // heures, ce qui donnait l'impression qu'une mise à jour ne "prenait"
    // jamais alors qu'elle attendait simplement son tour.
    navigator.serviceWorker.register('/sw.js')
      .then((reg) => reg.update().catch(() => {}))
      .catch(e => console.warn('Service Worker non enregistré :', e));
  }
}

const PROJECT_COLORS = [
  'var(--project-0)', 'var(--project-1)', 'var(--project-2)',
  'var(--project-3)', 'var(--project-4)', 'var(--project-5)',
];

function projectColor(projectId) {
  if (!projectId) return PROJECT_COLORS[0];
  let hash = 0;
  for (let i = 0; i < projectId.length; i++) {
    hash = (hash * 31 + projectId.charCodeAt(i)) >>> 0;
  }
  return PROJECT_COLORS[hash % PROJECT_COLORS.length];
}

// Construit un message d'erreur lisible à partir d'une réponse API en échec.
// FastAPI renvoie tantôt une chaîne (nos propres HTTPException(detail="...")),
// tantôt un tableau d'erreurs de validation Pydantic (ex: champ requis manquant :
// [{loc:["body","patient_id"], msg:"Field required", type:"missing"}]). Sans ce
// helper, afficher `data.detail` tel quel dans un textContent produit
// littéralement la chaîne "[object Object]".
function extractApiError(data, fallback) {
  const d = data && data.detail;
  if (!d) return fallback;
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) {
    return d.map(item => {
      if (typeof item === 'string') return item;
      if (item && typeof item === 'object') {
        const loc = Array.isArray(item.loc) ? item.loc.filter(p => p !== 'body').join('.') : '';
        return loc ? `${loc} : ${item.msg || ''}` : (item.msg || JSON.stringify(item));
      }
      return String(item);
    }).join(' | ');
  }
  if (typeof d === 'object') return d.msg || JSON.stringify(d);
  return String(d);
}

// Bouton "← Précédent" injecté en haut de chaque page qui appelle
// initAppShell — utilise l'historique du navigateur (comme le bouton
// "précédent" du navigateur lui-même), mais toujours visible et facile à
// trouver au même endroit sur toutes les pages, y compris en PWA installée
// où la barre du navigateur (et son propre bouton précédent) ne s'affiche
// pas du tout. Vient EN COMPLÉMENT du fil d'Ariane déjà présent sur
// certaines pages, pas à sa place — le fil d'Ariane pointe toujours vers un
// niveau précis (le projet, le dossier...), ce bouton revient lui à
// l'écran exact d'où on vient, avec ses filtres/onglets déjà choisis.
function insertBackButton() {
  const main = document.querySelector('.app-main');
  // history.length <= 1 : cette page est la première de l'onglet (ouverte
  // directement, ex: favori/lien tapé à la main) — revenir "en arrière"
  // n'irait nulle part d'utile, mieux vaut ne pas afficher un bouton qui ne
  // ferait rien.
  if (!main || window.history.length <= 1) return;
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'back-button';
  btn.innerHTML = `${window.icon ? icon('arrow-left') : '←'} Précédent`;
  btn.addEventListener('click', () => window.history.back());
  main.insertAdjacentElement('afterbegin', btn);
}

async function initAppShell(activeProjectId) {
  await loadIconsScript();
  await loadOfflineScripts();
  registerServiceWorker();
  insertBackButton();

  let meRes;
  try {
    meRes = await fetch('/api/auth/me', { credentials: 'same-origin' });
  } catch (e) {
    // Hors-ligne : pas de redirection vers /login.html (qui échouerait aussi) —
    // on affiche juste l'indicateur "hors ligne" et on laisse la page gérer
    // elle-même ce qu'elle peut montrer depuis le cache local.
    const slot = document.getElementById('app-sidebar-slot');
    if (slot) slot.innerHTML = `<div class="brand">Clinique</div><div id="online-indicator" class="online-indicator is-offline">Hors ligne</div>`;
    return null;
  }
  if (!meRes.ok) {
    window.location.href = '/login.html';
    return null;
  }
  const user = await meRes.json();

  const projectsRes = await fetch('/api/projects', { credentials: 'same-origin' });
  const projects = projectsRes.ok ? await projectsRes.json() : [];

  const slot = document.getElementById('app-sidebar-slot');
  if (slot) {
    const ic = window.icon || (() => '');
    const projectLinks = projects.map(p => {
      const isActive = p.id === activeProjectId;
      return `
        <a class="nav-link ${isActive ? 'active' : ''}" href="/project_hub.html?project_id=${p.id}">
          <span class="project-dot" style="background:${projectColor(p.id)}"></span>
          ${p.name}
        </a>`;
    }).join('');

    // La liste des projets est repliée par défaut (surtout utile dès qu'il y
    // en a beaucoup) — on retient le choix de l'agent d'une page à l'autre
    // via localStorage, puisque chaque navigation recharge entièrement cette
    // barre latérale (pas une appli une-page, pas d'état JS qui survivrait).
    const projectsExpanded = localStorage.getItem('sidebar_projects_expanded') === '1';

    slot.innerHTML = `
      <a class="brand" href="/">${ic('stethoscope')} Clinique</a>
      <div id="online-indicator" class="online-indicator"></div>
      <nav>
        <a class="nav-link ${!activeProjectId ? 'active' : ''}" href="/">${ic('folder')} Tous les projets</a>
        <a class="nav-link" href="/patients.html">${ic('users')} Patients</a>
        <button type="button" id="projects-toggle" class="nav-section-toggle" aria-expanded="${projectsExpanded ? 'true' : 'false'}">
          <span>Projets</span>
          <span class="toggle-chevron">${ic(projectsExpanded ? 'chevron-up' : 'chevron-down')}</span>
        </button>
        <div id="projects-list" class="${projectsExpanded ? '' : 'hidden'}">
          ${projectLinks || '<div style="font-size:0.8rem;color:var(--ink-faint);padding:4px 10px;">Aucun projet</div>'}
        </div>
        ${user.role === 'admin' ? `
        <div class="nav-section-label" style="margin-top:var(--space-4);">Administration</div>
        <a class="nav-link" href="/users.html">${ic('user')} Gérer les utilisateurs</a>
        <a class="nav-link" href="/audit_log.html">${ic('clock')} Journal d'activité</a>
        ` : ''}
      </nav>
      <div class="user-box">
        <div class="user-name">${user.full_name || user.email}</div>
        <button id="shell-logout-btn" type="button">${ic('log-out')} Se déconnecter</button>
      </div>
    `;
    if (window.updateOnlineIndicator) updateOnlineIndicator();

    const projectsToggleBtn = document.getElementById('projects-toggle');
    const projectsListEl = document.getElementById('projects-list');
    projectsToggleBtn.addEventListener('click', () => {
      const willExpand = projectsListEl.classList.contains('hidden');
      projectsListEl.classList.toggle('hidden', !willExpand);
      projectsToggleBtn.setAttribute('aria-expanded', String(willExpand));
      projectsToggleBtn.querySelector('.toggle-chevron').innerHTML = ic(willExpand ? 'chevron-up' : 'chevron-down');
      localStorage.setItem('sidebar_projects_expanded', willExpand ? '1' : '0');
    });

    document.getElementById('shell-logout-btn').addEventListener('click', async () => {
      await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' });
      window.location.href = '/login.html';
    });
  }

  // Met en cache ce projet pour une consultation hors-ligne plus tard —
  // silencieux, ne bloque jamais l'affichage de la page en cours.
  if (activeProjectId && window.cacheProjectForOffline) {
    cacheProjectForOffline(activeProjectId);
  }

  return user;
}
