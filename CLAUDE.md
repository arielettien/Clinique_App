# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

A clinical data-collection app (French-language, "Clinique App") for tracking patients, forms, and
longitudinal follow-up ("cases") within a clinic. Backend is Django + Django REST Framework; frontend is
static HTML/CSS/vanilla JS (no build step, no framework, no bundler) with an offline-first PWA layer.
Comments and docstrings throughout the codebase are written in French and are often deliberately verbose to
explain *why* a piece of logic exists (a past bug it fixes, a constraint it works around) — read them before
changing surrounding code, and keep that documentation style when you add non-obvious logic.

Deployment target: a single local server inside one NGO's office, reachable only over the LAN, used by 4–5
staff accounts. There is no internet exposure. See `DEPLOIEMENT.md` for the full install procedure.

## Running the app

Everything runs via Docker Compose; there is no local (non-Docker) dev setup documented or expected.

```
docker compose up --build      # start db + api + backup services, rebuild image
docker compose down            # stop (keeps the postgres_data volume — no data loss)
```

The `api` service mounts `./django_backend:/app` and runs `django_backend/docker-entrypoint.sh`, which
applies migrations then starts gunicorn. `APP_RELOAD=true` (the default) adds gunicorn's `--reload`, so
editing a backend file takes effect without rebuilding; `--build` is only needed after changing
`requirements.txt` or the `Dockerfile`. The app is served at `http://localhost:8000` (Django serves the
static frontend directly from `django_backend/frontend/`).

Requires a `.env` file (copy from `.env.example`) with `SECRET_KEY`, `POSTGRES_PASSWORD` and
`BACKUP_ENCRYPTION_PASSPHRASE` — the app refuses to start with the placeholder default `SECRET_KEY` (see
`django_backend/clinique/settings.py`). `COOKIE_SECURE` must stay `false` for local HTTP; only set `true`
once served behind HTTPS.

```
docker compose exec api python manage.py create_admin          # first admin account
docker compose exec api python manage.py generate_cert <ip>    # self-signed HTTPS cert
docker compose exec api python manage.py test                  # run the test suite
```

### HTTPS for offline access over LAN

The offline-first PWA layer (`sw.js`) requires a "secure context" (`https://` or exactly `localhost`) to
register the Service Worker — a plain `http://<lan-ip>:8000` (how phones on-site reach the server) never
qualifies, so offline mode silently never activates for phone users who aren't on `localhost`.
`docker-entrypoint.sh` checks for `django_backend/certs/cert.pem`/`key.pem` at every container start: if
present, gunicorn starts with TLS on the same port 8000; if absent, it falls back to plain HTTP, so a fresh
clone is never blocked waiting on a certificate. The cert is self-signed, so every browser shows a
"connection not private" warning on first visit — expected, accept once per device. Once HTTPS is confirmed
working, set `COOKIE_SECURE=true` in `.env` and restart.

## Database migrations

Standard Django migrations (`python manage.py makemigrations` / `migrate`). The entrypoint runs
`migrate --fake-initial` on every start: on a database inherited from the previous FastAPI/SQLAlchemy
version the tables already exist, so the initial migration is recorded as applied rather than re-run; on a
fresh database everything applies normally.

Two migrations exist purely to adopt such an inherited database, and are no-ops on a fresh one:

- `accounts/migrations/0002` — adds the missing `last_login` column, and tags existing raw bcrypt password
  hashes with Django's `bcrypt$` algorithm prefix so existing accounts keep working without a reset.
- `clinical_forms/migrations/0002` — same tagging for `public_access_code_hash`, so community links already
  handed out to field agents stay valid.

## Architecture

### Backend (`django_backend/`)

Apps map to the domain, one per bounded area. Each has `models.py`, `views.py`, `urls.py`, and — where there
is real business logic — a `services.py` kept separate from the HTTP layer.

- `clinique/` — project config (`settings.py`, `urls.py`, `wsgi.py`). URL routing is assembled here; the
  `/api/...` includes and `/health` must stay **before** the catch-all frontend route.
- `accounts/` — custom `User` model (email login, `admin`/`operator` roles), session auth, user management.
  `backends.py` deliberately lets inactive accounts through authentication so the login view can tell
  "account disabled" apart from "wrong password".
- `projects/` — `Project`, the top-level container.
- `patients/` — `Patient`, `PotentialDuplicate`. `services.py` holds anonymous-code generation, name-duplicate
  detection and phone-uniqueness checks.
- `clinical_forms/` — `Form`, whose `definition` is a JSON blob holding the entire form schema
  (sections/fields/formulas) so new forms never require a schema or code change. `exports.py` holds the
  data exports and trend/status-change analyses.
- `submissions/` — `Submission` (one filled-in form instance, optionally tied to a `Case`), plus
  server-side uniqueness and age-consistency checks.
- `cases/` — `Case` (a longitudinal enrollment of a `Patient` in a `Project`), its timeline, and exports.
- `attachments/` — `Media` metadata and upload/serving rules.
- `community/` — the public, unauthenticated endpoints behind a shared access code.
- `indicators/` — monitoring & evaluation dashboard. Models: `Indicator` (source `Form`, `calculation` =
  `count` / `count_patients` / `sum` / `percentage`, `condition` and `denominator_condition` written in the
  `formula_engine` syntax — equality is `==`, patient variables `age` and `sexe` are available — annual
  `target`), `Site` (project, `code` = the choice *value* stored in the form's site field, name, health
  region), optional `IndicatorTarget` (per month, per site or whole project) and `DashboardSettings` (which
  form field holds the site, "on track" threshold, age bands). **Indicator values are never stored**:
  `services.py` loads the period's submissions in one query, turns each into an evaluated row, and every view
  (by month, site, region, age × sex, cumulative) re-aggregates a subset of rows — percentages and distinct
  people are therefore recomputed over the whole set, never summed. Months are bucketed on `collected_at`;
  age is taken at the collection date; missing sex/age/site go to "Non renseigné", never dropped. Targets
  are split equally (1/12 per month, then per site) unless an `IndicatorTarget` overrides them; a percentage
  target is a rate and is never split or summed; sex/age filters do not scale targets. Routes:
  `GET /api/donors`, `GET /api/projects/{id}/dashboard` (also `format=csv|xlsx`, audit-logged), and
  admin-only configuration endpoints for indicators, sites, settings and detailed targets.
- `core/` — cross-cutting pieces: `AuditLog`, audit helper, permissions, rate limiting, frontend serving,
  the full test suite, and framework-agnostic modules carried over unchanged:
  - `formula_engine.py` — a small hand-rolled, **eval-free** expression parser/evaluator (tokenizer +
    recursive descent) for calculated fields, `relevant` (conditional display) and `alert_if` conditions.
    Loosely mirrors XLSForm/Kobo semantics. Never add `eval`/`exec` here — avoiding them is the whole point.
  - `form_validation.py` — server-side re-validation of a submission against its form's JSON `definition`.
    Never trusts client-side calculation: recomputes every `calculated` field itself.
  - `form_import_validation.py`, `spreadsheet_io.py` — imported form structure validation, and Excel/CSV IO.
  - `form_import_formats.py` — format detection for a form-structure import. The NGO's questionnaires are
    authored in Kobo Toolbox, so `/api/forms/import` must accept an XLSForm export (`survey` + `choices`
    sheets, `label::Langue` columns, `select_one list` types, `${x} = 'v'` expressions) as well as the
    app's own template and French column headers; everything is normalised to the template's columns
    before `spreadsheet_io.rows_to_sections`. Anything adapted, dropped or invented during conversion is
    returned as `avertissements` and shown to the user — never silent on a medical form. When nothing is
    recognised, `expliquer_echec` reports the sheets and columns actually seen, not just what was expected.
  - `etude_import.py` — import of an already-completed study from its *answers* file (Google Forms / Kobo
    data export: one row per respondent, one column per question). The header row becomes the form, the
    rows become patients + submissions. Framework-free: `analyser()` proposes a role per column (identity
    columns — prénom/nom/code/téléphone/sexe/naissance/date de collecte — become the `Patient`; the rest are
    questions whose type is inferred from the *values*, with Google checkbox grids `Q [opt]` folded into one
    `select_multiple`), `construire_definition()` and `preparer_ligne()` do the mapping. HTTP side is
    `clinical_forms/etudes.py` (`/api/etudes/analyser`, `/api/etudes/importer`, admin-only): stateless
    two-step (the file is re-sent with the corrected proposal), `a_blanc=1` runs everything inside a
    rolled-back transaction and returns the exact report. Every row goes through
    `validate_and_process_submission` like a manual entry; a refused row is reported with its Excel line
    number and skipped, the others go in. Idempotency key per row (`etude:` + sha256 of project, study name,
    row content) makes re-running the same file safe. Decisions baked in: form type is always `survey`, a
    year-only birth date becomes 1 July, a missing name becomes "Patient <code>", one file = one
    questionnaire (a sheet holding two questionnaires side by side must be split in Excel first). Audit-logged
    as `create` / `study`.

### Frontend (`django_backend/frontend/`)

Plain multi-page HTML/CSS/JS served as static files — no framework, no build step, no npm dependency. It
talks to the API in JSON and is unchanged from the previous version apart from the CSRF helper below.

- `app_shell.js` — renders the common sidebar (`initAppShell(currentProjectId)`), redirects to
  `/login.html` if unauthenticated (but not while offline), lazy-loads icons and the offline DB layer. It
  also wraps `fetch()` once to attach the `X-CSRFToken` header to every same-origin mutating request —
  without it, all 58 POST/PUT/DELETE calls scattered across the pages would be rejected.
- `tableau_de_bord.html` — multi-donor indicator dashboard (donor → project → indicator, site/sex/age filters,
  3/6/12-month rolling window ending on the last complete month, all kept in the query string so a shared
  link reopens the same view; KPIs, HTML/CSS charts with no chart library, per-site table, print-to-PDF and
  Excel export). `indicateurs.html` is its admin-only configuration screen. Both use their own visual
  direction (`tableau_de_bord.css`, scoped under `body.dash`: Barlow fonts hosted locally in `fonts/`,
  steel-blue accent, square outlined cards); the rest of the app keeps `style.css` + `style_blanc_bleu.css`.
  The dashboard needs the server — it is not available offline (the page says so).
- `sw.js` — Service Worker. App-shell files are cache-first, updated in the background; `/api/` requests are
  never cached here (offline data goes through IndexedDB, so the app's own logic controls freshness).
- `dexie.min.js` + `offline_db.js` — IndexedDB-backed local cache plus an `outbox` queueing submissions made
  offline, keyed by a client-generated `idempotency_key`. Polls `/health` to detect connectivity — that
  route is mounted at the root, not under `/api/`, and must stay there.
- `icons.js` — locally-hosted Lucide icon set (no CDN, so icons still render offline).
- `importer_etude.html` — three-step wizard for `core/etude_import.py` (file → check/correct the guessed
  roles and types in a table → dry-run report → real import, with links to the data and the patients).
  Reached from the Enquête page. Needs the server; the file is kept in memory client-side between steps.

### Cross-cutting patterns worth knowing before changing API behavior

- **Idempotency**: `/api/submissions` accepts a client-generated `idempotency_key`; a repeated request with
  the same key returns the existing submission instead of creating a duplicate (covers double-click and
  network-retry replay, and is what makes offline queue replay safe).
- **`URL_FORMAT_OVERRIDE` is disabled** in `REST_FRAMEWORK` settings. `format` is a *business* query
  parameter here (`csv`/`xlsx`); left at its default, DRF would intercept it and return 404 on every export.
- **Public/unauthenticated form access**: `/api/public/forms/{form_id}/...` routes let a form be filled out
  without a login, gated by a shared access code whose **hash only** is stored on the `Form` row. The
  project is always taken from the resolved form, never from client input. Regenerating the code invalidates
  all previously unlocked devices immediately.
- **Rate limiting** (`core/ratelimit.py`): in-process counters guard login and the public access code. They
  assume a **single** gunicorn worker — the entrypoint pins `--workers 1` for exactly this reason.
- **Roles**: `core.permissions.IsAdmin` must be layered on sensitive actions (project deletion, user
  management, audit log); being authenticated proves identity, not authorization. There is deliberately **no
  per-project access control** — all staff belong to the same NGO and legitimately see every project.
- **Media uploads**: stored under `django_backend/uploads/` with a randomly generated filename (never the
  original) to avoid path-traversal/collision. SVG/HTML uploads are rejected, and anything outside a small
  safe allowlist is served as `attachment` with `X-Content-Type-Options: nosniff`, so an uploaded file can
  never execute as a page in the app's origin.
- **Audit log** (`core/audit.py`): intentionally narrow scope — logins, account management, deletions and
  exports only, not routine reads. Logging failures are swallowed so a broken audit write never blocks the
  action it records.

## Tests

`core/tests.py` covers the paths that hurt most when they break: backdated data entry, offline-queue
idempotency, code/number allocation (including the deletion-in-the-middle case), permission boundaries, and
the unauthenticated community link. Run with `docker compose exec api python manage.py test`.
