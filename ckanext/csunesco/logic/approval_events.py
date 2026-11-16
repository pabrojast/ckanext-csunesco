"""Avisos durables hacia la app, separados de la decisión y del contenido."""
import logging
import json
import uuid

import ckan.model as model
import ckan.plugins.toolkit as tk
from ckanext.csunesco import db

KEY = '_approval_notifications'
log = logging.getLogger(__name__)


def record(project, kind='project_review', actor_id=None, note=None):
    if not tk.config.get('ckanext.csunesco.ofform_callback_token'):
        return
    model.Session.flush()
    # Serializar solicitudes simultáneas sin perder avisos en el JSON privado.
    model.Session.refresh(project, attribute_names=['extras'], with_for_update=True)
    author = model.User.get(actor_id or project.created_by)
    if author is None:
        return
    extras = dict(db._load_json(project.extras, {}))
    events = list(extras.get(KEY) or [])
    events.append({'event_id': 'ckan:' + uuid.uuid4().hex, 'kind': kind,
        'project_slug': project.slug, 'project_title': project.title,
        'actor_username': author.name, 'actor_name': getattr(author, 'fullname', None) or author.name,
        'actor_email': getattr(author, 'email', None) or '', 'language': getattr(author, 'language', None) or 'en',
        'note': (note or '')[:4000]})
    extras[KEY] = events
    project.extras = json.dumps(extras)


def flush(project):
    """La respuesta perdida se reintenta con el mismo id; la app deduplica."""
    from ckanext.csunesco.logic import snapshots, portal
    events = list(db._load_json(project.extras, {}).get(KEY) or [])
    for event in events:
        try:
            snapshots.app_request('/internal/ckan/approval-events', event)
        except Exception as exc:
            log.warning('Approval notification pending for project %s: %s', project.id, type(exc).__name__)
            break
        # Bloquear y releer después del HTTP conserva avisos concurrentes.
        model.Session.refresh(project, attribute_names=['extras'], with_for_update=True)
        extras = dict(db._load_json(project.extras, {}))
        extras[KEY] = [e for e in extras.get(KEY, []) if e['event_id'] != event['event_id']]
        project.extras = portal.canonical(extras)
        model.Session.commit()


def project_changed(project, data=None):
    """Tell the app a decision on this project was just saved, so it reflects
    it now instead of on its next reconcile sweep (up to five minutes, while
    the person already read "Approved" and still had no access).

    Best effort and after the commit: the sweep stays the guarantee, so a
    failure is only logged. Decisions the app itself made through the portal
    (``actor_id`` or ``decided_by_username``) are not echoed back: the app
    already holds them, and the call would race its own open transaction.
    """
    data = data or {}
    if project is None or data.get('actor_id') or data.get('decided_by_username'):
        return
    if not tk.config.get('ckanext.csunesco.ofform_callback_token'):
        return
    from ckanext.csunesco.logic import snapshots
    try:
        snapshots.app_request('/internal/ckan/project-changed', {'project_slug': project.slug}, timeout=5)
    except Exception as exc:
        log.info('App not told about project %s yet (%s); its sweep will catch up', project.id, type(exc).__name__)
