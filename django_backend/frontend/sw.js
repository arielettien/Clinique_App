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
// v9 : tableau de bord des indicateurs (nouvelles pages, feuille de style et
// polices Barlow hebergees localement).
// v11 : police Inter desormais hebergee localement (avant, chaque page la
// chargeait depuis Google Fonts, injoignable sur le serveur de l'ONG qui n'a
// pas d'acces internet).
// v12 : page de connexion en deux panneaux + son illustration (img/).
// v13 : le serveur redirige desormais vers /login.html quand la session
// manque. Deux consequences ici : on ne met jamais en cache une reponse
// issue d'une redirection, et l'ouverture d'une page passe par le reseau
// d'abord (voir les commentaires plus bas).
const CACHE_NAME = 'clinique-app-shell-v13';
const APP_SHELL_FILES = [
  '/', '/login.html', '/style.css', '/style_blanc_bleu.css', '/app_shell.js', '/icons.js', '/dexie.min.js', '/offline_db.js',
  '/index.html', '/project_hub.html', '/survey.html', '/follow_up.html',
  '/case_detail.html', '/history.html', '/submission.html', '/form_data.html',
  '/form_builder.html', '/users.html', '/audit_log.html', '/community.html', '/patients.html',
  '/spreadsheets.html', '/tableur.html', '/tableur_engine.js', '/importer_etude.html',
  '/tableau_de_bord.html', '/tableau_de_bord.css', '/indicateurs.html',
  '/fonts/barlow-latin-400-normal.woff2', '/fonts/barlow-latin-500-normal.woff2',
  '/fonts/barlow-latin-600-normal.woff2', '/fonts/barlow-condensed-latin-500-normal.woff2',
  '/fonts/barlow-condensed-latin-600-normal.woff2',
  '/fonts/inter-latin-400-normal.woff2', '/fonts/inter-latin-500-normal.woff2',
  '/fonts/inter-latin-600-normal.woff2', '/fonts/inter-latin-700-normal.woff2',
  '/img/connexion.svg',
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
              // Reponse obtenue APRES une redirection : le serveur a renvoye
              // la page de connexion a la place de celle demandee, faute de
              // session. La mettre en cache enregistrerait la page de
              // connexion SOUS l'adresse de la vraie page, qui serait ensuite
              // servie telle quelle une fois l'agent connecte.
              if (reponse.redirected) throw new Error('redirige vers ' + reponse.url);
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

  // Ouverture d'une page (navigation) : le RESEAU d'abord, le cache en repli.
  // Seul le serveur sait si la session est encore valide. En cache-first, une
  // personne deconnectee se voyait reservir l'accueil depuis le cache, puis
  // renvoyer vers la connexion par le JavaScript — precisement le
  // clignotement que la redirection serveur supprime. Le repli sur le cache
  // des que le reseau ne repond pas preserve entierement le mode hors-ligne.
  //
  // Une requete de navigation a un mode de redirection "manual" : fetch rend
  // alors la redirection telle quelle (reponse opaque, status 0) et c'est le
  // navigateur qui la suit. On ne la met donc jamais en cache.
  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request)
        .then((reponse) => {
          if (reponse && reponse.status === 200 && !reponse.redirected) {
            const copie = reponse.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copie));
          }
          return reponse;
        })
        .catch(() =>
          caches.match(event.request, { ignoreSearch: true })
            .then((cached) => cached || Response.error())
        )
    );
    return;
  }

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
          if (response && response.status === 200 && !response.redirected) {
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
