"""Datashare checks and current-policy feeds for managed CS partitions."""
import json
import logging

import ckan.plugins.toolkit as tk
from ckanext.csunesco import db

log = logging.getLogger(__name__)
LEVELS = ('public', 'private', 'confidential', 'findable', 'viewable', 'restricted')


def dataset_level(level):
    """Koen's Private shares metadata, not measurements: datashare Findable."""
    return 'findable' if level == 'private' else level


def validate_project_access(data, current=None):
    if not {'data_access', 'data_access_justification'}.intersection(data):
        return
    level = data.get('data_access', (current or {}).get('data_access'))
    reason = (data.get('data_access_justification', (current or {}).get('data_access_justification')) or '').strip()
    if len(reason) > 2000:
        raise tk.ValidationError({'data_access_justification': ['Maximum 2000 characters']})
    if level in ('private', 'confidential') and not reason:
        raise tk.ValidationError({'data_access_justification': [
            tk._('Please indicate why the project cannot operate under open access settings')]})
    data['data_access_justification'] = reason if level in ('private', 'confidential') else ''


def as_dict(source):
    return source if isinstance(source, dict) else db.data_source_dictize(source)


def permitted(context, source, capability='can_view_resources'):
    source = as_dict(source)
    if source.get('status') != 'approved':
        return False
    package_id = source.get('ckan_package_id')
    if not package_id:
        return False
    try:
        access = tk.get_action('datashare_access_check')(dict(context), {'id': package_id})
        return bool(access.get(capability))
    except (tk.ObjectNotFound, tk.NotAuthorized, KeyError):
        return False


def require(context, source, capability='can_view_resources'):
    if not permitted(context, source, capability):
        raise tk.ObjectNotFound(tk._('Data source not found'))


def bundle(source, materialize=True):
    """No stale-cache fallback: the app validates policy revision on every read."""
    from ckanext.csunesco.logic import snapshots, ofform
    source = as_dict(source)
    partition = source.get('partition_id')
    if not partition:
        # Legacy sources remain anonymous/public-only until explicit migration.
        return {'dashboard': ofform.fetch_dashboard_data(source['form_id']),
                'csv': ofform.fetch_csv(source['form_id'])}
    try:
        result = snapshots.app_request('/internal/ckan/forms/%d/snapshot?partition_id=%d&policy_revision=%d' % (
            int(source['form_id']), int(partition), int(source['policy_revision'])))
        if (result.get('partition_id') != int(partition)
                or result.get('policy_revision') != int(source['policy_revision'])
                or result.get('visibility') != source.get('access_level')):
            raise ValueError('Partition response mismatch')
        if materialize:
            source_row = db.get_data_source(source.get('id'))
            if source_row is None:
                raise ValueError('Source unavailable')
            dashboard_data, hashes = snapshots.materialize_observation_media(source_row, result['dashboard'], result.get('media') or [])
            result.update(dashboard=dashboard_data, csv=snapshots.csv_from_dashboard(dashboard_data), media_hashes=hashes)
        return result
    except Exception:
        raise ofform.OfformError('Data policy or source is unavailable')


def dashboard(source):
    return bundle(source, materialize=False)['dashboard']


def callback(source):
    from ckanext.csunesco.logic import snapshots
    source = as_dict(source)
    if not source.get('partition_id'):
        return
    try:
        snapshots.app_request('/internal/ckan/data-partitions/%d' % int(source['partition_id']), {
            'policy_revision': int(source['policy_revision']), 'status': source['status'],
            'source_id': source['id'], 'package_id': source.get('ckan_package_id')})
    except Exception:
        log.warning('Partition callback will retry for source %s', source['id'])


def upsert_partition(context, data, project):
    """Only the app service may set a partition; verify it against the app."""
    from ckanext.csunesco.logic import auth, package_sync
    if not auth._is_sysadmin(context):
        raise tk.NotAuthorized('Partition updates require the app service')
    level = data.get('access_level')
    if level not in LEVELS:
        raise tk.ValidationError({'access_level': ['Unknown access level']})
    try:
        form_id, partition_id, revision = [int(data[key]) for key in ('form_id', 'partition_id', 'policy_revision')]
        if min(form_id, partition_id, revision) < 1:
            raise ValueError()
    except (KeyError, ValueError, TypeError):
        raise tk.ValidationError({'partition_id': ['Invalid partition identity']})
    # Withdrawals are monotonic service requests; active requests additionally
    # prove their current project/form/level binding via the signed service.
    if not data.get('withdrawn'):
        checked = bundle(dict(data, access_level=level), materialize=False)
        if checked.get('programme_id') != data.get('programme_id'):
            raise tk.ValidationError({'partition_id': ['Project mismatch']})
    source = db.get_data_source_by_form(project.id, form_id, access_level=level)
    if source is None:
        # Keep the original dataset and resource IDs when adopting a legacy
        # source whose actual CKAN access matches this level.
        legacy = db.get_data_source_by_form(project.id, form_id, access_level='legacy')
        if legacy and legacy.ckan_package_id:
            package = tk.get_action('package_show')(dict(context), {'id': legacy.ckan_package_id})
            from ckanext.datashare import core
            actual = package.get('access_level') or ('confidential' if package.get('private') else core.dataset_level(package))
            if actual == dataset_level(level):
                source = legacy
    new = source is None
    if new:
        source = db.CsDataSource()
        source.project_id, source.form_id = project.id, form_id
        source.status = 'pending'
        source.source = 'app'
    extras = db._load_json(source.extras, {}) if not new else {}
    if int(extras.get('policy_revision', 0)) > revision:
        return db.data_source_dictize(source)
    source.access_level = level
    source.title = data.get('title') or project.title
    source.description = data.get('description') or ''
    extras.update(partition_id=partition_id, policy_revision=revision, programme_id=data.get('programme_id'))
    if data.get('owner_org'):
        extras['owner_org'] = data['owner_org']
    source.extras = json.dumps(extras)
    if data.get('withdrawn'):
        source.status = 'withdrawn'
    elif source.status in ('withdrawn', 'rejected'):
        source.status = 'pending'
    db.Session.add(source)
    db.Session.flush()
    if source.ckan_package_id:
        if source.status == 'approved':
            package_sync.ensure_dataset(dict(context, ignore_auth=True), project, source)
        elif source.status == 'withdrawn':
            tk.get_action('package_patch')(dict(context, ignore_auth=True, _cs_partition_sync=True),
                {'id': source.ckan_package_id, 'state': 'deleted'})
    db.Session.commit()
    callback(source)
    return db.data_source_dictize(source)
