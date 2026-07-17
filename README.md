# ckanext-csunesco

Citizen Science extension for CKAN / **IHP-WINS** (UNESCO). It brings the
water-family pattern from `ckanext-pages` (initiative + project landings,
news/events, approval dashboard) into a self-contained plugin, plus
Citizen-Scientist self-registration (no organization) inspired by
`ckanext-colab`. It is the IHP-WINS side of the **CS Toolbox** (`ofform`)
workflow — see [`docs/OFFORM_INTEGRATION.md`](docs/OFFORM_INTEGRATION.md).

## Features

- **Citizen-Scientist self-registration** — a colab-style blueprint at
  `/register-citizen` that creates an active CKAN account with **no
  organization** and flags a CS profile; optional reCAPTCHA v3. Also exposed
  server-to-server as `csunesco_register_citizen_scientist` for ofform.
- **Initiatives & projects** — the four initiatives (Be Resilient, Island Watch,
  River Watch, C4Water) are CKAN groups; CS projects are first-class rows with a
  request → approve/reject lifecycle (`csunesco_project_*`). Join requests use
  the same moderation pattern (`csunesco_join_*`).
- **Landing pages** — a hub at `/citizen-science`, per-initiative listings, and
  per-project landing pages with a **region map** (Leaflet + GeoJSON),
  **at-a-glance counters** (citizen scientists, observations, sites, member
  states) and a **join link / QR** code.
- **Admin approval panel** — `/cs-admin` aggregates pending project-requests and
  join-requests for sysadmins (`csunesco_admin_pending_list`).
- **News / events / media** — per-project content (`cs-news`, `cs-events`,
  `cs-media`) editable by project admins, HTML sanitised with `bleach`, exposed
  through public `side_effect_free` `csunesco_content_list` / `_show`.

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
