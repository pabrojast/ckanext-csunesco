"""One editorial owner after the app migration has been verified."""
from urllib.parse import quote, urlencode
import re
import ckan.plugins.toolkit as tk
from ckanext.csunesco import db


def enabled():
    return tk.config.get('ckanext.csunesco.editorial_owner') == 'app'


def project_app_id(project):
    """Verified editor linkage is independent of an approved publication."""
    from ckanext.csunesco.logic import portal
    if project is None:
        return None
    return (portal.metadata(project).get('app_project_id')
            or db._load_json(project.extras, {}).get('_editor_app_project_id'))


def project_enabled(project):
    return enabled() or bool(project_app_id(project))


def require_bridge(context, project=None):
    # CKAN skips normal auth functions for sysadmins, so this check belongs in
    # the action body. Only internal Python callers can set this context flag.
    if project_enabled(project) and not context.get('csunesco_portal_sync'):
        raise tk.NotAuthorized('Edit this information in the Citizen Science app')


def editor_url(scope='site', key='home', section='portal'):
    base = (tk.config.get('ckanext.csunesco.ofform_app_url') or '').rstrip('/')
    if not base:
        return None
    if scope == 'project':
        row = db.get_project(key)
        app_id = project_app_id(row)
        if app_id:
            return '%s/projects/%s/space/%s' % (base, int(app_id), section)
        return base + '/explorer/projects/' + quote(str(key), safe='')
    return '%s/admin/portal-pages/%s/%s' % (base, scope, quote(str(key), safe=''))


def redirect(scope='site', key='home', section='portal'):
    from flask import request
    if request.method != 'GET':
        return tk.abort(405, 'Save changes in the Citizen Science app')
    destination = editor_url(scope, key, section)
    if not destination:
        return tk.abort(503, 'The app editor is not configured')
    section_id = request.args.get('open', '')
    if section == 'portal' and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', section_id):
        if scope == 'project':
            from ckanext.csunesco.logic import blocks
            project = db.get_project(key)
            page = db.get_project_page(project.id) if project else None
            for block in blocks.blocks_from_json(page.published_json, default=[]) if page else []:
                if block['id'] == section_id and (block['type'] in blocks.DEFAULT_BLOCK_TYPES or block['type'] in ('project_facts', 'project_structure')):
                    section_id = block['type']
                    break
        destination += '?' + urlencode({'open': section_id})
    return tk.redirect_to(destination)


def editor_link(endpoint, **kwargs):
    """Use app links in existing navigation without losing legacy installations."""
    # New proposals stay in CKAN, independently of subsequent app editing.
    if endpoint == 'csunesco.project_new':
        return tk.url_for(endpoint, **kwargs)
    if endpoint == 'csunesco.site_page_edit' and enabled():
        return editor_url() or tk.url_for(endpoint, **kwargs)
    if endpoint == 'csunesco.initiative_page_edit' and enabled():
        return editor_url('initiative', kwargs['name']) or tk.url_for(endpoint, **kwargs)
    if endpoint == 'csunesco.content_edit':
        content = db.get_content(kwargs['id'])
        if content and content.project_id and project_enabled(db.get_project(content.project_id)):
            return editor_url('project', content.project_id, 'news') or tk.url_for(endpoint, **kwargs)
        return tk.url_for(endpoint, **kwargs)
    section = {'csunesco.project_edit': 'details', 'csunesco.project_page_edit': 'portal', 'csunesco.content_new': 'news'}.get(endpoint)
    if section and project_enabled(db.get_project(kwargs['slug'])):
        return editor_url('project', kwargs['slug'], section) or tk.url_for(endpoint, **kwargs)
    return tk.url_for(endpoint, **kwargs)
