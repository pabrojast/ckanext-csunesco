# encoding: utf-8
"""CS project actions: request / approve / reject / list / show / stats.

The project-request itself is modelled as ``cs_project.status`` (pending ->
approved/rejected) rather than a separate request table (see .mix/plan.md). On
approval the project's creator becomes its ``project_admin`` and the counter row
is created -- all in one transaction that commits exactly once.
"""
import datetime
import re

import ckan.plugins.toolkit as tk
import ckan.model as model
import sqlalchemy as sa

from ckanext.csunesco import db
from ckanext.csunesco.logic import auth
from ckanext.csunesco.logic import schema as cs_schema
from ckanext.csunesco.logic.action import current_user_id

# Server-side paging defaults for csunesco_project_list.
DEFAULT_LIST_LIMIT = 20
MAX_LIST_LIMIT = 100

# HTML allowlist for the sanitized ``short_description`` (bleach). Anything else
# is stripped so we never store active or structural markup.
_ALLOWED_TAGS = ['p', 'br', 'strong', 'em', 'b', 'i', 'ul', 'ol', 'li', 'a']
_ALLOWED_ATTRS = {'a': ['href', 'title', 'rel']}


def _utcnow():
    return datetime.datetime.utcnow()


def _sanitize_html(value):
    """Strip ``value`` down to the safe allowlist BEFORE it is stored.

    Uses bleach when available; if bleach is not installed we fail closed by
    stripping *all* tags so raw HTML never reaches the database.
    """
    if not value:
        return value
    try:
        import bleach
    except ImportError:
        return re.sub(r'<[^>]*>', '', value)
    return bleach.clean(
        value, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS, strip=True)


def _positive_int(value, default, maximum=None):
    """Coerce ``value`` to a non-negative int, clamping to ``maximum``."""
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    if result < 0:
        return default
    if maximum is not None and result > maximum:
        return maximum
    return result


def _stats_dict(project_id):
    """Return the stats for a project as a plain dict (zeros when absent)."""
    stats = db.get_stats(project_id)
    if stats is None:
        return {
            'project_id': project_id,
            'citizen_scientists': 0,
            'observations': 0,
            'sites_monitored': 0,
            'member_states': 0,
        }
    return {
        'project_id': stats.project_id,
        'citizen_scientists': stats.citizen_scientists or 0,
        'observations': stats.observations or 0,
        'sites_monitored': stats.sites_monitored or 0,
        'member_states': stats.member_states or 0,
    }


def _can_view_unapproved(context, project):
    """Creator / active member / sysadmin may view a not-yet-approved project."""
    if auth._is_sysadmin(context):
        return True
    user_id = current_user_id(context)
    if not user_id:
        return False
    if project.created_by == user_id:
        return True
    member = db.project_member(project.id, user_id)
    return member is not None and member.status == 'active'


def csunesco_project_request_create(context, data_dict):
    """Create a PENDING CS project request (any authenticated user)."""
    if not context.get('user'):
        raise tk.NotAuthorized(
            tk._('You must be logged in to request a project'))
    tk.check_access('csunesco_project_request_create', context, data_dict)

    schema = cs_schema.project_request_schema()
    # Keep only whitelisted keys so navl never reports "unexpected field"; the
    # schema itself re-adds required fields as missing when absent.
    incoming = {k: (data_dict or {}).get(k)
                for k in schema if k in (data_dict or {})}
    data, errors = tk.navl_validate(incoming, schema, context)
    if errors:
        raise tk.ValidationError(errors)

    slug_base = data.get('slug') or data['title']
    slug = db.unique_slug(slug_base)

    now = _utcnow()
    project = db.CsProject()
    project.slug = slug
    project.title = data['title']
    project.initiative_group = data.get('initiative')
    project.countries = data.get('countries')            # JSON string
    project.biosphere_reserve = data.get('biosphere_reserve')
    project.region_geojson = data.get('region_geojson')
    # SANITIZE before storing so no unsafe markup is ever persisted.
    project.short_description = _sanitize_html(data.get('short_description'))
    project.project_document_url = data.get('project_document_url')
    project.status = 'pending'
    project.created_by = current_user_id(context)
    project.created = now
    project.modified = now
    model.Session.add(project)
    model.Session.commit()
    return db.project_dictize(project)


def csunesco_project_approve(context, data_dict):
    """Approve a pending project: creator -> project_admin, stats seeded."""
    tk.check_access('csunesco_project_approve', context, data_dict)
    project_id = (data_dict or {}).get('id') or (data_dict or {}).get('project_id')
    project = db.get_project(project_id)
    if project is None:
        raise tk.ObjectNotFound(tk._('Project not found'))
    # GUARD: approving is only valid from the pending state (no re-approve).
    if project.status != 'pending':
        raise tk.ValidationError({'status': [tk._(
            'Only pending projects can be approved (current status: %s)'
        ) % project.status]})

    now = _utcnow()
    project.status = 'approved'
    project.reviewed_by = current_user_id(context)
    project.reviewed_at = now
    project.modified = now

    # SAME session, no intermediate commit: make the creator a project admin
    # (idempotently) and ensure the counter row exists, then commit once.
    if db.project_member(project.id, project.created_by) is None:
        member = db.CsProjectMember()
        member.project_id = project.id
        member.user_id = project.created_by
        member.role = 'admin'
        member.status = 'active'
        member.source = 'ckan'
        member.created = now
        model.Session.add(member)
    db.ensure_stats(project.id)
    model.Session.commit()
    return db.project_dictize(project)


def csunesco_project_reject(context, data_dict):
    """Reject a pending project, storing an optional rejection reason."""
    tk.check_access('csunesco_project_reject', context, data_dict)
    project_id = (data_dict or {}).get('id') or (data_dict or {}).get('project_id')
    project = db.get_project(project_id)
    if project is None:
        raise tk.ObjectNotFound(tk._('Project not found'))
    if project.status != 'pending':
        raise tk.ValidationError({'status': [tk._(
            'Only pending projects can be rejected (current status: %s)'
        ) % project.status]})

    now = _utcnow()
    project.status = 'rejected'
    project.reviewed_by = current_user_id(context)
    project.reviewed_at = now
    project.rejection_reason = (data_dict or {}).get('reason')
    project.modified = now
    model.Session.commit()
    return db.project_dictize(project)


@tk.side_effect_free
def csunesco_project_list(context, data_dict):
    """List projects with server-side filtering + paging.

    Anonymous and non-sysadmin callers are pinned to ``status='approved'`` HERE
    (not only in auth) so the restriction is applied to the data itself. The
    large ``region_geojson`` blob is excluded from every list row.
    """
    tk.check_access('csunesco_project_list', context, data_dict)
    db.ensure_mappers()
    data_dict = data_dict or {}

    initiative = data_dict.get('initiative')
    country = data_dict.get('country')
    q = data_dict.get('q')

    if auth._is_sysadmin(context):
        status = data_dict.get('status')   # may be None -> all statuses
    else:
        status = 'approved'

    limit = _positive_int(data_dict.get('limit'),
                          default=DEFAULT_LIST_LIMIT, maximum=MAX_LIST_LIMIT)
    offset = _positive_int(data_dict.get('offset'), default=0)

    query = model.Session.query(db.CsProject)
    if status:
        query = query.filter(db.CsProject.status == status)
    if initiative:
        query = query.filter(db.CsProject.initiative_group == initiative)
    if country:
        # ``countries`` is a JSON array stored as text; match the quoted name.
        # The value is a bound parameter (no SQL injection); wildcard chars in
        # it would only broaden the match, which is harmless for country names.
        query = query.filter(
            db.CsProject.countries.ilike('%' + '"{0}"'.format(country) + '%'))
    if q:
        like = '%{0}%'.format(q)
        query = query.filter(sa.or_(
            db.CsProject.title.ilike(like),
            db.CsProject.short_description.ilike(like),
        ))

    # Stable total, independent of limit/offset.
    total = query.count()
    rows = (
        query.order_by(db.CsProject.created.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    results = []
    for project in rows:
        item = db.project_dictize(project)
        item.pop('region_geojson', None)
        results.append(item)

    return {
        'count': total,
        'results': results,
        'applied_filters': {
            'initiative': initiative,
            'country': country,
            'status': status,
            'q': q,
            'limit': limit,
            'offset': offset,
        },
    }


@tk.side_effect_free
def csunesco_project_show(context, data_dict):
    """Show a single project by id OR slug, with stats.

    A not-yet-approved project is only visible to its creator, an active member
    or a sysadmin. ``region_geojson`` is included only on explicit request.
    """
    tk.check_access('csunesco_project_show', context, data_dict)
    data_dict = data_dict or {}
    id_or_slug = data_dict.get('id') or data_dict.get('slug')
    if not id_or_slug:
        raise tk.ValidationError({'id': [tk._('Missing value')]})
    project = db.get_project(id_or_slug)
    if project is None:
        raise tk.ObjectNotFound(tk._('Project not found'))
    if project.status != 'approved' and not _can_view_unapproved(context, project):
        raise tk.NotAuthorized(tk._('Not authorized to view this project'))

    result = db.project_dictize(project)
    if not data_dict.get('include_geojson'):
        result.pop('region_geojson', None)
    result['stats'] = _stats_dict(project.id)
    return result


@tk.side_effect_free
def csunesco_project_stats_show(context, data_dict):
    """Show the pre-aggregated counters for a project (zeros when none)."""
    tk.check_access('csunesco_project_stats_show', context, data_dict)
    data_dict = data_dict or {}
    id_or_slug = data_dict.get('id') or data_dict.get('project_id')
    if not id_or_slug:
        raise tk.ValidationError({'id': [tk._('Missing value')]})
    project = db.get_project(id_or_slug)
    if project is None:
        raise tk.ObjectNotFound(tk._('Project not found'))
    return _stats_dict(project.id)


@tk.side_effect_free
def csunesco_aggregate_stats(context, data_dict):
    """At-a-glance totals summed across ALL approved projects (one query).

    Public read used by the hub's "At a Glance" band. Delegates the whole
    computation to ``db.aggregate_stats`` (a single COALESCE(SUM) query) so the
    counters never require a per-project fan-out.
    """
    tk.check_access('csunesco_aggregate_stats', context, data_dict)
    db.ensure_mappers()
    return db.aggregate_stats()


def get_actions():
    return {
        'csunesco_project_request_create': csunesco_project_request_create,
        'csunesco_project_approve': csunesco_project_approve,
        'csunesco_project_reject': csunesco_project_reject,
        'csunesco_project_list': csunesco_project_list,
        'csunesco_project_show': csunesco_project_show,
        'csunesco_project_stats_show': csunesco_project_stats_show,
        'csunesco_aggregate_stats': csunesco_aggregate_stats,
    }
