# Installing ckanext-csunesco

Citizen Science (UNESCO / **IHP-WINS**) extension for **CKAN 2.10**. This guide
covers installing the plugin into a CKAN site, initialising its data, and
verifying the result. It is accurate to what the code actually does today.

## Requirements

- CKAN **2.10**.
- Python 3.8–3.10 (matches the CKAN 2.10 runtime).
- `bleach` (declared in `setup.py`'s `install_requires`; pulled in automatically
  by the install step below). It sanitises user-supplied news/event bodies.

## 1. Install the package

From a checkout (editable, recommended for dev):

```bash
pip install -e .
```

Or from a built wheel (recommended for production images):

```bash
pip install build           # once
python -m build             # produces dist/ckanext_csunesco-*.whl
pip install dist/ckanext_csunesco-*.whl
```

## 2. Enable the plugin

Add `csunesco` to `ckan.plugins` in your CKAN ini (e.g.
`/etc/ckan/default/ckan.ini`):

```ini
ckan.plugins = ... csunesco
```

Citizen-Scientist self-registration reuses CKAN's account creation, so it needs
web account creation enabled:

```ini
ckan.auth.create_user_via_web = true
```

## 3. Create the database tables

The plugin owns five tables (`cs_project`, `cs_project_member`, `cs_content`,
`cs_project_stats`, `cs_citizen_scientist`). There are **three** ways they get
created, and they are all idempotent:

```bash
# Standard CKAN migration entry point (no-op today: the plugin ships no Alembic
# migrations yet, so this just confirms core + plugins are up to date).
ckan -c /etc/ckan/default/ckan.ini db upgrade -p csunesco

# Explicit table bootstrap for this plugin.
ckan -c /etc/ckan/default/ckan.ini csunesco init-db
```

You do **not** strictly need to run either by hand: the plugin **self-heals** its
tables on load. Its `configure()` hook calls `db.ensure_tables()` once at
startup (wrapped in a broad try/except that only logs a generic error, so it can
never break CKAN boot). `ckan csunesco init-db` runs the exact same
`ensure_tables()` on demand.

## 4. Seed the four initiative groups

The four Citizen Science initiatives are modelled as CKAN **groups** (water-family
pattern). Seed them once (idempotent — re-running only syncs titles):

```bash
ckan -c /etc/ckan/default/ckan.ini csunesco seed-initiatives
```

This creates/syncs: **Be Resilient** (`be-resilient`), **Island Watch**
(`islandwatch`), **River Watch** (`riverwatch`) and **C4Water** (`c4water`).

## 5. Optional: reCAPTCHA on registration

Citizen-Scientist registration supports Google reCAPTCHA **v3**. It is enforced
**only when both** keys are configured; otherwise registration works without it.

```ini
ckan.recaptcha.publickey  = <site-key>
ckan.recaptcha.privatekey = <secret-key>
```

## Verify

Two complementary options, both in this repo:

- **Reproducible container harness** (real CKAN 2.10, no local CKAN needed):

  ```bash
  bash scripts/run-ckan-tests.sh
  ```

  Builds `Dockerfile.test` and runs the plugin-load smoke check (instantiates
  `CsunescoPlugin`, asserts the action/helper/validator/auth registries are
  non-empty → `PLUGIN OK`) plus the behavioral pytest files
  (`test_db_behavior.py`, `test_pure_logic.py`).

- **Full HTTP dev stack** (CKAN + Postgres + Solr + Redis):

  ```bash
  docker compose -f docker-compose.dev.yml up
  docker compose -f docker-compose.dev.yml exec ckan ckan db upgrade
  docker compose -f docker-compose.dev.yml exec ckan ckan csunesco seed-initiatives
  # then open http://localhost:5000/citizen-science
  ```

For a fast local sanity check without Docker (syntax-level only, no CKAN import):

```bash
bash .mix/verify.sh
```

## Deploy (CapRover / production)

- Bake the plugin into your CKAN image. Either `pip install -e .` from the source
  tree (as `Dockerfile.test` does) or `pip install` a wheel built in step 1.
- Add `csunesco` to `ckan.plugins` and set `ckan.auth.create_user_via_web = true`
  via your deploy env / ini (on CapRover, through the app's env vars or a mounted
  ini).
- On first deploy run `ckan db upgrade -p csunesco` and
  `ckan csunesco seed-initiatives` once. The table self-heal on `configure` makes
  redeploys safe even if that step is skipped.
- Serve behind HTTPS and set `ckan.site_url` accordingly so the join **link/QR**
  codes on project landing pages resolve to the public URL.
- To connect the **CS Toolbox (ofform)** PWA to this plugin, see
  [`docs/OFFORM_INTEGRATION.md`](docs/OFFORM_INTEGRATION.md).

## License

GNU Affero General Public License (AGPL) v3.0.
