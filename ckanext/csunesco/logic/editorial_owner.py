"""One editorial owner after the app migration has been verified."""
from urllib.parse import quote
import ckan.plugins.toolkit as tk
from ckanext.csunesco import db


def enabled():
    return tk.config.get('ckanext.csunesco.editorial_owner') == 'app'


def project_intake_in_app():
    """Move only proposal intake without transferring every editorial page."""
    return enabled() or tk.config.get('ckanext.csunesco.project_intake_owner') == 'app'


def require_bridge(context):
    # CKAN skips normal auth functions for sysadmins, so this check belongs in
    # the action body. Only internal Python callers can set this context flag.
    if enabled() and not context.get('csunesco_portal_sync'):
        raise tk.NotAuthorized('Edit this information in the Citizen Science app')


def editor_url(scope='site', key='home', section='portal'):
    base = (tk.config.get('ckanext.csunesco.ofform_app_url') or '').rstrip('/')
    if not base:
        return None
    if scope == 'project':
        from ckanext.csunesco.logic import portal
        row = db.get_project(key)
        app_id = (portal.metadata(row).get('app_project_id') or db._load_json(row.extras, {}).get('_editor_app_project_id')) if row else None
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
    return tk.redirect_to(destination)


def editor_link(endpoint, **kwargs):
    """Use app links in existing navigation without losing legacy installations."""
    if endpoint == 'csunesco.project_new' and project_intake_in_app():
        return (tk.config.get('ckanext.csunesco.ofform_app_url') or '').rstrip('/') + '/explorer/start'
    if not enabled():
        return tk.url_for(endpoint, **kwargs)
    if endpoint == 'csunesco.site_page_edit':
        return editor_url()
    if endpoint == 'csunesco.initiative_page_edit':
        return editor_url('initiative', kwargs['name'])
    if endpoint == 'csunesco.content_edit':
        content = db.get_content(kwargs['id'])
        if content and content.project_id:
            return editor_url('project', content.project_id, 'news')
        return tk.url_for(endpoint, **kwargs)
    section = {'csunesco.project_edit': 'details', 'csunesco.project_page_edit': 'portal', 'csunesco.content_new': 'news'}.get(endpoint)
    return editor_url('project', kwargs['slug'], section) if section else tk.url_for(endpoint, **kwargs)
