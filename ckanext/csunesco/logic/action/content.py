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


def _can_manage_project(context, project_id):
    """Project-admin, initiative-admin OR sysadmin -- the write authorization,
    enforced in logic."""
    return (auth._is_sysadmin(context)
            or auth._is_project_admin(context, project_id)
            or auth._is_project_initiative_admin(context, project_id))


def _can_view_unapproved(context, content):
    """Author / project-admin / initiative-admin / sysadmin may view
    not-yet-approved content."""
    if auth._is_sysadmin(context):
        return True
    user_id = current_user_id(context)
    if not user_id:
        return False
    if content.created_by == user_id:
        return True
    return (auth._is_project_admin(context, content.project_id)
            or auth._is_project_initiative_admin(context, content.project_id))


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
    """Create a news/event row for an APPROVED project (project-admin/sysadmin)."""
    if not context.get('user'):
        raise tk.NotAuthorized(
            tk._('You must be logged in to add content'))
    tk.check_access('csunesco_content_create', context, data_dict)

    data_dict = data_dict or {}
    project = _resolve_project(data_dict)
    if project is None:
        raise tk.ValidationError({'project_id': [tk._('Project not found')]})
    if project.status != 'approved':
        raise tk.ValidationError({'project_id': [tk._(
            'Content can only be added to an approved project')]})
    # Enforce write authorization on the RESOLVED project inside the logic.
    if not _can_manage_project(context, project.id):
        raise tk.NotAuthorized(tk._(
            'Only the project admin or a sysadmin can add content'))

    content_type = (data_dict.get('content_type') or '').strip()
    data = _validated_content(context, data_dict, content_type)
    source = _resolve_source(data_dict)

    is_sysadmin = auth._is_sysadmin(context)
    body = sanitize_html(data.get('body'))
    now = _utcnow()

    content = db.CsContent()
    content.content_type = data['content_type']
    content.project_id = project.id
    # Derived from the parent project -- NEVER trust a client-supplied value.
    content.initiative_group = project.initiative_group
    content.title = data['title']
    content.body = body
    content.media = data.get('media')                  # JSON string
    content.publish_date = data.get('publish_date')
    content.end_date = data.get('end_date')
    # Only a sysadmin may feature content; authors cannot self-promote.
    content.featured = bool(data.get('featured')) if is_sysadmin else False
    content.created_by = current_user_id(context)
    content.slug = db.unique_content_slug(data['title'])
    # Sysadmin-authored content is published straight away; everyone else queues.
    # App-pushed content queues too: the app's service token is a sysadmin, but
    # the real author is a project member, so it must go through review.
    content.status = ('approved' if is_sysadmin and source != 'app'
                      else 'pending')
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

    auth_dict = dict(data_dict)
    auth_dict['project_id'] = content.project_id
    tk.check_access('csunesco_content_update', context, auth_dict)
    if not _can_manage_project(context, content.project_id):
        raise tk.NotAuthorized(tk._(
            'Only the project admin or a sysadmin can edit this content'))

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
    if is_sysadmin:
        content.featured = bool(data.get('featured'))
    # Slug is permanent (URL stability) -- deliberately not regenerated.
    # A non-sysadmin edit must go back through review.
    if not is_sysadmin:
        content.status = 'pending'
    content.extras = json.dumps(_merge_extras(
        content, excerpt=_excerpt(body), **_type_extras(data)))
    content.modified = _utcnow()
    model.Session.commit()
    return db.content_dictize(content)


def csunesco_content_approve(context, data_dict):
    """Approve pending content (sysadmin only); optional ``featured`` toggle."""
    tk.check_access('csunesco_content_approve', context, data_dict)
    data_dict = data_dict or {}
    content = db.get_content(data_dict.get('id'))
    if content is None:
        raise tk.ObjectNotFound(tk._('Content not found'))
    if content.status != 'pending':
        raise tk.ValidationError({'status': [tk._(
            'Only pending content can be approved (current status: %s)'
        ) % content.status]})

    content.status = 'approved'
    if 'featured' in data_dict:
        content.featured = tk.asbool(data_dict.get('featured'))
    content.extras = json.dumps(
        _merge_extras(content, rejection_reason=None))
    content.modified = _utcnow()
    model.Session.commit()
    return db.content_dictize(content)


def csunesco_content_reject(context, data_dict):
    """Reject content (sysadmin only), storing a SANITIZED reason in extras."""
    tk.check_access('csunesco_content_reject', context, data_dict)
    data_dict = data_dict or {}
    content = db.get_content(data_dict.get('id'))
    if content is None:
        raise tk.ObjectNotFound(tk._('Content not found'))

    reason = sanitize_html((data_dict.get('reason') or '').strip()) or None
    content.status = 'rejected'
    content.extras = json.dumps(
        _merge_extras(content, rejection_reason=reason))
    content.modified = _utcnow()
    model.Session.commit()
    return db.content_dictize(content)


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
    initiative = data_dict.get('initiative') or data_dict.get('initiative_group')
    featured = (tk.asbool(data_dict.get('featured'))
                if data_dict.get('featured') is not None else None)

    if auth._is_sysadmin(context):
        status = data_dict.get('status')          # None -> all statuses
    else:
        status = 'approved'

    summary = not tk.asbool(data_dict.get('include_body'))
    limit = _positive_int(data_dict.get('limit'),
                          default=DEFAULT_LIST_LIMIT, maximum=MAX_LIST_LIMIT)
    offset = _positive_int(data_dict.get('offset'), default=0)

    total, rows = db.list_content(
        content_type=content_type,
        project_id=project_id,
        status=status,
        initiative_group=initiative,
        featured=featured,
        summary=summary,
        limit=limit,
        offset=offset,
    )
    results = [db.content_dictize(row, summary=summary) for row in rows]

    return {
        'count': total,
        'results': results,
        'limit': limit,
        'offset': offset,
        'applied_filters': {
            'content_type': content_type,
            'project_id': project_id,
            'initiative': initiative,
            'status': status,
            'featured': featured,
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
    return db.content_dictize(content)


def get_actions():
    return {
        'csunesco_content_create': csunesco_content_create,
        'csunesco_content_update': csunesco_content_update,
        'csunesco_content_approve': csunesco_content_approve,
        'csunesco_content_reject': csunesco_content_reject,
        'csunesco_content_list': csunesco_content_list,
        'csunesco_content_show': csunesco_content_show,
    }
