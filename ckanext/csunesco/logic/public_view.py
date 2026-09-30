"""Publication-only queries on public pages, independent of reviewer rights."""


def public_context(context):
    # Keep the identity for logged-in visibility and dataset access checks.
    # Never mutate the caller: editors and review queues need the full scope.
    return dict(context, csunesco_public_view=True)
