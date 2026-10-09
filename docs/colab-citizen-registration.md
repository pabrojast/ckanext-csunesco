# Citizen registration and Colab review

Citizen Science registration is required before a new project proposal. A linked
applicant completes their dossier, verifies their email and requests Project
Manager access. A pending PM may submit a proposal; publishing still requires
both PM and project approval. Existing organizational roles remain valid.

Colab owns the review UI at `/colab/admin?tab=managers`, `tab=joins` and
`tab=projects`. CS actions and tables remain authoritative, including existing
requests, decisions, reviewer attribution and notification behavior. Legacy CS
review URLs redirect to Colab. Contents, data sources and pages stay in CS.

The Toolbox links portal requests to Colab. Its compatibility decision endpoints
check the actual reviewer in CKAN and wait for the canonical decision before
changing local membership. Workshop accounts retain local approval. This change
does not change generic CKAN collaborator registration at `/colab`.

Deploy the coordinated CS and Colab versions before the Toolbox. The CS plugin's
idempotent table setup supplies the registration columns; no queue copy or
application database migration is needed. Reverting the UI does not delete any
request. Retain the previous CKAN image digest for rollback.
