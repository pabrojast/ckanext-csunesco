"""Errores públicos de registro: mensajes permitidos, nunca excepciones crudas."""
import ckan.plugins.toolkit as tk

MESSAGES = {
    'required': 'Please complete the required fields.',
    'email_invalid': 'Enter a valid email address.',
    'username_invalid': 'Use at least 2 characters: lowercase letters, numbers, hyphens or underscores.',
    'username_taken': 'This username is already in use. Choose another one or sign in.',
    'account_conflict': 'An account already uses these details. Sign in or recover your password.',
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
    'service_unavailable': 'Registration is temporarily unavailable. Please try again later.',
}


def problem(code, *fields):
    if code not in MESSAGES:
        code = 'service_unavailable'
    return {'message': tk._(MESSAGES[code]), 'code': 'registration_' + code,
            'fields': {field: 'registration_' + code for field in fields}}


def from_validation(error):
    data = error.error_dict or {}
    if str(data.get('code', '')).removeprefix('registration_') in MESSAGES:
        return data
    for source, field in [('name', 'username'), ('email', 'email'), ('password', 'password')]:
        if source not in data:
            continue
        text = str(data[source]).lower()
        duplicate = any(x in text for x in ('already', 'unavailable', 'exists', 'in use'))
        code = ('username_taken' if duplicate else 'username_invalid') if field == 'username' else (
            ('account_conflict' if duplicate else 'email_invalid') if field == 'email' else 'password_short')
        return problem(code, field)
    return problem('service_unavailable')
