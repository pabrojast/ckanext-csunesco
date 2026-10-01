"""Trusted app bridge for Home and initiative pages; no moderation queue."""
import copy
import datetime
import hashlib
import json
import secrets
import time
from types import SimpleNamespace

import ckan.model as model
import ckan.plugins.toolkit as tk
from ckanext.csunesco import constants, db
from ckanext.csunesco.logic import blocks, portal, snapshots, page_render

META = '_app_editorial'


def identity(data):
    scope, key = data.get('scope'), data.get('key')
    if scope == 'site' and key == 'home':
        return db.SITE_PAGE_ID
    if scope == 'initiative' and key in {i['name'] for i in constants.CS_INITIATIVES}:
        return db.initiative_page_id(key)
    raise tk.ObjectNotFound('Page not found')


def actor(context, data):
    supplied = data.get('actor') or {}
    user = model.User.get(supplied.get('ckan_id') or supplied.get('username'))
    if (not user or user.state != 'active' or supplied.get('username') != user.name
            or user.name == tk.config.get('ckanext.csunesco.portal_service_user')):
        raise tk.NotAuthorized('An active CKAN publication author is required')
    effective = {'user': user.name, 'auth_user_obj': user, 'model': model,
                 'session': model.Session, 'csunesco_portal_sync': True}
    action = 'csunesco_site_page_publish' if data['scope'] == 'site' else 'csunesco_initiative_page_publish'
    from ckanext.csunesco.logic import auth
    if not getattr(auth, action)(effective, {'initiative': data['key']})['success']:
        raise tk.NotAuthorized('The author cannot publish this page')
    tk.check_access(action, effective, {'initiative': data['key']})
    return user


def validated(data):
    identity(data)
    raw = data.get('payload')
    if not isinstance(raw, dict) or len(portal.canonical(raw).encode()) > portal.MAX_PAYLOAD:
        raise tk.ValidationError({'payload': ['Invalid page']})
    if data.get('schema_version') != 1 or data.get('page_key') != data['scope'] + ':' + data['key']:
        raise tk.ValidationError({'page_key': ['Invalid page identity']})
    if not isinstance(data.get('revision'), int) or data['revision'] < 1 or portal.checksum(raw) != data.get('checksum'):
        raise tk.ValidationError({'revision': ['Invalid page revision or checksum']})
    raw_blocks = raw.get('blocks')
    if not isinstance(raw_blocks, list) or len(raw_blocks) > blocks.MAX_BLOCKS:
        raise tk.ValidationError({'blocks': ['Invalid sections']})
    ids = []
    for block in raw_blocks:
        kind = blocks.BLOCK_TYPES.get(block.get('type')) if isinstance(block, dict) else None
        if not kind or data['scope'] not in kind.scopes:
            raise tk.ValidationError({'blocks': ['Section is unavailable for this page']})
        ids.append(block.get('id'))
    if not all(ids) or len(ids) != len(set(ids)):
        raise tk.ValidationError({'blocks': ['Sections must have unique identifiers']})
    candidate = copy.deepcopy(raw)
    candidate['blocks'] = blocks.ensure_builtins(blocks.normalize_blocks(raw_blocks), scope=data['scope'])
    from ckanext.csunesco.logic.action.page import _validate_policy
    _validate_policy(candidate['blocks'])
    if blocks.oversized(candidate['blocks']):
        raise tk.ValidationError({'blocks': ['Page is too large']})
    return candidate


def export(context, data):
    portal.require_service(context)
    page_id = identity(data)
    page = db.get_project_page(page_id)
    result = db.page_dictize(page, include_draft=True) if page else None
    if result:
        meta = db._load_json(page.extras, {}).get(META, {})
        result.update(draft_layout=meta.get('layout', {}), published_layout=meta.get('layout', {}))
    return {'page_key': data['scope'] + ':' + data['key'], 'page': result,
            'defaults': blocks.default_site_blocks() if data['scope'] == 'site' else blocks.default_initiative_blocks()}


def apply(context, data):
    portal.require_service(context)
    page_id = identity(data)
    author = actor(context, data)
    candidate = validated(data)
    candidate = snapshots.materialize_media(SimpleNamespace(id=page_id), candidate, endpoint_kind='page-assets')
    page = db.get_or_create_project_page(page_id, created_by=author.id)
    # Serialize publications to prevent two workers overwriting a newer revision.
    page = db.Session.query(db.CsProjectPage).filter(db.CsProjectPage.project_id == page_id).with_for_update().one()
    extras = db._load_json(page.extras, {})
    prior = extras.get(META, {})
    if prior.get('revision', 0) > data['revision'] or (prior.get('revision') == data['revision'] and prior.get('checksum') != data['checksum']):
        raise tk.ValidationError({'revision': ['Stale or conflicting page revision']})
    if prior.get('revision') != data['revision']:
        now = datetime.datetime.utcnow()
        page.draft_json = page.published_json = blocks.blocks_to_json(candidate['blocks'])
        from ckanext.csunesco.logic.action.page import draft_hash
        page.draft_hash = draft_hash(candidate['blocks'])
        page.status, page.reviewed_by = 'approved', author.id
        page.published_at = page.reviewed_at = page.modified = now
        page.rejection_reason = None
        import re
        hashes = re.findall(re.escape('/citizen-science/portal/media/' + page_id + '/') + r'([a-f0-9]{64})', portal.canonical(candidate))
        extras[META] = {'revision': data['revision'], 'checksum': data['checksum'],
                        'layout': candidate.get('layout', {}), 'media_hashes': sorted(set(hashes))}
        page.extras = portal.canonical(extras)
        db.Session.add(page); db.Session.commit()
    return {'page_key': data['page_key'], 'revision': data['revision'], 'checksum': data['checksum'],
            'status': 'approved', 'published_blocks': blocks.blocks_from_json(page.published_json)}


def verify_saved(saved):
    claims = portal._app_preview_access(saved['preview_grant'])
    if (claims.get('scope') != saved['scope'] or claims.get('key') != saved['key']
            or claims.get('checksum') != saved['checksum'] or claims.get('revision') != saved['revision']):
        raise tk.NotAuthorized('Preview belongs to another page')
    return claims


def preview(context, data):
    portal.require_service(context)
    page_id = identity(data)
    candidate = validated(data)
    claims = verify_saved(data)
    warnings = []
    candidate = snapshots.materialize_media(SimpleNamespace(id=page_id), candidate, endpoint_kind='page-assets', preview_warnings=warnings)
    ticket = secrets.token_urlsafe(32)
    candidate = json.loads(portal.canonical(candidate).replace('/citizen-science/portal/media/' + page_id + '/', '/citizen-science/portal/preview-media/' + ticket + '/'))
    expires = min(int(claims['expires']), int(time.time()) + 300)
    snapshots.write_private('preview', hashlib.sha256(ticket.encode()).hexdigest(), {
        'scope': data['scope'], 'key': data['key'], 'project_id': page_id,
        'preview_grant': data['preview_grant'], 'candidate': candidate,
        'checksum': data['checksum'], 'revision': data['revision'], 'expires': expires})
    return {'warnings': warnings, 'url': (tk.config.get('ckan.site_url') or '').rstrip('/') + '/citizen-science/portal/preview/' + ticket,
            'expires_at': datetime.datetime.utcfromtimestamp(expires).isoformat() + 'Z'}


def render_preview(saved):
    candidate = saved['candidate']
    scope = saved['scope']
    initiative = next((i for i in constants.CS_INITIATIVES if i['name'] == saved['key']), None)
    ctx = page_render.build_context({'user': None}, None, candidate['blocks'], can_manage=False,
                                     preview=True, scope=scope, initiative=initiative)
    return tk.render('csunesco/citizen-science.html' if scope == 'site' else 'csunesco/initiative.html', extra_vars={
        'initiative': initiative, 'blocks': candidate['blocks'], 'ctx': ctx,
        'is_draft_preview': True, 'portal_readonly_preview': True,
        'preview_parent_origin': tk.config.get('ckanext.csunesco.portal_preview_origin')})


def public_media_allowed(page_id, digest):
    if page_id != db.SITE_PAGE_ID and page_id not in {db.initiative_page_id(i['name']) for i in constants.CS_INITIATIVES}:
        return False
    page = db.get_project_page(page_id)
    return bool(page and page.published_json and digest in db._load_json(page.extras, {}).get(META, {}).get('media_hashes', []))


def export_all(context, data):
    portal.require_service(context)
    projects = []
    for row in db.Session.query(db.CsProject).all():
        item = portal.export(context, {'project_id': row.id})
        item['source_origin'] = tk.config.get('ckan.site_url')
        owners = set(db.project_admin_user_ids(row.id))
        actor_ids = owners | {row.created_by} | {c.get('created_by') for c in item.get('content', [])}
        item['actors'] = []
        for identifier in actor_ids:
            user = model.User.get(identifier) if identifier else None
            if user:
                item['actors'].append({'ckan_id': user.id, 'username': user.name,
                    'display_name': user.fullname or user.name, 'active': user.state == 'active',
                    'project_owner': user.id in owners})
        projects.append(item)
    scopes = [{'scope': 'site', 'key': 'home'}] + [{'scope': 'initiative', 'key': i['name']} for i in constants.CS_INITIATIVES]
    return {'schema_version': 1, 'projects': projects,
            'pages': [dict(item, **export(context, item)) for item in scopes]}


def link_projects(context, data):
    portal.require_service(context)
    links = data.get('projects')
    if not isinstance(links, list):
        raise tk.ValidationError({'projects': ['Expected project mappings']})
    from ckanext.csunesco.logic.editorial_owner import project_app_id
    existing_links = {row.id: project_app_id(row) for row in db.Session.query(db.CsProject).all()}
    prepared = []
    for link in links:
        if not isinstance(link, dict):
            raise tk.ValidationError({'projects': ['Invalid project mapping']})
        project = db.get_project(link.get('ckan_id'))
        app_id = link.get('app_project_id')
        if not project or type(app_id) is not int or app_id < 1:
            raise tk.ValidationError({'projects': ['Invalid project mapping']})
        extras = db._load_json(project.extras, {})
        existing = existing_links.get(project.id)
        if existing and existing != app_id:
            raise tk.ValidationError({'projects': ['Project already linked to another app record']})
        if any(value == app_id and key != project.id for key, value in existing_links.items()):
            raise tk.ValidationError({'projects': ['App record already linked to another project']})
        existing_links[project.id] = app_id
        prepared.append((project, extras, app_id))
    for project, extras, app_id in prepared:
        extras['_editor_app_project_id'] = app_id
        project.extras = portal.canonical(extras); db.Session.add(project)
    db.Session.commit()
    return {'linked': len(links)}


def get_actions():
    return {'csunesco_editorial_page_export': export, 'csunesco_editorial_page_apply': apply,
            'csunesco_editorial_page_preview': preview, 'csunesco_editorial_export_all': export_all,
            'csunesco_editorial_link_projects': link_projects}
