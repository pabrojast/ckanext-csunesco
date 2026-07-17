# encoding: utf-8
"""Template helpers for the public Citizen Science presentation layer.

Increment 4: thin helpers consumed by the ``csunesco/*`` templates. They read
plain data from ``constants``, build URLs via the toolkit, and -- crucially --
delegate any aggregation to the action layer so a helper NEVER touches the DB
directly. Optional dependencies degrade gracefully (the QR helper returns
``None`` when ``qrcode`` / Pillow are not installed).

Registered with CKAN through the plugin's ``ITemplateHelpers`` (``get_helpers``)
and imported lazily there so there is no import-time dependency on CKAN.
"""
import base64
import functools
import io
import logging

import ckan.plugins.toolkit as tk

from ckanext.csunesco import constants

log = logging.getLogger(__name__)

# The parent group whose active children are the valid member states. Kept in
# sync with ``logic/validators.MEMBER_STATES_GROUP`` (water-family pattern).
MEMBER_STATES_GROUP = 'member-states'

# Neutral zeros returned whenever the aggregate stats cannot be computed, so the
# "At a Glance" band always renders (shows 0 gracefully).
_ZERO_STATS = {
    'citizen_scientists': 0,
    'observations': 0,
    'sites_monitored': 0,
    'member_states': 0,
}


def csunesco_initiatives():
    """Return the four Citizen Science initiatives (list of ``{name, title}``)."""
    return list(constants.CS_INITIATIVES)


def csunesco_aggregate_stats():
    """At-a-glance totals across approved projects (via the domain action).

    Delegates to the ``csunesco_aggregate_stats`` action so the helper never
    queries the DB directly. Returns zeros on ANY error so the band still
    renders rather than breaking the page.
    """
    try:
        return tk.get_action('csunesco_aggregate_stats')({}, {})
    except Exception:
        log.warning('csunesco: aggregate stats could not be computed')
        return dict(_ZERO_STATS)


def csunesco_project_url(slug):
    """Path of a project's public landing page (same-origin; safe for fetch)."""
    return tk.url_for('csunesco.project_landing', slug=slug)


def csunesco_join_url(slug):
    """Path of a project's join endpoint (POST target for the join form)."""
    return tk.url_for('csunesco.join_project', slug=slug)


@functools.lru_cache(maxsize=256)
def csunesco_qr_data_uri(text):
    """Return a PNG ``data:`` URI QR code for ``text`` (cached per URL).

    Degrades gracefully: returns ``None`` when the optional ``qrcode`` package
    (or its Pillow image backend) is not installed, or if generation fails for
    any reason -- templates then show only the short link. The ``lru_cache``
    keys on the exact ``text`` so a given URL's PNG is encoded once per process.
    """
    if not text:
        return None
    try:
        import qrcode
        import qrcode.image.pil  # noqa: F401 -- ensure a PIL backend exists
    except ImportError:
        return None
    try:
        image = qrcode.make(text)
        buffer = io.BytesIO()
        image.save(buffer, format='PNG')
        encoded = base64.b64encode(buffer.getvalue()).decode('ascii')
    except Exception:
        log.warning('csunesco: QR code could not be generated')
        return None
    return 'data:image/png;base64,{0}'.format(encoded)


def csunesco_pending_count():
    """Total pending items awaiting the acting user's review (0 for anon).

    Reuses the SAME per-request cached counts as the admin panel so the header
    badge and the panel tabs can never disagree. Fails soft to 0 so a template
    that always renders the badge never breaks the page.
    """
    if not tk.g.user:
        return 0
    try:
        import ckan.model as model
        from ckanext.csunesco.logic.action.admin import _get_pending_counts
        context = {'model': model, 'session': model.Session, 'user': tk.g.user}
        return _get_pending_counts(context).get('total', 0)
    except Exception:
        log.warning('csunesco: pending count could not be computed')
        return 0


def csunesco_can_manage_project(project_id):
    """True when the acting user may add/edit content for ``project_id``.

    Thin wrapper over the ``csunesco_content_create`` auth (sysadmin OR the
    project's admin) so templates can conditionally show manager-only affordances
    (status badges, "Add news/event" links) without touching the DB or auth
    internals. Any failure degrades to False.
    """
    if not project_id or not tk.g.user:
        return False
    try:
        import ckan.model as model
        context = {'model': model, 'user': tk.g.user}
        return tk.check_access(
            'csunesco_content_create', context, {'project_id': project_id})
    except Exception:
        return False


def csunesco_member_state_title(name):
    """Human title for a member-state group name (falls back to the name).

    Best-effort lookup of the CKAN group's display title; any failure (missing
    group, DB error) simply returns the raw ``name`` so a card/label never
    breaks.
    """
    if not name:
        return name
    try:
        import ckan.model as model
        group = model.Group.get(name)
        if group is not None and group.title:
            return group.title
    except Exception:
        log.warning('csunesco: member-state title lookup failed')
    return name
