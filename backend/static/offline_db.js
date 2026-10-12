// Base de données locale (navigateur), pour consulter patients/cas/formulaires
// d'un projet même sans connexion. Étape 3.2 du chemin vers le hors-ligne :
// pour l'instant, LECTURE SEULE — remplir un formulaire hors-ligne et le
// synchroniser au retour du réseau, c'est l'étape suivante (3.3-3.4), pas
// encore celle-ci.

const offlineDb = new Dexie('clinique_offline');
offlineDb.version(1).stores({
  // '&id' = clé primaire unique. Champs suivis d'un index (project_id) pour
  // pouvoir filtrer rapidement par projet.
  forms: '&id, project_id',
  patients: '&id, project_id',
  cases: '&id, project_id, patient_id',
  meta: '&key', // pour mémoriser la date de dernière mise en cache par projet
});
// v2 (étape 3.3) : file d'attente des soumissions faites hors-ligne, pas
// encore envoyées au serveur. '++id' = clé auto-incrémentée (on n'a pas
// encore d'identifiant serveur pour ces soumissions).
offlineDb.version(2).stores({
  forms: '&id, project_id',
  patients: '&id, project_id',
  cases: '&id, project_id, patient_id',
  meta: '&key',
  outbox: '++id, &idempotency_key, form_id, status',
});

// Enregistre une soumission faite hors-ligne, pour envoi différé (étape 3.4).
// Ne touche jamais au serveur — juste un dépôt local, immédiat et fiable.
async function queueOfflineSubmission({ formId, patientId, caseId, responses, collectedAt, idempotencyKey }) {
  await offlineDb.outbox.add({
    idempotency_key: idempotencyKey,
    form_id: formId,
    patient_id: patientId || null,
    case_id: caseId || null,
    responses,
    collected_at: collectedAt,
    queued_at: new Date().toISOString(),
    status: 'pending', // pending -> synced (ou 'error' si le serveur la refuse a la synchro)
  });
}

async function countPendingSubmissions() {
  return offlineDb.outbox.where('status').equals('pending').count();
}

async function getPendingSubmissions() {
  return offlineDb.outbox.where('status').equals('pending').toArray();
}

// Indicateur en ligne/hors-ligne — mis à jour automatiquement, et affiché
// dans la sidebar (voir app_shell.js). navigator.onLine seul n'est pas
// toujours fiable (il peut dire "en ligne" alors que le serveur precis n'est
// pas joignable) ; un vrai ping léger vers l'API vient compléter l'info.
let isOnline = navigator.onLine;
window.addEventListener('online', () => { isOnline = true; updateOnlineIndicator(); });
window.addEventListener('offline', () => { isOnline = false; updateOnlineIndicator(); });

async function updateOnlineIndicator() {
  const el = document.getElementById('online-indicator');
  if (!el) return;
  const pending = window.countAllPending ? await countAllPending() : 0;
  const pendingText = pending > 0 ? ` · ${pending} en attente d'envoi` : '';
  el.textContent = (isOnline ? 'En ligne' : 'Hors ligne') + pendingText;
  el.className = 'online-indicator ' + (isOnline ? 'is-online' : 'is-offline') + (pending > 0 ? ' has-pending' : '');

  // Bouton de synchronisation manuelle — indispensable car le navigateur ne
  // signale pas toujours de façon fiable "je viens de me reconnecter"
  // (particulièrement visible en test avec les outils de développement, où
  // basculer Network sur "No throttling" ne déclenche pas toujours le
  // véritable événement 'online'). Sans ce bouton, l'agent n'aurait aucun
  // moyen fiable de forcer l'envoi.
  let syncBtn = document.getElementById('manual-sync-btn');
  if (pending > 0) {
    if (!syncBtn) {
      syncBtn = document.createElement('button');
      syncBtn.id = 'manual-sync-btn';
      syncBtn.className = 'btn btn-secondary btn-sm';
      syncBtn.style.cssText = 'margin-top:6px; width:100%;';
      el.insertAdjacentElement('afterend', syncBtn);
      syncBtn.addEventListener('click', async () => {
        syncBtn.disabled = true;
        syncBtn.textContent = 'Synchronisation en cours…';
        const result = await syncPendingData();
        if (result.failed > 0) {
          syncBtn.disabled = false;
          syncBtn.textContent = `${result.synced} envoyé(s), ${result.failed} échec(s) — réessayer`;
        } else if (result.synced === 0) {
          syncBtn.disabled = false;
          syncBtn.textContent = "Rien à envoyer (vérifie ta connexion)";
          setTimeout(() => updateOnlineIndicator(), 2000);
        } else {
          // Succès complet : on recharge pour que les patients/cas/visites
          // créés hors-ligne apparaissent dans les listes de CETTE page
          // (numéro de dossier définitif compris, attribué par le serveur).
          syncBtn.textContent = `${result.synced} élément(s) envoyé(s) — actualisation…`;
          setTimeout(() => window.location.reload(), 800);
        }
      });
    }
    syncBtn.textContent = 'Synchroniser maintenant';
  } else if (syncBtn) {
    syncBtn.remove();
  }
}

// Vérifie que le serveur est réellement joignable (pas juste que le Wi-Fi est
// connecté) — un aller-retour très léger, sans authentification requise.
async function checkServerReachable() {
  try {
    const res = await fetch('/health', { cache: 'no-store', signal: AbortSignal.timeout(4000) });
    return res.ok;
  } catch {
    return false;
  }
}

// Met en cache localement tout ce qu'il faut pour consulter un projet
// hors-ligne : ses formulaires, ses patients, ses cas de suivi. Appelée
// automatiquement par survey.html/follow_up.html quand la page se charge
// AVEC une connexion — silencieuse, ne bloque jamais l'affichage normal.
async function cacheProjectForOffline(projectId) {
  if (!isOnline) return; // pas de sens de "rafraîchir le cache" hors-ligne
  try {
    const [projectRes, formsRes, patientsRes, casesRes] = await Promise.all([
      fetch(`/api/projects/${projectId}`, { credentials: 'same-origin' }),
      fetch(`/api/forms?project_id=${projectId}`, { credentials: 'same-origin' }),
      fetch(`/api/patients?project_id=${projectId}`, { credentials: 'same-origin' }),
      fetch(`/api/projects/${projectId}/cases?status=active`, { credentials: 'same-origin' }),
    ]);
    if (!formsRes.ok || !patientsRes.ok) return;

    const formSummaries = await formsRes.json();
    const patients = await patientsRes.json();
    const cases = casesRes.ok ? await casesRes.json() : [];
    const project = projectRes.ok ? await projectRes.json() : null;

    // La liste ne contient que {id, name, form_type, project_id} — jamais la
    // définition (sections/champs). Sans elle, un formulaire mis en cache ne
    // pourrait pas s'afficher hors-ligne. On récupère donc le détail complet
    // de chaque formulaire séparément.
    const forms = await Promise.all(formSummaries.map(async (f) => {
      try {
        const detailRes = await fetch(`/api/forms/${f.id}`, { credentials: 'same-origin' });
        if (detailRes.ok) return await detailRes.json();
      } catch (e) { /* on garde au moins le resume ci-dessous */ }
      return f;
    }));

    // Idem pour l'historique (timeline) de chaque cas actif — sans lui,
    // case_detail.html n'aurait que le nom/statut du dossier hors-ligne,
    // jamais ses visites déjà enregistrées.
    const timelines = await Promise.all(cases.map(async (c) => {
      try {
        const tlRes = await fetch(`/api/cases/${c.id}/timeline`, { credentials: 'same-origin' });
        if (tlRes.ok) { const tl = await tlRes.json(); return { case_id: c.id, events: tl.events }; }
      } catch (e) { /* pas grave, on retentera a la prochaine mise en cache */ }
      return null;
    }));

    await offlineDb.transaction('rw', offlineDb.forms, offlineDb.patients, offlineDb.cases, offlineDb.meta, offlineDb.case_timelines, async () => {
      // On ne vide/reconstruit QUE les entrées de CE projet (pas toute la
      // base), pour ne jamais effacer le cache d'un autre projet déjà visité.
      await offlineDb.forms.where('project_id').equals(projectId).delete();
      await offlineDb.patients.where('project_id').equals(projectId).delete();
      await offlineDb.cases.where('project_id').equals(projectId).delete();

      await offlineDb.forms.bulkPut(forms.map(f => ({ ...f, project_id: projectId })));
      await offlineDb.patients.bulkPut(patients);
      await offlineDb.cases.bulkPut(cases.map(c => ({ ...c, project_id: projectId })));
      await offlineDb.case_timelines.bulkPut(timelines.filter(Boolean));

      await offlineDb.meta.put({ key: `last_cached_${projectId}`, value: new Date().toISOString() });
      if (project) {
        await offlineDb.meta.put({ key: `project_info_${projectId}`, value: { name: project.name, description: project.description } });
      }
    });
    console.log(`Hors-ligne : ${forms.length} formulaire(s), ${patients.length} patient(s), ${cases.length} cas mis en cache pour ce projet.`);
  } catch (e) {
    console.warn('Échec de la mise en cache hors-ligne (pas grave, juste pas à jour) :', e);
  }
}

// Nom/description du projet, pour afficher un titre correct même hors-ligne
// (plutôt qu'un ID illisible ou un écran d'erreur bloquant).
async function getCachedProjectInfo(projectId) {
  const entry = await offlineDb.meta.get(`project_info_${projectId}`);
  return entry ? entry.value : null;
}

// v3 (étape 3.4/3.5 fusionnées) : patients et cas créés ENTIÈREMENT hors-ligne
// (le scénario le plus important — dépistage en communauté, sans réseau, où
// le patient n'existe pas encore dans le système). L'identifiant est généré
// ici même dans le navigateur et devient directement le bon identifiant
// définitif une fois synchronisé — jamais un identifiant temporaire à
// réconcilier après coup.
// v4 : actions "hors création" faites hors-ligne — pour l'instant, seulement
// la clôture d'un dossier (déclenchée par une fenêtre modale, pas par le
// formulaire classique — un circuit à part qui n'était pas encore protégé).
// v5 : historique complet (timeline) de chaque cas actif — nécessaire pour
// que case_detail.html reste consultable hors-ligne, pas seulement les
// listes. Stocké à part car son contenu est plus volumineux et se met à
// jour différemment (une entrée par cas, pas par élément individuel).
// v6 : fichiers (photo/audio/vidéo/document) attachés à un formulaire rempli
// hors-ligne. IndexedDB stocke le fichier lui-même (Blob), pas juste une
// référence — sans réseau, il n'y a personne à qui l'envoyer tout de suite.
// L'identifiant local ('local:...') est substitué par le vrai identifiant
// serveur au moment de la synchronisation (voir syncPendingData).
offlineDb.version(6).stores({
  forms: '&id, project_id',
  patients: '&id, project_id',
  cases: '&id, project_id, patient_id',
  meta: '&key',
  outbox: '++id, &idempotency_key, form_id, status',
  pending_patients: '&id, project_id, status',
  pending_cases: '&id, project_id, patient_id, status',
  pending_actions: '++id, case_id, status',
  case_timelines: '&case_id',
  pending_media: '&id, status',
});

// v7 : lien communautaire (point 7) — file d'attente ENTIÈREMENT séparée de
// celle ci-dessus (aucune table existante modifiée), utilisée uniquement par
// community.html. Chaque soumission en attente garde son propre code d'accès
// (au cas où il change entre la saisie hors-ligne et la synchronisation).
offlineDb.version(7).stores({
  community_forms: '&form_id',
  community_pending_patients: '&id, form_id, status',
  community_outbox: '++id, &idempotency_key, form_id, status',
  community_pending_media: '&id, status',
});

// Stocke un fichier hors-ligne et renvoie son identifiant LOCAL (à utiliser
// tout de suite comme valeur de champ, exactement comme un identifiant
// serveur — la substitution par le vrai identifiant se fait plus tard, de
// façon invisible pour le formulaire).
async function storeMediaOffline(blob, fieldType, originalFilename, contentType) {
  const id = 'local:' + generateOfflineId();
  await offlineDb.pending_media.put({ id, blob, field_type: fieldType, original_filename: originalFilename, content_type: contentType, status: 'pending' });
  return id;
}

// Reconstruit une URL affichable (aperçu image/audio/vidéo) à partir d'un
// fichier stocké localement — utilisé quand la page est rechargée alors
// qu'un média hors-ligne était déjà attaché (l'URL mémoire d'origine ne
// survit pas à un rechargement).
async function getOfflineMediaObjectUrl(localId) {
  const entry = await offlineDb.pending_media.get(localId);
  return entry ? URL.createObjectURL(entry.blob) : null;
}

async function getCachedCaseTimeline(caseId) {
  const entry = await offlineDb.case_timelines.get(caseId);
  return entry ? entry.events : null;
}

// Clôture un dossier hors-ligne : mémorise l'action à rejouer à la
// synchronisation, ET met à jour tout de suite le cas en cache local pour
// que l'écran reflète correctement le changement sans attendre le retour du
// réseau (l'agent voit son dossier passer "Clôturé" immédiatement).
async function queueCloseCaseOffline({ caseId, reason, status, closedAt }) {
  await offlineDb.pending_actions.add({
    type: 'close_case', case_id: caseId, reason, new_status: status, // 'closed' ou 'lost' -- statut cible du cas
    closed_at: closedAt || new Date().toISOString(),
    queued_at: new Date().toISOString(), status: 'pending', // statut de SYNCHRONISATION (pending/synced/error)
  });
  const existing = await offlineDb.cases.get(caseId);
  if (existing) {
    await offlineDb.cases.put({
      ...existing, status, closed_reason: reason,
      closed_at: closedAt || new Date().toISOString(), updated_at: new Date().toISOString(),
    });
  }
}

// Rouvrir/Modifier/Supprimer un dossier hors-ligne — même principe que la
// clôture ci-dessus : on mémorise l'action à rejouer à la synchronisation,
// ET on reflète tout de suite le changement dans le cache local pour que
// l'écran ne reste jamais bloqué en attendant le réseau.
async function queueReopenCaseOffline({ caseId }) {
  await offlineDb.pending_actions.add({
    type: 'reopen_case', case_id: caseId,
    queued_at: new Date().toISOString(), status: 'pending',
  });
  const existing = await offlineDb.cases.get(caseId);
  if (existing) {
    await offlineDb.cases.put({
      ...existing, status: 'active', closed_reason: null, closed_at: null,
      updated_at: new Date().toISOString(),
    });
  }
}

async function queueUpdateCaseOffline({ caseId, caseNumber, enrolledAt }) {
  await offlineDb.pending_actions.add({
    type: 'update_case', case_id: caseId, case_number: caseNumber || null, enrolled_at: enrolledAt || null,
    queued_at: new Date().toISOString(), status: 'pending',
  });
  const existing = await offlineDb.cases.get(caseId);
  if (existing) {
    await offlineDb.cases.put({
      ...existing,
      case_number: caseNumber || existing.case_number,
      enrolled_at: enrolledAt || existing.enrolled_at,
      updated_at: new Date().toISOString(),
    });
  }
}

// Suppression : retirée du cache local tout de suite (elle ne doit plus
// apparaître nulle part), l'action réelle sera rejouée côté serveur à la
// synchronisation. Risque assumé, comme pour la création hors-ligne : si le
// dossier a reçu une visite entre-temps ailleurs, la suppression échouera à
// la synchronisation et sera signalée en erreur plutôt que silencieusement.
async function queueDeleteCaseOffline({ caseId }) {
  await offlineDb.pending_actions.add({
    type: 'delete_case', case_id: caseId,
    queued_at: new Date().toISOString(), status: 'pending',
  });
  await offlineDb.cases.delete(caseId);
  await offlineDb.case_timelines.delete(caseId);
}

// Un autre onglet resté ouvert (avec une version plus ancienne du code) peut
// bloquer indéfiniment l'ouverture de la base ici — sans ce garde-fou, la
// page reste bloquée sur "chargement" sans aucune explication. On force la
// fermeture de la connexion d'un autre onglet dès qu'une version plus
// récente veut s'ouvrir (protège les MISES À JOUR FUTURES), et on affiche un
// message clair dans les cas où l'ouverture reste bloquée malgré tout
// (ancien onglet avec un code trop ancien pour connaître ce mécanisme).
offlineDb.on('versionchange', () => { offlineDb.close(); });
offlineDb.on('blocked', () => {
  console.warn("Stockage local bloqué par un autre onglet de l'application resté ouvert — ferme les autres onglets puis recharge cette page.");
});

function generateOfflineId() {
  return (crypto.randomUUID ? crypto.randomUUID() : 'off-' + Date.now() + '-' + Math.random().toString(36).slice(2));
}

// Crée un patient ENTIÈREMENT hors-ligne : génère son identifiant définitif
// tout de suite, l'ajoute au cache de consultation (pour qu'il apparaisse
// immédiatement dans les recherches/listes) ET à la file d'attente de
// synchronisation.
async function createPatientOffline({ projectId, firstName, lastName, dateOfBirth, sex, phone }) {
  const id = generateOfflineId();
  const patient = {
    id, project_id: projectId, first_name: firstName, last_name: lastName,
    date_of_birth: dateOfBirth || null, sex: sex || null, phone: phone || null,
    created_at: new Date().toISOString(),
  };
  await offlineDb.patients.put(patient);
  await offlineDb.pending_patients.put({ ...patient, status: 'pending' });
  return patient;
}

// Crée un cas de suivi ENTIÈREMENT hors-ligne, rattaché à un patient qui peut
// lui-même être tout juste créé hors-ligne (même principe d'identifiant
// définitif immédiat).
async function createCaseOffline({ projectId, patientId, enrolledAt }) {
  const id = generateOfflineId();
  const patient = await offlineDb.patients.get(patientId);
  const enrolled = enrolledAt || new Date().toISOString();
  const caseObj = {
    id, project_id: projectId, patient_id: patientId,
    patient_name: patient ? `${patient.first_name} ${patient.last_name}` : '',
    patient_sex: patient ? patient.sex : null,
    patient_date_of_birth: patient ? patient.date_of_birth : null,
    case_number: null, // attribué par le serveur à la synchronisation (garde la numérotation cohérente et sans collision)
    status: 'active', enrolled_at: enrolled, closed_at: null, closed_reason: null,
    last_visit_at: null, visit_count: 0, next_visit_at: null, is_overdue: false,
    updated_at: new Date().toISOString(),
  };
  await offlineDb.cases.put(caseObj);
  await offlineDb.pending_cases.put({ id, project_id: projectId, patient_id: patientId, enrolled_at: enrolled, status: 'pending' });
  return caseObj;
}

async function countAllPending() {
  const [p, c, o, a] = await Promise.all([
    offlineDb.pending_patients.where('status').equals('pending').count(),
    offlineDb.pending_cases.where('status').equals('pending').count(),
    offlineDb.outbox.where('status').equals('pending').count(),
    offlineDb.pending_actions.where('status').equals('pending').count(),
  ]);
  return p + c + o + a;
}

// Envoie tout ce qui est en attente au serveur, DANS L'ORDRE DES DÉPENDANCES :
// patients d'abord (les cas en ont besoin), puis cas (les soumissions en ont
// besoin), puis soumissions. Chaque élément garde son identifiant déjà
// généré hors-ligne — le serveur les accepte tels quels (voir main.py) et ne
// recrée jamais de doublon si un élément a déjà été synchronisé (idempotent).
async function syncPendingData() {
  if (!isOnline) return { synced: 0, failed: 0 };
  let synced = 0, failed = 0;
  const errors = [];

  // Envoie un fichier média stocké localement et renvoie l'identifiant
  // définitif attribué par le serveur — appelé juste avant l'envoi de
  // chaque soumission qui le référence (voir plus bas). Idempotent : si ce
  // fichier a déjà été envoyé lors d'une synchronisation précédente (mais
  // interrompue avant d'avoir pu envoyer la soumission elle-même), on
  // réutilise l'identifiant déjà obtenu plutôt que d'envoyer une deuxième
  // fois le même fichier.
  async function syncOneMediaFile(localId) {
    const entry = await offlineDb.pending_media.get(localId);
    if (!entry) return null;
    if (entry.status === 'synced' && entry.server_id) return entry.server_id;
    try {
      const formData = new FormData();
      formData.append('file', entry.blob, entry.original_filename || 'fichier');
      formData.append('field_type', entry.field_type);
      const res = await fetch('/api/media/upload', { method: 'POST', body: formData, credentials: 'same-origin' });
      if (!res.ok) return null;
      const data = await res.json();
      await offlineDb.pending_media.update(localId, { status: 'synced', server_id: data.id });
      return data.id;
    } catch (e) {
      return null;
    }
  }

  const pendingPatients = await offlineDb.pending_patients.where('status').equals('pending').toArray();
  for (const p of pendingPatients) {
    try {
      const res = await fetch('/api/patients', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin',
        body: JSON.stringify({
          id: p.id, project_id: p.project_id, first_name: p.first_name, last_name: p.last_name,
          date_of_birth: p.date_of_birth, sex: p.sex, phone: p.phone,
        }),
      });
      if (res.ok) {
        await offlineDb.pending_patients.update(p.id, { status: 'synced' });
        synced++;
      } else {
        const data = await res.json().catch(() => ({}));
        await offlineDb.pending_patients.update(p.id, { status: 'error', error: JSON.stringify(data) });
        failed++;
        errors.push(`Patient ${p.first_name} ${p.last_name} : ${data.detail || res.status}`);
      }
    } catch (e) { failed++; break; } // reseau reparti hors ligne en cours de route -> on s'arrete, on reessaiera plus tard
  }

  const pendingCases = await offlineDb.pending_cases.where('status').equals('pending').toArray();
  for (const c of pendingCases) {
    try {
      const res = await fetch('/api/cases', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin',
        body: JSON.stringify({ id: c.id, project_id: c.project_id, patient_id: c.patient_id, enrolled_at: c.enrolled_at }),
      });
      if (res.ok) {
        await offlineDb.pending_cases.update(c.id, { status: 'synced' });
        synced++;
      } else {
        const data = await res.json().catch(() => ({}));
        await offlineDb.pending_cases.update(c.id, { status: 'error', error: JSON.stringify(data) });
        failed++;
        errors.push(`Dossier de suivi : ${data.detail || res.status}`);
      }
    } catch (e) { failed++; break; }
  }

  const pendingActions = await offlineDb.pending_actions.where('status').equals('pending').toArray();
  for (const a of pendingActions) {
    try {
      let res;
      if (a.type === 'close_case') {
        res = await fetch(`/api/cases/${a.case_id}/close`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin',
          body: JSON.stringify({ reason: a.reason, status: a.new_status, closed_at: a.closed_at }),
        });
      } else if (a.type === 'reopen_case') {
        res = await fetch(`/api/cases/${a.case_id}/reopen`, { method: 'POST', credentials: 'same-origin' });
      } else if (a.type === 'update_case') {
        const payload = {};
        if (a.case_number) payload.case_number = a.case_number;
        if (a.enrolled_at) payload.enrolled_at = a.enrolled_at;
        // Pas de expected_updated_at ici : comme pour la création hors-ligne,
        // on privilégie "la dernière synchro gagne" plutôt qu'un conflit
        // qu'on ne pourrait de toute façon pas résoudre sans réseau.
        res = await fetch(`/api/cases/${a.case_id}`, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin',
          body: JSON.stringify(payload),
        });
      } else if (a.type === 'delete_case') {
        res = await fetch(`/api/cases/${a.case_id}`, { method: 'DELETE', credentials: 'same-origin' });
      }
      if (res && res.ok) {
        await offlineDb.pending_actions.update(a.id, { status: 'synced' });
        synced++;
      } else if (res) {
        const data = await res.json().catch(() => ({}));
        await offlineDb.pending_actions.update(a.id, { status: 'error', error: JSON.stringify(data) });
        failed++;
        const label = { close_case: 'Clôture de dossier', reopen_case: 'Réouverture de dossier', update_case: 'Modification de dossier', delete_case: 'Suppression de dossier' }[a.type] || 'Action sur un dossier';
        errors.push(`${label} : ${extractApiError(data, res.status)}`);
      }
    } catch (e) { failed++; break; }
  }

  const pendingSubmissions = await offlineDb.outbox.where('status').equals('pending').toArray();
  for (const s of pendingSubmissions) {
    try {
      // Les champs média rempli hors-ligne contiennent un identifiant local
      // ('local:...') — on envoie d'abord le vrai fichier au serveur pour
      // obtenir son identifiant définitif, puis on substitue avant d'envoyer
      // la soumission elle-même (le serveur ne connaît pas les id locaux).
      const resolvedResponses = { ...s.responses };
      let mediaFailed = false;
      for (const [key, value] of Object.entries(resolvedResponses)) {
        if (typeof value === 'string' && value.startsWith('local:')) {
          const realId = await syncOneMediaFile(value);
          if (realId) {
            resolvedResponses[key] = realId;
          } else {
            mediaFailed = true;
            break;
          }
        }
      }
      if (mediaFailed) { failed++; break; } // reseau reparti hors ligne en cours de route

      const res = await fetch('/api/submissions', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin',
        body: JSON.stringify({
          form_id: s.form_id, patient_id: s.patient_id, case_id: s.case_id,
          responses: resolvedResponses, collected_at: s.collected_at, idempotency_key: s.idempotency_key,
        }),
      });
      if (res.ok) {
        await offlineDb.outbox.update(s.id, { status: 'synced' });
        synced++;
      } else {
        const data = await res.json().catch(() => ({}));
        await offlineDb.outbox.update(s.id, { status: 'error', error: JSON.stringify(data) });
        failed++;
        errors.push(`Soumission : ${data.detail || res.status}`);
      }
    } catch (e) { failed++; break; }
  }

  if (window.updateOnlineIndicator) updateOnlineIndicator();
  return { synced, failed, errors };
}

// Synchronisation automatique dès que la connexion revient, sans que l'agent
// ait besoin d'y penser — le comportement attendu sur le terrain.
window.addEventListener('online', () => {
  setTimeout(() => { if (window.syncPendingData) syncPendingData(); }, 1500);
});

// Lecture depuis le cache local — utilisée par les pages quand un appel
// réseau échoue (voir ex. dans follow_up.html).
async function getCachedPatients(projectId) {
  return offlineDb.patients.where('project_id').equals(projectId).toArray();
}
async function getCachedCases(projectId) {
  return offlineDb.cases.where('project_id').equals(projectId).toArray();
}
async function getCachedForms(projectId) {
  return offlineDb.forms.where('project_id').equals(projectId).toArray();
}
async function getLastCachedAt(projectId) {
  const entry = await offlineDb.meta.get(`last_cached_${projectId}`);
  return entry ? entry.value : null;
}

// Recherches par identifiant SANS connaître encore le projet — utilisées
// quand on arrive directement sur une page avec juste un patient_id/case_id
// dans l'adresse (ex: submission.html), avant même de savoir à quel projet
// ça appartient.
async function getCachedPatientById(patientId) {
  return offlineDb.patients.get(patientId);
}
async function getCachedCaseById(caseId) {
  return offlineDb.cases.get(caseId);
}

// ════════════════════════════════════════════════════════════════════════
// Lien communautaire (point 7) — file d'attente séparée, utilisée uniquement
// par community.html. Même principe général que ci-dessus (identifiant
// définitif généré tout de suite, média stocké en Blob local, résolu à la
// synchronisation) mais avec un code d'accès à présenter à chaque appel
// serveur (pas de cookie de session ici).
// ════════════════════════════════════════════════════════════════════════

// Met en cache la définition du formulaire, pour pouvoir l'afficher hors
// ligne si ce lien a déjà été ouvert au moins une fois en ligne sur cet
// appareil (comme cacheProjectForOffline, mais pour un seul formulaire).
// Délai avant d'abandonner un appel réseau du lien communautaire (upload de
// média compris, donc pas trop court). Sans ça, un appel resterait "en
// attente" très longtemps quand le serveur est simplement injoignable —
// notamment quand le téléphone est passé sur ses données mobiles au lieu du
// Wi-Fi local où vit le serveur (l'adresse IP locale n'a alors aucune route
// possible, mais rien ne le signale rapidement sans ce délai explicite).
const COMMUNITY_FETCH_TIMEOUT_MS = 8000;

// Message clair et identique pour toute panne réseau côté lien communautaire
// (délai dépassé ou coupure pure et simple) — évite de laisser deviner à
// l'agent de terrain pourquoi "ça ne marche pas".
function communityNetworkErrorMessage() {
  return "Connexion au serveur introuvable — vérifie que tu es sur le même réseau Wi-Fi que l'ordinateur.";
}

async function cacheCommunityForm(form) {
  await offlineDb.community_forms.put({
    form_id: form.id, name: form.name, project_id: form.project_id,
    definition: form.definition, cached_at: new Date().toISOString(),
  });
}
async function getCachedCommunityForm(formId) {
  return offlineDb.community_forms.get(formId);
}

async function queueCommunityPatientOffline({ formId, firstName, lastName, dateOfBirth, sex, phone }) {
  const id = generateOfflineId();
  await offlineDb.community_pending_patients.put({
    id, form_id: formId, first_name: firstName, last_name: lastName,
    date_of_birth: dateOfBirth || null, sex: sex || null, phone: phone || null,
    status: 'pending', queued_at: new Date().toISOString(),
  });
  return id; // devient tout de suite le patient_id définitif, comme ailleurs dans l'appli
}

async function storeCommunityMediaOffline(blob, fieldType, originalFilename, contentType) {
  const localId = 'local:' + generateOfflineId();
  await offlineDb.community_pending_media.put({
    id: localId, blob, field_type: fieldType, original_filename: originalFilename,
    content_type: contentType, status: 'pending',
  });
  return localId;
}
async function getCommunityOfflineMediaObjectUrl(localId) {
  const entry = await offlineDb.community_pending_media.get(localId);
  return entry ? URL.createObjectURL(entry.blob) : null;
}

async function queueCommunitySubmissionOffline({ formId, patientId, responses, collectedAt, idempotencyKey }) {
  await offlineDb.community_outbox.add({
    form_id: formId, patient_id: patientId, responses, collected_at: collectedAt,
    idempotency_key: idempotencyKey, status: 'pending', queued_at: new Date().toISOString(),
  });
}

async function countCommunityPending(formId) {
  const [p, s] = await Promise.all([
    offlineDb.community_pending_patients.where({ form_id: formId, status: 'pending' }).count(),
    offlineDb.community_outbox.where({ form_id: formId, status: 'pending' }).count(),
  ]);
  return p + s;
}

// Envoie tout ce qui est en attente pour CE formulaire, patients d'abord
// (les soumissions en ont besoin), avec le code d'accès fourni à chaque
// appel — si le code a été régénéré depuis (donc changé) pendant que
// l'appareil était hors-ligne, chaque appel échouera avec une erreur claire
// plutôt que d'être accepté à tort.
async function syncCommunityQueue(formId, code) {
  if (!isOnline) return { synced: 0, failed: 0, errors: [] };
  let synced = 0, failed = 0;
  const errors = [];
  const authHeaders = { 'X-Community-Code': code };

  async function syncOneCommunityMedia(localId) {
    const entry = await offlineDb.community_pending_media.get(localId);
    if (!entry) return null;
    if (entry.status === 'synced' && entry.server_id) return entry.server_id;
    try {
      const formData = new FormData();
      formData.append('file', entry.blob, entry.original_filename || 'fichier');
      formData.append('field_type', entry.field_type);
      const res = await fetch(`/api/public/forms/${formId}/media/upload`, {
        method: 'POST', headers: authHeaders, body: formData,
        signal: AbortSignal.timeout(COMMUNITY_FETCH_TIMEOUT_MS),
      });
      if (!res.ok) return null;
      const data = await res.json();
      await offlineDb.community_pending_media.update(localId, { status: 'synced', server_id: data.id });
      return data.id;
    } catch (e) {
      errors.push(`Fichier : ${communityNetworkErrorMessage()}`);
      return null;
    }
  }

  const pendingPatients = await offlineDb.community_pending_patients.where({ form_id: formId, status: 'pending' }).toArray();
  for (const p of pendingPatients) {
    try {
      const res = await fetch(`/api/public/forms/${formId}/patients`, {
        method: 'POST',
        headers: { ...authHeaders, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: p.id, first_name: p.first_name, last_name: p.last_name,
          date_of_birth: p.date_of_birth, sex: p.sex, phone: p.phone,
        }),
        signal: AbortSignal.timeout(COMMUNITY_FETCH_TIMEOUT_MS),
      });
      if (res.ok) {
        await offlineDb.community_pending_patients.update(p.id, { status: 'synced' });
        synced++;
      } else {
        const data = await res.json().catch(() => ({}));
        await offlineDb.community_pending_patients.update(p.id, { status: 'error', error: JSON.stringify(data) });
        failed++;
        errors.push(`Patient ${p.first_name} ${p.last_name} : ${(data && data.detail) || res.status}`);
      }
    } catch (e) {
      // Délai dépassé ou réseau reparti hors ligne en cours de route : on
      // arrête tout de suite (les tentatives suivantes échoueraient pareil)
      // avec un message clair plutôt qu'un blocage silencieux prolongé.
      failed++;
      errors.push(`Patient ${p.first_name} ${p.last_name} : ${communityNetworkErrorMessage()}`);
      break;
    }
  }

  const pendingSubs = await offlineDb.community_outbox.where({ form_id: formId, status: 'pending' }).toArray();
  for (const s of pendingSubs) {
    try {
      const resolvedResponses = { ...s.responses };
      let mediaFailed = false;
      for (const [key, value] of Object.entries(resolvedResponses)) {
        if (typeof value === 'string' && value.startsWith('local:')) {
          const realId = await syncOneCommunityMedia(value);
          if (realId) resolvedResponses[key] = realId;
          else { mediaFailed = true; break; }
        }
      }
      if (mediaFailed) { failed++; break; }

      const res = await fetch(`/api/public/forms/${formId}/submissions`, {
        method: 'POST',
        headers: { ...authHeaders, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          patient_id: s.patient_id, responses: resolvedResponses,
          collected_at: s.collected_at, idempotency_key: s.idempotency_key,
        }),
        signal: AbortSignal.timeout(COMMUNITY_FETCH_TIMEOUT_MS),
      });
      if (res.ok) {
        await offlineDb.community_outbox.update(s.id, { status: 'synced' });
        synced++;
      } else {
        const data = await res.json().catch(() => ({}));
        await offlineDb.community_outbox.update(s.id, { status: 'error', error: JSON.stringify(data) });
        failed++;
        errors.push(`Soumission : ${(data && data.detail) || res.status}`);
      }
    } catch (e) {
      failed++;
      errors.push(`Soumission : ${communityNetworkErrorMessage()}`);
      break;
    }
  }

  return { synced, failed, errors };
}

