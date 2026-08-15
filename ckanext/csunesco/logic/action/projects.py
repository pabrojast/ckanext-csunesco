# encoding: utf-8
"""CS project actions: request / approve / reject / list / show / stats.

The project-request itself is modelled as ``cs_project.status`` (pending ->
approved/rejected) rather than a separate request table (see .mix/plan.md). On
approval the project's creator becomes its ``project_admin`` and the counter row
is created -- all in one transaction that commits exactly once.
"""
import datetime
import json
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


def _project_extras(data, current=None):
    """Merge the validated non-column project fields into an ``extras`` dict.

    Only keys PRESENT in ``data`` are touched. That is what lets the CS Toolbox
    (ofform) outbox -- which posts none of them -- leave them alone, and what
    lets a partial API update mention one field without blanking the rest.

    An explicitly empty value REMOVES the key, so "cleared the field" and
    "never set it" read back the same (absent). ``False`` is a VALUE, not
    empty: an unchecked ``open_participation`` must persist as False, hence the
    spelled-out emptiness test rather than a bare falsiness check.

    ``csunesco_valid_iso_date`` returns a ``datetime`` and this column is JSON,
    so dates are stored as ISO strings -- without this, the first save with a
    date raises "Object of type datetime is not JSON serializable".
    """
    # dict() and not the parsed object itself: _load_json hands back the very
    # dict it was given when the caller already parsed it, and mutating the
    # caller's copy in place is a surprise waiting to happen.
    extras = db._load_json(current, {})
    extras = dict(extras) if isinstance(extras, dict) else {}
    for key in cs_schema.PROJECT_EXTRA_FIELDS:
        if key not in data:
            continue
        value = data[key]
        if key in cs_schema.PROJECT_EXTRA_HTML_FIELDS:
            value = _sanitize_html(value)
        if isinstance(value, datetime.datetime):
            value = value.date().isoformat()
        if value is None or value == '' or value == []:
            extras.pop(key, None)
        else:
            extras[key] = value
    return extras


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
    """Creator / active member / initiative-admin / sysadmin may view a
    not-yet-approved project."""
    if auth._is_sysadmin(context):
        return True
    user_id = current_user_id(context)
    if not user_id:
        return False
    if project.created_by == user_id:
        return True
    member = db.project_member(project.id, user_id)
    if member is not None and member.status == 'active':
        return True
    return auth._is_project_initiative_admin(context, project.id)


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
    project.image_url = data.get('image_url')
    # The staged form's extra detail fields. No migration: they ride in the
    # existing JSON column and project_dictize merges them back on read.
    project.extras = json.dumps(_project_extras(data))
    project.status = 'pending'
    project.created_by = current_user_id(context)
    project.created = now
    project.modified = now
    model.Session.add(project)
    model.Session.commit()
    return db.project_dictize(project)


# Form field -> ``cs_project`` column for the fields an edit may change.
# Deliberately EXCLUDES slug (URL stability), status, trusted, created_by,
# organization_id and the whole moderation audit trail.
PROJECT_EDITABLE_COLUMNS = (
    ('title', 'title'),
    ('initiative', 'initiative_group'),
    ('countries', 'countries'),
    ('biosphere_reserve', 'biosphere_reserve'),
    ('region_geojson', 'region_geojson'),
    ('project_document_url', 'project_document_url'),
    ('image_url', 'image_url'),
)


def csunesco_project_update(context, data_dict):
    """Edit an existing project's details (sysadmin / PM / initiative admin).

    Editing NEVER changes moderation state: an approved project stays approved,
    a pending request stays pending, a rejected one stays rejected.

    That DIVERGES from ``csunesco_content_update``, which re-queues a
    non-sysadmin edit, and the divergence is deliberate. A content item is a
    publication whose text a reviewer approved; a project record is the
    identity of something already live on the portal. Sending an approved
    project back to ``pending`` would unpublish its landing page, orphan its
    data sources and stall its news queue because someone fixed a typo in a
    contact address. Prose moderation belongs to the project PAGE, which has
    its own queue (``csunesco_project_page_submit``).

    KNOWN GAP, deliberately not solved here: a *rejected* project is a dead end
    -- its manager can edit it but has no way to ask for another review. The
    fix is a separate ``csunesco_project_resubmit`` (rejected -> pending,
    clearing ``rejection_reason``), not a side effect smuggled into this one.
    """
    data_dict = data_dict or {}
    # Resolve FIRST, then authorize against the RESOLVED project -- the order
    # csunesco_content_update uses, so a caller cannot slip a project they do
    # control past a check meant for one they do not.
    project = db.get_project(data_dict.get('id') or data_dict.get('slug'))
    if project is None:
        raise tk.ObjectNotFound(tk._('Project not found'))
    tk.check_access('csunesco_project_update', context,
                    dict(data_dict, id=project.id, project_id=project.id))
    # Defence in depth: the auth function is a cheap pre-check that lets an
    # authenticated caller through, so the real decision is made HERE against
    # the resolved row -- which is also the only place that knows whether the
    # caller is the author of a request that has not been approved yet.
    if not auth.can_edit_project_details(context, project):
        raise tk.NotAuthorized(tk._(
            'Only the project admin or the initiative admin can edit this '
            'project'))

    incoming = {k: data_dict[k] for k in cs_schema.project_update_schema()
                if k in data_dict}
    data, errors = tk.navl_validate(
        incoming, cs_schema.project_update_schema(incoming.keys()), context)
    if errors:
        raise tk.ValidationError(errors)

    for field, column in PROJECT_EDITABLE_COLUMNS:
        if field in data:
            setattr(project, column, data[field])
    if 'short_description' in data:
        # SANITIZE before storing -- the same rule as the create path.
        project.short_description = _sanitize_html(data['short_description'])
    project.extras = json.dumps(_project_extras(data, project.extras))
    project.modified = _utcnow()
    model.Session.commit()
    return db.project_dictize(project)


def csunesco_project_resubmit(context, data_dict):
    """Put a REJECTED project back in the review queue (rejected -> pending).

    Without this a rejection is terminal. The reviewer's whole vocabulary is
    approve/reject plus an optional reason, so "reject" is used for
    everything from "this will never fly" to "wrong initiative, fix it and
    send it back" -- and the second one had no send-it-back. The author's only
    recourse was to file the request again under a fresh slug, leaving a dead
    row behind and losing every edit they had made to it.

    Deliberately NOT a review decision: this only returns the request to the
    queue. Approving it still takes a sysadmin or the initiative's admin, so
    an author cannot talk their way past moderation by resubmitting -- the
    worst they can do is ask again.

    The stale review stamp is cleared along with the reason. Leaving
    ``reviewed_by`` / ``reviewed_at`` behind would show the pending queue a
    row that claims it was already reviewed, by someone who has not yet seen
    this version of it.
    """
    data_dict = data_dict or {}
    project = db.get_project(data_dict.get('id') or data_dict.get('slug'))
    if project is None:
        raise tk.ObjectNotFound(tk._('Project not found'))
    tk.check_access('csunesco_project_resubmit', context,
                    dict(data_dict, id=project.id, project_id=project.id))
    if not auth.can_edit_project_details(context, project):
        raise tk.NotAuthorized(tk._(
            'Only the project admin or the initiative admin can resubmit '
            'this project'))
    # GUARD: only a rejected project can be resubmitted. Mirrors the
    # "only pending projects can be approved" guard -- re-queueing an already
    # pending request would be a no-op that reorders the queue, and
    # re-queueing an APPROVED one would unpublish a live landing page.
    if project.status != 'rejected':
        raise tk.ValidationError({'status': [tk._(
            'Only rejected projects can be resubmitted (current status: %s)'
        ) % project.status]})

    now = _utcnow()
    project.status = 'pending'
    project.rejection_reason = None
    project.reviewed_by = None
    project.reviewed_at = None
    project.modified = now
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


def csunesco_project_trusted_set(context, data_dict):
    """Toggle a project's ``trusted`` flag (sysadmin-only policy lever).

    Trusted projects publish news/events without review; publications, maps
    and data sources keep queueing regardless.
    """
    tk.check_access('csunesco_project_trusted_set', context, data_dict)
    data_dict = data_dict or {}
    project = db.get_project(data_dict.get('id') or data_dict.get('project_id'))
    if project is None:
        raise tk.ObjectNotFound(tk._('Project not found'))
    if 'trusted' not in data_dict:
        raise tk.ValidationError({'trusted': [tk._('Missing value')]})
    project.trusted = tk.asbool(data_dict.get('trusted'))
    project.modified = _utcnow()
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
    # Usernames of the project's admins (PM) + its initiative admins (ADM). The
    # CS Toolbox app mirrors these as programme owners when the project syncs.
    # Usernames only (public CKAN identifiers) -- never emails.
    admin_ids = set(db.project_admin_user_ids(project.id))
    admin_ids.update(db.initiative_admin_user_ids(project.initiative_group))
    admins = []
    if admin_ids:
        users = (
            model.Session.query(model.User)
            .filter(model.User.id.in_(admin_ids))
            .filter(model.User.state == 'active')
            .all()
        )
        admins = sorted(u.name for u in users)
    result['admins'] = admins
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


@tk.side_effect_free
def csunesco_member_state_list(context, data_dict):
    """The member states a project may declare: ``{'member_states': [...]}``.

    Each entry is ``{'name': <group slug>, 'title': <human label>}``, sorted by
    title. ``member_states: []`` means the portal has no ``member-states`` group
    seeded -- a real, reportable state, NOT an error, so callers can say so
    instead of rendering an empty picker.
    """
    tk.check_access('csunesco_member_state_list', context, data_dict)
    db.ensure_mappers()
    return {'member_states': db.member_state_choices()}


@tk.side_effect_free
def csunesco_my_projects(context, data_dict):
    """The projects the acting user administers (PM role).

    Exists because a project manager otherwise had no way back to their own
    project: the public listing filters by initiative and free text only, and
    approval sends no notification, so after a request was approved the manager
    had to remember the title and search for it.

    Deliberately the DIRECT admin relationship only, for every role including
    sysadmins. "Every project on the portal" is not a personal list, and an
    initiative admin's much larger scope is better served by their initiative
    pages -- which is what the panel links alongside this.
    """
    tk.check_access('csunesco_my_projects', context, data_dict)
    return {'projects': db.projects_administered(current_user_id(context))}


def get_actions():
    return {
        'csunesco_my_projects': csunesco_my_projects,
        'csunesco_project_request_create': csunesco_project_request_create,
        'csunesco_project_update': csunesco_project_update,
        'csunesco_project_resubmit': csunesco_project_resubmit,
        'csunesco_project_approve': csunesco_project_approve,
        'csunesco_project_reject': csunesco_project_reject,
        'csunesco_project_trusted_set': csunesco_project_trusted_set,
        'csunesco_project_list': csunesco_project_list,
        'csunesco_project_show': csunesco_project_show,
        'csunesco_project_stats_show': csunesco_project_stats_show,
        'csunesco_aggregate_stats': csunesco_aggregate_stats,
        'csunesco_member_state_list': csunesco_member_state_list,
    }
