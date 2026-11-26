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



def hidden_package_ids():
    """Packages of managed partitions that the Toolbox does not confirm now.

    One query and one batched request, shared inside the HTTP request. It
    fails closed: when the Toolbox cannot be asked, every one is hidden.
    """
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
    return [row['ckan_package_id'] for row in records if row.get('partition_id') not in allowed]


@tk.chained_action
@tk.side_effect_free
def package_search(original, context, data_dict):
    # Exclude stale/revoked managed packages BEFORE Solr computes pagination,
    # totals and facets, so those metadata paths cannot leak hidden partitions.
    hidden = hidden_package_ids()
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
        # Send the exclusion as its own top-level filter and leave the caller's
        # fq alone: nested as "(fq) AND (-id:.. AND -id:..)" the purely
        # negative group matches nothing in Solr, so every search returned 0.
        import json
        exclude = '-id:(%s)' % ' OR '.join(json.dumps(value) for value in sorted(set(hidden)))
        # Over the GET API a single fq_list arrives as a plain string.
        filters = data_dict.get('fq_list') or []
        data_dict['fq_list'] = ([filters] if isinstance(filters, str) else list(filters)) + [exclude]
    return original(context, data_dict)


@tk.chained_auth_function
@tk.auth_allow_anonymous_access
def package_show_auth(next_auth, context, data_dict):
    """Refuse a hidden managed package wherever CKAN asks if it may be shown.

    The chained action above hides it from package_show. CKAN's activity
    plugin never calls that action: its auth functions ask this one and then
    read the activity table, so the history of a hidden package, with its
    whole metadata, stayed readable by anyone (package_activity_list,
    activity_show, activity_data_show, activity_diff). The resource and
    datastore auth functions ask it too.

    It raises NotFound, as the action does, so the answer stays a 404.
    Sysadmins and ignore_auth never reach an auth function.
    """
    if not context.get('_cs_partition_sync'):
        model = context.get('model')
        if model is None:
            import ckan.model as model
        key = (data_dict or {}).get('id')
        package = model.Package.get(key) if key else context.get('package')
        if package is not None:
            sources = _request_checks()
            memo = ('source', package.id)
            if memo not in sources:
                sources[memo] = source_for(package.id)
            if sources[memo]:
                check_current(sources[memo])
    return next_auth(context, data_dict)


# Lists of the activity plugin that mix the activities of many packages.
ACTIVITY_LISTS = ('group_activity_list', 'organization_activity_list', 'user_activity_list',
                  'dashboard_activity_list', 'recently_changed_packages_activity_list')


def _managed_among(package_ids):
    rows = db.Session.query(db.CsDataSource.ckan_package_id).filter(
        db.CsDataSource.ckan_package_id.in_(sorted(package_ids)))
    return {package_id for (package_id,) in rows}


def without_hidden_packages(activities):
    """Drop the activities of hidden managed packages from a list CKAN built.

    The Toolbox is asked only when the page holds a managed package. The
    list can come back shorter than the limit that was asked for.
    """
    package_ids = {item.get('object_id') for item in activities
                   if isinstance(item, dict) and 'package' in str(item.get('activity_type') or '')}
    package_ids.discard(None)
    if not package_ids or not _managed_among(package_ids):
        return activities
    hidden = set(hidden_package_ids())
    return [item for item in activities
            if not (isinstance(item, dict) and item.get('object_id') in hidden)]


@tk.chained_action
@tk.side_effect_free
def activity_list(original, context, data_dict):
    result = original(context, data_dict)
    if context.get('ignore_auth') or not isinstance(result, list):
        return result
    return without_hidden_packages(result)


def get_actions():
    actions = {'package_show': package_show, 'package_update': package_update,
               'package_patch': package_patch, 'package_search': package_search,
               'datastore_create': datastore_create}
    # A chained action needs its original: only where the activity plugin runs.
    if 'activity' in tk.aslist(tk.config.get('ckan.plugins', '')):
        actions.update({name: activity_list for name in ACTIVITY_LISTS})
    return actions


def get_auth_functions():
    return {'package_show': package_show_auth}
