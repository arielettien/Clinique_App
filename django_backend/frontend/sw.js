// Service Worker — permet à l'application de s'ouvrir et de rester utilisable
// sans connexion. Deux stratégies distinctes, volontairement différentes :
//
// 1. Fichiers de l'application (HTML/CSS/JS/icônes) : "cache d'abord" — on les
//    a déjà vus, pas besoin du réseau pour les réafficher. Mis à jour en
//    arrière-plan à chaque visite en ligne, sans jamais faire attendre l'agent.
// 2. Appels à l'API (/api/...) : jamais mis en cache ici — les données
//    (patients, cas, soumissions) passent par IndexedDB (voir offline_db.js),
//    gérées explicitement par l'application, pas par un cache HTTP générique
//    qui masquerait la fraîcheur réelle des données.

// v4 : refonte du systeme de design dans style.css (contraste des boutons,
// echelle typographique, elevation, cibles tactiles). Sans ce changement de
// numero, les appareils deja visites continuent de servir l'ancienne feuille
// depuis le cache et personne ne voit la mise a jour.
const CACHE_NAME = 'clinique-app-shell-v8';
const APP_SHELL_FILES = [
  '/', '/login.html', '/style.css', '/style_blanc_bleu.css', '/app_shell.js', '/icons.js', '/dexie.min.js', '/offline_db.js',
  '/index.html', '/project_hub.html', '/survey.html', '/follow_up.html',
  '/case_detail.html', '/history.html', '/submission.html', '/form_data.html',
  '/form_builder.html', '/users.html', '/audit_log.html', '/community.html', '/patients.html',
  '/spreadsheets.html', '/tableur.html', '/tableur_engine.js',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      // addAll échouerait entièrement si UN SEUL fichier manque (ex: une page
      // pas encore créée) — on ajoute donc individuellement, en ignorant les
      // échecs isolés, pour ne jamais bloquer l'installation du reste.
      //
      // `cache: 'no-store'` au lieu de cache.add() : cache.add() passe par le
      // cache HTTP du navigateur, donc un nouveau cache de Service Worker
      // pouvait se remplir avec les ANCIENS fichiers — on changeait le numéro
      // de version pour rien, et la mise à jour n'arrivait jamais. Même piège
      // que celui déjà rencontré sur Firefox avec le lien communautaire.
      return Promise.all(
        APP_SHELL_FILES.map((url) =>
          fetch(url, { cache: 'no-store' })
            .then((reponse) => {
              if (!reponse.ok) throw new Error('HTTP ' + reponse.status);
              return cache.put(url, reponse);
            })
            .catch((e) => console.warn('SW: échec cache', url, e))
        )
      );
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(names.filter((n) => n !== CACHE_NAME).map((n) => caches.delete(n)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Jamais de mise en cache HTTP générique pour l'API — les données passent
  // par IndexedDB, gérées explicitement (voir offline_db.js).
  if (url.pathname.startsWith('/api/')) return;

  if (event.request.method !== 'GET') return;

  event.respondWith(
    // ignoreSearch : nos pages utilisent des paramètres (?project_id=...,
    // ?form_id=...) qui ne changent jamais LE FICHIER servi, seulement ce
    // qu'il affiche ensuite en JS — sans ça, une adresse avec paramètres ne
    // correspond jamais à l'entrée mise en cache (juste "/follow_up.html"),
    // et la page entière échoue à s'ouvrir hors-ligne.
    caches.match(event.request, { ignoreSearch: true }).then((cached) => {
      // cache: 'no-store' — sans ça, Firefox (contrairement à Chrome) peut
      // laisser son propre cache HTTP interne répondre à cette requête de
      // vérification avec une ancienne version du fichier, sans même
      // recontacter le serveur : la mise à jour "en arrière-plan" ne se
      // produirait alors jamais vraiment, peu importe le nombre de
      // réouvertures de la page.
      const network = fetch(event.request, { cache: 'no-store' })
        .then((response) => {
          if (response && response.status === 200) {
            const copy = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
          }
          return response;
        })
        .catch(() => cached); // hors-ligne : on retombe sur la version déjà en cache
      return cached || network;
    })
  );
});
