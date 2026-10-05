# Project proposal validation

The project form accepts independent projects and an external initiative name.
`external_initiative_name` lives in project extras; canonical initiative groups
continue to control review permissions. External projects use the existing
platform administrator review path and do not create groups or memberships.
Deploy compatible Toolbox support before releasing this CKAN form.

Authenticated, CSRF-protected `POST /citizen-science/project/validate` accepts the
form's fields, an optional editable `project_id`, and `step` (`1`–`8` or `all`).
It returns `{valid, errors, step}` without uploads, writes or notifications.
The final form POST uses the same checks. Draft saving retains the lenient
schema; the Toolbox action contract remains compatible with partial payloads.

`GET /citizen-science/project/editor-options?q=...` requires the same proposal
or project-edit authority. It searches active CKAN users and returns at most 20
`{name, fullname}` matches in `results`, with no email addresses. The proposer is
excluded from editor membership creation. Exact usernames remain a fallback.

Next and Enter check the active section; final submission checks all sections.
Errors focus the visible rich-text/country control. Failed requests preserve
entries and allow retry. Back and Save for later remain available. An expired
CSRF token can be refreshed after signing in again without discarding entries.
Project address examples refer to the CKAN URL ending; existing URLs stay fixed.

Verification: `bash scripts/run-ckan-tests.sh`, plus browser checks of step
navigation, retry, collaborator selection, draft saving and final submission.
