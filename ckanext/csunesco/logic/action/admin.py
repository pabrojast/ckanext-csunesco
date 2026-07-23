# encoding: utf-8
"""CS admin approval panel action: the single aggregated "pending work" query.

``csunesco_admin_pending_list`` gathers everything awaiting review for the acting
user, scoped by role:

  * a SYSADMIN sees pending project requests + ALL pending join requests + ALL
    pending content + ALL pending data sources;
  * an INITIATIVE ADMIN (ADM) sees the pending projects, joins, content and
    data sources of THEIR initiatives (plus the joins/content of any project
    they also project-admin);
  * a PROJECT-ADMIN sees only pending joins + pending content for THEIR projects
    (project_requests/data_requests are always ``[]`` for them).

The three lists are paginated independently and the ``counts`` block reuses the
SAME per-request cached ``pending_counts`` that feeds the header badge, so the
tab counters and the badge can never disagree (DRY).
"""
import ckan.plugins.toolkit as tk

from ckanext.csunesco import db
from ckanext.csunesco.logic import auth
from ckanext.csunesco.logic.action import current_user_id

DEFAULT_LIST_LIMIT = 20
MAX_LIST_LIMIT = 100

# Per-request cache key on the Flask/CKAN ``g`` (counts depend on the user, so an
# lru_cache would be wrong -- ``g`` is naturally scoped to one request/user).
_COUNTS_CACHE_ATTR = '_csunesco_pending_counts'


def _positive_int(value, default, maximum=None):
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    if result < 0:
        return default
    if maximum is not None and result > maximum:
        return maximum
    return result


def _get_pending_counts(context):
    """Role-aware pending counts, cached once per request on ``tk.g``.

    Shared by ``csunesco_admin_pending_list`` and the ``csunesco_pending_count``
    template helper so both always report identical numbers. Falls back to a
    direct compute when there is no request context (e.g. a CLI/API call).
    """
    g = getattr(tk, 'g', None)
    if g is not None:
        cached = getattr(g, _COUNTS_CACHE_ATTR, None)
        if cached is not None:
            return cached
    counts = db.pending_counts(context)
    if g is not None:
        try:
            setattr(g, _COUNTS_CACHE_ATTR, counts)
        except Exception:
            # A missing/again-immutable request global just means "no caching".
            pass
    return counts


def _admin_scope(context):
    """Return ``(is_sysadmin, project_ids, initiative_groups)`` for the user.

    ``project_ids`` is ``None`` for a sysadmin (unrestricted); otherwise the
    union of the user's project-admin ids and every project of the initiatives
    they ADM (possibly empty). ``initiative_groups`` is the list of initiative
    names the user ADMs ([] for sysadmins and plain project admins).
    """
    if auth._is_sysadmin(context):
        return True, None, []
    user_id = current_user_id(context)
    if not user_id:
        return False, [], []
    project_ids = db.admin_project_ids(user_id)
    initiative_groups = db.admin_initiative_groups(user_id)
    if initiative_groups:
        project_ids = sorted(
            set(project_ids)
            | set(db.initiative_project_ids(initiative_groups)))
    return False, project_ids, initiative_groups


def _pending_project_requests(context, limit, offset):
    """Pending project requests (sysadmin scope only). Returns ``(count, list)``.

    Delegates to ``csunesco_project_list`` (which honours ``status`` for
    sysadmins) so paging + dictizing + the geojson strip are reused.
    """
    listing = tk.get_action('csunesco_project_list')(
        context, {'status': 'pending', 'limit': limit, 'offset': offset})
    return listing.get('count', 0), listing.get('results', [])


def _pending_join_requests(context, project_ids, limit, offset):
    """Pending join requests in scope. Returns ``(count, list)``."""
    return db.pending_joins(project_ids, limit, offset)


def _pending_content(context, project_ids, limit, offset):
    """Pending content in scope (summarized). Returns ``(count, list)``."""
    return db.pending_content(project_ids, limit, offset)


def csunesco_admin_pending_list(context, data_dict):
    """Aggregate everything awaiting the acting user's review (role-scoped)."""
    tk.check_access('csunesco_admin_pending_list', context, data_dict)
    db.ensure_mappers()
    data_dict = data_dict or {}
    limit = _positive_int(data_dict.get('limit'),
                          default=DEFAULT_LIST_LIMIT, maximum=MAX_LIST_LIMIT)
    offset = _positive_int(data_dict.get('offset'), default=0)

    is_sysadmin, project_ids, initiative_groups = _admin_scope(context)

    if is_sysadmin:
        _proj_count, project_requests = _pending_project_requests(
            context, limit, offset)
        _join_count, join_requests = _pending_join_requests(
            context, None, limit, offset)
        _content_count, content_requests = _pending_content(
            context, None, limit, offset)
        _data_count, data_requests = db.pending_data_sources(limit, offset)
    else:
        # Initiative admins review the projects + data sources of THEIR
        # initiatives; plain project-admins never see either list.
        if initiative_groups:
            _proj_count, project_requests = db.pending_projects(
                initiative_groups, limit, offset)
            _data_count, data_requests = db.pending_data_sources(
                limit, offset, initiative_groups=initiative_groups)
        else:
            project_requests = []
            data_requests = []
        scope = project_ids or []
        _join_count, join_requests = _pending_join_requests(
            context, scope, limit, offset)
        _content_count, content_requests = _pending_content(
            context, scope, limit, offset)

    return {
        'project_requests': project_requests,
        'join_requests': join_requests,
        'content_requests': content_requests,
        'data_requests': data_requests,
        # Identical numbers to the header badge (single cached source).
        'counts': _get_pending_counts(context),
        'limit': limit,
        'offset': offset,
    }


def get_actions():
    return {
        'csunesco_admin_pending_list': csunesco_admin_pending_list,
    }
