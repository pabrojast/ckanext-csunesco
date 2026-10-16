"""Toolbox PM intake; identity and moderation remain in CKAN/Colab."""
import base64
import io

import ckan.model as model
import ckan.plugins.toolkit as tk
from werkzeug.datastructures import FileStorage

from ckanext.csunesco import constants, db
from ckanext.csunesco.logic import registration
from ckanext.csunesco.logic.registration_profile import acting_user, require_registered
from ckanext.csunesco.logic.registration_errors import problem


def csunesco_manager_application_show(context, data_dict):
    from ckanext.csunesco.logic.registration_profile import csunesco_registration_profile_show
    profile = csunesco_registration_profile_show(context, data_dict)
    return {'profile': profile, 'organizations': registration._organization_options(),
            'org_types': list(constants.ORG_TYPES)}


def csunesco_manager_application_submit(context, data_dict):
    from ckanext.csunesco.logic.onboarding import store_org_logo, exact_org_match
    user = acting_user(context, data_dict)
    require_registered(user)
    profile = db.get_citizen_scientist(user.id)
    if profile.profile_type == 'manager':
        return csunesco_manager_application_show(context, data_dict)
    data = data_dict
    fields = {key: str(data.get(key) or '').strip() for key in
              ('org_type', 'org_name', 'new_org_name', 'org_title', 'org_role', 'org_description')}
    try:
        accepted = tk.asbool(data.get('responsibilities', False))
    except (ValueError, TypeError):
        accepted = False
    if not accepted:
        raise tk.ValidationError(problem('terms_required', 'responsibilities'))
    creating = fields['org_name'] == '__new__'
    if (fields['org_type'] not in {row['name'] for row in constants.ORG_TYPES}
            or not fields['org_title'] or len(fields['org_title']) > 200
            or fields['org_role'] not in ('member', 'admin')
            or len(fields['org_description']) > 5000
            or (creating and (not fields['new_org_name'] or len(fields['new_org_name']) > 200
                              or exact_org_match(fields['new_org_name'])))
            or (not creating and fields['org_name'] not in
                {row['name'] for row in registration._organization_options()})):
        raise tk.ValidationError(problem('organization_invalid', 'org_name', 'org_title', 'org_type', 'new_org_name'))
    image_url, upload = None, None
    if creating and data.get('org_logo'):
        try:
            logo = data['org_logo']
            encoded = logo['content_base64']
            if len(encoded) > 2800000:
                raise ValueError('too large')
            raw = base64.b64decode(encoded, validate=True)
            image_url, upload = store_org_logo(FileStorage(
                stream=io.BytesIO(raw), filename=logo['filename']))
        except (ValueError, KeyError, TypeError):
            raise tk.ValidationError(problem('logo_invalid', 'org_logo'))
    try:
        created = registration._request_manager_access(user.id, {}, {
            'org_id': None if creating else fields['org_name'],
            'org_name_requested': fields['new_org_name'] if creating else None,
            'org_type': fields['org_type'], 'org_title': fields['org_title'],
            'org_role': 'admin' if creating else fields['org_role'],
            'org_description': fields['org_description'] if creating else None,
            'org_image_url': image_url,
        })
        if not created and upload:
            upload.rollback()
    except Exception:
        model.Session.rollback()
        if upload:
            upload.rollback()
        raise
    return csunesco_manager_application_show(context, data_dict)


def get_actions():
    return {fn.__name__: fn for fn in
            (csunesco_manager_application_show, csunesco_manager_application_submit)}
