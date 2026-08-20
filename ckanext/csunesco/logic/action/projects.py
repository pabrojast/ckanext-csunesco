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

from ckanext.csunesco import constants
from ckanext.csunesco import db
from ckanext.csunesco.logic import auth
from ckanext.csunesco.logic import schema as cs_schema
from ckanext.csunesco.logic.action import current_user_id

# Server-side paging defaults for csunesco_project_list.
DEFAULT_LIST_LIMIT = 20
MAX_LIST_LIMIT = 100

# HTML allowlist for the sanitized ``short_description`` (bleach). Anything else
# is stripped so we never store active or structural markup.
_ALLOWED_TAGS = [
    'p', 'br', 'strong', 'em', 'b', 'i', 'ul', 'ol', 'li', 'a',
    'h3', 'h4', 'blockquote',
]
_ALLOWED_ATTRS = {'a': ['href', 'title', 'rel']}


def _utcnow():
    return datetime.datetime.utcnow()


def _member_states_count(project):
    """Distinct declared countries of one project (feeds its counter row).

    The per-project ``member_states`` counter is DERIVED from the declared
    countries -- unlike observations it has no external data source, so it is
    recomputed wherever the countries can change (approve, update, backfill).
    """
    countries = db._load_json(project.countries, [])
    if not isinstance(countries, list):
        return 0
    return len({str(c).strip() for c in countries if str(c).strip()})


def _sync_participation(data):
    """Keep ``participation_mode`` and ``open_participation`` coherent.

    The web form posts the spec's ``participation_mode`` choice; the CS
    Toolbox app (and the Fase-0 join gate) speak the boolean
    ``open_participation``. Whichever side arrives, the other is derived so
    both readers keep working.
    """
    if data.get('participation_mode'):
        data['open_participation'] = data['participation_mode'] == 'open'
    elif 'open_participation' in data and 'participation_mode' not in data:
        data['participation_mode'] = (
            'open' if data['open_participation'] else 'limited')
    return data


def _circle_geojson(lat, lng, radius_km, segments=48):
    """A polygon approximating a circle, for the spec's point+radius region.

    The region validator only accepts (Multi)Polygon shapes and the landing
    map renders those, so the point+radius input is materialized into a
    48-vertex polygon instead of teaching every reader a new geometry type.
    """
    import math
    lat_rad = math.radians(lat)
    dlat = radius_km / 111.32
    dlng = radius_km / max(0.001, 111.32 * math.cos(lat_rad))
    ring = []
    for step in range(segments + 1):
        angle = 2 * math.pi * step / segments
        ring.append([round(lng + dlng * math.cos(angle), 6),
                     round(lat + dlat * math.sin(angle), 6)])
    return json.dumps({
        'type': 'Feature',
        'properties': {'csunesco_point_radius_km': radius_km,
                       'csunesco_point': [round(lng, 6), round(lat, 6)]},
        'geometry': {'type': 'Polygon', 'coordinates': [ring]},
    })


def _apply_point_radius(data):
    """When a full point+radius triple arrives WITHOUT an explicit region,
    synthesize the region polygon from it. An explicit region always wins."""
    lat = data.get('point_lat')
    lng = data.get('point_lng')
    radius = data.get('point_radius_km')
    if lat is None or lng is None or radius is None:
        return data
    if data.get('region_geojson'):
        return data
    data['region_geojson'] = _circle_geojson(lat, lng, radius)
    return data


def _resolve_lead_organisation(data):
    """CKAN org id for ``lead_organisation`` when it names an existing org.

    The field itself is free text (the spec's "not listed -- create a new
    one" is satisfied by storing the declared name verbatim); the column link
    is a bonus that only fires on an exact org name/id match.
    """
    name = (data.get('lead_organisation') or '').strip()
    if not name:
        return None
    try:
        group = model.Group.get(name)
    except Exception:
        return None
    if group is not None and getattr(group, 'is_organization', False):
        return group.id
    return None


def _resolve_organization(data):
    """Resolve an explicit CKAN organization id/name to an active org row."""
    key = (data.get('organization_id') or '').strip()
    if not key:
        return None
    try:
        group = model.Group.get(key)
    except Exception:
        group = None
    if (group is None or not getattr(group, 'is_organization', False)
            or getattr(group, 'state', 'active') != 'active'):
        raise tk.ValidationError({'organization_id': [tk._(
            'Select an active CKAN organization')]})
    return group


def _sync_editor_members(project_id, editors, now):
    """Reconcile the ``editor``-role member rows with a username list.

    Unknown usernames are a HARD error -- silently dropping one would tell
    the manager their colleague was added when they were not. Existing
    admin/scientist rows are never touched; an editor removed from the list
    loses only the editor row. Runs in the caller's session (no commit).
    """
    resolved = {}
    for username in editors:
        user = model.User.get(username)
        if user is None:
            raise tk.ValidationError({'editors': [tk._(
                'Unknown user: %s') % username]})
        resolved[user.id] = username
    existing = (
        model.Session.query(db.CsProjectMember)
        .filter(db.CsProjectMember.project_id == project_id)
        .filter(db.CsProjectMember.role == 'editor')
        .all()
    )
    seen = set()
    for member in existing:
        if member.user_id in resolved:
            seen.add(member.user_id)
            if member.status != 'active':
                member.status = 'active'
        else:
            model.Session.delete(member)
    for user_id in resolved:
        if user_id in seen:
            continue
        member = db.CsProjectMember()
        member.project_id = project_id
        member.user_id = user_id
        member.role = 'editor'
        member.status = 'active'
        member.source = 'ckan'
        member.created = now
        model.Session.add(member)


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
    """Create a project request under an authorized CKAN organization.

    The sysadmin service token used by Toolbox remains compatible with its
    legacy payload, which has no organization_id. Human portal callers must
    supply an organization where they have create_dataset permission.
    """
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
    _sync_participation(data)
    _apply_point_radius(data)

    organization = _resolve_organization(data)
    if (organization is not None and not auth._is_sysadmin(context)
            and not auth._is_org_editor(context, organization.id)):
        raise tk.NotAuthorized(tk._(
            'Only an organization admin or editor can propose a project'))

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
    project.logo_url = data.get('logo_url')
    project.heading_image_url = data.get('heading_image_url')
    project.organization_id = (organization.id if organization is not None
                               else _resolve_lead_organisation(data))
    # The staged form's extra detail fields. No migration: they ride in the
    # existing JSON column and project_dictize merges them back on read.
    project.extras = json.dumps(_project_extras(data))
    # The web form's "Save for later" path creates a DRAFT (visible only to
    # its creator, absent from every queue and listing) instead of filing a
    # review request. Only the view sets the flag -- the API/outbox path
    # always files pending, unchanged.
    project.status = 'draft' if context.get('csunesco_draft') else 'pending'
    project.created_by = current_user_id(context)
    project.created = now
    project.modified = now
    model.Session.add(project)
    if data.get('editors'):
        # flush() so the new project has an id for the member rows; still ONE
        # commit for the whole create.
        model.Session.flush()
        _sync_editor_members(project.id, data['editors'], now)
    model.Session.commit()
    return db.project_dictize(project)


# Form field -> ``cs_project`` column for the fields an edit may change.
# Deliberately EXCLUDES slug (URL stability), status, trusted, created_by and
# the whole moderation audit trail. ``organization_id`` is handled separately
# after its target and the acting user's organization role have been checked.
PROJECT_EDITABLE_COLUMNS = (
    ('title', 'title'),
    ('initiative', 'initiative_group'),
    ('countries', 'countries'),
    ('biosphere_reserve', 'biosphere_reserve'),
    ('region_geojson', 'region_geojson'),
    ('project_document_url', 'project_document_url'),
    ('image_url', 'image_url'),
    ('logo_url', 'logo_url'),
    ('heading_image_url', 'heading_image_url'),
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
    # Countries this project already declared are grandfathered through the
    # member-state check: re-saving one is keeping it, not adding it. Without
    # this the edit form is unsavable whenever the member-state list is
    # unreachable or a state has been de-published since.
    context = dict(context)
    context['csunesco_existing_countries'] = db._load_json(
        project.countries, [])
    data, errors = tk.navl_validate(
        incoming, cs_schema.project_update_schema(incoming.keys()), context)
    if errors:
        raise tk.ValidationError(errors)
    _sync_participation(data)
    _apply_point_radius(data)

    organization = None
    if 'organization_id' in data:
        organization = _resolve_organization(data)
        if (organization is not None and not auth._is_sysadmin(context)
                and not auth._is_org_editor(context, organization.id)):
            raise tk.NotAuthorized(tk._(
                'You cannot move this project to that organization'))

    old_values = db.project_dictize(project)
    previous_initiative = project.initiative_group
    for field, column in PROJECT_EDITABLE_COLUMNS:
        if field in data:
            setattr(project, column, data[field])
    if 'short_description' in data:
        # SANITIZE before storing -- the same rule as the create path.
        project.short_description = _sanitize_html(data['short_description'])
    extras = _project_extras(data, project.extras)
    candidate = db.project_dictize(project)
    candidate.update(extras)
    changed = []
    tracked = [field for field, _column in PROJECT_EDITABLE_COLUMNS]
    tracked.extend(['short_description'] + list(cs_schema.PROJECT_EXTRA_FIELDS))
    for field in tracked:
        if field not in data:
            continue
        old_key = 'initiative_group' if field == 'initiative' else field
        new_value = (extras.get(field)
                     if field in cs_schema.PROJECT_EXTRA_FIELDS
                     else candidate.get(old_key))
        if old_values.get(old_key) != new_value:
            changed.append(field)
    if changed:
        user_id = current_user_id(context)
        user = context.get('auth_user_obj')
        user_name = ((getattr(user, 'display_name', None)
                      or getattr(user, 'name', None)) if user else None)
        history = extras.get('edit_history')
        history = list(history) if isinstance(history, list) else []
        history.append({
            'user_id': user_id,
            'user_name': user_name or context.get('user') or user_id or u'',
            'timestamp': _utcnow().replace(microsecond=0).isoformat() + 'Z',
            'fields': sorted(set(changed)),
        })
        extras['edit_history'] = history[-50:]
    project.extras = json.dumps(extras)
    if previous_initiative != project.initiative_group:
        # Content inherits its moderation scope from the project. Keep it in
        # sync atomically when a reviewer corrects the initiative.
        (model.Session.query(db.CsContent)
         .filter(db.CsContent.project_id == project.id)
         .update({'initiative_group': project.initiative_group},
                 synchronize_session=False))
    if 'organization_id' in data:
        project.organization_id = organization.id if organization else None
    elif 'lead_organisation' in data and not project.organization_id:
        # Compatibility for legacy/Toolbox projects that predate explicit
        # ownership. Never overwrite an already selected canonical org from a
        # free-text display field.
        project.organization_id = _resolve_lead_organisation(data)
    if 'editors' in data:
        _sync_editor_members(project.id, data.get('editors') or [], _utcnow())
    if 'countries' in data and project.status == 'approved':
        # Keep the derived member-states counter in step with the edit. Only
        # for approved projects: pending requests get theirs seeded on
        # approval, so no counter row is created ahead of moderation.
        db.stats_set(project.id, member_states=_member_states_count(project))
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
    # 'draft' joined 'rejected' when the form gained "Save for later": both
    # are author-held states whose only exit is this send-for-review door.
    if project.status not in ('rejected', 'draft'):
        raise tk.ValidationError({'status': [tk._(
            'Only rejected or draft projects can be submitted for review '
            '(current status: %s)') % project.status]})

    now = _utcnow()
    project.status = 'pending'
    project.rejection_reason = None
    project.reviewed_by = None
    project.reviewed_at = None
    project.modified = now
    model.Session.commit()
    return db.project_dictize(project)


def _project_for_state_change(context, data_dict, action):
    data_dict = data_dict or {}
    project = db.get_project(data_dict.get('id') or data_dict.get('project_id')
                             or data_dict.get('slug'))
    if project is None:
        raise tk.ObjectNotFound(tk._('Project not found'))
    tk.check_access(action, context, dict(data_dict, id=project.id,
                                          project_id=project.id))
    return project


def csunesco_project_delete(context, data_dict):
    """Permanently remove an unapproved proposal and its private dependants."""
    project = _project_for_state_change(
        context, data_dict, 'csunesco_project_delete')
    if project.status not in ('draft', 'pending', 'rejected'):
        raise tk.ValidationError({'status': [tk._(
            'Published projects must be archived instead of deleted')]})
    project_id = project.id
    # A proposal normally has none of these rows.  Deleting defensively keeps
    # an interrupted/demo workflow from leaving unreachable records behind.
    for cls in (db.CsProjectMember, db.CsContent, db.CsDataSource):
        (model.Session.query(cls)
         .filter(cls.project_id == project_id)
         .delete(synchronize_session=False))
    (model.Session.query(db.CsProjectStats)
     .filter(db.CsProjectStats.project_id == project_id)
     .delete(synchronize_session=False))
    (model.Session.query(db.CsProjectPage)
     .filter(db.CsProjectPage.project_id == project_id)
     .delete(synchronize_session=False))
    model.Session.delete(project)
    model.Session.commit()
    return {'id': project_id, 'deleted': True}


def _archive_audit(project, context, reason=None, restored=False):
    extras = db._load_json(project.extras, {})
    extras = dict(extras) if isinstance(extras, dict) else {}
    prefix = 'restored' if restored else 'archived'
    extras[prefix + '_by'] = current_user_id(context)
    extras[prefix + '_at'] = _utcnow().replace(microsecond=0).isoformat() + 'Z'
    if reason and not restored:
        extras['archive_reason'] = str(reason).strip()[:1000]
    if restored:
        extras.pop('archive_reason', None)
    project.extras = json.dumps(extras)


def csunesco_project_archive(context, data_dict):
    """Hide a published project without destroying its related records."""
    project = _project_for_state_change(
        context, data_dict, 'csunesco_project_archive')
    if project.status != 'approved':
        raise tk.ValidationError({'status': [tk._(
            'Only approved projects can be archived')]})
    _archive_audit(project, context, (data_dict or {}).get('reason'))
    project.status = 'archived'
    project.modified = _utcnow()
    model.Session.commit()
    return db.project_dictize(project)


def csunesco_project_restore(context, data_dict):
    """Restore an archived project to its previous public approved state."""
    project = _project_for_state_change(
        context, data_dict, 'csunesco_project_restore')
    if project.status != 'archived':
        raise tk.ValidationError({'status': [tk._(
            'Only archived projects can be restored')]})
    _archive_audit(project, context, restored=True)
    project.status = 'approved'
    project.modified = _utcnow()
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
    # Seed the derived member-states counter from the declared countries; the
    # landing page's At-a-Glance band reads the counter row, which was
    # otherwise never fed for this field.
    db.stats_set(project.id, member_states=_member_states_count(project))
    model.Session.commit()
    # AFTER the commit: a mailer hiccup must never roll back an approval.
    from ckanext.csunesco.logic import notify
    notify.notify_project_decision(project.created_by, project.title,
                                   approved=True)
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
    from ckanext.csunesco.logic import notify
    notify.notify_project_decision(project.created_by, project.title,
                                   approved=False,
                                   reason=project.rejection_reason)
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

    # Spec phase-1 facets living in the extras JSON blob. Filtered in PYTHON
    # over a BOUNDED sweep rather than with LIKE-over-JSON: a quoted-substring
    # match cannot tell `water_type: ["River"]` from `keywords: ["River"]`,
    # and the portal counts projects in the tens, not thousands. Promote to
    # columns if that ever changes.
    extras_filters = {}
    for facet in ('water_type', 'water_data_type', 'activity_status',
                  'geographic_extent'):
        value = (data_dict.get(facet) or '').strip() \
            if isinstance(data_dict.get(facet), str) else data_dict.get(facet)
        if value:
            extras_filters[facet] = str(value)

    if extras_filters:
        swept = (query.order_by(db.CsProject.created.desc())
                 .limit(1000).all())
        dictized = [db.project_dictize(project) for project in swept]
        matched = []
        for item in dictized:
            ok = True
            for facet, wanted in extras_filters.items():
                stored = item.get(facet)
                if isinstance(stored, (list, tuple)):
                    ok = wanted in stored
                else:
                    ok = stored == wanted
                if not ok:
                    break
            if ok:
                matched.append(item)
        total = len(matched)
        results = matched[offset:offset + limit]
        for item in results:
            item.pop('region_geojson', None)
    else:
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

    applied = {
        'initiative': initiative,
        'country': country,
        'status': status,
        'q': q,
        'limit': limit,
        'offset': offset,
    }
    applied.update(extras_filters)
    return {
        'count': total,
        'results': results,
        'applied_filters': applied,
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
    data_dict = data_dict or {}
    initiative = data_dict.get('initiative') or None
    if initiative:
        canonical = {item['name'] for item in constants.CS_INITIATIVES}
        if initiative not in canonical:
            raise tk.ValidationError({'initiative': [tk._(
                'Unknown Citizen Science initiative')]})
    return db.aggregate_stats(initiative_group=initiative)


@tk.side_effect_free
def csunesco_option_lists(context, data_dict):
    """The phase-1 form's option lists as one JSON document (public read).

    The single source the CS Toolbox app mirrors instead of hard-coding a
    second copy of each list -- its snapshot test compares against this
    payload, so renaming or removing an option is a VISIBLE contract change.
    """
    tk.check_access('csunesco_option_lists', context, data_dict)
    return {
        'water_types': list(constants.WATER_TYPES),
        'water_data_types': list(constants.WATER_DATA_TYPES),
        'geographic_extents': list(constants.GEOGRAPHIC_EXTENTS),
        'stakeholder_groups': list(constants.STAKEHOLDER_GROUPS),
        'activity_statuses': list(constants.ACTIVITY_STATUSES),
        'lead_partner_types': list(constants.LEAD_PARTNER_TYPES),
        'funding_bodies': list(constants.FUNDING_BODIES),
        'international_frameworks': list(constants.INTL_FRAMEWORKS),
        'participation_modes': list(constants.PARTICIPATION_MODES),
        'org_types': [dict(row) for row in constants.ORG_TYPES],
        'initiatives': [dict(row) for row in constants.CS_INITIATIVES],
    }


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


@tk.side_effect_free
def csunesco_my_joined_projects(context, data_dict):
    """The APPROVED projects the acting user participates in (any role).

    The participant half of "My projects" (spec section 4): a citizen
    scientist with an approved membership previously had NO page listing
    their projects -- the admin dashboard is manager/creator-only.
    """
    tk.check_access('csunesco_my_joined_projects', context, data_dict)
    return {'projects': db.projects_joined(current_user_id(context))}


def get_actions():
    return {
        'csunesco_my_projects': csunesco_my_projects,
        'csunesco_my_joined_projects': csunesco_my_joined_projects,
        'csunesco_project_request_create': csunesco_project_request_create,
        'csunesco_project_update': csunesco_project_update,
        'csunesco_project_resubmit': csunesco_project_resubmit,
        'csunesco_project_delete': csunesco_project_delete,
        'csunesco_project_archive': csunesco_project_archive,
        'csunesco_project_restore': csunesco_project_restore,
        'csunesco_project_approve': csunesco_project_approve,
        'csunesco_project_reject': csunesco_project_reject,
        'csunesco_project_trusted_set': csunesco_project_trusted_set,
        'csunesco_project_list': csunesco_project_list,
        'csunesco_project_show': csunesco_project_show,
        'csunesco_project_stats_show': csunesco_project_stats_show,
        'csunesco_aggregate_stats': csunesco_aggregate_stats,
        'csunesco_member_state_list': csunesco_member_state_list,
        'csunesco_option_lists': csunesco_option_lists,
    }
