# encoding: utf-8
"""CS content actions: create / update / approve / reject / list / show.

Content (news, events, publications and Terria maps) is stored as ``cs_content``
rows discriminated by
``content_type`` (water-family "Page + extras" shape). Moderation mirrors the
project flow: a non-sysadmin author's content is ``pending`` until a sysadmin
approves it; a sysadmin authoring content publishes it as ``approved`` directly.

Key guarantees (advisor refinements, see .mix/plan.md):
  * ``initiative_group`` is ALWAYS derived from the parent project -- never taken
    from the client.
  * ``body`` is sanitized through the ONE shared allowlist before storage.
  * Authorization (project-admin/sysadmin) is enforced HERE in the logic, not
    only in the auth functions / routes.
  * ``list`` returns SUMMARIZED rows (no body) by default; the full body comes
    only from ``show`` (or an explicit ``include_body`` on list).
"""
import datetime
import json
import re

import ckan.plugins.toolkit as tk
import ckan.model as model

from ckanext.csunesco import db
from ckanext.csunesco.logic import auth
from ckanext.csunesco.logic import schema as cs_schema
from ckanext.csunesco.logic.sanitize import sanitize_html
from ckanext.csunesco.logic.action import current_user_id

# Server-side paging defaults for csunesco_content_list.
DEFAULT_LIST_LIMIT = 20
MAX_LIST_LIMIT = 100

# Cap for the multi-project filter (the app's "news from MY projects" feed).
MAX_PROJECT_IDS = 50

# Plain-text teaser length stored alongside each row so summaries never load the
# full body.
EXCERPT_LENGTH = 240

# Content types this increment accepts (kept in sync with the validator set).
CONTENT_TYPES = {'cs-news', 'cs-event', 'cs-publication', 'cs-map'}

# Where a row was authored: on this portal ('ckan') or pushed from the CS
# Toolbox app ('app'). App-authored content ALWAYS queues for sysadmin review,
# even though the app pushes with a sysadmin service token.
CONTENT_SOURCES = {'ckan', 'app'}

_TAG_RE = re.compile(r'<[^>]*>')
_WS_RE = re.compile(r'\s+')


def _utcnow():
    return datetime.datetime.utcnow()


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


def _excerpt(html):
    """Strip tags + collapse whitespace + truncate to a plain-text teaser."""
    if not html:
        return ''
    text = _WS_RE.sub(' ', _TAG_RE.sub('', html)).strip()
    if len(text) > EXCERPT_LENGTH:
        text = text[:EXCERPT_LENGTH].rsplit(' ', 1)[0].rstrip() + '…'
    return text


def _resolve_project(data_dict):
    """Resolve the parent project from an id or a slug (None when unresolved)."""
    key = (data_dict.get('project_id') or data_dict.get('project')
           or data_dict.get('project_slug'))
    return db.get_project(key)


def _resolve_organization(data_dict):
    """Resolve ``owner_org`` (id or name) to an ACTIVE CKAN organization."""
    key = (data_dict.get('owner_org') or '').strip()
    if not key:
        return None
    group = model.Group.get(key)
    if (group is None or not group.is_organization
            or group.state != 'active'):
        return None
    return group


def _resolve_scope(data_dict):
    """XOR owner scope: exactly ONE of (project, organization).

    The app's outbox payload always references a project and never sends
    ``owner_org``, so its path is byte-identical to before this existed.
    """
    wants_project = bool(data_dict.get('project_id')
                         or data_dict.get('project')
                         or data_dict.get('project_slug'))
    wants_org = bool((data_dict.get('owner_org') or '').strip())
    if wants_project and wants_org:
        raise tk.ValidationError({'owner_org': [tk._(
            'Provide either a project or an organization, not both')]})
    if not wants_project and not wants_org:
        raise tk.ValidationError({'project_id': [tk._(
            'A project or an owner_org is required')]})
    if wants_project:
        project = _resolve_project(data_dict)
        if project is None:
            raise tk.ValidationError(
                {'project_id': [tk._('Project not found')]})
        return project, None
    organization = _resolve_organization(data_dict)
    if organization is None:
        raise tk.ValidationError(
            {'owner_org': [tk._('Organization not found')]})
    return None, organization


def _is_active_project_member(context, project_id):
    """True when the acting user is an ACTIVE member (any role) of the
    project -- the audience of its private content."""
    user_id = current_user_id(context)
    if not user_id:
        return False
    member = db.project_member(project_id, user_id)
    return bool(member and member.status == 'active')


def _can_manage_project(context, project_id):
    """Project-admin, initiative-admin OR sysadmin -- the write authorization,
    enforced in logic. Delegates to the shared composite in ``logic/auth`` so
    content and project pages can never drift apart."""
    return auth.can_manage_project(context, project_id)


def _can_view_unapproved(context, content):
    """Author / scope managers (PM, ADM, org editor) / sysadmin may view
    not-yet-approved content."""
    if auth._is_sysadmin(context):
        return True
    user_id = current_user_id(context)
    if not user_id:
        return False
    if content.created_by == user_id:
        return True
    if content.project_id:
        return (auth._is_project_admin(context, content.project_id)
                or auth._is_project_initiative_admin(
                    context, content.project_id))
    return auth._is_org_editor(context, content.organization_id)


def _can_view_private(context, content):
    """Private content is served only to the SCOPE's people: its managers
    (same set that may view unapproved rows) or a member of the scope --
    an active cs_project_member for project content, any org capacity for
    organization content. One membership query at most."""
    if _can_view_unapproved(context, content):
        return True
    user_id = current_user_id(context)
    if not user_id:
        return False
    if content.project_id:
        member = db.project_member(content.project_id, user_id)
        return bool(member and member.status == 'active')
    return auth._is_org_member(context, content.organization_id)


def _merge_extras(content, **updates):
    """Return the row's extras dict merged with ``updates`` (dropping None)."""
    extras = db._load_json(content.extras, {})
    if not isinstance(extras, dict):
        extras = {}
    for key, value in updates.items():
        if value is None:
            extras.pop(key, None)
        else:
            extras[key] = value
    return extras


def _validated_content(context, data_dict, content_type):
    """Run the content schema, returning validated data (raises on errors)."""
    schema = cs_schema.content_schema(content_type)
    incoming = {k: data_dict.get(k) for k in schema if k in data_dict}
    incoming['content_type'] = content_type
    data, errors = tk.navl_validate(incoming, schema, context)
    if errors:
        raise tk.ValidationError(errors)
    return data


# Sortable fields for csunesco_content_list (kept in sync with the db layer).
CONTENT_SORT_FIELDS = ('publish_date', 'created', 'title')
CONTENT_SORT_DIRECTIONS = ('asc', 'desc')


def parse_content_sort(value):
    """Parse a ``sort`` param ("field" or "field dir") against the allowlist.

    Pure helper (unit-tested): returns ``(field, direction)`` or None for
    empty input. Raises ``ValueError`` on anything outside the allowlist --
    the action maps that to a ValidationError. Never feeds raw client input
    to the ORM.
    """
    if not value or not str(value).strip():
        return None
    parts = str(value).strip().lower().split()
    if len(parts) > 2:
        raise ValueError('sort must be "field" or "field direction"')
    field = parts[0]
    direction = parts[1] if len(parts) == 2 else 'desc'
    if field not in CONTENT_SORT_FIELDS:
        raise ValueError('unknown sort field: %s' % field)
    if direction not in CONTENT_SORT_DIRECTIONS:
        raise ValueError('unknown sort direction: %s' % direction)
    return field, direction


def content_initial_status(is_sysadmin, source, content_type, trusted):
    """Moderation status a content row lands in (pure helper, unit-tested).

    * Sysadmin PORTAL-authored content publishes immediately (app-pushed rides
      the service token but is really member-authored, so it queues).
    * In a TRUSTED project, news/events skip review regardless of surface —
      the create/update is already gated to PM/ADM/sysadmin. Publications and
      maps ALWAYS queue (they carry external links/embeds).
    * Everything else queues as ``pending``.
    """
    if is_sysadmin and source != 'app':
        return 'approved'
    if trusted and content_type in ('cs-news', 'cs-event'):
        return 'approved'
    return 'pending'


def _resolve_source(data_dict):
    """Normalize + validate the ``source`` flag (defaults to ``ckan``)."""
    source = (data_dict.get('source') or 'ckan').strip().lower()
    if source not in CONTENT_SOURCES:
        raise tk.ValidationError({'source': [tk._(
            'Source must be one of: %s') % ', '.join(sorted(CONTENT_SOURCES))]})
    return source


def _type_extras(data):
    """Type-specific extras: only the keys that belong to the content type.

    Values are ``None`` for non-applicable keys so ``_merge_extras`` drops stale
    values after a type switch on update.
    """
    content_type = data.get('content_type')
    return {
        'terria_url': (data.get('terria_url') or None
                       if content_type == 'cs-map' else None),
        'doi': (data.get('doi') or None
                if content_type == 'cs-publication' else None),
        'authors': (data.get('authors') or None
                    if content_type == 'cs-publication' else None),
    }


def csunesco_content_create(context, data_dict):
    """Create a content row for an APPROVED project (PM/ADM/sysadmin) or for
    a CKAN organization (org admin/editor or sysadmin) -- exactly one scope."""
    if not context.get('user'):
        raise tk.NotAuthorized(
            tk._('You must be logged in to add content'))
    tk.check_access('csunesco_content_create', context, data_dict)

    data_dict = data_dict or {}
    project, organization = _resolve_scope(data_dict)
    if project is not None:
        if project.status != 'approved':
            raise tk.ValidationError({'project_id': [tk._(
                'Content can only be added to an approved project')]})
        # Enforce write authorization on the RESOLVED project inside the logic.
        if not _can_manage_project(context, project.id):
            raise tk.NotAuthorized(tk._(
                'Only the project admin or a sysadmin can add content'))
    else:
        # Org content: sysadmin or an admin/editor of the organization.
        if not auth.can_manage_content_scope(context, None, organization.id):
            raise tk.NotAuthorized(tk._(
                'Only an admin or editor of the organization can add its '
                'content'))

    content_type = (data_dict.get('content_type') or '').strip()
    data = _validated_content(context, data_dict, content_type)
    source = _resolve_source(data_dict)

    is_sysadmin = auth._is_sysadmin(context)
    body = sanitize_html(data.get('body'))
    now = _utcnow()

    content = db.CsContent()
    content.content_type = data['content_type']
    content.project_id = project.id if project is not None else None
    content.organization_id = (
        organization.id if organization is not None else None)
    # Derived from the parent project -- NEVER trust a client-supplied value.
    # Org content has NO initiative, which pins its moderation to sysadmins
    # (_is_content_initiative_admin returns False on NULL).
    content.initiative_group = (
        project.initiative_group if project is not None else None)
    content.title = data['title']
    content.body = body
    content.media = data.get('media')                  # JSON string
    content.publish_date = data.get('publish_date')
    content.end_date = data.get('end_date')
    content.visibility = data.get('visibility') or u'public'
    # Native column (filterable); mirrored in extras below for old consumers.
    content.source = source
    # Only a sysadmin may feature content; authors cannot self-promote.
    content.featured = bool(data.get('featured')) if is_sysadmin else False
    content.created_by = current_user_id(context)
    content.slug = db.unique_content_slug(data['title'])
    # Sysadmin portal-authored content publishes straight away; a TRUSTED
    # project's news/events skip review too (P2 policy toggle). Everything
    # else queues — including all app-pushed content in non-trusted projects
    # (the service token is a sysadmin, but the real author is a member) and
    # all org content by non-sysadmins (orgs have no trusted flag).
    content.status = content_initial_status(
        is_sysadmin, source, data['content_type'],
        bool(getattr(project, 'trusted', False)) if project is not None
        else False)
    extras = {'excerpt': _excerpt(body)}
    extras.update({k: v for k, v in _type_extras(data).items() if v})
    if source == 'app':
        extras['source'] = 'app'
        app_author = sanitize_html((data_dict.get('author') or '').strip())
        if app_author:
            extras['app_author'] = app_author
    content.extras = json.dumps(extras)
    content.created = now
    content.modified = now
    model.Session.add(content)
    model.Session.commit()
    return db.content_dictize(content)


def csunesco_content_update(context, data_dict):
    """Update a content row; a non-sysadmin edit re-queues it as ``pending``."""
    if not context.get('user'):
        raise tk.NotAuthorized(
            tk._('You must be logged in to edit content'))

    data_dict = data_dict or {}
    content = db.get_content(data_dict.get('id'))
    if content is None:
        raise tk.ObjectNotFound(tk._('Content not found'))

    # The owning scope is IMMUTABLE (like the slug): incoming project/org
    # references are ignored, authorization runs against the STORED scope.
    auth_dict = dict(data_dict)
    auth_dict['project_id'] = content.project_id
    auth_dict['organization_id'] = content.organization_id
    tk.check_access('csunesco_content_update', context, auth_dict)
    if not auth.can_manage_content_scope(
            context, content.project_id, content.organization_id):
        raise tk.NotAuthorized(tk._(
            'Only the scope\'s managers or a sysadmin can edit this content'))

    content_type = (data_dict.get('content_type')
                    or content.content_type or '').strip()
    data = _validated_content(context, data_dict, content_type)

    is_sysadmin = auth._is_sysadmin(context)
    body = sanitize_html(data.get('body'))

    content.content_type = data['content_type']
    content.title = data['title']
    content.body = body
    content.media = data.get('media')
    content.publish_date = data.get('publish_date')
    content.end_date = data.get('end_date')
    if 'visibility' in data:
        content.visibility = data.get('visibility') or u'public'
    # ``featured`` only changes when the caller EXPLICITLY sent the key: the
    # web editor omits it unless its (sysadmin-only) checkbox is rendered, so
    # a sysadmin editing a featured row no longer un-features it silently.
    if is_sysadmin and 'featured' in data:
        content.featured = bool(data.get('featured'))
    # Slug is permanent (URL stability) -- deliberately not regenerated.
    # A non-sysadmin edit goes back through review, EXCEPT news/events of a
    # trusted project (same policy as creation; org content has no trusted
    # flag, so it always re-queues).
    if not is_sysadmin:
        parent = (db.get_project(content.project_id)
                  if content.project_id else None)
        content.status = content_initial_status(
            False, 'ckan', data['content_type'],
            bool(getattr(parent, 'trusted', False)))
    content.extras = json.dumps(_merge_extras(
        content, excerpt=_excerpt(body), **_type_extras(data)))
    content.modified = _utcnow()
    model.Session.commit()
    return db.content_dictize(content)


def _stamp_review(context, content):
    """Record who moderated the row and when (audit trail)."""
    content.reviewed_by = current_user_id(context)
    content.reviewed_at = _utcnow()


def csunesco_content_approve(context, data_dict):
    """Approve content (sysadmin / initiative admin).

    Accepts ``pending`` rows (the normal queue) AND ``rejected`` ones -- the
    "undo" for a wrong rejection or a withdrawal, reachable from the panel's
    recently-moderated list. Optional ``featured`` toggle.
    """
    tk.check_access('csunesco_content_approve', context, data_dict)
    data_dict = data_dict or {}
    content = db.get_content(data_dict.get('id'))
    if content is None:
        raise tk.ObjectNotFound(tk._('Content not found'))
    if content.status not in ('pending', 'rejected'):
        raise tk.ValidationError({'status': [tk._(
            'Only pending or rejected content can be approved '
            '(current status: %s)') % content.status]})

    content.status = 'approved'
    if 'featured' in data_dict:
        content.featured = tk.asbool(data_dict.get('featured'))
    content.extras = json.dumps(
        _merge_extras(content, rejection_reason=None, withdrawn=None))
    _stamp_review(context, content)
    content.modified = _utcnow()
    model.Session.commit()
    return db.content_dictize(content)


def csunesco_content_reject(context, data_dict):
    """Reject content (sysadmin / initiative admin).

    The SANITIZED ``reason`` is stored in ``extras`` (NOT a native column, on
    purpose: every existing reader -- cards, panel, dictize merge -- already
    consumes it from there and nothing filters by it). Deliberately accepts any
    starting status for backwards compatibility; the explicit, state-checked
    way to unpublish an approved row is ``csunesco_content_withdraw``.
    """
    tk.check_access('csunesco_content_reject', context, data_dict)
    data_dict = data_dict or {}
    content = db.get_content(data_dict.get('id'))
    if content is None:
        raise tk.ObjectNotFound(tk._('Content not found'))

    reason = sanitize_html((data_dict.get('reason') or '').strip()) or None
    content.status = 'rejected'
    content.extras = json.dumps(
        _merge_extras(content, rejection_reason=reason))
    _stamp_review(context, content)
    content.modified = _utcnow()
    model.Session.commit()
    return db.content_dictize(content)


def csunesco_content_withdraw(context, data_dict):
    """Withdraw (unpublish) an APPROVED row -- sysadmin / initiative admin.

    The intentional counterpart to approve: it demands the row actually be
    published (a state check ``csunesco_content_reject`` deliberately lacks),
    marks ``extras.withdrawn`` so the panel can tell a withdrawal from a plain
    rejection, and keeps the row restorable via approve.
    """
    tk.check_access('csunesco_content_withdraw', context, data_dict)
    data_dict = data_dict or {}
    content = db.get_content(data_dict.get('id'))
    if content is None:
        raise tk.ObjectNotFound(tk._('Content not found'))
    if content.status != 'approved':
        raise tk.ValidationError({'status': [tk._(
            'Only approved content can be withdrawn (current status: %s)'
        ) % content.status]})

    reason = sanitize_html((data_dict.get('reason') or '').strip()) or None
    content.status = 'rejected'
    content.extras = json.dumps(
        _merge_extras(content, rejection_reason=reason, withdrawn=True))
    _stamp_review(context, content)
    content.modified = _utcnow()
    model.Session.commit()
    return db.content_dictize(content)


def csunesco_content_delete(context, data_dict):
    """Hard-delete a content row (SYSADMIN only).

    The one true removal: the row disappears and its slug becomes reusable
    (the unique index lives on the row). Everyday moderation should withdraw
    instead -- delete is for content that must not remain in the database at
    all (legal/privacy).
    """
    tk.check_access('csunesco_content_delete', context, data_dict)
    data_dict = data_dict or {}
    content = db.get_content(data_dict.get('id'))
    if content is None:
        raise tk.ObjectNotFound(tk._('Content not found'))
    content_id = content.id
    model.Session.delete(content)
    model.Session.commit()
    return {'id': content_id, 'deleted': True}


@tk.side_effect_free
def csunesco_content_list(context, data_dict):
    """List content with filtering + paging (SUMMARIZED rows by default).

    Anonymous and non-sysadmin callers are pinned to ``status='approved'`` HERE.
    ``body`` is excluded from every row unless ``include_body`` is truthy.
    """
    tk.check_access('csunesco_content_list', context, data_dict)
    db.ensure_mappers()
    data_dict = data_dict or {}

    content_type = data_dict.get('content_type')
    if content_type and content_type not in CONTENT_TYPES:
        raise tk.ValidationError({'content_type': [tk._(
            'Unknown content type: %s') % content_type]})

    project = _resolve_project(data_dict) if (
        data_dict.get('project_id') or data_dict.get('project')
        or data_dict.get('project_slug')) else None
    project_id = project.id if project is not None else None
    organization = (_resolve_organization({'owner_org':
                                           data_dict.get('organization')})
                    if data_dict.get('organization') else None)
    organization_id = organization.id if organization is not None else None
    initiative = data_dict.get('initiative') or data_dict.get('initiative_group')
    featured = (tk.asbool(data_dict.get('featured'))
                if data_dict.get('featured') is not None else None)

    # --- Additive filters ----------------------------------------------------
    q = (data_dict.get('q') or '').strip() or None

    from ckanext.csunesco.logic import validators as v
    try:
        date_from = v.csunesco_valid_iso_date(data_dict.get('date_from'))
        date_to = v.csunesco_valid_iso_date(data_dict.get('date_to'))
    except tk.Invalid as err:
        raise tk.ValidationError({'date_from': [err.error]})

    upcoming = tk.asbool(data_dict.get('upcoming') or False)
    if upcoming:
        if content_type and content_type != 'cs-event':
            raise tk.ValidationError({'upcoming': [tk._(
                'upcoming only applies to events (content_type=cs-event)')]})
        content_type = 'cs-event'

    created_by = (data_dict.get('created_by') or '').strip() or None
    if created_by:
        user = model.User.get(created_by)
        created_by = user.id if user is not None else created_by

    source_filter = (data_dict.get('source') or '').strip().lower() or None
    if source_filter and source_filter not in CONTENT_SOURCES:
        raise tk.ValidationError({'source': [tk._(
            'Source must be one of: %s') % ', '.join(sorted(CONTENT_SOURCES))]})

    raw_project_ids = data_dict.get('project_ids')
    project_ids = None
    if raw_project_ids:
        if isinstance(raw_project_ids, str):
            keys = [k.strip() for k in raw_project_ids.split(',') if k.strip()]
        elif isinstance(raw_project_ids, (list, tuple)):
            keys = [str(k).strip() for k in raw_project_ids if str(k).strip()]
        else:
            raise tk.ValidationError({'project_ids': [tk._(
                'project_ids must be a list or a comma-separated string')]})
        if len(keys) > MAX_PROJECT_IDS:
            raise tk.ValidationError({'project_ids': [tk._(
                'project_ids accepts at most %d projects') % MAX_PROJECT_IDS]})
        # Unknown keys resolve to nothing (and match nothing) on purpose.
        project_ids = db.resolve_project_ids(keys) or ['__none__']

    try:
        sort = parse_content_sort(data_dict.get('sort'))
    except ValueError as err:
        raise tk.ValidationError({'sort': [str(err)]})

    # Same scoping rule as ``csunesco_data_source_list``: a manager listing
    # THEIR OWN project also sees its unapproved rows.
    #
    # Without this, content was the one review pipeline with no way back for
    # its author. Rejecting stores a reason (see csunesco_content_reject) and
    # ``csunesco_content_show`` lets the author open the row -- but nothing
    # listed it, so a rejected item vanished from every page and the author was
    # never told. The landing block has passed ``show_status=can_manage`` to
    # the card since it was written; it simply never received a row to badge.
    #
    # The privilege is per-project on purpose: with no project filter (the
    # public /news and /events indexes) every non-sysadmin stays pinned to
    # approved, so nothing leaks into a public listing.
    is_sysadmin = auth._is_sysadmin(context)
    # An initiative admin filtering by THEIR OWN initiative is privileged too:
    # without this an ADM could not pull their initiative's pending rows by
    # API (only via the panel action).
    is_initiative_admin = bool(
        initiative
        and initiative in auth._admin_initiative_groups(context))
    privileged = is_sysadmin or is_initiative_admin or (
        project_id and auth.can_manage_project(context, project_id)) or (
        organization_id and auth._is_org_editor(context, organization_id))
    if privileged:
        status = data_dict.get('status')          # None -> all statuses
    else:
        status = 'approved'

    # Visibility: one pass, no per-row checks. Sysadmins see everything.
    # Scoped listings include the private rows of a scope the caller belongs
    # to (managers count via ``privileged``; plain members via ONE membership
    # query). Unscoped listings -- the public indexes -- are pinned to public,
    # so private content can never leak into a global feed.
    public_only = False
    private_project_ids = None
    private_org_ids = None
    if not is_sysadmin:
        public_only = True
        if project_id and (privileged
                           or _is_active_project_member(context, project_id)):
            private_project_ids = [project_id]
        if organization_id and (
                privileged
                or auth._is_org_member(context, organization_id)):
            private_org_ids = [organization_id]

    summary = not tk.asbool(data_dict.get('include_body'))
    limit = _positive_int(data_dict.get('limit'),
                          default=DEFAULT_LIST_LIMIT, maximum=MAX_LIST_LIMIT)
    offset = _positive_int(data_dict.get('offset'), default=0)

    total, rows = db.list_content(
        content_type=content_type,
        project_id=project_id,
        organization_id=organization_id,
        status=status,
        initiative_group=initiative,
        featured=featured,
        summary=summary,
        limit=limit,
        offset=offset,
        public_only=public_only,
        include_logged_in=bool(current_user_id(context)),
        private_project_ids=private_project_ids,
        private_org_ids=private_org_ids,
        q=q,
        date_from=date_from,
        date_to=date_to,
        upcoming=upcoming,
        created_by=created_by,
        source=source_filter,
        project_ids=project_ids,
        sort=sort,
    )
    results = [db.content_dictize(row, summary=summary) for row in rows]
    if tk.asbool(data_dict.get('include_project') or False):
        db.decorate_content_rows(results)

    return {
        'count': total,
        'results': results,
        'limit': limit,
        'offset': offset,
        'applied_filters': {
            'content_type': content_type,
            'project_id': project_id,
            'organization': organization_id,
            'initiative': initiative,
            'status': status,
            'featured': featured,
            'q': q,
            'date_from': date_from.isoformat() if date_from else None,
            'date_to': date_to.isoformat() if date_to else None,
            'upcoming': upcoming or None,
            'created_by': created_by,
            'source': source_filter,
            'project_ids': project_ids,
            'sort': ' '.join(sort) if sort else None,
        },
    }


@tk.side_effect_free
def csunesco_content_show(context, data_dict):
    """Show a single content row by id OR slug (full body).

    Approved content is public; not-yet-approved content is visible only to its
    author, the project admin or a sysadmin.
    """
    tk.check_access('csunesco_content_show', context, data_dict)
    data_dict = data_dict or {}
    id_or_slug = data_dict.get('id') or data_dict.get('slug')
    if not id_or_slug:
        raise tk.ValidationError({'id': [tk._('Missing value')]})
    content = db.get_content(id_or_slug)
    if content is None:
        raise tk.ObjectNotFound(tk._('Content not found'))
    if (content.status != 'approved'
            and not _can_view_unapproved(context, content)):
        raise tk.NotAuthorized(tk._('Not authorized to view this content'))
    # Private content: approved or not, only the scope's people may read it.
    if (content.visibility == 'private'
            and not _can_view_private(context, content)):
        raise tk.NotAuthorized(tk._('Not authorized to view this content'))
    # 'logged-in' content (spec's middle tier): any authenticated portal
    # user; anonymous visitors are refused.
    if (content.visibility == 'logged-in'
            and not current_user_id(context)):
        raise tk.NotAuthorized(tk._('Not authorized to view this content'))
    return db.content_dictize(content)


def get_actions():
    return {
        'csunesco_content_create': csunesco_content_create,
        'csunesco_content_update': csunesco_content_update,
        'csunesco_content_approve': csunesco_content_approve,
        'csunesco_content_reject': csunesco_content_reject,
        'csunesco_content_withdraw': csunesco_content_withdraw,
        'csunesco_content_delete': csunesco_content_delete,
        'csunesco_content_list': csunesco_content_list,
        'csunesco_content_show': csunesco_content_show,
    }
