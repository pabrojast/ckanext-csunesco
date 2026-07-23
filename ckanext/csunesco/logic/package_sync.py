# encoding: utf-8
"""Create/refresh the CKAN dataset that publishes an approved data source.

On approval, a data source becomes a REAL CKAN package (in the project's
organization, or the configured fallback org) with two proxy-backed resources:

  * ``{site_url}/citizen-science/data/<id>.csv``     (format CSV)
  * ``{site_url}/citizen-science/data/<id>.geojson`` (format GeoJSON)

Those routes fetch ofform's public endpoints live (TTL-cached), so the package
needs no stored data and every IHP-WINS tool that understands a CSV/GeoJSON
resource -- terria_view, maplibre, Data Stories dataset links, chart views --
works against it unchanged.

``ensure_dataset`` is idempotent: a source that already carries a
``ckan_package_id`` is patched, not re-created, so re-approval after an edit
never duplicates packages or resources.
"""
import json
import logging
import re

import ckan.plugins.toolkit as tk

log = logging.getLogger(__name__)

OWNER_ORG_OPTION = 'ckanext.csunesco.dataset_owner_org'
DATASET_DEFAULTS_OPTION = 'ckanext.csunesco.dataset_defaults'

# CKAN's hard limit on package names.
MAX_NAME_LENGTH = 100

_NAME_RE = re.compile(r'[^a-z0-9-]+')


def _munge_name(text):
    """Reduce ``text`` to a CKAN-safe package name fragment."""
    name = _NAME_RE.sub('-', (text or '').lower()).strip('-')
    return re.sub(r'-{2,}', '-', name)


def package_name(project, data_source):
    """Deterministic package name for a project/form pair (<= 100 chars)."""
    suffix = '-{0}'.format(data_source.form_id)
    base = _munge_name('cs-data-{0}'.format(project.slug))
    return base[:MAX_NAME_LENGTH - len(suffix)] + suffix


def _dataset_defaults():
    """Optional JSON dict merged into ``package_create`` (portal-schema aid)."""
    raw = tk.config.get(DATASET_DEFAULTS_OPTION) or ''
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        log.warning('csunesco: %s is not valid JSON; ignoring',
                    DATASET_DEFAULTS_OPTION)
        return {}
    return parsed if isinstance(parsed, dict) else {}


def resolve_owner_org(project, data_source, override_org=None):
    """Which organization owns the dataset, in priority order.

    1. ``override_org`` — the sysadmin's explicit choice at approval time.
    2. The org suggested by the app (stored in the row's extras: ofform keeps
       its organizations synchronized with the portal via ``ckan_slug``).
    3. The project's own organization (``cs_project.organization_id``,
       reserved for a future project→org mapping).
    4. The configured default (``ckanext.csunesco.dataset_owner_org``).
    """
    if override_org:
        return override_org
    from ckanext.csunesco import db
    extras = db._load_json(data_source.extras, {})
    suggested = (extras.get('owner_org') or '').strip() \
        if isinstance(extras, dict) else ''
    if suggested:
        return suggested
    org = getattr(project, 'organization_id', None)
    if org:
        return org
    return (tk.config.get(OWNER_ORG_OPTION) or '').strip() or None


def _org_exists(context, owner_org):
    try:
        tk.get_action('organization_show')(
            dict(context), {'id': owner_org})
        return True
    except tk.ObjectNotFound:
        return False


def _proxy_url(data_source_id, extension):
    site_url = (tk.config.get('ckan.site_url') or '').rstrip('/')
    return '{0}/citizen-science/data/{1}.{2}'.format(
        site_url, data_source_id, extension)


def _resource_dicts(data_source):
    return [
        {
            'name': '{0} (CSV)'.format(data_source.title or 'Observations'),
            'description':
                'Live CSV export of the observations collected in the CS '
                'Toolbox app. Served through the IHP-WINS proxy and always '
                'up to date.',
            'url': _proxy_url(data_source.id, 'csv'),
            'format': 'CSV',
        },
        {
            'name': '{0} (GeoJSON)'.format(data_source.title or 'Observations'),
            'description':
                'Live GeoJSON of the geolocated observations (one time-stamped '
                'point Feature per observation; Terria time-slider compatible).',
            'url': _proxy_url(data_source.id, 'geojson'),
            'format': 'GeoJSON',
        },
    ]


def ensure_dataset(context, project, data_source, override_org=None):
    """Create or refresh the CKAN package for ``data_source``.

    ``override_org`` is the sysadmin's approval-time choice; otherwise the
    app-suggested org from the row's extras applies, then the configured
    default (see ``resolve_owner_org``). A suggested org that does NOT exist
    on the portal falls back to the default instead of failing — the reviewer
    saw (and could change) the selection in the approval form.

    Returns ``{'package_id': ..., 'resource_ids': [...], 'owner_org': ...}``.
    Raises whatever the core actions raise -- the caller decides how to surface
    failure (the approve action leaves the row pending and reports it).
    """
    owner_org = resolve_owner_org(project, data_source, override_org)
    if owner_org and not _org_exists(context, owner_org):
        if override_org:
            # An EXPLICIT choice that does not resolve is an input error.
            raise tk.ValidationError({'owner_org': [tk._(
                'Organization not found: %s') % override_org]})
        log.warning('csunesco: suggested org does not exist on this portal; '
                    'falling back to the default organization')
        owner_org = (tk.config.get(OWNER_ORG_OPTION) or '').strip() or None
        if owner_org and not _org_exists(context, owner_org):
            owner_org = None
    if not owner_org:
        raise tk.ValidationError({'owner_org': [tk._(
            'No organization is available for Citizen Science datasets. '
            'Set ckanext.csunesco.dataset_owner_org to an existing '
            'organization or pick one when approving.')]})

    package_dict = dict(_dataset_defaults())
    package_dict.update({
        'name': package_name(project, data_source),
        'title': data_source.title or project.title,
        'notes': data_source.description or '',
        'owner_org': owner_org,
    })
    # Portal schemas (schemingdcat) require a per-dataset identifier; the
    # package name is unique by construction. dataset_defaults may still
    # override it explicitly, and never clobbers one already set.
    package_dict.setdefault('identifier', package_dict['name'])

    resource_ids = []
    if data_source.ckan_package_id:
        package_dict['id'] = data_source.ckan_package_id
        package = tk.get_action('package_patch')(dict(context), package_dict)
        # Patch the two known resources in place (ids stored on approval).
        from ckanext.csunesco import db
        extras = db._load_json(data_source.extras, {})
        stored = extras.get('resource_ids') or []
        for resource, resource_id in zip(_resource_dicts(data_source), stored):
            patched = dict(resource)
            patched['id'] = resource_id
            tk.get_action('resource_patch')(dict(context), patched)
            resource_ids.append(resource_id)
        if not resource_ids:
            for resource in _resource_dicts(data_source):
                created = tk.get_action('resource_create')(
                    dict(context),
                    dict(resource, package_id=package['id']))
                resource_ids.append(created['id'])
    else:
        package = tk.get_action('package_create')(dict(context), package_dict)
        for resource in _resource_dicts(data_source):
            created = tk.get_action('resource_create')(
                dict(context), dict(resource, package_id=package['id']))
            resource_ids.append(created['id'])

    return {'package_id': package['id'], 'resource_ids': resource_ids,
            'owner_org': owner_org}
