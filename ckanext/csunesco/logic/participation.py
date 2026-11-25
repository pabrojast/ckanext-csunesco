"""Totales de participación deduplicados, sin identidades en la caché pública."""
import hashlib
import json
import time

from ckanext.csunesco import constants, db
from ckanext.csunesco.logic import snapshots

MAX_AGE = 900
# The period of the portal repair pass (snapshots.start_worker). Every uWSGI
# worker of every pod runs that pass, so a total is confirmed many times per
# period; only one of those confirmations has to renew the file.
RENEW_AFTER = 300


def _key(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode('utf-8')).hexdigest()


def cached_count(payload):
    # Sin proyectos no hay miembros de la app que incorporar.
    if not payload['project_slugs']:
        return len(set(payload['ckan_user_ids']) - set(payload['excluded_ckan_user_ids']))
    try:
        return _stored(_key(payload), MAX_AGE)
    except (OSError, ValueError, snapshots.tk.ValidationError):
        snapshots.log.warning('Participation cache unavailable')
        return None


def _stored(key, max_age):
    """The stored total, if it is valid and at most max_age seconds old."""
    record = snapshots.read_private('participation', key)
    if not isinstance(record, dict) or not 0 <= time.time() - record.get('created_at', 0) <= max_age:
        return None
    value = record.get('citizen_scientists')
    return value if type(value) is int and value >= 0 else None


def refresh(initiative_group=None, project_id=None):
    payload = db.participation_inputs(initiative_group, project_id=project_id)
    if not payload['project_slugs']:
        return cached_count(payload)
    result = snapshots.app_request('/internal/ckan/participation-summary', payload)
    value = result.get('citizen_scientists')
    if type(value) is not int or value < 0:
        raise ValueError('Invalid participation total')
    if project_id:
        db.stats_set(project_id, citizen_scientists=value)
        db.Session.commit()
    key = _key(payload)
    # A total that has not changed is renewed once per repair period, not once
    # per worker: the file lives on a share that is backed up while it is
    # written. A total that changed, expired or cannot be read is written now.
    try:
        unchanged = _stored(key, RENEW_AFTER) == value
    except (OSError, ValueError, TypeError):
        unchanged = False
    if not unchanged:
        snapshots.write_private('participation', key,
                                {'created_at': time.time(), 'citizen_scientists': value})
    return value


def refresh_all():
    failed = 0
    for project in db.Session.query(db.CsProject).filter(db.public_project_clause()).all():
        try:
            refresh(project_id=project.id)
        except Exception:
            db.Session.rollback()
            failed += 1
            snapshots.log.warning('Keeping last participation total for project %s', project.id)
    for initiative in [None] + [item['name'] for item in constants.CS_INITIATIVES]:
        try:
            refresh(initiative)
        except Exception:
            failed += 1
            snapshots.log.warning('Keeping last participation total for %s', initiative or 'hub')
    return failed
