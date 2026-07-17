# ofform ↔ IHP-WINS (ckanext-csunesco) integration

This document describes how the **CS Toolbox** PWA (`ofform`, FastAPI backend +
React frontend) connects to **IHP-WINS** (CKAN running `ckanext-csunesco`). The
two repositories are wired through this plugin's `csunesco_*` actions.

- **IHP-WINS = CKAN** (source of truth for initiatives, projects, roles, content,
  counters). This plugin exposes the `csunesco_*` API actions.
- **CS Toolbox = ofform** (offline-first operational client). Its FastAPI backend
  proxies every mutation to CKAN; the browser never talks to CKAN's write API.

## 1. Configuration (ofform side)

Two settings in `ofform/backend/app/config.py` drive the connection:

| Setting | Meaning | Default |
| --- | --- | --- |
| `CKAN_API_TOKEN` | A CKAN **API token** for a sysadmin / service user. Used only server-side to authorise `csunesco_*` writes. | `None` (worker stays idle) |
| `CKAN_WRITE_BASE_URL` | The CKAN action API base, i.e. `.../api/3/action`. | `https://ihp-wins.unesco.org/api/3/action` |

Create the token in CKAN under the service user's profile (`API Tokens`), then set
it in ofform's environment / `.env`. Related flags: `CS_SYNC_ENABLED` (master
switch for the outbox worker) and `CS_RECONCILE_INTERVAL_SECONDS`.

The authenticated write client is `ofform/backend/app/ihpwins_write.py`
(`IHPWinsWriteClient`). It sends the token as the **raw** value of the
`Authorization` header (CKAN's convention — **no** `Bearer ` prefix) and redacts
the token from every error it raises, so nothing leaks into the outbox
`last_error`.

## 2. Trusted-proxy security model

- The `CKAN_API_TOKEN` lives **server-side only**. It is never sent to the
  browser and never returned in any API response.
- The React frontend calls the ofform FastAPI backend; the backend is the **only**
  party that holds the token and calls CKAN's write API.
- Reads that don't need a token (public, `side_effect_free` actions) use a
  separate **token-less** read client (`ihpwins.IHPWinsClient`), so the write
  token is never spent on reads.

## 3. Async outbox

Every CS mutation from ofform is enqueued into the `cs_sync_outbox` table
(`ofform/backend/app/services/cs_sync.py`) and pushed to CKAN by a single daemon
worker, so the user response never waits on CKAN. Guarantees: HMAC-SHA256
idempotency keys (unique in DB), atomic row claim, bounded retries. Without a
`CKAN_API_TOKEN` the worker logs once and stays idle (never crashes).

The **one exception** is Citizen-Scientist registration, which is **synchronous
and CKAN-first** (see §5): it must confirm the CKAN account before creating the
local shadow user.

## 4. Endpoint → action mapping

ofform `/cs/*` endpoints and the `csunesco_*` actions they drive:

| ofform endpoint | Path (`ofform/backend/app/routers/`) | Outbox kind | CKAN action |
| --- | --- | --- | --- |
| Register a Citizen Scientist | `POST /cs/register` (`cs_auth.py`, public, no Bearer) | — (synchronous) | `csunesco_register_citizen_scientist` |
| Request a new CS project | `POST /cs/projects/request` (`cs_projects.py`) | `project_request` | `csunesco_project_request_create` |
| List CS projects | `GET /cs/projects` | — | local Programme mirror (no CKAN call) |
| CS project detail | `GET /cs/projects/{id}` | — (read) | `csunesco_content_list` (best-effort counters) |
| Join a project | `POST /cs/projects/{id}/join` | `join_request` | `csunesco_join_request_create` |
| Approve a join | `POST /cs/projects/{id}/join/{user_id}/approve` | `join_approve` | `csunesco_join_approve` |
| Retry failed sync | `POST /cs/projects/{id}/retry-sync` | re-queues `failed` rows | (whatever the requeued rows target) |
| Publish news/event/media | `POST /cs/content` (`cs_content.py`) | `content` | `csunesco_content_create` |
| List project content | `GET /cs/content` | — (read) | `csunesco_content_list` |

The outbox worker's `kind → write-client method` dispatch lives in
`cs_sync._DISPATCH`. The write-client wrappers are in `ihpwins_write.py`.

Public reads used elsewhere in ofform (token-less, via `ihpwins.IHPWinsClient`):
`csunesco_project_show` and `csunesco_content_list`.

### Plugin actions reference

The plugin registers these `csunesco_*` actions (all consumed or available to
ofform):

- **Registration:** `csunesco_register_citizen_scientist` (server-to-server,
  sysadmin-gated).
- **Projects:** `csunesco_project_request_create`, `csunesco_project_approve`,
  `csunesco_project_reject`, `csunesco_project_list`, `csunesco_project_show`,
  `csunesco_project_stats_show`.
- **Members / join:** `csunesco_join_request_create`, `csunesco_join_approve`,
  `csunesco_join_reject`.
- **Content (news/events/media):** `csunesco_content_create`,
  `csunesco_content_update`, `csunesco_content_approve`,
  `csunesco_content_reject`, `csunesco_content_list`, `csunesco_content_show`.
- **Admin panel:** `csunesco_admin_pending_list`, `csunesco_aggregate_stats`.

## 5. Identity model (registration dual-write)

CKAN is the identity authority. `POST /cs/register` is public, synchronous and
**CKAN-first** (`ofform/backend/app/services/cs_registration.py`):

1. If `CKAN_API_TOKEN` is set, ofform first calls
   `csunesco_register_citizen_scientist` (a sysadmin server-to-server action that
   creates the active CKAN account with no organization and flags the CS
   profile). If that write fails, **the local user is not created** — no silos.
2. Then ofform creates the local shadow user and returns
   `ckan_synced=true`.
3. In dev with no token, it creates only the local user and returns
   `ckan_synced=false` (not an error — the token-less dev mode).

CS **projects** are mirrored as ofform `Programme` rows (`kind='cs_project'`), so
join = `MembershipRequest(scope_type=programme)` and the project admin =
`Membership(scope=programme, role=owner)`; `can_decide_request` already authorises
the owner to approve.

## 6. Workflow (from *Citizen Science Project workflow*)

Condensed Toolbox ↔ IHP-WINS steps (full detail in the PDF at the repo root):

| Step | CS Toolbox (ofform) | IHP-WINS (CKAN / this plugin) |
| --- | --- | --- |
| 0 — Preparatory | Registration pages for PM (linked to org) and Citizen Scientists (any individual). | Citizen-Scientist role not linked to an org; CS tab on Orgs/Members and the 4 CS Initiatives. |
| 0 — Registration | Colab-style form integrated into the Toolbox. | Add CS tab to roles in WINS; add org to WINS. |
| 1 — Context & shared vision | PM runs stakeholder meeting, co-develops objectives, optional project document. | Guidance questions, generic workshop programme, standard CS project template. |
| 2 — Project request | PM requests a new CS project (initiative, countries, biosphere reserve, region/map, name, description, URL, optional document). | Integrated form for new CS projects under the initiative. |
| 2 — Approval | — | IHP-WINS admin approves → PM gets admin role, project URL under its initiative, landing page with map + at-a-glance counters + join link/QR. |
| 3 — Survey setup | PM selects/creates a survey for the project. | — |
| 4 — Data collection | PM trains + invites Citizen Scientists (project page, link or QR); data collected in the Toolbox. | Colab-integrated registration; counters update on new data; public data on the landing page, private on the internal project page. |
| 5 — Knowledge generation | PM + scientists analyse via the data viewer/analyzer; AI tailored report. | Data analyzer with differential access roles; AI insights (future). |
| 6 — Public landing for outreach | PM reviews the landing page and adds news/events/videos/photos. | Landing page made editable for the PM; integrated form to add news/events/media. |

> Steps 5–6 (data analyzer + AI insights) are **Phase 2**, out of scope for the
> current plugin surface.

## 7. Future hardening

The current model uses a single service `CKAN_API_TOKEN`. A planned hardening is
**HMAC-signed per-user tokens**: ofform would forward the verified username and
CKAN would apply its own access control per user (never trusting a client-supplied
`user_id`), reducing the blast radius of a single shared token.
