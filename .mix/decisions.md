<!-- 2026-07-16 17:34:54 -->
## Proyecto ckanext-csunesco + integración ofform (Citizen Science / IHP-WINS · UNESCO)

Decisiones de arquitectura estables (mantener en futuras corridas):
- **CKAN/IHP-WINS es la fuente de verdad**; la PWA `ofform` (/home/pabrojast/ofform) es cliente offline-first.
  Toda mutación desde ofform pasa por su backend FastAPI (proxy server-to-side con `CKAN_API_TOKEN`); el browser
  nunca ve el token.
- **`ckanext-csunesco` es plugin propio y auto-contenido** que COPIA el patrón *water family* de `ckanext-pages`
  (modelo tipo Page + page_type/extras, iniciativas y member-states como CKAN groups, landing, admin dashboard,
  API pública `side_effect_free`, branding UNESCO #0072BC), SIN depender de ckanext-pages.
- **Registro Citizen Scientist sin organización** = blueprint estilo `ckanext-colab` (user_create + captura de
  ValidationError → error genérico) con la validación de org removida; perfil en tabla `cs_citizen_scientist`.
- **Menos tablas**: project-request = `cs_project.status` (pending/approved/rejected); join-request =
  `cs_project_member.status`; news/events = `cs_content` (content_type cs-news/cs-event). Creador aprobado = project_admin.
- **En ofform: CS Project = un `Programme` espejo** (kind='cs_project', ckan_slug, ckan_sync_status). Join reusa
  `MembershipRequest(scope_type=programme)`; project admin = `Membership(owner)`; `Submission.programme_id` (aditivo)
  asocia el registro al proyecto. Escrituras vía **outbox idempotente** (`CsSyncOutbox`, HMAC key con SECRET_KEY,
  worker daemon con claim atómico, token redactado en logs/errores).
- **CKAN NO está instalado localmente** → la verificación del plugin es sintáctica fuerte vía `.mix/verify.sh`
  (compileall + AST del entry point/acciones + estructura). ofform SÍ verifica fuerte (pytest backend, tsc+vite+vitest front).
- Bridge de identidad ofform↔CKAN (JIT shadow user) quedó PENDIENTE y necesita decisión del usuario (riesgo #1).
- Contadores at-a-glance pre-agregados (tabla `cs_project_stats`, update atómico), nunca agregación pesada en cada carga.

<!-- 2026-07-16 19:23:19 -->
## Proyecto ckanext-csunesco + integración ofform (Citizen Science / IHP-WINS · UNESCO)

Decisiones de arquitectura estables (mantener en futuras corridas):
- **CKAN/IHP-WINS es la fuente de verdad**; la PWA `ofform` (/home/pabrojast/ofform) es cliente offline-first.
  Toda mutación desde ofform pasa por su backend FastAPI (proxy con `CKAN_API_TOKEN` server-side, raw en Authorization,
  base `.../api/3/action`); el browser nunca ve el token.
- **`ckanext-csunesco` es plugin propio y auto-contenido** que copia el patrón *water family* de `ckanext-pages`
  (modelo tipo Page + page_type/extras, iniciativas y member-states como CKAN groups, landing, admin dashboard,
  API pública `side_effect_free`, branding UNESCO #0072BC), SIN depender de ckanext-pages.
- **CKAN 2.10 / Python 3.10.** El plugin CARGA en CKAN real (verificado con docker ckan/ckan-dev:2.10 +
  Dockerfile.test + scripts/run-ckan-tests.sh). IMPORTANTE: `ckanext/__init__.py` DEBE usar
  `pkg_resources.declare_namespace` (no pkgutil.extend_path) porque setup.py declara namespace_packages=['ckanext'];
  si no, `pip install -e .` falla. db.py usa classic Table+mapper (meta.mapper existe en 2.10/SQLAlchemy 1.4).
- **Registro Citizen Scientist sin organización** = blueprint web + acción API `csunesco_register_citizen_scientist`
  (idempotente, auth SOLO sysadmin). Decisión de identidad: **dual-write CKAN-first síncrono** (crea user CKAN +
  User local ofform con hash PBKDF2); password nunca persistido; `POST /cs/register` público rate-limited. HMAC per-user = futuro.
- **Menos tablas**: project-request=`cs_project.status`; join-request=`cs_project_member.status`; news/events=`cs_content`
  (content_type). Creador aprobado = project_admin. Contadores atómicos en `cs_project_stats`.
- **En ofform: CS Project = un `Programme` espejo** (kind='cs_project', ckan_slug, ckan_sync_status, region_geojson).
  Join reusa `MembershipRequest(scope_type=programme)`; project admin = `Membership(owner)`; `Submission.programme_id`
  asocia el registro al proyecto. Escrituras vía **outbox idempotente** (`CsSyncOutbox`, HMAC key con SECRET_KEY, worker
  daemon con claim atómico, token redactado). **Reconcile CKAN->ofform** (cs_reconcile.py, read token-less, no pisa outbox vivo)
  + `retry-sync` owner-gated.
- Toda columna nueva en ofform DEBE registrarse en `app/db._SCHEMA_UPGRADES` (SQLite CapRover /app/data).
- Verificación: plugin = sintáctica (.mix/verify.sh) + CONDUCTUAL en CKAN real (79 tests). ofform = pytest backend +
  tsc/vite/vitest frontend. Falta solo el test HTTP end-to-end con stack CKAN completo (docker-compose.dev.yml listo).

<!-- 2026-07-25 (revisión: caché del proxy ofform + registro de auth) -->
## Decisión estable — la caché del proxy protege de verdad al worker
- **El tope `MAX_CACHE_ENTRIES` no se aplicaba.** `_cache_set` sólo purgaba las entradas EXPIRADAS: con más formularios calientes que el tope, nada estaba expirado y la caché crecía sin límite. Con el propio dato del comentario (~1,6 MB por payload parseado), 100 formularios activos son ~160 MB residentes por worker — justo la memoria que ese tope existe para proteger. Ahora, tras purgar lo vencido, se desaloja por vencimiento más próximo (TTL uniforme ⇒ orden de inserción) hasta volver al techo.
- **Los fallos no se recordaban.** Sólo se cacheaban los éxitos, así que un upstream caído —o un formulario archivado/despublicado después de crear su dataset— convertía cada bloque de gráfico, cada mapa de observaciones y cada enlace CSV en un intento nuevo con `REQUEST_TIMEOUT = 15 s`. Los gráficos son AJAX: una landing con varios bloques abre varias peticiones a la vez, cada una reteniendo un worker de CKAN 15 s, y sin ningún backoff porque se repetía en cada visita. Nueva caché negativa con `FAILURE_CACHE_TTL = 30 s`: el primer fallo sale a la red, los siguientes fallan rápido, y la recuperación sigue siendo de medio minuto.
- **El probe del panel de revisión NO alimenta la caché negativa** (`PROBE_TIMEOUT = 6 s`): si falla por lentitud no debe marcar como caído un upstream que a 15 s sí respondería. Sólo el camino normal recuerda el fallo.
- **Sigue habiendo estampida en frío** (N peticiones simultáneas fallan la caché y salen todas a la red). No se toca: acotarlo pide un lock por clave y el riesgo real —el fallo repetido— ya está cubierto.
- **`csunesco_project_page_submit` era la única acción sin función de auth propia.** No abría ningún agujero (la acción llama a `check_access('..._page_update')` y revalida contra el proyecto resuelto), pero el día que alguien la nombrase en un `check_access`/`h.check_access` CKAN lanzaría `ValueError: Authorization function not found` — un 500, no un 403. Se añade el alias explícito.
- **Guardas nuevas contra deriva**, que es lo que se pudre en silencio en este repo: acción⇄auth en ambos sentidos (una auth huérfana suele ser un typo que deja la acción real con el default), y `_AUTO_HEAL_COLUMNS` ⇄ modelo (una columna curada que el modelo ya no declara sólo deja un `log.error` genérico).
- 319 tests en CKAN 2.10 real (antes 310). Verificado que los nuevos fallan sin el arreglo: 2 por comportamiento (tope de caché, caché negativa) y 1 por el registro de auth.
