"""Private, self-service registration for existing CKAN identities.

Login is deliberately not registration: reads never create a dossier or consent.
The same completion check protects browser and trusted Toolbox entry points.
"""
import datetime

import ckan.model as model
import ckan.plugins.toolkit as tk

from ckanext.csunesco import db
from ckanext.csunesco.logic import auth

FIELDS = ('fullname', 'date_of_birth', 'nationality', 'gender', 'language',
          'language_other', 'motivation')
REQUIRED = ('fullname', 'date_of_birth', 'nationality', 'gender', 'language',
            'motivation', 'terms_accepted_at')


def completeness(user, profile=None):
    profile = profile or (db.get_citizen_scientist(user.id) if user else None)
    missing = [key for key in REQUIRED if not (
        getattr(user, key, None) if key == 'fullname' else getattr(profile, key, None))]
    return {'profile_exists': profile is not None, 'profile_complete': not missing,
            'missing_fields': missing}


def require_complete(user):
    state = completeness(user)
    if not state['profile_complete']:
        raise tk.ValidationError({'registration_profile_required': state['missing_fields']})


def require_registered(user):
    require_complete(user)
    profile = db.get_citizen_scientist(user.id)
    if user.state != 'active' or not profile.email_verified:
        raise tk.ValidationError({'email_verification_required': ['Verify your email before continuing.']})
    return profile


def acting_user(context, data):
    from ckanext.csunesco.logic.onboarding import review_context
    actual = review_context(context, data)
    user = auth._user_obj(actual)
    if user is None or getattr(user, 'state', None) != 'active':
        raise tk.NotAuthorized('An active account is required')
    # Neither a browser nor the trusted service may accidentally edit a second identity.
    if data.get('id') and data['id'] != user.id:
        raise tk.NotAuthorized('Only your own registration may be edited')
    return user


def csunesco_registration_profile_show(context, data_dict):
    from ckanext.csunesco.logic.onboarding import profile_dict
    user = acting_user(context, data_dict or {})
    state = completeness(user)
    profile = db.get_citizen_scientist(user.id)
    eligible = bool(state['profile_complete'] and profile.email_verified
                    and auth.can_propose_project({'user': user.name, 'auth_user_obj': user}))
    return dict(profile_dict(user), **state, can_propose_project=eligible)


def csunesco_registration_profile_update(context, data_dict):
    from ckanext.csunesco.logic import registration
    from ckanext.csunesco.logic.onboarding import profile_dict
    data = data_dict or {}
    user = acting_user(context, data)
    allowed = set(FIELDS) | {'terms_accepted', 'id', 'actor_id', 'actor_username'}
    if set(data) - allowed:
        raise tk.ValidationError({'profile': ['Unknown profile fields']})
    values = {key: str(data.get(key) or '').strip() for key in FIELDS}
    missing = [key for key in REQUIRED if key != 'terms_accepted_at' and not values.get(key)]
    if missing:
        raise tk.ValidationError({key: ['Missing value'] for key in missing})
    if not 20 <= len(values['motivation']) <= 500 or len(values['fullname']) > 200:
        raise tk.ValidationError({'motivation': ['Use between 20 and 500 characters']})
    if values['language'] not in ('en', 'es', 'fr', 'pt', 'ar', 'uk', 'quh') or len(values['language_other']) > 64:
        raise tk.ValidationError({'language': ['Invalid language']})
    dob, nationality, gender = registration._parse_optional_profile(values)
    try:
        accepted = tk.asbool(data.get('terms_accepted', False))
    except (TypeError, ValueError):
        accepted = False
    # The user-row lock serializes first-time creation as well as edits.
    user = registration._lock_manager_account(user.id)
    profile = db.get_citizen_scientist(user.id)
    if not accepted and not getattr(profile, 'terms_accepted_at', None):
        raise tk.ValidationError({'terms_accepted': ['Accept the terms of use']})
    if profile is None:
        profile = db.get_or_create_citizen_scientist(user.id, defer_commit=True)
        # Existing active CKAN accounts retain the established trusted-identity policy.
        profile.email_verified = True
    user.fullname = values.pop('fullname')
    values.update(date_of_birth=dob, nationality=nationality, gender=gender)
    for key, value in values.items():
        setattr(profile, key, value or None)
    if accepted and not profile.terms_accepted_at:
        profile.terms_accepted_at = datetime.datetime.utcnow()
    model.Session.commit()
    return dict(profile_dict(user, profile), **completeness(user, profile))


def pending_manager(user):
    if user is None or getattr(user, 'state', None) != 'active':
        return None
    profile = db.get_citizen_scientist(user.id)
    if (profile and profile.profile_type == 'manager' and profile.email_verified
            and profile.manager_decision is None and completeness(user, profile)['profile_complete']):
        return profile
    return None


def safe_next(value):
    # Only return to CS pages, never to login actions, arbitrary origins or protocol URLs.
    from urllib.parse import urlsplit
    value = str(value or '')
    parsed = urlsplit(value)
    path = parsed.path
    for locale in ('en', 'es', 'fr', 'pt', 'ar', 'uk', 'quh'):
        if path.startswith('/' + locale + '/'):
            path = path[len(locale) + 1:]
            break
    if (parsed.scheme or parsed.netloc or '\\' in value or any(ord(c) < 32 for c in value)
            or not path.startswith('/citizen-science/')
            or path.startswith('/citizen-science/profile/')):
        return tk.url_for('csunesco.index')
    return value


def complete_profile():
    from flask import request
    from ckanext.csunesco.logic import registration
    context = {'model': model, 'session': model.Session, 'user': tk.g.user}
    if not tk.g.user:
        return tk.redirect_to('user.login', came_from=tk.url_for('csunesco.complete_profile'))
    destination = safe_next(request.values.get('next'))
    current = csunesco_registration_profile_show(context, {})
    errors = {}
    if request.method == 'POST':
        data = {key: request.form.get(key) for key in FIELDS}
        data['terms_accepted'] = bool(request.form.get('terms_accepted'))
        try:
            csunesco_registration_profile_update(context, data)
            return tk.redirect_to(destination)
        except tk.ValidationError as exc:
            model.Session.rollback()
            errors = exc.error_dict
            current.update(data)
    return tk.render('csunesco/profile_complete.html', extra_vars={
        'data': current, 'errors': errors, 'next': destination,
        'country_options': registration._country_options(),
        'today': datetime.date.today().isoformat(),
    })


def get_actions():
    return {fn.__name__: fn for fn in (csunesco_registration_profile_show,
                                      csunesco_registration_profile_update)}


def needs_verification_link(user, profile):
    return bool(profile and user and user.state == 'pending'
                and getattr(profile, 'manager_decision', None) != 'rejected'
                and (not profile.email_verified or profile.profile_type == 'manager'))
