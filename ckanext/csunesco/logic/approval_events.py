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
