"""Prevent alternate CKAN APIs from bypassing managed data policy."""
from contextvars import ContextVar

import ckan.plugins.toolkit as tk
from flask import g, has_request_context
from ckanext.csunesco import db


_withdrawing_package = ContextVar('csunesco_withdrawing_package', default=None)


def withdraw_package(context, package_id):
    # CKAN 2.10 activity reads package_show with a fresh context while the
    # revise action is firing its package_update signal.
    token = _withdrawing_package.set(package_id)
    try:
        tk.get_action('package_revise')(
            dict(context, ignore_auth=True, _cs_partition_sync=True,
                 use_cache=False, defer_commit=True),
            {'match': {'id': package_id}, 'update': {'state': 'deleted'}})
    finally:
        _withdrawing_package.reset(token)


def _request_checks():
    # CKAN validators can nest hundreds of searches within one action. Share
    # its current-policy check only inside that HTTP request, never across
    # visitors or subsequent requests, and never use a persisted fallback.
    if not has_request_context():
        return {}
    if not hasattr(g, '_cs_current_policy_checks'):
        g._cs_current_policy_checks = {}
    return g._cs_current_policy_checks


def source_for(package_id):
    if not package_id:
        return None
    return db.Session.query(db.CsDataSource).filter(db.CsDataSource.ckan_package_id == package_id).first()


def check_current(source):
    from ckanext.csunesco.logic import snapshots
    data = db.data_source_dictize(source)
    if not data.get('partition_id'):
        return
    checks = _request_checks()
    key = ('partition', data['partition_id'], data['policy_revision'], data.get('access_level'))
    if key in checks:
        if not checks[key]:
            raise tk.ObjectNotFound('Dataset is unavailable while its data policy synchronizes')
        return
    try:
        current = snapshots.app_request('/internal/ckan/data-partitions/%d?policy_revision=%d' % (
            int(data['partition_id']), int(data['policy_revision'])))
        if current.get('access_level') != data.get('access_level'):
            raise ValueError('Access level changed')
        checks[key] = True
    except Exception:
        checks[key] = False
        raise tk.ObjectNotFound('Dataset is unavailable while its data policy synchronizes')


@tk.chained_action
@tk.side_effect_free
def package_show(original, context, data_dict):
    package_id = _withdrawing_package.get()
    activity_read = (package_id is not None and data_dict.get('id') == package_id
                     and data_dict.get('include_tracking') is False
                     and context.get('ignore_auth') and context.get('for_view') is False
                     and not context.get('csunesco_public_view'))
    # The active Solr document can outlive the transaction's state change.
    read_context = dict(context, use_cache=False) if activity_read else context
    package = original(read_context, data_dict)
    if activity_read and package.get('id') == package_id and package.get('state') == 'deleted':
        return {'id': package_id, 'state': 'deleted'}
    source = source_for(package.get('id'))
    if source and not context.get('_cs_partition_sync'):
        # CKAN's search indexer reads the unvalidated database record on
        # commit, outside the action context. It needs the deleted state to
        # remove the active Solr document even after Toolbox closes the feed.
        if (package.get('state') == 'deleted' and context.get('ignore_auth')
                and context.get('validate') is False and context.get('use_cache') is False
                and not context.get('csunesco_public_view')):
            return package
        if context.get('csunesco_public_view'):
            from ckanext.csunesco.logic.data_access import published
            if not published(source):
                raise tk.ObjectNotFound('Dataset is not published')
        check_current(source)
    return package


def _check_update(context, data_dict):
    if context.get('_cs_partition_sync'):
        return
    model = context.get('model')
    package = model.Package.get(data_dict.get('id')) if model else None
    source = source_for(package.id) if package else None
    if source and getattr(source, 'access_level', 'legacy') != 'legacy':
        from ckanext.csunesco.logic.data_access import dataset_level
        level = dataset_level(source.access_level)
        extras = data_dict.get('extras') or []
        if isinstance(extras, list):
            extras = {entry.get('key'): entry.get('value') for entry in extras if isinstance(entry, dict)}
        if isinstance(extras, dict) and 'access_level' in extras and extras['access_level'] != level:
            raise tk.ValidationError({'access_level': ['Manage project data access in CS Toolbox Observations']})
        if ('access_level' in data_dict and data_dict['access_level'] != level
                or 'private' in data_dict and tk.asbool(data_dict['private']) != (level == 'confidential')):
            raise tk.ValidationError({'access_level': ['Manage project data access in CS Toolbox Observations']})


@tk.chained_action
def package_update(original, context, data_dict):
    _check_update(context, data_dict)
    return original(context, data_dict)


@tk.chained_action
def package_patch(original, context, data_dict):
    _check_update(context, data_dict)
    return original(context, data_dict)


@tk.chained_action
def datastore_create(original, context, data_dict):
    model = context.get('model')
    resource = model.Resource.get(data_dict.get('resource_id')) if model else None
    inline_resource = data_dict.get('resource') or {}
    package_id = resource.package_id if resource else inline_resource.get('package_id')
    if package_id and source_for(package_id):
        raise tk.ValidationError({'resource_id': ['Managed observation data must use the policy-controlled proxy']})
    return original(context, data_dict)



@tk.chained_action
@tk.side_effect_free
def package_search(original, context, data_dict):
    # Exclude stale/revoked managed packages BEFORE Solr computes pagination,
    # totals and facets, so those metadata paths cannot leak hidden partitions.
    from ckanext.csunesco.logic import snapshots
    rows = db.Session.query(db.CsDataSource).filter(db.CsDataSource.access_level != 'legacy').all()
    records = [db.data_source_dictize(row) for row in rows if row.ckan_package_id]
    checks = [{'id': int(row['partition_id']), 'revision': int(row['policy_revision'])}
              for row in records if row.get('partition_id')]
    cache = _request_checks()
    key = ('search', tuple(sorted((item['id'], item['revision']) for item in checks)))
    if key not in cache:
        allowed = set()
        try:
            for offset in range(0, len(checks), 1000):
                allowed.update(snapshots.app_request('/internal/ckan/data-policy/check',
                    {'partitions': checks[offset:offset + 1000]})['available'])
        except Exception:
            allowed.clear()
        cache[key] = allowed
    allowed = cache[key]
    hidden = [row['ckan_package_id'] for row in records if row.get('partition_id') not in allowed]
    if context.get('csunesco_public_view'):
        # Apply publication before Solr counts, facets and pagination. Include
        # legacy sources too: an archived parent must disappear from blocks.
        import sqlalchemy as sa
        unpublished = db.Session.query(db.CsDataSource.ckan_package_id).outerjoin(
            db.CsProject, db.CsProject.id == db.CsDataSource.project_id,
        ).filter(sa.or_(
            db.CsDataSource.status.is_(None), db.CsDataSource.status != 'approved',
            db.CsProject.id.is_(None), db.CsProject.status.is_(None),
            ~db.public_project_clause()))
        hidden.extend(package_id for (package_id,) in unpublished.all() if package_id)
    data_dict = dict(data_dict)
    if hidden:
        # Package IDs are CKAN-generated UUIDs; quote through JSON nonetheless.
        import json
        exclude = ' AND '.join('-id:' + json.dumps(value) for value in sorted(set(hidden)))
        existing = (data_dict.get('fq') or '').strip()
        data_dict['fq'] = ('(%s) AND (%s)' % (existing, exclude)) if existing else exclude
    return original(context, data_dict)


def get_actions():
    return {'package_show': package_show, 'package_update': package_update,
            'package_patch': package_patch, 'package_search': package_search, 'datastore_create': datastore_create}
