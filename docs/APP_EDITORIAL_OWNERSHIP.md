# Citizen Science editorial ownership

The app owns project details, structure, content, images, page sections and
drafts. Home and initiative pages use the full editor at
`/admin/portal-pages`; project managers use `/projects/{id}/space`.
CKAN retains accounts, organizations, institutional permissions, moderation,
and public rendering. Project content and project pages keep their existing
review queues. Authorized Home/initiative administrators publish directly
through the app, with CKAN checking the real author's permissions.

## Preview

The app authorizes each preview with its current project/initiative permissions
and a five-minute signed grant bound to the scope, page, revision and checksum.
CKAN verifies the grant through `/internal/ckan/preview-access`, including when
the ticket or its media are viewed. The app manager does not need a CKAN account
to preview. Publication still requires an active authorized CKAN author; a
transport account cannot act as that author. Preview cannot publish a draft.

The parent and iframe exchange an origin-checked, source-checked, correlated
readiness message. Tickets renew before the supplied expiry. Missing images
become placeholders and section warnings in previews; publishing requires
valid images. Ordinary document hyperlinks are not downloaded as images.

## Bridge and migration

The existing project bridge remains available. New service-only actions are:

- `csunesco_editorial_page_export`: one Home or initiative draft/public version.
- `csunesco_editorial_page_apply`: publish a checked immutable revision directly.
- `csunesco_editorial_page_preview`: render an app-authorized private ticket.
- `csunesco_editorial_export_all`: projects, content, scoped authors and global pages.
- `csunesco_editorial_link_projects`: map imported app IDs to existing CKAN IDs.

The app's `scripts/migrate_portal_editors.py` consumes the full export. Its
default dry-run uses a SQLite copy. `--apply` first backs up the database and
retains source exports and conflict reports. Existing app values/drafts win;
published CKAN versions, IDs, asset URLs and custom sections are retained.
Repeating the import does not overwrite app work. Project links are registered
only after a successful import.

After reviewing the import, set `ckanext.csunesco.editorial_owner = app`
(`CKANEXT__CSUNESCO__EDITORIAL_OWNER=app` in the container environment).
Legacy editorial GETs then link to the app; legacy form POSTs and direct action
writes are rejected, including human sysadmins. Internal bridge contexts retain
only their existing publishing/moderation permissions. Organization content
editing is unaffected.

Do not enable the cutover before importing and verifying every project/page.
Use dev Kubernetes context `default`; the context named `ckan` is production.
Build the plugin over the exact current dev image to retain other extensions.
