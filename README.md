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
- **Project page builder** — a project admin composes the whole landing body
  from ordered **blocks** (`cs_project_page`): the standard sections become
  built-in blocks they can reorder or hide, and on top of those they add rich
  text, **charts of the app's observations**, counters, image galleries, video,
  observation maps, Terria scenes, content and dataset listings, and callouts.
  The editor keeps a **draft** separate from what the public sees; publishing
  queues the page for review (a *trusted* project publishes directly, unless the
  page embeds external media). The registry in `logic/blocks.py` is the single
  source of truth for block types — every layer derives from it.
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
- **Ask the data** — a page-builder block where a signed-in visitor asks about
  a project's observations in plain language ("what is the average pH at each
  site?") and gets a chart or a table back. The model **never produces a
  number**: it picks one call from a closed, validated vocabulary of four
  tools, the server runs it through the same `logic/aggregate.py` that draws
  the chart blocks, and only then does the model write the sentence around the
  result. See [Ask the data](#ask-the-data).

## Endpoints & permissions

Every HTTP view lives under the blueprint prefix **`/citizen-science`** (so the
self-registration page is `/citizen-science/register-citizen`, **not**
`/register-citizen`). Authorization roles:

- **public** — anonymous allowed. Read views only ever expose *approved* rows to
  non-privileged callers; the action layer does the filtering.
- **authenticated** — any logged-in CKAN user.
- **project admin** (PM) — an active `admin` member of the *target* project.
- **initiative admin** (ADM) — an active `admin`-capacity member of the target
  project's initiative group (`be-resilient`, `islandwatch`, `riverwatch`,
  `c4water`). Sysadmins grant it from the group's standard *Members* page. An
  ADM can moderate the projects, content and data sources of *their* initiative
  and do everything a project admin can within it.
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
| POST | `/citizen-science/data/<id>/chat` | One plain-language question about an **approved** data source (JSON in/out, never cached, per-user daily quota) | authenticated |
| GET·POST | `/citizen-science/register-citizen` | Citizen Scientist self-registration (account created **pending** until email is verified) | public — gated by `ckan.auth.create_user_via_web`; reuses core `user_create` auth |
| GET | `/citizen-science/verify/<token>` | Activate a pending account via its emailed link | public (single-use token) |
| GET·POST | `/citizen-science/verify/resend` | Request a fresh verification link | public (generic response) |
| GET·POST | `/citizen-science/project/new` | Propose a project (request) | authenticated |
| POST | `/citizen-science/project/<slug>/join` | Request to join a project | authenticated |
| GET·POST | `/citizen-science/project/<slug>/content/new` | Add news/event to a project | sysadmin, initiative admin **or** that project's admin |
| GET·POST | `/citizen-science/content/<id>/edit` | Edit an existing content item | sysadmin, initiative admin **or** project admin |
| GET·POST | `/citizen-science/project/<slug>/data/connect` | Connect a CS Toolbox form's data (request, lands pending) | sysadmin, initiative admin **or** that project's admin |
| GET | `/citizen-science/admin` | Approval panel (pending projects/joins/content/data) | sysadmin, any initiative admin **or** any project admin |
| POST | `/citizen-science/admin/project/<id>/approve` · `/reject` | Moderate a project request | sysadmin **or** the project's initiative admin |
| POST | `/citizen-science/admin/join/<project_id>/<user_id>/approve` · `/reject` | Moderate a join request | sysadmin, initiative admin **or** project admin |
| POST | `/citizen-science/admin/content/<id>/approve` · `/reject` | Moderate a content item | sysadmin **or** the content's initiative admin |
| POST | `/citizen-science/admin/data/<id>/approve` · `/reject` | Moderate a data source (approve creates the CKAN dataset) | sysadmin **or** the source's initiative admin (org override is sysadmin-only) |
| POST | `/citizen-science/admin/content/bulk-approve` | Approve a checkbox selection of content rows (≤100, per-row auth) | sysadmin **or** initiative admin (per row) |
| POST | `/citizen-science/admin/data/bulk-approve` | Approve a checkbox selection of data sources (≤20, per-row auth; suggested/default org) | sysadmin **or** initiative admin (per row) |
| GET·POST | `/citizen-science/project/<slug>/page` | Project-page editor (one endpoint for every block operation) | sysadmin, initiative admin **or** that project's admin |
| GET | `/citizen-science/project/<slug>/page/preview` | Render the unpublished draft through the public template | same as the editor |
| GET | `/citizen-science/data/<id>/series` | Aggregated chart series for an **approved** data source (TTL-cached) | public |
| GET | `/citizen-science/data/<id>/fields` | Chartable field list of an **approved** data source | public |
| POST | `/citizen-science/admin/page/<project_id>/approve` · `/reject` | Moderate a project page (approve requires the `draft_hash` shown in the panel) | sysadmin **or** the project's initiative admin |
| POST | `/citizen-science/project/<slug>/trusted` | Toggle the project's trusted flag | sysadmin |

All POST forms carry CKAN's CSRF token (`h.csrf_input()`); mutating routes use
POST-redirect-GET. **Note**: CKAN only *validates* extension tokens when
`ckan.csrf_protection.ignore_extensions = false` — set it on the portal so the
moderation POSTs (approve/reject/trusted/bulk) are actually CSRF-checked.

### API actions (`/api/3/action/<name>`)

Read actions are `side_effect_free` (callable via GET). Registration validation
is deliberately generic (no per-field errors) so the endpoint cannot be used to
enumerate accounts.

| Action | Access |
| --- | --- |
| `csunesco_project_list`, `csunesco_project_show`, `csunesco_project_stats_show`, `csunesco_aggregate_stats` | public (read; approved only for non-sysadmins) |
| `csunesco_content_list`, `csunesco_content_show` | public (read; approved only — **except** a manager reading their own project, who also sees its pending/rejected rows) |
| `csunesco_project_request_create` | authenticated |
| `csunesco_join_request_create` | authenticated |
| `csunesco_my_projects` | authenticated (the projects **you** administer, whatever your role) |
| `csunesco_data_chat` | authenticated — one plain-language question about an **approved** data source; per-user daily quota |
| `csunesco_content_create`, `csunesco_content_update` | sysadmin, initiative admin **or** project admin (an explicit `source: 'app'` forces `pending` even for sysadmins) |
| `csunesco_data_source_list`, `csunesco_data_source_show` | public (read; approved only for non-privileged callers) |
| `csunesco_data_source_series`, `csunesco_data_source_fields` | public (read; **approved** sources only — aggregated server-side) |
| `csunesco_project_page_show` | public (read; the draft only for a manager/reviewer) |
| `csunesco_project_page_update`, `csunesco_project_page_submit` | sysadmin, initiative admin **or** project admin |
| `csunesco_project_page_approve`, `csunesco_project_page_reject` | sysadmin **or** the project's initiative admin |
| `csunesco_data_source_create` | sysadmin, initiative admin **or** project admin — **always** creates `pending`; idempotent per `(project, form)` |
| `csunesco_admin_pending_list` | sysadmin, any initiative admin **or** any project admin |
| `csunesco_project_approve`, `csunesco_project_reject` | sysadmin **or** the project's initiative admin |
| `csunesco_content_approve`, `csunesco_content_reject` | sysadmin **or** the content's initiative admin |
| `csunesco_data_source_approve`, `csunesco_data_source_reject` | sysadmin **or** the source's initiative admin (approve creates/refreshes the CKAN dataset; `owner_org` override is sysadmin-only) |
| `csunesco_join_approve`, `csunesco_join_reject` | sysadmin, initiative admin **or** project admin (decision + reviewer audited) |
| `csunesco_project_trusted_set` | sysadmin — toggles unreviewed news/events for one project |
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
| `ckanext.csunesco.llm_api_key` | *(unset — the data chat is disabled)* | API key for the chat-completions provider that powers the **Ask the data** block. Unset ⇒ the block renders with a "not switched on" notice; nothing else on the page changes. This is the extension's only outbound credential. |
| `ckanext.csunesco.llm_base_url` | `https://api.deepseek.com` | Base URL of an **OpenAI-compatible** `/chat/completions` endpoint (the portal already runs one for `ckanext-terriassistant`). |
| `ckanext.csunesco.llm_model` | `deepseek-chat` | Model id to send. It must support tool / function calling — the whole design rests on it. |
| `ckanext.csunesco.llm_daily_quota` | `40` | Questions one signed-in user may ask per UTC day, across every project. `0` disables the cap. |
| `ckanext.csunesco.llm_timeout` | `45` | Seconds to wait for the provider, per call. One answer makes at most two calls. |
| `ckanext.data_stories.enabled` | — | Not ours (ckanext-pages), but when true the project landing shows a "Create a data story" entry point. |

Terria embeds additionally require the Terria host to allow framing (no
`X-Frame-Options: DENY`); otherwise the map page falls back to a plain link.

### Sysadmin review runbook

Everything users publish flows through **one** approval panel at
`/citizen-science/admin`. The navbar shows a **Review** link to anyone who can
review — gated on the panel's own auth, not on the queue being non-empty, so
the panel never disappears once a reviewer clears their work — carrying an
**n** badge when something is pending (the badge and the tab counters share one
query, so they never disagree). Review can be **delegated per initiative**:
grant a user `admin` capacity on an initiative group (its *Members* page) and
they become that initiative's ADM — their panel/badge scope covers only their
initiatives.

Above the tabs the panel shows **Your projects**: the projects the acting user
administers, *including* pending and rejected ones. It is the only page a
manager can always reach, and it answers the one question the rest of the UI
could not — the public listing filters by initiative and free text only, and
approval sends no notification, so a manager otherwise had to remember their
project's title and search for it. Initiative admins additionally get links to
their initiative pages; sysadmins, to the full listing.

Four tabs:

1. **Project requests** (sysadmin or initiative admin) — approve turns the
   requester into the project's admin and seeds its counters.
2. **Join requests** (sysadmin, initiative admin or project admin).
3. **Content to review** — news, events, publications and maps; portal-authored
   sysadmin content publishes directly, everything else (including *all*
   app-authored content) waits here. The author sees the outcome on their own
   project page: `csunesco_content_list` scopes like `csunesco_data_source_list`,
   so a manager listing **their** project also gets its pending and rejected
   rows, badged, with the rejection reason. (The public indexes pass no project
   filter, so they stay approved-only.) Editing a rejected item sends it back
   through review, which closes the loop.
4. **Data to review** (sysadmin or initiative admin) — each row shows a live probe of the form's
   public data (reachable? observations, geolocated count, date range) and an
   "Open in the app" link (`ofform_app_url`), so nothing is approved blind.
   Approving creates/refreshes a live CKAN
   dataset fed by the CS Toolbox app (CSV + GeoJSON proxy resources). The
   approve form includes an **organization picker** preselected with the
   app-suggested org (when it exists on the portal) or the configured
   default — the reviewer can change it before approving (the picker is
   sysadmin-only; an initiative admin's approval always uses the
   suggested/default org). If
   dataset creation fails (e.g. missing `dataset_owner_org` or portal-schema
   fields), the row **stays pending** and can be retried after fixing config.
   Data truncates at ofform's 20 000-row export cap. If a form owner later
   reverts the form to private in the app, the proxy starts returning 502 for
   that source.

5. **Pages to review** (sysadmin or initiative admin) — project pages a manager
   published. Each row links to a **Preview** of that exact draft and carries
   its `draft_hash`: if the manager edits the page after the panel was rendered,
   approving is refused rather than publishing a version nobody read. (Editing a
   pending page also withdraws it from the queue, so this is the narrow race,
   not the common path.) Rejecting sends it back with a reason and leaves the
   currently published version untouched — and that reason stays visible in the
   editor until the page is published again, not just until the author's next
   save. Each row shows who submitted it, whether it is a first publication or
   an update, and which block types put it in the queue.

### Project pages (block builder)

The landing body is an ordered list of blocks stored per project in
`cs_project_page` as two JSON documents: `draft_json` (what the manager edits)
and `published_json` (what the public sees). A project that has never published
a page renders `blocks.default_blocks()` — exactly the section order the landing
had before it became block-driven, so there is **one** rendering path rather than
a default template plus a custom one.

- **Block types** live in `logic/blocks.py`, the single registry every layer
  reads (renderer, editor, validator). Ten author blocks — text, chart, counters,
  images, video, observation map, map viewer, news/events, datasets, highlight —
  plus six built-in wrappers for the standard sections, which can be reordered
  and hidden but never deleted.
- **The JS-free path is the primary path.** Add / move / delete / hide / save /
  publish are ordinary submit buttons carrying `op=…` to one endpoint; the
  enhanced editor (formatting toolbar, chart field picker) posts to the same
  place. Nothing needs JavaScript to work. The op is carried by the pressed
  button alone — a script-written fallback uses a *different* name (`op_js`,
  resolved by `blocks.choose_op`), because sharing one name makes the server's
  answer depend on DOM order.
- **Blocks are collapsed** (`<details>`/`<summary>`), with a jump list above
  them: 17 blocks is already ~150 form controls and the cap is 60. Collapsed
  fields still submit — the form is built from the DOM tree, not from what is
  painted — which every operation relies on, since each re-parses the whole
  list. The form is `novalidate` for the same reason: the browser cannot
  validate a control it cannot show, and would otherwise refuse to submit at
  all.
- **Nothing the author typed disappears in silence.** `normalize_blocks` takes
  an optional `DropReport`; whatever it discards (a non-https image URL, a
  dataset reference with spaces, a chart field name that is not a column) is
  reported back beside the field that produced it. The report never changes
  what is stored.
- **Charts are aggregated server-side.** `csunesco_data_source_series` buckets
  observations by an auto-chosen period, caps the series at 8 and rounds the
  values, turning a ~1.6 MB dashboard payload into ~3 KB of dense arrays. The
  labels are ready-made period keys, so no Chart.js date adapter is needed.
  Chart.js 4.5.1 is **vendored**, not loaded from a CDN, and only shipped to
  pages that actually chart something.
- **Trust is not a blanket bypass.** A trusted project's page publishes
  immediately only while it embeds nothing from an origin we do not control
  (images, video, Terria) — mirroring why `content_initial_status` keeps
  publications and maps out of the trusted fast path.
- **Nothing stored is trusted at render time.** A block's `data_source_id` is
  re-checked against *this* project's approved set on every render, and a Terria
  URL is re-validated against the configured allowlist, so a source rejected
  later (or JSON copied from another project) simply renders nothing.

### Trusted projects & bulk review (P2)

- **Trusted flag** (sysadmin-only, toggled from the project landing page):
  a trusted project's **news/events publish without review** — on both the
  portal and the app surface. Publications, maps and data sources always
  queue. Trust supersedes rejection for news/events: editing a rejected item
  in a trusted project republishes it without review (same power as creating
  a new one). `csunesco_project_trusted_set` is the API lever.
- **Bulk approve** in the content tab (≤100 per request) and in the data tab
  (≤20 — each approval creates a dataset): checkbox selection + "Approve
  selected"; every row re-checks authorization individually and failures
  never abort the batch. Bulk data approvals use each row's suggested org
  (or the configured default); unresolvable rows stay pending.
- Join decisions now record their reviewer (`cs_project_member.reviewed_by`
  / `reviewed_at`, auto-healed columns).

### Ask the data

The *"Knowledge generation"* step of the Citizen Science workflow
(`docs/OFFORM_INTEGRATION.md` §6): a reader who can see the observation map but
cannot open a spreadsheet still gets to ask "in which months did conductivity
rise at Río Claro?".

It is a page-builder block (`data_chat`, one per page). A signed-in visitor
types a question; the answer arrives as a **computed result plus a sentence
about it**, in that order on screen.

**The model never produces a number.** That is the whole design, and it is what
the standard failure of these interfaces looks like when you skip it — fluent
prose with an invented figure inside. Instead:

1. The columns the model may refer to come from `csunesco_data_source_fields`,
   the same introspection the chart editor's field picker uses.
2. The model answers with **one call** from a closed vocabulary of four tools
   (`series`, `stat`, `top_categories`, `cannot_answer`), validated against
   those columns. An invented column name is rejected with a message naming the
   real ones and retried **once**; a second failure becomes a refusal.
3. The server runs the call through `logic/aggregate.py` — the code that draws
   the chart blocks. So an answer and a chart asking the same thing agree by
   construction.
4. Only then does the model write prose, with the computed result in front of
   it and **no tools available**, so it cannot go and compute something else.

A refusal or an empty result short-circuits before step 4: there is nothing to
narrate, and paying a provider to dress up "no data" is how these panels end up
sounding confident about nothing.

What the reader sees with every answer: the question restated in their own
terms ("Electrical conductivity (µS/cm) · Site"), the chart or table, and the
line that makes it checkable — *"Calculated from 412 of 1605 observations,
2024-03-02 to 2026-06-11"* — plus a CSV link. Starter chips name fields that
actually hold data, so a suggested question is never one the panel then refuses.

**Cost and access.** Asking requires a CKAN account (the data itself stays
public — the *asking* is what is gated) and is capped per user per day
(`llm_daily_quota`). Answers are never cached. Without `llm_api_key` the block
renders a "not switched on" notice and the rest of the page is unaffected.

**What leaves the portal.** Only the question, the conversation so far, the
**column profile** (names, labels, units, row counts, date span) and the
**already-aggregated result**. Raw observation rows are never sent to the
provider. Note this differs from the CS Toolbox's own `/ai/dashboard-chat`,
which does send a 120-row sample.

Related module map: `logic/chat.py` (pure — tools, validation, prompt, card),
`logic/llm.py` (the one outbound call), `logic/action/chat.py` (the loop),
`assets/js/cs-data-chat.js` (the panel).

### Next stages (agreed, not yet built)

- Email notification / daily digest to sysadmins when items land in the
  review queue (SMTP is already configured on the portal).
- **Ask the data**: stream the answer in two phases (chart first at ~3 s, prose
  after) if the current single round trip tests as too slow, and let the CS
  Toolbox move its own chat onto this tool contract so there is one
  implementation.
- Auto-enqueue the data-source request when approving an app-originated
  project that already has published forms.
- Image **uploads** for the gallery block (today its URLs are https links, the
  same convention as the `media` field on content).

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
ckan -c /etc/ckan/default/ckan.ini csunesco stats-refresh    # observation counters (cron-able)
```

**"Sampling points"** counts DISTINCT GPS locations rounded to ~11 m, not named
sites: a form whose site column holds 26 names can easily have 450 coordinates.
It is labelled that way so the counter cannot contradict a chart legend on the
same page.

**At-a-Glance counters.** Observations and monitored sites are recomputed
from the connected (approved) app data sources: automatically on data-source
approval and on every landing-page map view, and in bulk via `csunesco
stats-refresh` (schedule it if you want them fresh without traffic). Citizen
scientists = distinct registered profiles ∪ active members of approved
projects; member states = distinct countries declared across approved
projects. Outages keep the last stored values (never zeroed).

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
