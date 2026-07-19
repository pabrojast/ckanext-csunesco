# ckanext-csunesco

Citizen Science extension for CKAN / **IHP-WINS** (UNESCO). It brings the
water-family pattern from `ckanext-pages` (initiative + project landings,
news/events, approval dashboard) into a self-contained plugin, plus
Citizen-Scientist self-registration (no organization) inspired by
`ckanext-colab`. It is the IHP-WINS side of the **CS Toolbox** (`ofform`)
workflow — see [`docs/OFFORM_INTEGRATION.md`](docs/OFFORM_INTEGRATION.md).

## Features

- **Citizen-Scientist self-registration** — a colab-style blueprint at
  `/citizen-science/register-citizen` that creates an active CKAN account with
  **no organization** and flags a CS profile; optional reCAPTCHA v3. Also exposed
  server-to-server as `csunesco_register_citizen_scientist` for ofform.
- **Initiatives & projects** — the four initiatives (Be Resilient, Island Watch,
  River Watch, C4Water) are CKAN groups; CS projects are first-class rows with a
  request → approve/reject lifecycle (`csunesco_project_*`). Join requests use
  the same moderation pattern (`csunesco_join_*`).
- **Landing pages** — a hub at `/citizen-science`, per-initiative listings, and
  per-project landing pages with a **region map** (Leaflet + GeoJSON),
  **at-a-glance counters** (citizen scientists, observations, sites, member
  states) and a **join link / QR** code.
- **Admin approval panel** — `/citizen-science/admin` aggregates pending
  project-requests and join-requests for sysadmins and project admins
  (`csunesco_admin_pending_list`).
- **Content management** — per-project content of four types: news (`cs-news`),
  events (`cs-event`), **publications** (`cs-publication`, with document links,
  optional DOI/authors) and **maps** (`cs-map`, an embedded Terria share link
  validated against a configured base-URL allowlist). Editable by project
  admins, HTML sanitised with `bleach`, exposed through public
  `side_effect_free` `csunesco_content_list` / `_show`, with public indexes at
  `/citizen-science/news`, `/events`, `/publications` and `/maps`. Content
  pushed from the CS Toolbox app carries `source: 'app'` and **always** lands
  `pending` (sysadmin review), even though the app pushes with a sysadmin token.
- **App-data pipeline** — a project admin (from the portal) or a project owner
  (from the CS Toolbox app) can publish a form's collected observations on
  IHP-WINS. The request is a `cs_data_source` row that **always** starts
  `pending`; on sysadmin approval the plugin creates a real CKAN dataset whose
  CSV + GeoJSON resources point at the live proxy routes
  (`/citizen-science/data/<id>.csv|.geojson`), which fetch ofform's public
  endpoints with a TTL cache. The project landing gains a **Data** section with
  an observation map (Leaflet), download links and — when
  `ckanext.data_stories.enabled` is on — a "Create a data story" entry point
  so users can combine their datasets in Data Stories / Terria.

## Endpoints & permissions

Every HTTP view lives under the blueprint prefix **`/citizen-science`** (so the
self-registration page is `/citizen-science/register-citizen`, **not**
`/register-citizen`). Authorization roles:

- **public** — anonymous allowed. Read views only ever expose *approved* rows to
  non-privileged callers; the action layer does the filtering.
- **authenticated** — any logged-in CKAN user.
- **project admin** — an active `admin` member of the *target* project.
- **sysadmin** — a CKAN sysadmin (the IHP admin).

### HTTP routes

| Method | Path | Purpose | Who can access |
| --- | --- | --- | --- |
| GET | `/citizen-science/` | Citizen Science hub | public |
| GET | `/citizen-science/initiative/<name>` | Single initiative + its approved projects | public |
| GET | `/citizen-science/projects` | Filterable project listing | public |
| GET | `/citizen-science/project/<slug>` | Project landing page | public (approved) |
| GET | `/citizen-science/project/<slug>/geojson` | Async region GeoJSON for the map | public |
| GET | `/citizen-science/news` · `/news/<slug>` | News index / detail | public (approved) |
| GET | `/citizen-science/events` · `/events/<slug>` | Events index / detail | public (approved) |
| GET | `/citizen-science/publications` · `/publications/<slug>` | Publications index / detail | public (approved) |
| GET | `/citizen-science/maps` · `/maps/<slug>` | Maps index / detail (Terria embed) | public (approved) |
| GET | `/citizen-science/data/<id>.csv` · `.geojson` | Live data proxy for an **approved** data source (fetches ofform's public endpoints, TTL-cached) | public |
| GET·POST | `/citizen-science/register-citizen` | Citizen Scientist self-registration (account created **pending** until email is verified) | public — gated by `ckan.auth.create_user_via_web`; reuses core `user_create` auth |
| GET | `/citizen-science/verify/<token>` | Activate a pending account via its emailed link | public (single-use token) |
| GET·POST | `/citizen-science/verify/resend` | Request a fresh verification link | public (generic response) |
| GET·POST | `/citizen-science/project/new` | Propose a project (request) | authenticated |
| POST | `/citizen-science/project/<slug>/join` | Request to join a project | authenticated |
| GET·POST | `/citizen-science/project/<slug>/content/new` | Add news/event to a project | sysadmin **or** that project's admin |
| GET·POST | `/citizen-science/content/<id>/edit` | Edit an existing content item | sysadmin **or** project admin |
| GET·POST | `/citizen-science/project/<slug>/data/connect` | Connect a CS Toolbox form's data (request, lands pending) | sysadmin **or** that project's admin |
| GET | `/citizen-science/admin` | Approval panel (pending projects/joins/content/data) | sysadmin **or** any project admin |
| POST | `/citizen-science/admin/project/<id>/approve` · `/reject` | Moderate a project request | sysadmin |
| POST | `/citizen-science/admin/join/<project_id>/<user_id>/approve` · `/reject` | Moderate a join request | sysadmin **or** project admin |
| POST | `/citizen-science/admin/content/<id>/approve` · `/reject` | Moderate a content item | sysadmin |
| POST | `/citizen-science/admin/data/<id>/approve` · `/reject` | Moderate a data source (approve creates the CKAN dataset) | sysadmin |

All POST forms carry CKAN's CSRF token (`h.csrf_input()`); mutating routes use
POST-redirect-GET.

### API actions (`/api/3/action/<name>`)

Read actions are `side_effect_free` (callable via GET). Registration validation
is deliberately generic (no per-field errors) so the endpoint cannot be used to
enumerate accounts.

| Action | Access |
| --- | --- |
| `csunesco_project_list`, `csunesco_project_show`, `csunesco_project_stats_show`, `csunesco_aggregate_stats` | public (read; approved only for non-sysadmins) |
| `csunesco_content_list`, `csunesco_content_show` | public (read; approved only for non-sysadmins) |
| `csunesco_project_request_create` | authenticated |
| `csunesco_join_request_create` | authenticated |
| `csunesco_content_create`, `csunesco_content_update` | sysadmin **or** project admin (an explicit `source: 'app'` forces `pending` even for sysadmins) |
| `csunesco_data_source_list`, `csunesco_data_source_show` | public (read; approved only for non-privileged callers) |
| `csunesco_data_source_create` | sysadmin **or** project admin — **always** creates `pending`; idempotent per `(project, form)` |
| `csunesco_admin_pending_list` | sysadmin **or** any project admin |
| `csunesco_project_approve`, `csunesco_project_reject` | sysadmin |
| `csunesco_content_approve`, `csunesco_content_reject` | sysadmin |
| `csunesco_data_source_approve`, `csunesco_data_source_reject` | sysadmin (approve creates/refreshes the CKAN dataset) |
| `csunesco_join_approve`, `csunesco_join_reject` | sysadmin **or** project admin |
| `csunesco_register_citizen_scientist` | **sysadmin token only** — server-to-server (ofform); idempotent |

The full ofform endpoint→action mapping and identity model live in
[`docs/OFFORM_INTEGRATION.md`](docs/OFFORM_INTEGRATION.md).

### Registration validation

The `/citizen-science/register-citizen` POST and the `csunesco_register_citizen_scientist`
action share one implementation (`logic/registration.create_citizen_scientist`).
Server-side checks (the browser form is progressive-enhancement only — it carries
`novalidate`, so the server is the source of truth):

- **email**, **username**, **password** required; username lower-cased + stripped.
- **password** ≥ 8 chars and must equal **confirm password** (web form).
- **terms** checkbox must be accepted (web form).
- **reCAPTCHA v3** verified server-side (score > 0.5) **only when both
  `ckan.recaptcha.publickey` and `ckan.recaptcha.privatekey` are set**; skipped
  otherwise.
- Core `user_create` then enforces CKAN's own rules (name charset + uniqueness,
  email format + uniqueness, password policy). Any failure — including duplicates
  — collapses to one generic error.

**Email verification (web flow).** A web self-registration lands the CKAN account
in `pending` state — login is blocked (both core `default_authenticate` and the
custom authenticator gate on `user.is_active`) until the user opens the emailed
`/citizen-science/verify/<token>` link, which flips the account to `active`.
Tokens are single-use and expire after `VERIFICATION_TOKEN_TTL_HOURS` (48h);
`/citizen-science/verify/resend` re-issues one (generic response, no enumeration).
The declared **country** is persisted on the CS profile. Requires a working SMTP
config (`smtp.*`). The server-to-server `csunesco_register_citizen_scientist`
action is unaffected — trusted (sysadmin) callers still create active,
already-verified accounts.

## Configuration

All options are read lazily (no restart-ordering constraints beyond a normal
config reload). Features gated on an option **fail closed** when it is unset.

| Option | Default | Purpose |
| --- | --- | --- |
| `ckanext.csunesco.terria_base_url` | *(unset — maps disabled)* | Space-separated allowlist of Terria base URLs a `cs-map` may embed (e.g. `https://ihp-wins.unesco.org/terria`). Unset ⇒ the `cs-map` validator rejects every URL and stored maps render as plain links. List every host if Terria lives on several. |
| `ckanext.csunesco.ofform_base_url` | *(unset — data pipeline disabled)* | The **only** origin the data proxy will fetch (the CS Toolbox API base, e.g. `https://ofform-api.aquedra.com`). Anti-SSRF: form ids are int-coerced into a fixed path under this base. |
| `ckanext.csunesco.ofform_cache_ttl` | `60` | Seconds a proxied response (CSV / dashboard JSON) is cached per form. |
| `ckanext.csunesco.ofform_app_url` | *(unset — links hidden)* | The CS Toolbox **frontend** base (e.g. `https://ofform.aquedra.com`). Used only to render "Open in the app" links in the review panel. |
| `ckanext.csunesco.dataset_owner_org` | *(unset)* | **Fallback** organization for datasets created on data-source approval. The actual owner is resolved in priority order: the sysadmin's choice in the approval form → the org suggested by the app (`owner_org` in the request; ofform keeps its orgs synchronized with the portal via `ckan_slug`) → `cs_project.organization_id` → this option. A suggestion that does not exist on the portal falls back to this default. |
| `ckanext.csunesco.dataset_defaults` | `{}` | Optional JSON object merged into `package_create` — use it to satisfy portal-schema (e.g. schemingdcat) required fields, licences, etc. |
| `ckanext.data_stories.enabled` | — | Not ours (ckanext-pages), but when true the project landing shows a "Create a data story" entry point. |

Terria embeds additionally require the Terria host to allow framing (no
`X-Frame-Options: DENY`); otherwise the map page falls back to a plain link.

### Sysadmin review runbook

Everything users publish flows through **one** approval panel at
`/citizen-science/admin`, and the navbar shows a **Review n** badge whenever
something is pending (the badge and the tab counters share one query, so they
never disagree). Four tabs:

1. **Project requests** (sysadmin) — approve turns the requester into the
   project's admin and seeds its counters.
2. **Join requests** (sysadmin or project admin).
3. **Content to review** — news, events, publications and maps; portal-authored
   sysadmin content publishes directly, everything else (including *all*
   app-authored content) waits here.
4. **Data to review** (sysadmin) — each row shows a live probe of the form's
   public data (reachable? observations, geolocated count, date range) and an
   "Open in the app" link (`ofform_app_url`), so nothing is approved blind.
   Approving creates/refreshes a live CKAN
   dataset fed by the CS Toolbox app (CSV + GeoJSON proxy resources). The
   approve form includes an **organization picker** preselected with the
   app-suggested org (when it exists on the portal) or the configured
   default — the reviewer can change it before approving. If
   dataset creation fails (e.g. missing `dataset_owner_org` or portal-schema
   fields), the row **stays pending** and can be retried after fixing config.
   Data truncates at ofform's 20 000-row export cap. If a form owner later
   reverts the form to private in the app, the proxy starts returning 502 for
   that source.

### Next stages (agreed, not yet built)

- Email notification / daily digest to sysadmins when items land in the
  review queue (SMTP is already configured on the portal).
- Automatic `cs_project_stats` counters (observations/sites) refreshed from
  the proxy fetch totals instead of manual upkeep.
- Bulk approve (checkbox selection) in the content and data tabs.
- Per-project `trusted` flag auto-approving news/events only (policy call).
- Auto-enqueue the data-source request when approving an app-originated
  project that already has published forms.

## Requirements

CKAN 2.10.

## Installation

Quick start:

```bash
pip install -e .
```

Then add `csunesco` to `ckan.plugins`, create the tables and seed the
initiatives. See **[`INSTALL.md`](INSTALL.md)** for the full install / seed /
deploy guide (including reCAPTCHA and CapRover notes).

```ini
ckan.plugins = ... csunesco
ckan.auth.create_user_via_web = true
```

```bash
ckan -c /etc/ckan/default/ckan.ini csunesco init-db          # also self-heals on load
ckan -c /etc/ckan/default/ckan.ini csunesco seed-initiatives # the 4 initiative groups
```

## ofform integration

The CS Toolbox PWA (`ofform`) proxies all Citizen Science mutations to this
plugin's `csunesco_*` actions using a server-side `CKAN_API_TOKEN` /
`CKAN_WRITE_BASE_URL`. The browser never holds the token. Full endpoint→action
mapping, identity model and workflow table:
**[`docs/OFFORM_INTEGRATION.md`](docs/OFFORM_INTEGRATION.md)**.

## Design

UNESCO water-family branding: the CSS exposes the shared blue palette as
design tokens (`--unesco-blue: #0072BC` and its dark/light/pale variants,
`--admin-gold`, `--text-primary`, plus spacing/radius/shadow tokens) in
`ckanext/csunesco/assets/css/csunesco.css`.

## Verification

The plugin is now **confirmed to load and behaviorally tested in real CKAN
2.10** via [`Dockerfile.test`](Dockerfile.test). The reproducible harness builds
that image and runs a plugin-load smoke check (`PLUGIN OK`) plus the behavioral
pytest files:

```bash
bash scripts/run-ckan-tests.sh
```

To exercise the plugin end-to-end over HTTP, bring up the full CKAN 2.10 dev
stack (CKAN + Postgres + Solr + Redis):

```bash
docker compose -f docker-compose.dev.yml up
docker compose -f docker-compose.dev.yml exec ckan ckan db upgrade
docker compose -f docker-compose.dev.yml exec ckan ckan csunesco seed-initiatives
# then open http://localhost:5000/citizen-science
```

**When CKAN is not installed locally**, verification falls back to
**syntax-level only** (no `import ckan`, no `pytest-ckan`). Runtime modules still
`import ckan...`; they only need to be syntactically parseable there:

```bash
bash .mix/verify.sh
```

The script runs `python -m compileall`, AST checks on `setup.py` / `plugin.py`
and the domain modules, and structural checks. The real behavioral suite runs in
the container harness above.

## License

GNU Affero General Public License (AGPL) v3.0.
