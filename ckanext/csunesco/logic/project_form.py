"""Read-only project form checks shared by the wizard and the final POST."""
import html
import re

import ckan.model as model
import ckan.plugins.toolkit as tk

from ckanext.csunesco import db
from ckanext.csunesco.logic import auth, schema, validators
from ckanext.csunesco.logic.action import current_user_id
from ckanext.csunesco.logic.action.projects import resolve_editors, _sanitize_html
from ckanext.csunesco.logic.data_access import validate_project_access

EXTERNAL_INITIATIVE = '__external__'


def validate(data, context, strict=True, project=None, draft=False):
    """Return normalized data and field errors, without writes or uploads."""
    incoming = dict(data)
    external_selected = incoming.get('initiative') == EXTERNAL_INITIATIVE
    if external_selected:
        incoming['initiative'] = ''
    rules = schema.project_request_form_schema() if strict else schema.project_request_schema()
    if project:
        rules.pop('slug', None)
        context = dict(context, csunesco_existing_countries=project.get('countries') or [])
    incoming = {key: value for key, value in incoming.items() if key in rules}
    normalized, errors = tk.navl_validate(incoming, rules, context)
    errors = dict(errors)

    def error(field, message):
        errors.setdefault(field, []).append(tk._(message))

    def check(fn):
        try:
            fn()
        except tk.ValidationError as exc:
            for key, messages in (exc.error_dict or {}).items():
                errors.setdefault(key, []).extend(messages)

    if not draft and external_selected and not normalized.get('external_initiative_name'):
        error('external_initiative_name', 'Enter the name of the external initiative, or choose an independent project.')
    check(lambda: validators.validate_initiative_affiliation(normalized, project))
    check(lambda: validate_project_access(normalized, project))
    description = html.unescape(re.sub(r'<[^>]*>', '', _sanitize_html(incoming.get('short_description') or ''))).strip()
    if strict and not description and 'short_description' not in errors:
        error('short_description', 'Enter a short description of the project.')
    for name, value, limit in (
        ('title', incoming.get('title') or '', 200),
        ('slug', incoming.get('slug') or '', 100),
        ('short_description', description, 2000),
    ):
        if len(value) > limit:
            errors.setdefault(name, []).append(tk._('Maximum %s characters.') % limit)
    point_keys = ('point_lat', 'point_lng', 'point_radius_km')
    if not draft and not normalized.get('region_geojson') and any(incoming.get(key) not in (None, '') for key in point_keys):
        for key in point_keys:
            if incoming.get(key) in (None, ''):
                error(key, 'Enter latitude, longitude and radius together, or leave all three empty.')
    if normalized.get('organization_id'):
        org = model.Group.get(normalized['organization_id'])
        if not org or not org.is_organization or org.state != 'active':
            error('organization_id', 'Select an active CKAN organization.')
        elif not auth.can_propose_for_org(context, org.id):
            error('organization_id', 'You do not have permission to propose projects for this organization.')
    if 'editors' not in errors:
        owner = project.get('created_by') if project else current_user_id(context)
        def editors():
            normalized['editors'] = list(resolve_editors(normalized.get('editors') or [], owner).values())
        check(editors)
    return normalized, errors


def authorized_context(project_id=None):
    """Use the same authority as the form; never trust a supplied user name."""
    context = {'model': model, 'session': model.Session, 'user': tk.g.user}
    user = auth._user_obj(context)
    if not user or user.state != 'active':
        tk.abort(401, tk._('Sign in again, then retry. Your form has not been submitted.'))
    project = None
    if project_id:
        row = db.get_project(project_id)
        if row is None or not auth.can_edit_project_details(context, row):
            tk.abort(403)
        project = db.project_dictize(row)
    elif not auth.can_propose_project(context):
        tk.abort(403)
    return context, project
