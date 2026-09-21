# Managed Citizen Science data

Toolbox owns project sharing consent and observation/site/form exceptions.
`csunesco_data_source_create` accepts a service-only partition envelope:
`partition_id`, `form_id`, `programme_id`, `project_slug`, `access_level`,
`policy_revision`, `withdrawn`, title/description and optional owner organization.
Active envelopes are checked against Toolbox before writing. Policy revisions
are monotonic; a late request cannot restore an older policy.

Source identity is `(project_id, form_id, access_level)`. PostgreSQL bootstrap
adds `access_level` with the legacy sentinel, then replaces the old pair unique
constraint with a triple unique index. Existing source/package/resource IDs and
catalogue URLs survive adoption. Legacy sources are adopted only when their
actual datashare level matches. Private legacy packages without an explicit
level map to confidential. Unknown policy is never promoted to public.

New partitions remain pending until normal IHP-WINS approval. Existing approved
partitions keep their moderation decision as policy revisions synchronize.
Empty superseded partitions are withdrawn and their package state is set to
deleted, without deleting any original observation or package identity.

The existing `ckanext.csunesco.ofform_callback_token` authenticates private
Toolbox feeds. User access is checked first via datashare. CSV and GeoJSON
require `can_download`; fields, chart series and authorized chat require
`can_view_resources`. Metadata uses `can_read_metadata`. Confidential sources
are omitted before pagination; package search excludes stale/revoked partitions
before Solr calculates counts and facets. Preview access is not DRM.

Managed feeds check the current revision on every read and never substitute an
old snapshot after failure. Observation media has a source-bound, authorized
route. Legacy media URLs check the current public partition. Managed package
privacy cannot be edited outside Toolbox; grants and collaborators remain in
IHP-WINS. DataStore creation is refused for managed proxy resources. Before
adopting legacy sources, verify they have no old DataStore copies.

Deploy only after the Toolbox API exposes `/internal/ckan/data-partitions/*`,
`/internal/ckan/data-policy/check` and partition-aware snapshots. Run the Toolbox
legacy-access and quality-impact scripts before historical activation. Preserve
the previous image and database backup. A rollback must retain equivalent
external access gates; restoring old public proxy code alone would weaken the
newly authorized privacy policy.

Validation: `bash scripts/run-ckan-tests.sh` includes the data-privacy matrix,
revision failure, legacy-cache revocation, manual policy-drift and DataStore
regressions as well as the existing CKAN 2.10 plugin-load and behavior checks.
