# encoding: utf-8
"""Versioned project publications. The transport account never acts as author."""
import copy
import datetime
import hashlib
import hmac
import json
import secrets
import time
import urllib.request
from urllib.parse import urlsplit

import ckan.model as model
import ckan.plugins.toolkit as tk
from ckanext.csunesco import db, constants
from ckanext.csunesco.logic import auth, blocks, schema, sanitize

KEY = '_portal'
SCHEMA_VERSION = 2
MAX_PAYLOAD = 4_000_000
PROJECT_FIELDS = tuple(dict.fromkeys(
    ['title', 'short_description', 'biosphere_reserve', 'countries',
     'region_geojson', 'project_document_url', 'logo_url', 'heading_image_url',
     'landing_content', 'initiative', 'logo_focal_x', 'logo_focal_y', 'logo_zoom', 'heading_zoom'] + list(schema.PROJECT_EXTRA_FIELDS)))
# Public content cannot opt into a private audience via a block selector.
FACT_FIELDS = tuple(k for k in PROJECT_FIELDS if k not in constants.FIELD_AUDIENCE
                    and k not in ('editors', 'title', 'region_geojson', 'logo_url',
                                  'heading_image_url', 'landing_content', 'logo_focal_x',
                                  'logo_focal_y', 'logo_zoom', 'heading_zoom',
                                  'heading_focal_x', 'heading_focal_y',
                                  'image_focal_x', 'image_focal_y', 'initiative'))
STRUCTURE_FIELDS = ('aim', 'focus_areas', 'engagement_activities', 'target_groups',
                    'incentives', 'engagement_level', 'training_level',
                    'how_to_participate', 'water_parameters', 'expected_outcomes', 'timeframe_start', 'timeframe_end')
LEADERSHIP_FIELDS = ('lead_partner_type', 'lead_organisation', 'other_organisations',
                     'funding_body', 'funding_programme', 'international_frameworks', 'project_document_url')
ENGAGEMENT_FIELDS = tuple(k for k in STRUCTURE_FIELDS if k not in ('water_parameters', 'timeframe_start', 'timeframe_end'))


def standard_sections(project=None):
    """Existing authored labels are the baseline, including legacy overrides."""
    saved = {}
    page = db.get_project_page(project.id) if project else None
    if page:
        for raw in (page.published_json, page.draft_json):
            for item in blocks.blocks_from_json(raw, default=[]):
                saved[item['type']] = item
    result = []
    for key in tuple(blocks.DEFAULT_BLOCK_TYPES) + ('project_facts', 'project_structure'):
        item = blocks.BLOCK_TYPES[key]
        baseline = saved.get(key) or blocks.normalize_block({'type': key})
        fields = ['title', 'intro'] if key in blocks.DEFAULT_BLOCK_TYPES else ['title']
        result.append({'type': key, 'label': item.label, 'protected_fields': fields,
                       'baseline': {field: baseline.get(field, '') for field in fields}})
    return result


def can_edit_standard_sections(context):
    # The service account never acquires authoring rights through its sysadmin bit.
    return bool(context.get('user') != tk.config.get('ckanext.csunesco.portal_service_user')
                and auth._is_sysadmin(context))


def validate_standard_sections(context, project, candidate_blocks):
    if can_edit_standard_sections(context) or (project and auth.can_manage_project(context, project.id)
                                              and context.get('user') != tk.config.get('ckanext.csunesco.portal_service_user')):
        return
    standards = {item['type']: item for item in standard_sections(project)}
    for candidate in candidate_blocks:
        policy = standards.get(candidate.get('type'))
        if not policy:
            continue
        for field in policy['protected_fields']:
            old = policy['baseline'].get(field) or ''
            new = candidate.get(field) or ''
            # Empty title means the registry's translated label in the renderer.
            if field == 'title':
                old, new = old or policy['label'], new or policy['label']
            if old != new:
                raise tk.NotAuthorized('Only a CKAN administrator may change standard section titles or introductions')


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False)


def checksum(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def metadata(project):
    return db._load_json(project.extras, {}).get(KEY, {}) or {}


def managed(project):
    return bool(metadata(project).get('app_project_id')) if project else False


def withdrawn(project):
    if not project:
        return False
    extras = db._load_json(project.extras, {})
    return bool(extras.get('_portal_withdrawn') or metadata(project).get('status') == 'withdrawn')


def set_metadata(project, value):
    extras = dict(db._load_json(project.extras, {}))
    # Retraction remains effective while a replacement draft awaits review.
    if value.get('status') == 'approved' and value.get('published_revision') is not None:
        extras.pop('_portal_withdrawn', None)
    elif value.get('status') == 'withdrawn' or withdrawn(project):
        extras['_portal_withdrawn'] = True
    extras[KEY] = value
    project.extras = canonical(extras)


def editor_url(project):
    value = metadata(project).get('app_project_id')
    base = (tk.config.get('ckanext.csunesco.ofform_app_url') or '').rstrip('/')
    return ('%s/projects/%s/space/portal' % (base, int(value))) if value and base else None


def service_auth(context, data_dict=None):
    configured = tk.config.get('ckanext.csunesco.portal_service_user')
    success = auth._is_sysadmin(context) and (
        not configured or context.get('user') == configured)
    return {'success': bool(success), 'msg': 'Project portal service required'}


def require_service(context):
    if not service_auth(context)['success']:
        raise tk.NotAuthorized('Project portal service required')


def resolve_project(data):
    ref = data.get('project') or {}
    key = (ref.get('id') or ref.get('slug')) if isinstance(ref, dict) else ref
    project = db.get_project(key or data.get('project_id') or data.get('project_slug'))
    if project is None:
        raise tk.ObjectNotFound('Project not found')
    return project


def actor_context(context, data, project):
    actor = data.get('actor') or {}
    user = model.User.get(actor.get('ckan_id') or actor.get('username'))
    if user is None or getattr(user, 'state', None) != 'active':
        raise tk.NotAuthorized('An active CKAN author is required')
    if user.name == context.get('user') or user.name == tk.config.get('ckanext.csunesco.portal_service_user'):
        raise tk.NotAuthorized('The transport service cannot be a publication author')
    if actor.get('username') and actor['username'] != user.name:
        raise tk.NotAuthorized('Author identity mismatch')
    # Start a new context: never inherit ignore_auth/auth_user_obj from service.
    effective = {'model': model, 'session': model.Session,
                 'user': user.name, 'auth_user_obj': user,
                 'csunesco_portal_sync': True}
    if not (auth.can_manage_project(effective, project.id)
            or project.status in ('draft', 'pending', 'rejected')
            and project.created_by == user.id):
        raise tk.NotAuthorized('The author cannot edit this project')
    return effective


def validate_payload(raw):
    if not isinstance(raw, dict) or len(canonical(raw).encode('utf-8')) > MAX_PAYLOAD:
        raise tk.ValidationError({'payload': ['Invalid or oversized publication']})
    unknown = set(raw) - {'project', 'structure', 'workplan', 'blocks', 'contents', 'media', 'layout', 'capabilities_version', 'content_review_version'}
    if unknown:
        raise tk.ValidationError({'payload': ['Unsupported fields: ' + ', '.join(sorted(unknown))]})
    if raw.get('capabilities_version') not in (None, 1, 'project-portal-1'):
        raise tk.ValidationError({'capabilities_version': ['Unsupported block capabilities']})
    candidate = copy.deepcopy(raw)
    if not isinstance(candidate.get('media', []), list):
        raise tk.ValidationError({'media': ['Media must be a list']})
    assets = {}
    for item in candidate.get('media', []):
        if not isinstance(item, dict) or item.get('id') is None:
            continue
        target = item.get('fetch_url') or item.get('source_url') or item.get('url')
        if not target:
            continue
        parsed, site = urlsplit(target), urlsplit(tk.config.get('ckan.site_url') or '')
        if parsed.netloc and (parsed.scheme, parsed.netloc) == (site.scheme, site.netloc):
            target = parsed.path + ('?' + parsed.query if parsed.query else '')
        assets[str(item['id'])] = target
    def resolve_refs(value):
        if isinstance(value, str) and value.startswith('asset:'):
            target = assets.get(value[6:])
            if not target:
                raise tk.ValidationError({'media': ['Unknown project asset reference']})
            return target
        if isinstance(value, list):
            return [resolve_refs(item) for item in value]
        if isinstance(value, dict):
            return {key: resolve_refs(item) for key, item in value.items()}
        return value
    candidate = resolve_refs(candidate)
    raw_blocks = candidate.get('blocks')
    if not isinstance(raw_blocks, list):
        raise tk.ValidationError({'blocks': ['A block list is required']})
    for block in raw_blocks:
        if not isinstance(block, dict) or block.get('type') not in blocks.BLOCK_TYPES:
            raise tk.ValidationError({'blocks': ['Unsupported block type; refresh capabilities']})
        if 'project' not in blocks.BLOCK_TYPES[block['type']].scopes:
            raise tk.ValidationError({'blocks': ['This block is not allowed on projects']})
        normalized = blocks.normalize_block(block)
        unknown_fields = set(block) - set(normalized)
        if unknown_fields:
            raise tk.ValidationError({'blocks': ['Unsupported block fields: ' + ', '.join(sorted(unknown_fields))]})
        allowed = FACT_FIELDS if block['type'] == 'project_facts' else STRUCTURE_FIELDS
        if block['type'] in ('project_facts', 'project_structure') and any(
                field not in allowed for field in block.get('fields', [])):
            raise tk.ValidationError({'fields': ['Private or unknown field selection']})
    candidate['blocks'] = blocks.ensure_builtins(blocks.normalize_blocks(raw_blocks))
    if len(candidate['blocks']) != len(raw_blocks):
        # Builtins may be restored but user blocks may NEVER disappear silently.
        if len(candidate['blocks']) < len(raw_blocks):
            raise tk.ValidationError({'blocks': ['Block limits exceeded']})
    from ckanext.csunesco.logic.action.page import _validate_policy
    _validate_policy(candidate['blocks'])
    if blocks.oversized(candidate['blocks']):
        raise tk.ValidationError({'blocks': ['Page too large']})
    incoming = candidate.get('project') or {}
    project = {}
    for key in PROJECT_FIELDS:
        if key not in incoming:
            continue
        value = incoming[key]
        if key in ('logo_url', 'heading_image_url', 'project_document_url') and value:
            from ckanext.csunesco.logic.action.page import _validated_project_image
            value = _validated_project_image(value)
        elif key in ('logo_focal_x', 'logo_focal_y', 'logo_zoom', 'heading_zoom', 'heading_focal_x', 'heading_focal_y'):
            try:
                value = (100 if 'zoom' in key else 50) if value is None else value
                value = max(100 if 'zoom' in key else 0, min(300 if 'zoom' in key else 100, float(value)))
            except (ValueError, TypeError):
                raise tk.ValidationError({key: ['Invalid image position']})
        elif key in ('short_description', 'landing_content'):
            value = sanitize.sanitize_html(value or '')
        elif key == 'region_geojson' and value:
            try:
                value = json.loads(value) if isinstance(value, str) else value
                if not isinstance(value, dict) or value.get('type') not in ('Feature', 'FeatureCollection', 'Polygon', 'MultiPolygon', 'Point', 'MultiPoint', 'LineString', 'MultiLineString', 'GeometryCollection'):
                    raise ValueError()
            except (ValueError, TypeError):
                raise tk.ValidationError({'region_geojson': ['Invalid project geometry']})
        elif isinstance(value, str):
            value = value[:20000]
        project[key] = value
    candidate['project'] = project
    from ckanext.csunesco.logic.action.structure import _clean_structure, _clean_workplan
    candidate['structure'] = _clean_structure(candidate.get('structure'))
    candidate['workplan'] = _clean_workplan(candidate.get('workplan'))
    contents = candidate.get('contents') or []
    if not isinstance(contents, list) or len(contents) > 100:
        raise tk.ValidationError({'contents': ['At most 100 content items per publication']})
    from ckanext.csunesco.logic.action.content import _validated_content, _type_extras
    clean_contents = []
    for item in contents:
        if not isinstance(item, dict) or not (item.get('id') or item.get('app_content_id')):
            raise tk.ValidationError({'contents': ['Stable content IDs required']})
        values = _validated_content({}, item, item.get('content_type', 'cs-news'))
        values['body'] = sanitize.sanitize_html(values.get('body') or '')
        if values.get('visibility', 'public') != 'public':
            raise tk.ValidationError({'contents': ['Private content cannot enter a public publication']})
        values['app_content_id'] = str(item.get('app_content_id') or item['id'])
        values['ckan_id'] = item.get('ckan_id')
        values['extras'] = _type_extras(item, body=values['body'])
        values['location'] = values['extras'].get('location')
        for key in ('publish_date', 'end_date'):
            if isinstance(values.get(key), datetime.datetime):
                values[key] = values[key].isoformat()
        clean_contents.append(values)
    candidate['contents'] = clean_contents
    return candidate


def validate_envelope(context, data):
    require_service(context)
    if data.get('schema_version') not in (1, SCHEMA_VERSION):
        raise tk.ValidationError({'schema_version': ['Unsupported schema version']})
    project = resolve_project(data)
    effective = actor_context(context, data, project)
    try:
        revision = int(data['revision'])
        app_id = int(data['app_project_id'])
    except (KeyError, TypeError, ValueError):
        raise tk.ValidationError({'revision': ['Positive revision and app project ID required']})
    if revision < 1 or app_id < 1:
        raise tk.ValidationError({'revision': ['Positive revision and app project ID required']})
    meta = metadata(project)
    if meta.get('app_project_id') and meta['app_project_id'] != app_id:
        raise tk.ValidationError({'app_project_id': ['Project already linked']})
    actual = checksum(data.get('payload'))
    if not hmac.compare_digest(str(data.get('checksum', '')), actual):
        raise tk.ValidationError({'checksum': ['Publication checksum mismatch']})
    return project, effective, revision, app_id


def status(project):
    meta = metadata(project)
    page = db.get_project_page(project.id)
    out = {key: meta.get(key) for key in (
        'app_project_id', 'revision', 'checksum', 'published_revision',
        'published_checksum', 'updated_at')}
    out.update({'project_id': project.id, 'project_slug': project.slug,
                'status': meta.get('status') or (page.status if page else 'draft'),
                'reason': page.rejection_reason if page else None,
                'draft_hash': page.draft_hash if page else None,
                'project_status': project.status, 'managed': managed(project), 'withdrawn': withdrawn(project)})
    return out


def _public_project(candidate):
    return {key: value for key, value in candidate.get('project', {}).items()
            if key not in constants.FIELD_AUDIENCE and key not in ('editors', 'initiative')}


def publish_candidate(project, page):
    """Called inside the approval transaction, before its single commit."""
    extras = db._load_json(page.extras, {})
    candidate = extras.get('draft_portal_payload')
    if candidate is None:
        return
    from ckanext.csunesco.logic import snapshots
    snapshots.prepare_project_sources(project)
    candidate = snapshots.materialize_media(project, candidate)
    page.published_json = blocks.blocks_to_json(candidate['blocks'])
    public = _public_project(candidate)
    public_extras = dict(db._load_json(project.extras, {}))
    columns = set(project.__table__.columns.keys()) if hasattr(project, '__table__') else {
        'title', 'short_description', 'biosphere_reserve', 'countries', 'region_geojson',
        'project_document_url', 'logo_url', 'heading_image_url', 'landing_content'}
    for key, value in public.items():
        if key == 'initiative':
            # Initiative is identity/moderation metadata; never a layout edit.
            continue
        if key in columns:
            if key == 'countries' and value is None:
                value = []
            if value is not None and key in ('countries', 'region_geojson') and not isinstance(value, str):
                value = canonical(value)
            setattr(project, key, value)
        else:
            public_extras[key] = value
    public_extras['structure'] = {k: v for k, v in candidate.get('structure', {}).items()
                                  if k not in constants.FIELD_AUDIENCE}
    # Private workplan remains in the protected draft/export only.
    public_extras.pop('workplan', None)
    public_extras['_portal_contacts'] = {k: candidate.get('project', {}).get(k) for k in ('contact_person', 'contact_email')}
    for key in ('contact_person', 'contact_email', 'editors', 'allowed_participants'):
        public_extras.pop(key, None)
    meta = dict(public_extras.get(KEY, {}))
    meta.update(status='approved', published_revision=meta.get('revision'),
                published_checksum=meta.get('checksum'),
                updated_at=datetime.datetime.utcnow().isoformat())
    public_extras[KEY] = meta
    public_extras.pop('_portal_withdrawn', None)
    if candidate.get('content_review_version') != 2 and not tk.asbool(tk.config.get('ckanext.csunesco.independent_content_reviews', False)):
        publish_contents(project, page, candidate)
    project.extras = canonical(public_extras)
    snapshots.publish_media_manifest(project, candidate)


def mark_review(project, page, approved):
    meta = dict(metadata(project))
    meta.update(status='approved' if approved else 'rejected',
                updated_at=datetime.datetime.utcnow().isoformat())
    set_metadata(project, meta)


def callback(project):
    from ckanext.csunesco.logic import snapshots
    try:
        snapshots.app_request('/internal/ckan/project-reviews', status(project))
    except Exception:
        # Polling status repairs delivery; never roll back a review on app outage.
        import logging
        logging.getLogger(__name__).warning('Project review callback pending: %s', project.id)


def _field(key, kind='text', options=None, **kwargs):
    field = {'key': key, 'label': key.replace('_', ' ').capitalize(), 'type': kind}
    if options is not None:
        field['options'] = [{'value': item, 'label': str(item).replace('_', ' ').capitalize()} for item in options]
    field.update(kwargs)
    return field


def block_fields(item, default):
    enums = {
        'width': ('full', 'narrow'), 'chart': ('line', 'bar', 'pie'),
        'mode': ('numeric', 'category', 'count'), 'agg': ('mean', 'min', 'max', 'sum', 'count'),
        'bucket': ('auto', 'day', 'week', 'month'), 'range': ('all', '1y', '90d', '30d'),
        'ratio': ('16:9', '4:3'), 'content_type': blocks.CONTENT_LIST_TYPES,
        'scope': ('project', 'initiative', 'site'), 'media_side': ('left', 'right'),
        'tone': ('info', 'success', 'warning', 'action')}
    if item.key == 'image':
        enums['layout'] = ('grid', 'single', 'wide', 'carousel')
    elif item.key == 'content_list':
        enums['layout'] = ('grid', 'list')
    if item.key == 'datasets_list':
        enums['source'] = ('project', 'organization', 'ids')
    readonly = {'id', 'type', 'provider', 'video_id', 'parameter_charts'}
    result = []
    for key, value in default.items():
        if key in readonly:
            continue
        kind = ('boolean' if isinstance(value, bool) else 'number' if isinstance(value, (int, float))
                else 'string_list' if isinstance(value, list) else 'text')
        if key in enums:
            kind = 'select'
        elif key == 'html':
            kind = 'rich_text'
        elif key in ('intro', 'caption', 'empty_text'):
            kind = 'textarea'
        elif key in ('url', 'image_url', 'cta_url'):
            kind = 'image' if key == 'image_url' or item.key in ('image', 'media_text') and key == 'url' else 'url'
        field = _field(key, kind, enums.get(key), default=value)
        if key == 'height':
            limits = {'chart': (200, 600), 'observation_map': (240, 700), 'terria_map': (300, 800)}
            field['minimum'], field['maximum'] = limits.get(item.key, (200, 800))
        elif key == 'limit':
            field.update(minimum=1, maximum=12)
        elif key == 'items' and item.key == 'image':
            field.update(type='items', maximum=12, item_fields=[
                _field('url', 'image', required=True), _field('alt'),
                _field('caption', 'textarea'), _field('link', 'url')])
        elif key == 'items' and item.key == 'stats':
            field.update(type='items', maximum=4, item_fields=[
                _field('source', 'select', blocks.STAT_SOURCES, default='manual'),
                _field('value'), _field('label')])
        elif key == 'items' and item.key == 'site_initiatives':
            name = _field('name', 'select')
            name['options'] = [{'value': i['name'], 'label': i['title']} for i in constants.CS_INITIATIVES]
            field.update(type='items', maximum=4, item_fields=[name, _field('image_url', 'image', required=True)])
        elif key == 'fields' and item.key in ('project_facts', 'project_structure'):
            allowed = FACT_FIELDS if item.key == 'project_facts' else STRUCTURE_FIELDS
            field.update(options=[{'value': k, 'label': k.replace('_', ' ').capitalize()} for k in allowed])
        elif key == 'data_source_id':
            field.update(type='select', options=[], options_source='data_sources')
        elif key in ('field', 'group_by'):
            field.update(options_source='data_source_fields')
        elif key == 'ids':
            field.update(options_source='datasets', maximum=10)
        result.append(field)
    return result


def capabilities(context, data):
    require_service(context)
    project = resolve_project(data) if (data.get('project') or data.get('project_id') or data.get('project_slug')) else None
    effective = actor_context(context, data, project) if project and data.get('actor') else None
    scope = data.get('scope', 'project')
    if scope not in ('project', 'site', 'initiative'):
        raise tk.ValidationError({'scope': ['Unknown page scope']})
    standards = standard_sections(project) if scope == 'project' else []
    standards_by_type = {item['type']: item for item in standards}
    registry = []
    for item in blocks._TYPES:
        if scope not in item.scopes:
            continue
        default = blocks.normalize_block({'type': item.key})
        fields = block_fields(item, default)
        if scope != 'project':
            for field in fields:
                if field.get('options_source') == 'datasets':
                    field.pop('options_source')
        selected = LEADERSHIP_FIELDS if item.key == 'project_facts' else ENGAGEMENT_FIELDS
        registry.append({'key': item.key, 'type': item.key, 'label': item.label,
                         'description': item.description, 'builtin': item.builtin,
                         'standard_section': item.key in standards_by_type,
                         'protected_fields': standards_by_type.get(item.key, {}).get('protected_fields', []),
                         'addable': item.addable or item.key in ('project_facts', 'project_structure'), 'max_instances': item.max_instances,
                         'requires_review': item.requires_review, 'default': default,
                         'fields': fields,
                         'selectable_fields': [{'key': k, 'label': k.replace('_', ' ').capitalize()} for k in selected]
                             if item.key in ('project_facts', 'project_structure') else []})
    sources = []
    datasets = []
    if data.get('project') or data.get('project_id') or data.get('project_slug'):
        project = resolve_project(data)
        sources = [db.data_source_dictize(row) for row in db.Session.query(db.CsDataSource).filter(
            db.CsDataSource.project_id == project.id, db.CsDataSource.status == 'approved').all()]
        datasets = [{'id': source['ckan_package_id'], 'title': source.get('title')}
                    for source in sources if source.get('ckan_package_id')]
        for item in registry:
            for field in item['fields']:
                if field.get('options_source') == 'data_sources':
                    field['options'] = [{'value': source['id'], 'label': source.get('title') or str(source['form_id'])} for source in sources]
    return {'schema_version': SCHEMA_VERSION, 'capabilities_version': 'project-portal-1',
            'can_edit_standard_sections': bool(not effective or can_edit_standard_sections(effective) or project and auth.can_manage_project(effective, project.id)),
            'standard_sections': standards,
            'blocks': registry, 'data_sources': sources, 'datasets': datasets,
            'project_fields': [{'key': k, 'label': k.replace('_', ' ').capitalize()} for k in FACT_FIELDS],
            'structure_fields': [{'key': k, 'label': k.replace('_', ' ').capitalize()} for k in STRUCTURE_FIELDS],
            'fixed_hero': True, 'snapshot_interval_seconds': 300}


def apply(context, data):
    project, effective, revision, app_id = validate_envelope(context, data)
    meta = dict(metadata(project))
    intent = data.get('intent', 'submit')
    if intent not in ('draft', 'submit', 'withdraw'):
        raise tk.ValidationError({'intent': ['Unsupported publication operation']})
    if revision < int(meta.get('revision') or 0):
        raise tk.ValidationError({'revision': ['Stale publication revision']})
    if revision == meta.get('revision'):
        if data['checksum'] != meta.get('checksum'):
            raise tk.ValidationError({'revision': ['Revision already has different content']})
        if intent == meta.get('intent') and (intent != 'submit' or meta.get('status') in ('pending', 'approved', 'rejected')):
            return dict(status(project), accepted=True)
    if intent == 'withdraw':
        from ckanext.csunesco.logic import snapshots
        snapshots.withdraw_project(project)
        page = db.get_project_page(project.id)
        if page:
            page.published_json = '[]'
            page.status = 'draft'
        meta.update(app_project_id=app_id, revision=revision, checksum=data['checksum'],
                    status='withdrawn', intent=intent, published_revision=None,
                    published_checksum=None, updated_at=datetime.datetime.utcnow().isoformat())
        set_metadata(project, meta)
        model.Session.commit()
        return dict(status(project), accepted=True)
    candidate = validate_payload(data['payload'])
    validate_standard_sections(effective, project, candidate['blocks'])
    meta.update(app_project_id=app_id, revision=revision, checksum=data['checksum'],
                status='draft', intent=intent, updated_at=datetime.datetime.utcnow().isoformat())
    set_metadata(project, meta)
    from ckanext.csunesco.logic.action import page as page_actions
    result = page_actions.csunesco_project_page_update(effective, {
        'project_id': project.id, 'blocks': candidate['blocks']})
    page = db.get_project_page(project.id)
    extras = dict(db._load_json(page.extras, {}))
    extras['draft_portal_payload'] = candidate
    extras['draft_portal_revision'] = revision
    extras['draft_portal_checksum'] = data['checksum']
    page.extras = canonical(extras)
    # Hash includes ALL candidate fields, never just its blocks.
    page.draft_hash = checksum(candidate)
    model.Session.commit()
    if intent == 'submit':
        page_actions.csunesco_project_page_submit(effective, {'project_id': project.id})
        meta = dict(metadata(project))
        meta['status'] = page.status
        set_metadata(project, meta)
        model.Session.commit()
    return dict(status(project), accepted=True)


def export(context, data):
    require_service(context)
    project = resolve_project(data)
    page = db.get_project_page(project.id)
    content_rows = model.Session.query(db.CsContent).filter(db.CsContent.project_id == project.id).all()
    sources = model.Session.query(db.CsDataSource).filter(db.CsDataSource.project_id == project.id).all()
    result = db.project_dictize(project)
    result.update(db._load_json(project.extras, {}).get('_portal_contacts', {}))
    return {'schema_version': SCHEMA_VERSION, 'project': result,
            'page': db.page_dictize(page, include_draft=True) if page else None,
            'content': [db.content_dictize(row) for row in content_rows if not db._load_json(row.extras, {}).get('_app_content_review')],
            'content_reviews': [db.content_dictize(row) for row in content_rows if db._load_json(row.extras, {}).get('_app_content_review')],
            'data_sources': [db.data_source_dictize(row) for row in sources],
            'structure': result.get('structure', {}), 'workplan': result.get('workplan', []),
            'portal': status(project), 'exported_at': datetime.datetime.utcnow().isoformat()}


def _app_preview_access(grant, project=None):
    from ckanext.csunesco.logic import snapshots
    try:
        claims = snapshots.app_request('/internal/ckan/preview-access', {'grant': grant})
        if claims.get('purpose') != 'portal-preview' or claims.get('expires', 0) <= time.time():
            raise ValueError()
        if project and (claims.get('scope') != 'project' or claims.get('project_slug') != project.slug):
            raise ValueError()
        if claims.get('institutional_ckan_id'):
            user = model.User.get(claims['institutional_ckan_id'])
            if (claims.get('scope') not in ('site', 'initiative') or not user
                    or user.state != 'active' or not user.sysadmin
                    or user.name != claims.get('institutional_username')
                    or user.name == tk.config.get('ckanext.csunesco.portal_service_user')):
                raise ValueError()
        return claims
    except Exception:
        raise tk.NotAuthorized('Preview permission expired or revoked') from None


def authorize_saved_preview(saved, project=None):
    if saved.get('scope') in ('site', 'initiative'):
        from ckanext.csunesco.logic.editorial_pages import verify_saved
        return verify_saved(saved)
    if saved.get('preview_grant'):
        return _app_preview_access(saved['preview_grant'], project)
    return actor_context({}, {'actor': saved['actor']}, project)


def preview(context, data):
    grant = data.get('preview_grant')
    if grant:
        require_service(context)
        project = resolve_project(data)
        claims = _app_preview_access(grant, project)
        if (data.get('schema_version') not in (1, SCHEMA_VERSION)
                or str(claims.get('key')) != str(data.get('app_project_id'))
                or claims.get('revision') != data.get('revision')
                or claims.get('checksum') != checksum(data.get('payload'))
                or data.get('checksum') != claims.get('checksum')
                or int(data.get('revision', 0)) < 1):
            raise tk.ValidationError({'preview': ['Preview revision mismatch']})
        linked = metadata(project).get('app_project_id')
        if linked and int(linked) != int(data['app_project_id']):
            raise tk.NotAuthorized('Preview belongs to another project')
        revision = data['revision']
        candidate = validate_payload(data['payload'])
    else:
        project, effective, revision, app_id = validate_envelope(context, data)
        candidate = validate_payload(data['payload'])
        validate_standard_sections(effective, project, candidate['blocks'])
    return create_preview_ticket(project, candidate, data.get('actor', {}), revision,
                                 grant=grant, expires=claims['expires'] if grant else None)


def create_preview_ticket(project, candidate, actor, revision, grant=None, expires=None):
    from ckanext.csunesco.logic import snapshots
    token = secrets.token_urlsafe(32)
    expires = min(int(expires or time.time() + 300), int(time.time()) + 300)
    warnings = []
    candidate = snapshots.materialize_media(project, candidate, preview_warnings=warnings)
    prefix = '/citizen-science/portal/media/' + project.id + '/'
    candidate = json.loads(canonical(candidate).replace(prefix, '/citizen-science/portal/preview-media/' + token + '/'))
    snapshots.write_private('preview', hashlib.sha256(token.encode()).hexdigest(), {
        'project_id': project.id, 'revision': revision, 'candidate': candidate,
        'actor': actor, 'expires': expires, 'preview_grant': grant})
    base = (tk.config.get('ckan.site_url') or '').rstrip('/')
    return {'warnings': warnings, 'ticket': token, 'url': base + '/citizen-science/portal/preview/' + token,
            'expires_at': datetime.datetime.utcfromtimestamp(expires).isoformat() + 'Z'}


def preview_view(ticket):
    from flask import Response
    from ckanext.csunesco.logic import snapshots, page_render
    try:
        if len(ticket) > 100:
            raise ValueError()
        saved = snapshots.read_private('preview', hashlib.sha256(ticket.encode()).hexdigest())
        if not saved or saved['expires'] < time.time():
            raise ValueError()
        project = db.get_project(saved['project_id'])
        authorize_saved_preview(saved, project)
    except Exception:
        return tk.abort(404, 'Preview expired')
    if saved.get('scope') in ('site', 'initiative'):
        from ckanext.csunesco.logic.editorial_pages import render_preview
        body = render_preview(saved)
    else:
        candidate = saved['candidate']
        public = db.project_dictize(project)
        from ckanext.csunesco.logic.action.projects import _stats_dict
        public['stats'] = _stats_dict(project.id)
        public.update(_public_project(candidate))
        public['structure'] = {k: v for k, v in candidate['structure'].items()
                               if k not in constants.FIELD_AUDIENCE}
        public['workplan'] = []
        public['portal_managed'] = True
        ctx = page_render.build_context({'user': None}, public, candidate['blocks'],
                                       has_region=bool(public.get('region_geojson')), can_manage=False, preview=True)
        ctx['contacts'] = {k: candidate.get('project', {}).get(k) for k in ('contact_person', 'contact_email')}
        ctx['region_url'] = '/citizen-science/portal/preview-region/' + ticket
        ctx['news_events'] = candidate.get('contents', [])
        body = tk.render('csunesco/project_landing.html', extra_vars={
            'project': public, 'blocks': candidate['blocks'], 'ctx': ctx,
            'is_draft_preview': True, 'portal_readonly_preview': True,
            'preview_parent_origin': tk.config.get('ckanext.csunesco.portal_preview_origin')})
    response = Response(body)
    origin = tk.config.get('ckanext.csunesco.portal_preview_origin') or ''
    if not origin or urlsplit(origin).scheme not in ('https', 'http'):
        return tk.abort(503, 'Preview origin is not configured')
    origin = '{0.scheme}://{0.netloc}'.format(urlsplit(origin))
    response.headers['Content-Security-Policy'] = "frame-ancestors " + origin + "; form-action 'none'"
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Robots-Tag'] = 'noindex, nofollow'
    return response


def get_actions():
    from ckanext.csunesco.logic import content_reviews
    from ckanext.csunesco.logic import editorial_pages
    return dict(editorial_pages.get_actions(), **{'csunesco_project_content_apply': content_reviews.apply,
            'csunesco_project_content_status': content_reviews.status,'csunesco_project_portal_capabilities': capabilities,
            'csunesco_project_portal_apply': apply,
            'csunesco_project_portal_status': portal_status,
            'csunesco_project_portal_export': export,
            'csunesco_project_portal_preview': preview})


def portal_status(context, data):
    require_service(context)
    project = resolve_project(data)
    # Service-only export/status never exposes profiles or registration data.
    return status(project)


def candidate_preview(project_dict, page):
    extras = db._load_json(page.extras, {}) if page else {}
    candidate = extras.get('draft_portal_payload')
    if candidate:
        project_dict.update(_public_project(candidate))
        project_dict['portal_managed'] = True
        project_dict['structure'] = {k: v for k, v in candidate.get('structure', {}).items()
                                     if k not in constants.FIELD_AUDIENCE}
        project_dict['workplan'] = []
    return candidate


def publish_contents(project, page, candidate):
    """Publish exactly the content the moderator saw, preserving CKAN URLs."""
    now = datetime.datetime.utcnow()
    existing = db.Session.query(db.CsContent).filter(db.CsContent.project_id == project.id).all()
    mapping = {str(db._load_json(row.extras, {}).get('app_content_id')): row for row in existing
               if db._load_json(row.extras, {}).get('app_content_id') is not None
               and not db._load_json(row.extras, {}).get('independent_content')}
    kept = set()
    for item in candidate.get('contents', []):
        app_id = item['app_content_id']
        row = mapping.get(app_id)
        if row is None and item.get('ckan_id'):
            row = db.get_content(item['ckan_id'])
            if row is None or row.project_id != project.id:
                raise tk.ValidationError({'contents': ['Content belongs to a different project']})
        if row is not None and db._load_json(row.extras, {}).get('independent_content'):
            continue
        if row is None:
            row = db.CsContent()
            row.slug = db.unique_content_slug(item['title'])
            row.project_id = project.id
            row.initiative_group = project.initiative_group
            row.created_by = page.submitted_by
            row.created = now
            db.Session.add(row)
        for key in ('content_type', 'title', 'body', 'media'):
            value = item.get(key)
            if key == 'media' and isinstance(value, (dict, list)):
                value = canonical(value)
            setattr(row, key, value)
        for key in ('publish_date', 'end_date'):
            value = item.get(key)
            setattr(row, key, datetime.datetime.fromisoformat(value.replace('Z', '+00:00')) if value else None)
        row.visibility = 'public'
        row.source = 'app'
        row.status = 'approved'
        row.reviewed_by = page.reviewed_by
        row.reviewed_at = now
        row.modified = now
        row.rejection_reason = None
        row.extras = canonical(dict(item.get('extras') or {}, app_content_id=app_id))
        kept.add(app_id)
        db.Session.flush()
    # Omission withdraws only entries governed by this app publication, never
    # unrelated historic CKAN rows or organization-wide editorial content.
    for app_id, row in mapping.items():
        if app_id not in kept:
            row.status = 'rejected'
            row.rejection_reason = 'Withdrawn in project publication'
            row.modified = now


def send_initial_request(project):
    from ckanext.csunesco.logic import snapshots
    extras = dict(db._load_json(project.extras, {}))
    pending = extras.get('_portal_intake')
    if not pending:
        return None
    author = model.User.get(project.created_by)
    payload = {'schema_version': 1, 'idempotency_key': 'ckan-project:' + project.id,
               'project': db.project_dictize(project),
               'actor': {'ckan_id': author.id, 'username': author.name},
               'initial_setup': {}}
    result = snapshots.app_request('/internal/ckan/project-requests', payload)
    app_id = result.get('app_project_id') or result.get('programme_id') or result.get('project_id')
    if not app_id:
        raise ValueError('App did not acknowledge project ID')
    extras.pop('_portal_intake', None)
    extras[KEY] = {'app_project_id': int(app_id), 'status': 'draft',
                   'intake_synced': True, 'updated_at': datetime.datetime.utcnow().isoformat()}
    project.extras = canonical(extras)
    model.Session.commit()
    return result


def preview_media_view(ticket, digest):
    from flask import send_file
    from ckanext.csunesco.logic import snapshots
    import re
    if len(ticket) > 100 or not re.fullmatch('[a-f0-9]{64}', digest):
        return tk.abort(404, 'Preview expired')
    saved = snapshots.read_private('preview', hashlib.sha256(ticket.encode()).hexdigest())
    if not saved or saved['expires'] < time.time():
        return tk.abort(404, 'Preview expired')
    project = db.get_project(saved['project_id'])
    try:
        authorize_saved_preview(saved, project)
    except Exception:
        return tk.abort(404, 'Preview expired')
    expected = '/citizen-science/portal/preview-media/' + ticket + '/' + digest
    if expected not in canonical(saved['candidate']):
        return tk.abort(404, 'Media not found')
    kind = snapshots.read_private('asset-types', digest)
    response = send_file(str(snapshots.root() / 'assets' / digest), mimetype=kind['type'])
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Content-Security-Policy'] = "default-src 'none'; sandbox"
    return response


def preview_region_view(ticket):
    from flask import Response
    from ckanext.csunesco.logic import snapshots
    if len(ticket) > 100:
        return tk.abort(404, 'Preview expired')
    saved = snapshots.read_private('preview', hashlib.sha256(ticket.encode()).hexdigest())
    if not saved or saved['expires'] < time.time():
        return tk.abort(404, 'Preview expired')
    project = db.get_project(saved['project_id'])
    try:
        authorize_saved_preview(saved, project)
    except Exception:
        return tk.abort(404, 'Preview expired')
    geometry = saved['candidate'].get('project', {}).get('region_geojson')
    response = Response(canonical(geometry), mimetype='application/json')
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response
