# encoding: utf-8
"""Data-source actions: connect / approve / reject / list / show.

A *data source* links an approved CS project to a PUBLIC form in the CS Toolbox
app (ofform) whose observations should be published on IHP-WINS. The moderation
contract is stricter than content: EVERY new source starts ``pending`` -- even
when created by a sysadmin or pushed by the app's service token -- because
approval is what creates a real CKAN dataset on the portal.

On approval (sysadmin only) ``package_sync.ensure_dataset`` creates/refreshes
the CKAN package whose resources point at this plugin's live proxy routes
(``/citizen-science/data/<id>.csv`` / ``.geojson``). If package creation fails
the row STAYS pending so the reviewer can retry after fixing configuration.
"""
import datetime
import json
import logging

import ckan.plugins.toolkit as tk
import ckan.model as model

from ckanext.csunesco import db
from ckanext.csunesco.logic import auth
from ckanext.csunesco.logic import package_sync
from ckanext.csunesco.logic.sanitize import sanitize_html
from ckanext.csunesco.logic.action import current_user_id

log = logging.getLogger(__name__)

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 100

DATA_SOURCES = {'ckan', 'app'}


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


def _resolve_project(data_dict):
    key = (data_dict.get('project_id') or data_dict.get('project')
           or data_dict.get('project_slug'))
    return db.get_project(key)


def _can_manage_project(context, project_id):
    return (auth._is_sysadmin(context)
            or auth._is_project_admin(context, project_id))


def _required_form_id(data_dict):
    try:
        form_id = int(data_dict.get('form_id'))
    except (TypeError, ValueError):
        raise tk.ValidationError({'form_id': [tk._(
            'A numeric CS Toolbox form id is required')]})
    if form_id <= 0:
        raise tk.ValidationError({'form_id': [tk._(
            'A numeric CS Toolbox form id is required')]})
    return form_id


def csunesco_data_source_create(context, data_dict):
    """Request publication of an ofform form's data for an APPROVED project.

    Idempotent on ``(project, form_id)``: a rejected row is re-queued as
    pending (fresh title/description, reason cleared); a pending/approved row
    is returned unchanged with ``already_requested: True``.
    """
    if not context.get('user'):
        raise tk.NotAuthorized(tk._('You must be logged in to connect data'))
    tk.check_access('csunesco_data_source_create', context, data_dict)

    data_dict = data_dict or {}
    project = _resolve_project(data_dict)
    if project is None:
        raise tk.ValidationError({'project_id': [tk._('Project not found')]})
    if project.status != 'approved':
        raise tk.ValidationError({'project_id': [tk._(
            'Data can only be connected to an approved project')]})
    if not _can_manage_project(context, project.id):
        raise tk.NotAuthorized(tk._(
            'Only the project admin or a sysadmin can connect data'))

    form_id = _required_form_id(data_dict)
    title = (data_dict.get('title') or '').strip()
    if not title:
        raise tk.ValidationError({'title': [tk._('Missing value')]})
    description = sanitize_html((data_dict.get('description') or '').strip())
    source = (data_dict.get('source') or 'ckan').strip().lower()
    if source not in DATA_SOURCES:
        raise tk.ValidationError({'source': [tk._(
            'Source must be one of: %s') % ', '.join(sorted(DATA_SOURCES))]})
    # Suggested CKAN organization for the dataset (the app keeps its orgs
    # synchronized with the portal). NOT validated here -- the sysadmin sees
    # and may change it at approval time, where it is resolved and checked.
    owner_org = (data_dict.get('owner_org') or '').strip() or None

    now = _utcnow()
    existing = db.get_data_source_by_form(project.id, form_id)
    if existing is not None:
        if existing.status == 'rejected':
            # Re-request after rejection: back through review, fresh details.
            existing.status = 'pending'
            existing.title = title
            existing.description = description
            existing.source = source
            existing.rejection_reason = None
            existing.reviewed_by = None
            existing.reviewed_at = None
            extras = db._load_json(existing.extras, {})
            if not isinstance(extras, dict):
                extras = {}
            if owner_org:
                extras['owner_org'] = owner_org
            else:
                extras.pop('owner_org', None)
            existing.extras = json.dumps(extras)
            existing.modified = now
            model.Session.commit()
            return db.data_source_dictize(existing)
        result = db.data_source_dictize(existing)
        result['already_requested'] = True
        return result

    data_source = db.CsDataSource()
    data_source.project_id = project.id
    data_source.form_id = form_id
    data_source.title = title
    data_source.description = description
    # ALWAYS pending: approval is what publishes a dataset on the portal, so
    # not even a sysadmin author skips review here.
    data_source.status = 'pending'
    data_source.source = source
    data_source.created_by = current_user_id(context)
    if owner_org:
        data_source.extras = json.dumps({'owner_org': owner_org})
    data_source.created = now
    data_source.modified = now
    model.Session.add(data_source)
    model.Session.commit()
    return db.data_source_dictize(data_source)


def csunesco_data_source_approve(context, data_dict):
    """Approve a pending data source (sysadmin): creates the CKAN dataset.

    Optional ``owner_org`` overrides which organization owns the dataset
    (default: the app-suggested org when it exists on the portal, else the
    configured fallback). The dataset is created BEFORE the status flips; if
    creation fails the row stays pending and a generic error is raised
    (details go to the log only).
    """
    tk.check_access('csunesco_data_source_approve', context, data_dict)
    data_dict = data_dict or {}
    data_source = db.get_data_source(data_dict.get('id'))
    if data_source is None:
        raise tk.ObjectNotFound(tk._('Data source not found'))
    if data_source.status != 'pending':
        raise tk.ValidationError({'status': [tk._(
            'Only pending data sources can be approved (current status: %s)'
        ) % data_source.status]})
    project = db.get_project(data_source.project_id)
    if project is None:
        raise tk.ValidationError({'project_id': [tk._('Project not found')]})
    override_org = (data_dict.get('owner_org') or '').strip() or None

    try:
        sync = package_sync.ensure_dataset(
            context, project, data_source, override_org=override_org)
    except tk.ValidationError:
        raise
    except Exception:
        log.warning('csunesco: dataset creation failed for data source %s',
                    data_source.id, exc_info=True)
        raise tk.ValidationError({'package': [tk._(
            'The dataset could not be created on the portal. '
            'The request remains pending.')]})

    now = _utcnow()
    data_source.status = 'approved'
    data_source.reviewed_by = current_user_id(context)
    data_source.reviewed_at = now
    data_source.rejection_reason = None
    data_source.ckan_package_id = sync['package_id']
    extras = db._load_json(data_source.extras, {})
    if not isinstance(extras, dict):
        extras = {}
    extras['resource_ids'] = sync['resource_ids']
    # Record the org that actually took the dataset so re-approvals and the
    # admin UI show the real owner (not just the original suggestion).
    extras['owner_org'] = sync['owner_org']
    data_source.extras = json.dumps(extras)
    data_source.modified = now
    model.Session.commit()
    return db.data_source_dictize(data_source)


def csunesco_data_source_reject(context, data_dict):
    """Reject a pending data source (sysadmin), with a sanitized reason."""
    tk.check_access('csunesco_data_source_reject', context, data_dict)
    data_dict = data_dict or {}
    data_source = db.get_data_source(data_dict.get('id'))
    if data_source is None:
        raise tk.ObjectNotFound(tk._('Data source not found'))
    if data_source.status != 'pending':
        raise tk.ValidationError({'status': [tk._(
            'Only pending data sources can be rejected (current status: %s)'
        ) % data_source.status]})

    now = _utcnow()
    data_source.status = 'rejected'
    data_source.reviewed_by = current_user_id(context)
    data_source.reviewed_at = now
    data_source.rejection_reason = sanitize_html(
        (data_dict.get('reason') or '').strip()) or None
    data_source.modified = now
    model.Session.commit()
    return db.data_source_dictize(data_source)


def _can_view_unapproved(context, data_source):
    if auth._is_sysadmin(context):
        return True
    user_id = current_user_id(context)
    if not user_id:
        return False
    if data_source.created_by == user_id:
        return True
    return auth._is_project_admin(context, data_source.project_id)


@tk.side_effect_free
def csunesco_data_source_list(context, data_dict):
    """List data sources for a project (public callers see approved only)."""
    tk.check_access('csunesco_data_source_list', context, data_dict)
    db.ensure_mappers()
    data_dict = data_dict or {}

    project = _resolve_project(data_dict) if (
        data_dict.get('project_id') or data_dict.get('project')
        or data_dict.get('project_slug')) else None
    project_id = project.id if project is not None else None

    privileged = auth._is_sysadmin(context) or (
        project_id and auth._is_project_admin(context, project_id))
    if privileged:
        status = data_dict.get('status')      # None -> all statuses
    else:
        status = 'approved'

    limit = _positive_int(data_dict.get('limit'),
                          default=DEFAULT_LIST_LIMIT, maximum=MAX_LIST_LIMIT)
    offset = _positive_int(data_dict.get('offset'), default=0)
    total, rows = db.list_data_sources(
        project_id=project_id, status=status, limit=limit, offset=offset)
    return {
        'count': total,
        'results': [db.data_source_dictize(row) for row in rows],
        'limit': limit,
        'offset': offset,
    }


@tk.side_effect_free
def csunesco_data_source_show(context, data_dict):
    """Show one data source (approved is public; else creator/admin/sysadmin)."""
    tk.check_access('csunesco_data_source_show', context, data_dict)
    data_dict = data_dict or {}
    data_source = db.get_data_source(data_dict.get('id'))
    if data_source is None:
        raise tk.ObjectNotFound(tk._('Data source not found'))
    if (data_source.status != 'approved'
            and not _can_view_unapproved(context, data_source)):
        raise tk.NotAuthorized(tk._('Not authorized to view this data source'))
    return db.data_source_dictize(data_source)


def get_actions():
    return {
        'csunesco_data_source_create': csunesco_data_source_create,
        'csunesco_data_source_approve': csunesco_data_source_approve,
        'csunesco_data_source_reject': csunesco_data_source_reject,
        'csunesco_data_source_list': csunesco_data_source_list,
        'csunesco_data_source_show': csunesco_data_source_show,
    }
