# Independent Toolbox content reviews

The Toolbox submits news, events, publications and maps separately from the
project page. The service actions are `csunesco_project_content_apply` and
`csunesco_project_content_status`; both require the configured transport user.
The effective author must be an active project manager and cannot be the
transport account.

Review records appear in the existing **Citizen Science admin → Content** queue.
Each has a stable Toolbox content ID, revision and checksum. Approved records
update a separate public row with a stable ID and URL, preserving the previous
public version while changes await review. Internal review records never have
public approval status. Project-row locks serialize concurrent deliveries and
moderation. Withdrawals supersede older pending reviews and are idempotent.

Page schema 2 includes `content_review_version: 2`, preventing aggregate page
approval from publishing content. During migration,
`ckanext.csunesco.independent_content_reviews=true` also disables legacy
aggregate publication. Historical review records are included separately in
service exports. Existing page and content URLs are preserved.

Required configuration for the dev bridge:

```ini
ckanext.csunesco.portal_preview_origin = https://citizenscience.dev-wins.com
ckanext.csunesco.portal_service_user = cs-toolbox-transport-dev
ckanext.csunesco.portal_sync_enabled = true
ckanext.csunesco.independent_content_reviews = true
ckanext.csunesco.ofform_base_url = https://citizenscience-api.dev-wins.com
```

Supply `ckanext.csunesco.ofform_callback_token` through a Kubernetes Secret,
matching the app's `CKAN_CALLBACK_TOKEN`. Callbacks carry only the reviewed
identity, revision, checksum and decision; the Toolbox also polls for recovery.
Preview tickets remain short-lived and restricted by CSP to the Toolbox origin.
An authenticated preview signals readiness to that origin with `postMessage`.

Contact name/email are retained in a protected approved projection and added
to the render context only for authenticated visitors. They are not returned
in anonymous project dictionaries. Administrative allowlists never become
public page fields.

Run `bash scripts/run-ckan-tests.sh` for CKAN 2.10 plugin-load, template parsing
and behavioral verification, including `test_content_reviews.py`.
