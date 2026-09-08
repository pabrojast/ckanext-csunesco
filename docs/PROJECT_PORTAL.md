# CS Toolbox project publications

Only projects activated by `app_project_id` use this contract. Accounts,
registration, project and membership decisions, and the global/site/initiative
editors stay in CKAN. The app owns project drafts; CKAN owns the published copy
and moderation decisions. A revision freezes project fields, blocks, structure,
content and media together. Observation refreshes never change that revision.

## Integration API

All actions below require a sysadmin service token. Set
`ckanext.csunesco.portal_service_user` to pin its identity. Authors must be
active CKAN users with current project capabilities, and cannot be the transport
account. The backend must authorize its own user before delegating to CKAN.

- `csunesco_project_portal_capabilities`: schema version, project block registry,
  typed editing controls, selectable public fields and optional project data sources.
- `csunesco_project_portal_apply`: immutable submission, staged draft or withdrawal.
- `csunesco_project_portal_status`: authoritative decision and published revision.
- `csunesco_project_portal_export`: portable project-only migration export,
  including protected draft/publication and content/data references. No user profiles.
- `csunesco_project_portal_preview`: private 300-second ticket for the actual CKAN
  renderer, without a CKAN session or write authority.

Apply and preview accept:

```json
{
  "schema_version": 1,
  "project": {"id": "ckan-project-uuid"},
  "app_project_id": 42,
  "revision": 1,
  "checksum": "sha256-of-canonical-payload",
  "actor": {"ckan_id": "ckan-user-uuid", "username": "project-manager"},
  "intent": "submit",
  "payload": {
    "project": {"title": "River monitoring"},
    "structure": {},
    "workplan": [],
    "blocks": [],
    "contents": [],
    "media": []
  }
}
```

Canonical JSON uses sorted keys, UTF-8, no ASCII escapes and separators `,`/`:`.
`blocks` is a list, not a wrapper object. Content entries carry stable
`app_content_id` strings and optional existing `ckan_id` for migration. Same
revision/checksum/intent retries are idempotent; revisions cannot move backwards.
Unknown blocks and private field selections fail validation instead of vanishing.
Publication approval requires the exact CKAN `draft_hash` over the full candidate.
Trusted-project rules use the real author, never the service token.

Editor image references use `asset:<id>`. The accompanying `media` entry carries
that `id` and a `fetch_url` under `/internal/ckan/project-assets/<id>` on the
configured app API origin. CKAN fetches it with callback authorization and copies
it into private persistent storage before exposing the approved content hash.
External images are ingested by the app; expiring browser image URLs are not the
publication contract. Optional `layout` is preserved, and `capabilities_version`
accepts the supported registry version only.

The public project-request form keeps its existing first six steps. Step seven
contains logo and header images with upload/URL and pan/zoom/reset controls. Its
stable intake callback creates the app draft; later project editing opens
`/projects/<app_project_id>/space/portal`.

## Storage, refresh and privacy

Configure persistent `portal_storage_path` or `ckan.storage_path`. Snapshot files
are private: gated routes and current DB decisions control public access. JSON and
CSV share one observation bundle; existing CSV/GeoJSON/chart/map/fields/series/chat
URLs consume that bundle. No CMS draft is published by periodic refresh. The worker reads deduplicated
project statistics from `/internal/ckan/projects/<app_id>/stats`; observations
are counted from the same CKAN-approved local row bundles, and Member States
remain derived from the approved project countries. Provider video embeds stay
validated YouTube/Vimeo links; uploaded video binaries are persisted normally.

`portal_sync_enabled=true` starts a 300-second repair worker. Shared-storage flock
prevents concurrent repair, and atomic writes keep the last valid observation
bundle on transient failure. `ckan csunesco portal-refresh` runs one repair pass
for an external scheduler. Workers require shared storage across CKAN processes.

Withdrawal revokes sources and dataset visibility and clears active publication
state. The app must show withdrawal as pending until CKAN acknowledges completion.
Public asset URLs use no-store and consult the current approved manifest; staged
media files are inaccessible. Persistent copies are retained privately for audit. The `withdrawn` status flag
remains true through replacement drafts/rejections until a new approval commits;
visitors cannot find the withdrawn project through show/list APIs either.

When a migrated snapshot copies a legacy local `/uploads/csunesco/` file, CKAN
registers its old URL and checks it against current publication state. Withdrawal
protects project-only originals without deleting them. Published references from
home, initiatives or other projects keep shared originals available. These
checks require requests for that upload path to reach CKAN; a reverse proxy must
not serve protected legacy files directly from storage. URLs on external hosts
and copies already downloaded outside CKAN cannot be revoked by this plugin.

## App callbacks and preview

`ofform_base_url` pins the backend; `ofform_callback_token` is sent only as
`Authorization: Bearer ...`, with redirects disabled.

- `POST /internal/ckan/project-requests`: stable `ckan-project:<id>` idempotency key,
  initial project fields and real actor. A failed callback leaves a durable intake
  for repair rather than creating a duplicate project request.
- `POST /internal/ckan/project-reviews`: revision/checksum and decision. Failure
  never rolls back CKAN moderation; polling status and the repair worker recover.
- `GET /internal/ckan/forms/<id>/snapshot`: one coherent dashboard+CSV bundle;
  public/published only. An explicit 404 withdraws the CKAN data source.

`portal_preview_origin` permits exactly the app origin to frame the ticket route.
Responses are no-store/noindex with no-referrer and disallow form submissions.
New CKAN intake uploads are moved out of public FileStore storage and served by
project-scoped `intake-media` routes. Draft access needs the owner/reviewer or
callback bearer token. The app imports these binaries into its own asset store.
Observation attachment bundles use `/internal/ckan/submission-files/<id>` and
are copied with their observation rows; their public hash routes consult the
current approved source and snapshot manifest on every request.
The ticket is project/revision-scoped and author capabilities are rechecked on use.

## Activation

Import/export and compare each project first. Preserve CKAN IDs and URLs; do not
replace home/initiative content or identity tables. App edits win for app-owned
fields only after reviewing migration conflicts. The first accepted revision
activates the project's app editor links and blocks legacy project edit writes.
No database table deletion, push or deployment is part of this change.
