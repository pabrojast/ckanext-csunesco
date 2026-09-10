"""Per-content review records using the existing CKAN Content queue.

Review rows are immutable CsContent records. Published rows retain stable IDs
and URLs while a replacement waits in the standard administrator queue.
"""
import datetime
import logging

import ckan.model as model
import ckan.plugins.toolkit as tk
from ckanext.csunesco import db
from ckanext.csunesco.logic import portal

KEY = '_app_content_review'


def metadata(row):
    return db._load_json(row.extras, {}).get(KEY)


def _rows(project_id, app_id):
    return [row for row in model.Session.query(db.CsContent).filter(db.CsContent.project_id == project_id).all()
            if metadata(row) and str(metadata(row).get('app_content_id')) == str(app_id)]


def result(row):
    meta = metadata(row)
    return dict({key: meta[key] for key in ('app_project_id', 'app_content_id', 'revision', 'checksum')},
                status=meta['status'], ckan_id=meta.get('target_id'), reason=meta.get('reason'))


def callback(row):
    from ckanext.csunesco.logic import snapshots
    try:
        snapshots.app_request('/internal/ckan/content-reviews', result(row))
    except Exception:
        logging.getLogger(__name__).warning('Content review callback deferred; polling will retry')


def status(context, data):
    portal.require_service(context)
    project = portal.resolve_project(data)
    for row in _rows(project.id, data.get('app_content_id')):
        meta = metadata(row)
        if meta['revision'] == data.get('revision') and meta['checksum'] == data.get('checksum'):
            if str(meta['app_project_id']) != str(data.get('app_project_id')):
                raise tk.ValidationError({'project': ['Project mismatch']})
            return result(row)
    raise tk.ObjectNotFound('Content revision not found')


def _target(project, data):
    for row in model.Session.query(db.CsContent).filter(db.CsContent.project_id == project.id).all():
        extras = db._load_json(row.extras, {})
        if not metadata(row) and str(extras.get('app_content_id')) == str(data['app_content_id']):
            return row
    if data.get('ckan_id'):
        row = db.get_content(data['ckan_id'])
        if row is None or row.project_id != project.id or metadata(row):
            raise tk.ValidationError({'content': ['Content belongs to another project']})
        return row
    return None


def apply(context, data):
    from ckanext.csunesco.logic.action.content import _validated_content, _type_extras
    portal.require_service(context)
    if data.get('schema_version') != 2:
        raise tk.ValidationError({'schema_version': ['Expected version 2']})
    project = portal.resolve_project(data)
    # Serialize retries and review decisions per project in PostgreSQL.
    project = model.Session.query(db.CsProject).filter(db.CsProject.id == project.id).with_for_update().one()
    effective = portal.actor_context(context, data, project)
    if project.status != 'approved':
        raise tk.ValidationError({'project': ['Project must be approved']})
    app_project = portal.metadata(project).get('app_project_id')
    if app_project and str(app_project) != str(data.get('app_project_id')):
        raise tk.ValidationError({'project': ['Project mismatch']})
    if not data.get('app_content_id') or not isinstance(data.get('revision'), int) or data['revision'] < 1:
        raise tk.ValidationError({'revision': ['Stable content ID and positive revision required']})
    payload = data.get('payload')
    if not isinstance(payload, dict) or len(portal.canonical(payload).encode()) > 1_000_000 or portal.checksum(payload) != data.get('checksum'):
        raise tk.ValidationError({'checksum': ['Content checksum mismatch']})
    intent = data.get('intent')
    if intent not in ('submit', 'withdraw'):
        raise tk.ValidationError({'intent': ['Unknown intent']})
    existing = _rows(project.id, data['app_content_id'])
    for row in existing:
        meta = metadata(row)
        if meta['revision'] == data['revision']:
            if meta['checksum'] != data['checksum'] or meta['intent'] != intent:
                raise tk.ValidationError({'revision': ['Revision already has different content']})
            return result(row)
    if any(metadata(row)['revision'] > data['revision'] for row in existing):
        raise tk.ValidationError({'revision': ['Stale content revision']})
    values = _validated_content(effective, payload, data.get('content_type'))
    from ckanext.csunesco.logic.sanitize import sanitize_html
    values['body'] = sanitize_html(values.get('body'))
    if intent == 'submit' and values.get('visibility', 'public') != 'public':
        raise tk.ValidationError({'visibility': ['Private content cannot be published']})
    target = _target(project, data)
    now = datetime.datetime.utcnow()
    row = db.CsContent()
    row.project_id = project.id
    row.initiative_group = project.initiative_group
    row.slug = db.unique_content_slug(values['title'] + '-review')
    row.created_by = effective['auth_user_obj'].id
    row.created = row.modified = now
    for key in ('content_type', 'title', 'body', 'media', 'publish_date', 'end_date'):
        setattr(row, key, values.get(key))
    row.source = 'app'
    row.visibility = 'public'
    row.status = 'pending' if intent == 'submit' else 'revision-withdrawn'
    meta = {key: data[key] for key in ('app_project_id', 'app_content_id', 'revision', 'checksum', 'intent')}
    meta.update(status='pending' if intent == 'submit' else 'withdrawn', target_id=target.id if target else None)
    row.extras = portal.canonical(dict(_type_extras(values), **{KEY: meta}))
    model.Session.add(row)
    for old in existing:
        old_meta = metadata(old)
        if old_meta['status'] == 'pending':
            old_meta['status'] = 'superseded'
            extras = db._load_json(old.extras, {})
            extras[KEY] = old_meta
            old.extras = portal.canonical(extras)
            old.status = 'revision-superseded'
    if intent == 'withdraw' and target:
        target.status = 'rejected'
        extras = db._load_json(target.extras, {})
        extras.update(withdrawn=True, independent_content=True)
        target.extras = portal.canonical(extras)
        target.modified = now
    model.Session.commit()
    return result(row)


def decide(context, row, approved, reason=None, featured=None):
    model.Session.query(db.CsProject).filter(db.CsProject.id == row.project_id).with_for_update().one()
    model.Session.refresh(row)
    meta = metadata(row)
    if not meta:
        return None
    if meta['status'] not in ('pending', 'rejected'):
        raise tk.ValidationError({'status': ['This content revision is no longer awaiting review']})
    if any(metadata(other)['revision'] > meta['revision'] for other in _rows(row.project_id, meta['app_content_id'])):
        raise tk.ValidationError({'revision': ['A newer content revision supersedes this request']})
    now = datetime.datetime.utcnow()
    target = _target(db.get_project(row.project_id), dict(meta, ckan_id=meta.get('target_id')))
    if approved:
        if target is None:
            target = db.CsContent()
            target.slug = db.unique_content_slug(row.title)
            target.project_id = row.project_id
            target.initiative_group = row.initiative_group
            target.created = now
            target.created_by = row.created_by
            model.Session.add(target)
        for key in ('content_type', 'title', 'body', 'media', 'publish_date', 'end_date'):
            setattr(target, key, getattr(row, key))
        target.visibility = 'public'
        target.status = 'approved'
        target.source = 'app'
        target.modified = now
        target.reviewed_at = now
        from ckanext.csunesco.logic.action import current_user_id
        target.reviewed_by = current_user_id(context)
        if featured is not None:
            target.featured = tk.asbool(featured)
        extras = db._load_json(row.extras, {})
        extras.pop(KEY, None)
        extras.update(app_content_id=str(meta['app_content_id']), independent_content=True)
        target.extras = portal.canonical(extras)
        model.Session.flush()
        meta['target_id'] = target.id
    meta.update(status='approved' if approved else 'rejected', reason=reason)
    extras = db._load_json(row.extras, {})
    extras[KEY] = meta
    row.extras = portal.canonical(extras)
    # Only the stable target can be public. Rejected review records stay in
    # the standard moderated list so administrators can revisit a decision.
    row.status = 'revision-approved' if approved else 'rejected'
    row.modified = now
    from ckanext.csunesco.logic.action.content import _stamp_review
    _stamp_review(context, row)
    model.Session.commit()
    callback(row)
    return db.content_dictize(target if approved else row)
