"""Errores públicos de registro: mensajes permitidos, nunca excepciones crudas."""
import logging

import ckan.plugins.toolkit as tk

log = logging.getLogger(__name__)

MESSAGES = {
    'language_invalid': 'Choose a supported language and keep other language below 65 characters.',
    'logo_invalid': 'Use a PNG, JPG or WebP image, at most 2 MB.',
    'motivation_invalid': 'Tell us your motivation in 20 to 500 characters.',
    'required': 'Please complete the required fields.',
    'email_invalid': 'Enter a valid email address.',
    'username_invalid': 'Use at least 2 characters: lowercase letters, numbers, hyphens or underscores.',
    'username_taken': 'This username is already in use. Choose another one or sign in.',
    'account_conflict': 'An IHP-WINS account already uses this email. Sign in or reset your password.',
    'password_short': 'Your password must contain at least 8 characters.',
    'password_mismatch': 'The passwords do not match.',
    'date_invalid': 'Enter a valid date of birth that is not in the future.',
    'nationality_invalid': 'Choose a nationality from the list.',
    'gender_invalid': 'Choose a gender option from the list.',
    'terms_required': 'Please accept the terms or responsibilities to continue.',
    'organization_invalid': 'Complete the organization information and select a valid organization.',
    'captcha_failed': 'The security check failed. Please try again.',
    'too_many_attempts': 'Too many registration attempts. Please wait a few minutes and try again.',
    'registration_disabled': 'Account registration is currently unavailable. Please contact support.',
    'service_unavailable': 'We could not complete your request. Please try again in a few minutes.',
}


FIELDS = frozenset(('username', 'email', 'password', 'confirm_password', 'fullname',
    'date_of_birth', 'nationality', 'gender', 'terms_accepted', 'language',
    'language_other', 'motivation', 'org_name', 'org_type', 'org_title', 'org_role',
    'new_org_name', 'org_description', 'org_logo', 'responsibilities'))


def selected_language(value=None):
    from flask import has_request_context, request
    from ckanext.csunesco.logic.verification_copy import language_code
    if not value and has_request_context():
        value = request.form.get('language') or request.args.get('lng') or request.environ.get('CKAN_LANG')
    return language_code(value)


def copy_for(value=None):
    from ckanext.csunesco.logic.registration_error_copy import COPY
    return COPY[selected_language(value)]


def problem(code, *fields, retry_seconds=None):
    if code not in MESSAGES:
        code = 'service_unavailable'
    copy = copy_for()
    message = copy[code] if code in copy else tk._(MESSAGES[code])
    if code == 'too_many_attempts':
        import math
        message = (copy['too_many_attempts'].replace('{{minutes}}', str(max(1, math.ceil(retry_seconds / 60))))
                   if retry_seconds is not None else copy['rate_limit_unknown'])
    return {'message': message, 'code': 'registration_' + code,
            'fields': {field: 'registration_' + code for field in fields if field in FIELDS}}


def from_validation(error):
    data = error.error_dict or {}
    code = str(data.get('code', '')).removeprefix('registration_')
    if code in MESSAGES:
        return problem(code, *(data.get('fields') or {}))
    for source, field in [('name', 'username'), ('email', 'email'), ('password', 'password')]:
        if source not in data:
            continue
        text = str(data[source]).lower()
        duplicate = any(x in text for x in ('already', 'unavailable', 'not available', 'exists', 'in use', 'belongs to', 'registered user'))
        code = ('username_taken' if duplicate else 'username_invalid') if field == 'username' else (
            ('account_conflict' if duplicate else 'email_invalid') if field == 'email' else 'password_short')
        return problem(code, field)
    known = [key for key in data if key in FIELDS]
    if known:
        codes = {'motivation': 'motivation_invalid', 'language': 'language_invalid',
                 'language_other': 'language_invalid', 'org_logo': 'logo_invalid',
                 'terms_accepted': 'terms_required', 'responsibilities': 'terms_required'}
        return problem(codes.get(known[0], 'required'), *known)
    return problem('service_unavailable')


def integrity_problem(error):
    original = getattr(error, 'orig', None)
    if getattr(original, 'pgcode', None) != '23505':
        return None
    constraint = getattr(getattr(original, 'diag', None), 'constraint_name', None)
    known = {'idx_only_one_active_email': ('account_conflict', 'email'),
             'user_name_key': ('username_taken', 'username')}
    return problem(*known[constraint]) if constraint in known else None


def record_failure(stage, error):
    original = getattr(error, 'orig', None)
    constraint = getattr(getattr(original, 'diag', None), 'constraint_name', None)
    constraint = constraint if constraint in ('idx_only_one_active_email', 'user_name_key') else 'other'
    # Exception strings / traceback exception tails may contain passwords and SQL.
    log.warning('registration failure stage=%s exception=%s constraint=%s',
                stage, type(error).__name__, constraint)


def form_error_context(language=None, next_path=None):
    from flask import has_request_context, request
    lang = selected_language(language)
    from ckanext.csunesco.logic.registration_error_copy import COPY
    context = {'registration_error_translations': COPY, 'registration_error_copy': copy_for(lang), 'registration_error_language': lang}
    if not has_request_context():
        return context
    from urllib.parse import urlencode
    # Only the CS continuation already validated by the registration view is accepted.
    destination = tk.url_for('csunesco.register_manager') if next_path in (['/register-project-manager', '/citizen-science/register/project-manager'] + ['/' + code + '/citizen-science/register/project-manager' for code in COPY]) else tk.url_for('csunesco.index')
    context.update(registration_retry_url=request.full_path.rstrip('?'), registration_signin_url=tk.url_for('user.login', came_from=destination, lng=lang),
                   registration_reset_url=tk.url_for('user.request_reset', came_from=destination, lng=lang),
                   registration_resend_url=tk.url_for('csunesco.resend_verification') + '?' + urlencode({'lng': lang, 'next': next_path or ''}))
    return context
