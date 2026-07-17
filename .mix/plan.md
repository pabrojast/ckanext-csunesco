# Plan — `ckanext-csunesco` (CKAN/IHP-WINS) + integración con la app `ofform` (CS Toolbox)

> Objetivo: replicar el patrón **water family** de `ckanext-pages` (listado + landing pages con
> diseño UNESCO + news/events + panel de aprobación) en un **plugin CKAN propio** `ckanext-csunesco`,
> sumando **self-registration de Citizen Scientists sin organización** (patrón `ckanext-colab`), un
> modelo de **Iniciativas y Proyectos CS** con landing/mapa/contadores y flujo de **join + aprobación**,
> y **modificar la PWA `ofform`** para enviar registros/join/news/events a IHP-WINS asociados a un
> proyecto. El **data analyzer / AI es 2do paso** (fuera del alcance de Fase 1).

---

## 0. Contexto y hallazgos de exploración (fuente de verdad)

**Dos sistemas** (columnas del PDF de flujos):
- **IHP-WINS = CKAN** (Python/Flask), API en `https://ihp-wins.unesco.org/api/3/action`. Aquí va el
  plugin nuevo `ckanext-csunesco` (dir de trabajo, hoy solo tiene el PDF).
- **CS Toolbox = `ofform`**: backend FastAPI + SQLModel + SQLite; frontend React 19 + Vite + TS (PWA
  Workbox, Dexie, i18n es/en/fr/pt/ar RTL, dark mode). Deploy CapRover por commit a `master`
  (`ofform-api` + `ofform` web). Diseño ya usa `--primary:#0069b4` (azul UNESCO).

**water family (ckanext-pages)** — patrón a copiar (no forkear):
- Contenido = filas del modelo `Page` (tabla `ckanext_pages`) discriminadas por `page_type`
  (`water-news`, `water-events`, `water-publications`); campos propios en columna `extras` (JSON) que
  `table_dictize` aplana a claves top-level.
- **Iniciativas** = **CKAN groups** (no hijos de `member-states`); asociación por `extras['initiative_groups']`.
  **Member states** = groups hijos del group `member-states`; asociación por `extras['country_groups']`.
- Rutas por tipo (index→list, `<page>`→show, `_edit`/`_delete`), landing `/water-family`
  (`water_family_main_page`), **admin dashboard** `/water-admin` con approve/reject, y **API pública**
  `side_effect_free` `water_family_list`/`water_family_show` (solo `private=False`+`approved`).
- **Moderación**: `submission_status` (draft→pending→approved/rejected), `featured`, gating sysadmin.
- **Branding UNESCO**: `:root` con `--unesco-blue:#0072BC` (+ dark/light/pale), hero `.section-title`
  con overlay, cards `.module`/`.wf-section-*`, contadores `.stat-item`. Eventos con calendario
  (FullCalendar CDN). Mapa geográfico: patrón CRIDA (action GeoJSON + JS cliente).

**colab (ckanext-colab)** — mecanismo de registro:
- Blueprint Flask **paralelo** a `/user/register` (`/colab`, GET+POST). Crea la cuenta CKAN **activa**
  de inmediato: `check_access('user_create')` + `toolkit.get_action('user_create')(...)`, y guarda una
  fila de "aplicación" con `approved="Pending"` (la cuenta NO queda pendiente; el Pending gatea solo la
  membresía a organización). Requiere `ckan.auth.create_user_via_web = true`.
- Para **Citizen Scientist sin org**: copiar el scaffolding y **quitar** la validación/​backstop de
  organización (líneas del fieldset de org). reCAPTCHA opcional. Auto-heal de schema con `ALTER TABLE`.

**ofform** — arquitectura:
- Integración CKAN actual **solo lectura** (`app/ihpwins.py`, sin token): `group_show/list`,
  `organization_show/list`. **No existe** cliente de escritura ni `CKAN_API_TOKEN` → enviar
  news/events/requests a CKAN es **greenfield**.
- `Programme` (`models.py:119`) = concepto más cercano a "proyecto/iniciativa"; `Form`/`Submission` ya
  llevan `programme_id`/`organization_id`. `Membership`(scope=organization|programme|country, role
  owner>manager>member, `source` app|ihpwins) es el backbone de auth; `MembershipRequest` +
  `can_decide_request` (owner-of-scope o admin) ya implementan **join + aprobación**.
- `db.py`: SQLite `create_all` + `_SCHEMA_UPGRADES`/`_SCHEMA_INDEXES` (toda columna nueva debe
  registrarse ahí o no existe en el volumen CapRover `/app/data`).
- Auth: JWT HS256, `deps.py`. Frontend: `api.ts` (Bearer), Dexie v5, `styles.css` tokens + dark,
  i18n 5 idiomas. **No existe** hoy ningún entity News/Event/Project/Initiative de primera clase.

---

## 1. Enfoque / Arquitectura

**Principios (coincidencia Codex + DeepSeek + síntesis propia):**
1. **CKAN/IHP-WINS = fuente de verdad** de iniciativas, proyectos, roles y contenido (landing,
   news/events, contadores). `ofform` es cliente operativo/offline-first.
2. **`ckanext-csunesco` es un plugin propio y auto-contenido**: **copia el patrón** water-family
   (modelo tipo `Page` + `page_type`/`extras`, groups para iniciativas/member-states, rutas, landing,
   admin dashboard, API pública, branding UNESCO) **sin depender** del código de `ckanext-pages`.
3. **Registro Citizen Scientist sin org**: blueprint estilo colab con la validación de organización
   removida; el usuario CKAN queda activo; se marca un **perfil "Citizen Scientist"** del plugin (no un
   rol de organización — CKAN no tiene "roles globales" flexibles).
4. **Toda mutación desde `ofform` pasa por su backend FastAPI** (proxy server-to-server con
   `CKAN_API_TOKEN`); el navegador **nunca** llama la write-API de CKAN ni ve tokens (seguridad).
5. **Cola offline idempotente (outbox)** en `ofform` para requests/join/news/events (idempotency key,
   reintentos, `RecordSource`-style provenance). Envío **asíncrono** (no bloquear la respuesta al user).
6. **Contadores "at a glance" pre-agregados** en tabla propia del plugin, actualizados de forma
   incremental por la ingesta (nunca agregación pesada en cada carga de landing).

**Decisiones donde me aparté de una propuesta de asesor (y por qué):**
- **Menos tablas en CKAN**: fusiono *project-request* en `cs_project.status` (pending/approved/rejected)
  — espeja el `submission_status` de water-family — en vez de una tabla `cs_project_request` separada
  (Codex la separó). Igual fusiono *join-request* en `cs_project_member.status`. Menos superficie, mismo
  flujo de moderación.
- **Reuso de infra en `ofform` en vez de modelo paralelo nuevo**: represento cada **CS Project como un
  `Programme` espejo** (`kind='cs_project'`, `ckan_slug`, `external_id`=id CKAN). Así el **join** es un
  `MembershipRequest(scope_type=programme)`, el **project admin** es `Membership(scope=programme,
  role=owner)`, y `can_decide_request` ya permite que el owner apruebe. Además, como `Form`/`Submission`
  ya llevan `programme_id`, **"todo registro asociado a un proyecto"** sale gratis. (Codex proponía
  modelos `cs_project`/rutas nuevas en ofform; reusar Programme/Membership es más barato y consistente.)
- **Iniciativas y member-states como CKAN groups** (patrón water-family) en vez de tabla
  `cs_initiative` (Codex la listó). Encaja con el paso 0 ("agregar tab CS a las 4 Iniciativas") y reusa
  el sync de member-states que ofform ya tiene.
- **Identidad centralizada en CKAN** (donde los asesores solo marcaron el riesgo, yo decido): CKAN es la
  autoridad de identidad. El registro crea la cuenta CKAN; `ofform` **provisiona un usuario-sombra
  local (JIT)** en el primer login válido y guarda **server-side** un API token CKAN por usuario (nunca
  expuesto al browser). ⚠️ *Es el punto de integración más riesgoso — ver §4 y el gate.*

**Mapa de dominio (CKAN `ckanext-csunesco`):**
- `cs_initiative` → **CKAN group** (Be Resilient, IslandWatch, RiverWatch, C4Water). Semilla por CLI.
- `cs_project` (tabla nueva, first-class): `id, slug, title, short_description, initiative_group,
  countries(json), biosphere_reserve, region_geojson, project_document_url, landing_content(HTML),
  organization_id?, status(pending|approved|rejected), created_by, reviewed_by, timestamps`.
- `cs_project_member` (tabla): `project_id, user_id, role(admin|scientist), status(active|pending),
  source(ckan|ofform|qr)`. (join-request = fila con `status='pending'`.)
- `cs_content` (tabla tipo Page): `content_type(cs-news|cs-event|cs-media), project_id, initiative_group,
  title, body(HTML sanitizado), media(json), publish_date/end_date, status(draft|pending|approved),
  featured, created_by, timestamps`.
- `cs_project_stats` (tabla): `project_id, citizen_scientists, observations, sites_monitored,
  member_states` (pre-agregado, update incremental).
- `cs_citizen_scientist` (perfil): flag/fila que marca al user como CS (sin org).

**Mapa de dominio (`ofform`):**
- `Programme` extendido: `kind('generic'|'cs_project')`, `ckan_slug`, (usa `external_id`). Mirror de
  `cs_project`. Join/approval reusa `Membership`/`MembershipRequest`.
- `cs_sync_outbox` (tabla nueva SQLModel): `id, kind, payload_json, idempotency_key(unique), status,
  attempts, last_error, created_at`. Registrar columnas/tabla en `db._SCHEMA_UPGRADES`.
- `Submission`: garantizar `programme_id` (=cs_project) presente para envíos CS; validación server-side.

---

## 2. Archivos a crear / modificar

### CKAN plugin `ckanext-csunesco` (crear)
- `setup.py` / `pyproject.toml` — metadata + entry point `csunesco=ckanext.csunesco.plugin:CsunescoPlugin`.
- `ckanext/csunesco/plugin.py` — `IConfigurer, IBlueprint, IActions, IAuthFunctions, ITemplateHelpers,
  IConfigurable, IClick, ITranslation`; assets, templates dir, helpers (counters, QR, urls, permisos).
- `ckanext/csunesco/db.py` (o `model.py`) — tablas `cs_project`, `cs_project_member`, `cs_content`,
  `cs_project_stats`, `cs_citizen_scientist`; `ensure_*_table_exists()` con auto-heal (ALTER) estilo colab.
- `ckanext/csunesco/migration/csunesco/versions/*.py` — migraciones Alembic.
- `ckanext/csunesco/logic/actions.py` — API (server-to-server para ofform, `side_effect_free` las de
  lectura): `cs_register_citizen_scientist`, `cs_project_request_create`, `cs_project_approve`,
  `cs_project_reject`, `cs_project_list`, `cs_project_show`, `cs_join_request_create`, `cs_join_approve`,
  `cs_join_reject`, `cs_content_create/update/list`, `cs_observation_ingest` (valida membresía + asocia
  `project_id` + actualiza stats), `cs_project_stats_show`.
- `ckanext/csunesco/logic/auth.py` — permisos: sysadmin (IHP admin), project_admin, citizen_scientist,
  anónimo para lectura pública.
- `ckanext/csunesco/logic/validators.py` — país/iniciativa/slug/GeoJSON/uploads (extensión+MIME+magic).
- `ckanext/csunesco/blueprint.py` — rutas públicas + paneles:
  `/citizen-science` (landing hub + listado de proyectos por iniciativa),
  `/citizen-science/initiative/<slug>`, `/citizen-science/project/<slug>` (landing de proyecto),
  `/citizen-science/project/<slug>/join` (link/QR), `/register-citizen` (registro sin org),
  `/cs-admin` (panel aprobación de project-requests + join-requests), `/cs-news`, `/cs-events`
  (index/show/edit/delete), `/cs_geojson/<project>` (mapa de región).
- `ckanext/csunesco/templates/csunesco/*` — `citizen-science.html` (hub), `project_landing.html`,
  `project_list.html`, `cs-admin-dashboard.html`, `register_citizen.html`, `cs-news*`/`cs-events*`,
  `snippets/*` (cards, contadores, mapa, QR).
- `ckanext/csunesco/templates/group/` y `templates/organization/` — snippet "Citizen Science" tab.
- `ckanext/csunesco/assets/` — `css/csunesco.css` (paleta UNESCO copiada de water-family),
  `js/cs-map.js` (Leaflet región + GeoJSON), `js/cs-qr.js`, `webassets.yml`.
- `ckanext/csunesco/cli.py` — `csunesco init-db`, `csunesco seed-initiatives`.
- `ckanext/csunesco/tests/*` — tests de actions/auth/validators/vistas (pytest-ckan).
- `.mix/verify.sh` — verificación fuerte del proyecto (ver §5).

### `ofform` backend (modificar)
- `backend/app/ihpwins_write.py` (nuevo) — cliente CKAN autenticado (`Authorization: <CKAN_API_TOKEN>`)
  que llama las actions `cs_*`; reintentos/backoff (patrón del read-client).
- `backend/app/config.py` — `CKAN_API_TOKEN`, `CKAN_WRITE_BASE_URL` (o reusar `IHPWINS_API`), secret de
  callback. Inyectar en `deploy.yml` (merge de env vars).
- `backend/app/models.py` — `Programme.kind`, `Programme.ckan_slug`; tabla `cs_sync_outbox`.
- `backend/app/db.py` — registrar nuevas columnas/tabla en `_SCHEMA_UPGRADES`/`_SCHEMA_INDEXES`.
- `backend/app/routers/cs_projects.py` (nuevo) — request/list/join/approve (proxy a CKAN + espejo local).
- `backend/app/routers/cs_content.py` (nuevo) — news/events/media (encola en outbox → CKAN).
- `backend/app/services/cs_sync.py` (nuevo) — worker de outbox (BackgroundTasks/daemon), idempotente.
- `backend/app/routers/auth.py` / `deps.py` — provisión JIT del usuario-sombra + bridge de identidad CKAN.
- `backend/main.py` — montar routers nuevos; arrancar worker de sync en `lifespan`.
- `backend/app/services/submission_upsert.py` — exigir `programme_id`=cs_project para envíos CS.
- `backend/tests/*` — sync/outbox/retries/permisos/asociación a proyecto (mock del CKAN write).

### `ofform` frontend (modificar)
- `src/routes/RegisterCitizenScientist.tsx` + `RegisterProjectManager.tsx` (o un punto único con
  selección de rol) — registro (crea cuenta vía backend→CKAN).
- `src/routes/ProjectRequest.tsx`, `ProjectList.tsx`, `ProjectLanding.tsx`/`ProjectDashboard.tsx`.
- `src/admin/CsApprovalPanel.tsx` — reusar el patrón de `AdminRequests.tsx` (project + join requests).
- `src/routes/NewsEventEditor.tsx` — editor WYSIWYG con preview UNESCO + subida con progreso + encola offline.
- `src/components/RegionMapPicker.tsx` (Leaflet, ya usado en dashboards), `AtAGlanceCounters.tsx`,
  `JoinProjectPanel.tsx` (QR/link).
- `src/api.ts` — métodos `cs*`; `src/App.tsx` — rutas nuevas; `src/db.ts` — tablas Dexie (proyectos,
  requests, outbox); `src/i18n/locales/*.json` — claves nuevas (5 idiomas + RTL).

---

## 3. Pasos en orden (Fase 1 = alcance de esta corrida)

1. **Scaffold** `ckanext-csunesco` instalable (setup.py, plugin.py mínimo, `update_config`, assets/
   templates dirs, `configure` que crea tablas). *Verificable: `pip install -e .` + import del plugin.*
2. **Modelo CKAN + migraciones + CLI**: tablas `cs_*`, auto-heal, `seed-initiatives` (4 groups).
3. **Registro Citizen Scientist sin org** (blueprint colab-style + form + `user_create` + perfil CS).
4. **`cs_project` + request/approve/reject** (actions + auth + validators) — creador→project_admin al aprobar.
5. **Landing pública + listado + páginas de iniciativa** (título, descripción, mapa de región,
   contadores at-a-glance, link/QR de join) con branding UNESCO; tab "Citizen Science" en org/groups.
6. **Panel admin de aprobación** (project-requests + join-requests) `/cs-admin`.
7. **news/events por proyecto**: `cs_content` editable por project admin + API pública `side_effect_free`.
8. **ofform backend**: write-client + `CKAN_API_TOKEN` + outbox + routers `cs_projects`/`cs_content` +
   Programme mirror + asociación obligatoria de registros a proyecto + bridge de identidad JIT.
9. **ofform frontend**: registro, project request, listado, join, panel de aprobación, editor news/events,
   mapa de región, i18n/dark/RTL.
10. **Sync bidireccional mínima**: `ofform→CKAN` (requests/join/news/events/stats/records) y
    `CKAN→ofform` (estado aprobado/rechazado, roles, proyectos disponibles).

**Fase 2 (fuera de alcance ahora):** data viewer/analyzer con roles diferenciales + herramienta AI de
insights + analítica de mapas.

---

## 4. Riesgos / decisiones clave
- **Bridge de identidad ofform↔CKAN (riesgo #1)**: evitar silos y suplantación. Recomendación: CKAN
  autoridad; token CKAN por usuario **server-side** en ofform (mínimo privilegio, nunca al browser);
  provisión JIT del usuario-sombra; ofform firma/pasa el username verificado, CKAN aplica su control de
  acceso normal (nunca confiar en un `user_id` recibido). *Confirmar en el gate.*
- **Offline + uploads**: idempotency key + cola robusta; sync asíncrono con reintentos.
- **Slugs/URLs de proyecto permanentes**: cambios de nombre no rompen QR/links.
- **Seguridad**: sanitizar HTML de news/events (bleach en CKAN, DOMPurify en front); uploads validados
  por extensión+MIME+magic, servidos `Content-Disposition: attachment`, no ejecutables; CSRF en forms;
  errores genéricos en prod (sin enumeración de usuarios/recursos → 403); CORS restrictivo (solo GET
  público desde el origen PWA; todas las escrituras por el backend).
- **Rendimiento**: contadores materializados + update incremental; índices en FKs (`project_id`,
  status) y paginación server-side en paneles; evitar N+1 en listados (joinedload); reusar token CKAN.
- **DB CapRover**: registrar toda columna nueva en `db._SCHEMA_UPGRADES` (si no, se pierde en el volumen).
- **Diseño UNESCO desde tokens/templates compartidos**, no estilos hardcodeados dispersos.
- **No romper regresiones** en ofform: dark mode, i18n RTL, offline-first, flujo de organizaciones.

## 5. Cómo se verificará
- **CKAN plugin**: `.mix/verify.sh` que corra `pip install -e .` + `python -c "import ckanext.csunesco.plugin"`
  + `pytest ckanext/csunesco/tests` (pytest-ckan con DB de test) cuando exista; mientras tanto la
  verificación es **weak** (syntax/import) → se reportará honestamente y se sugerirá el harness de CKAN.
- **ofform backend**: `pytest` (mock del CKAN write-client; tests de outbox/idempotencia/retries y de
  asociación obligatoria a proyecto). **frontend**: `npm test` + `npm run build` (Vite) con rutas nuevas.
- **Criterios de aceptación (e2e)**:
  1. Un Citizen Scientist se registra **sin organización** (en CKAN y desde ofform).
  2. Un PM crea un project-request desde ofform → aparece **pendiente** en CKAN.
  3. Un admin aprueba → se crea proyecto + landing + URL + QR/link y el creador queda **project_admin**.
  4. El proyecto aparece bajo la **iniciativa correcta** y en el tab CS de org/miembros; se lista como water family.
  5. Un usuario entra por QR/link, hace **join**, y el **project admin lo aprueba desde ofform**.
  6. **news/events** creados en ofform aparecen en la **landing CKAN** (sanitizados).
  7. Un registro enviado desde ofform queda **asociado a `cs_project`**; sin proyecto → falla validación.
  8. **Contadores** actualizan citizen scientists/observaciones/sitios/member states.
  9. Landing respeta **diseño UNESCO**, responsive, mapa visible, QR funcional; sin regresiones dark/RTL.
