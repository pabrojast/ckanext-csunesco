# encoding: utf-8
"""Citizen Scientist self-registration view logic.

Increment 2: a blueprint-backed view (parallel to CKAN's ``/user/register``)
that creates an ACTIVE CKAN account for a Citizen Scientist -- with NO
organization step (ckanext-colab pattern, org fieldset removed). This module is
pure HTTP orchestration: it reads the form, validates it, calls the core
``user_create`` action and marks the new user as a Citizen Scientist profile.

Design notes (from advisors, see .mix/plan.md):
  * Do NOT pre-validate username/email uniqueness. Call ``user_create`` inside a
    try/except and render a GENERIC error -- never enumerate existing accounts.
  * Registration relies on CKAN's own ``user_create`` auth
    (``ckan.auth.create_user_via_web``); we add no bespoke auth function.
  * reCAPTCHA v3 is OPTIONAL: enforced only when BOTH the public and private
    keys are configured, verified SERVER-SIDE (score > 0.5); skipped silently
    otherwise.
"""
import datetime
import logging
import secrets

from flask import request

import ckan.plugins.toolkit as tk
import ckan.model as model
from ckan.logic import check_access, NotAuthorized, ValidationError

from ckanext.csunesco import constants

log = logging.getLogger(__name__)

MIN_PASSWORD_LENGTH = 8

# Single generic message for every validation/creation failure. We deliberately
# never surface per-field internals (e.g. "username already taken") so the form
# cannot be used to enumerate accounts.
GENERIC_ERROR = 'Registration data invalid, please review your details.'


def _recaptcha_configured():
    """True only when BOTH reCAPTCHA keys are present -> verification enforced."""
    return bool(
        tk.config.get('ckan.recaptcha.publickey')
        and tk.config.get('ckan.recaptcha.privatekey')
    )


def _verify_recaptcha(token):
    """Server-side reCAPTCHA v3 check (mirrors colab's verify_recaptcha).

    Returns True when Google reports success with a score above 0.5. Any error
    (network, missing token, bad response) is treated as a failed check. Only
    called when :func:`_recaptcha_configured` is True.
    """
    if not token:
        return False
    try:
        import requests

        resp = requests.post(
            constants.RECAPTCHA_SITEVERIFY_URL,
            data={
                'secret': tk.config.get('ckan.recaptcha.privatekey'),
                'response': token,
            },
            timeout=10,
        )
        result = resp.json()
    except Exception:
        log.warning('csunesco: reCAPTCHA verification could not be completed')
        return False
    return bool(result.get('success')) and result.get('score', 0) > 0.5


def _render(extra_vars):
    """Render the registration template with the reCAPTCHA public key attached."""
    extra_vars.setdefault('recaptcha_publickey',
                          tk.config.get('ckan.recaptcha.publickey'))
    return tk.render('csunesco/register_citizen.html', extra_vars=extra_vars)


def create_citizen_scientist(context, data, verification_token=None):
    """Core create-user + CS-profile flow, shared by the web view and the API.

    ``data`` is a plain dict with keys ``email``, ``username``, ``password`` and
    (optional) ``fullname`` / ``country``. It creates a CKAN account via the
    ``user_create`` action (using the passed ``context``) and idempotently
    inserts the ``cs_citizen_scientist`` profile row (persisting ``country``).

    When ``verification_token`` is given (the WEB self-registration path) the new
    account is held in CKAN ``pending`` state -- it cannot log in until the
    emailed ``/verify`` link activates it -- and the token is stored on its
    profile. With no token (the trusted API/ofform path) the account is active
    and the profile lands already verified.

    Every validation/creation failure is collapsed into a single generic
    ``ValidationError`` so callers never leak per-field internals (no account
    enumeration). ``check_access`` for ``user_create`` is left to the caller's
    context, so a ``NotAuthorized`` from a locked-down instance propagates
    unchanged. Returns the created user dict (from ``user_create``).
    """
    email = (data.get('email') or '').strip()
    username = (data.get('username') or '').lower().strip()
    fullname = (data.get('fullname') or '').strip()
    password = data.get('password') or ''
    country = (data.get('country') or '').strip()

    # Server-side minimums (mirror the web form). Any failure -> generic error.
    if not username or not email or not password:
        raise ValidationError({'message': GENERIC_ERROR})
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationError({'message': GENERIC_ERROR})

    # user_create runs its own auth check; NotAuthorized (self-registration
    # disabled / non-sysadmin token) is left to propagate to the caller.
    check_access('user_create', context)

    try:
        new_user = tk.get_action('user_create')(context, {
            'name': username,
            'email': email,
            'password': password,
            'fullname': fullname,
        })
    except ValidationError:
        # Duplicate name/email, weak password, invalid name chars, ... All are
        # collapsed into the same generic error -> no account-enumeration hint.
        log.warning('csunesco: citizen scientist account creation rejected')
        raise ValidationError({'message': GENERIC_ERROR})
    except NotAuthorized:
        raise
    except Exception:
        # Any unexpected error (DB, mailer, ...) -> still generic, never leaked.
        log.warning('csunesco: unexpected error creating citizen scientist')
        raise ValidationError({'message': GENERIC_ERROR})

    # Web path: hold the account in ``pending`` state until the emailed link is
    # opened. Both CKAN core login and the custom authenticator gate on
    # ``user.is_active()``, so a pending account cannot sign in.
    if verification_token:
        try:
            user_obj = model.User.get(new_user['id'])
            if user_obj is not None:
                user_obj.set_pending()
                model.Session.commit()
        except Exception:
            model.Session.rollback()
            log.warning('csunesco: could not set pending state on new account')

    # NON-ATOMICITY CAVEAT: the account now exists, but the profile insert below
    # is a SEPARATE transaction -- if it fails the user is still created. We log
    # a generic warning and continue rather than crash or leak internals. The
    # profile insert is idempotent (unique user_id), so retries are safe.
    try:
        from ckanext.csunesco import db
        db.get_or_create_citizen_scientist(
            new_user['id'], country=country,
            verification_token=verification_token)
    except Exception:
        model.Session.rollback()
        log.warning('csunesco: citizen scientist profile row could not be '
                    'created (account already exists)')

    return new_user


def _send_verification_email(recipient_name, recipient_email, token):
    """Email a single-use verification link. Returns True on a successful send.

    Best-effort: a mailer failure is logged (never raised) so registration still
    completes -- the user can request a fresh link from the resend form.
    """
    try:
        from ckan.lib.mailer import mail_recipient, MailerException
    except ImportError:
        log.warning('csunesco: mailer unavailable; verification email skipped')
        return False

    verify_url = tk.url_for('csunesco.verify_citizen', token=token,
                            _external=True)
    hours = constants.VERIFICATION_TOKEN_TTL_HOURS
    subject = tk._('Verify your UNESCO Citizen Science account')
    body = tk._(
        'Welcome to UNESCO Citizen Science!\n\n'
        'Please confirm your email address to activate your account by '
        'opening this link:\n\n{url}\n\n'
        'The link expires in {hours} hours. If you did not create this '
        'account, you can safely ignore this message.'
    ).format(url=verify_url, hours=hours)
    body_html = tk._(
        '<p>Welcome to <strong>UNESCO Citizen Science</strong>!</p>'
        '<p>Please confirm your email address to activate your account:</p>'
        '<p><a href="{url}">Verify my account</a></p>'
        '<p>The link expires in {hours} hours. If you did not create this '
        'account, you can safely ignore this message.</p>'
    ).format(url=verify_url, hours=hours)
    try:
        mail_recipient(recipient_name or recipient_email, recipient_email,
                       subject, body, body_html=body_html)
        return True
    except MailerException:
        log.warning('csunesco: verification email could not be sent')
        return False


def register_citizen():
    """GET renders the form; POST creates an active Citizen Scientist account."""
    if request.method == 'GET':
        return _render({'data': {}, 'errors': {}})

    # --- POST ---------------------------------------------------------------
    # Read form fields. Username is forced lowercase + stripped (CKAN name
    # rules). fullname and country are optional.
    email = request.form.get('email', '').strip()
    username = request.form.get('username', '').lower().strip()
    fullname = request.form.get('fullname', '').strip()
    password = request.form.get('password', '')
    confirm_password = request.form.get('confirm_password', '')
    country = request.form.get('country', '').strip()
    terms = request.form.get('terms')

    # Non-sensitive values we echo back on error (NEVER the password).
    data = {
        'email': email,
        'username': username,
        'fullname': fullname,
        'country': country,
    }

    def _fail():
        """Re-render the form with the generic error and the entered values."""
        return _render({'data': data, 'errors': {'message': GENERIC_ERROR}})

    # Terms acceptance is mandatory (server-side, truthy).
    if not terms:
        return _fail()

    # Password: required, min length, must match confirmation.
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        return _fail()
    if password != confirm_password:
        return _fail()

    # reCAPTCHA only enforced when configured.
    if _recaptcha_configured():
        if not _verify_recaptcha(request.form.get('recaptcha_response')):
            return _fail()

    context = {
        'model': model,
        'session': model.Session,
        'user': tk.g.user,
    }

    # Single-use, unguessable token that gates activation. The account is created
    # in ``pending`` state (cannot log in) until the emailed link is opened.
    verification_token = secrets.token_urlsafe(32)

    try:
        create_citizen_scientist(context, {
            'email': email,
            'username': username,
            'fullname': fullname,
            'password': password,
            'country': country,
        }, verification_token=verification_token)
    except NotAuthorized:
        # Self-registration via the web is disabled
        # (ckan.auth.create_user_via_web = false). Show the same generic error.
        log.warning('csunesco: user_create not authorized for citizen register')
        return _fail()
    except ValidationError:
        # Duplicate name/email, weak password, ... collapsed into one generic
        # error inside create_citizen_scientist -> no account-enumeration hint.
        return _fail()

    # Best-effort activation email (the resend form is the fallback).
    _send_verification_email(fullname or username, email, verification_token)

    # Confirmation state: the account is PENDING -> invite the user to check
    # their inbox rather than to log in. A ``pending_verification`` flag keeps the
    # confirmation inside this plugin's template (no separate success endpoint).
    return _render({'data': {}, 'errors': {},
                    'pending_verification': True, 'email': email})


def _render_verify(state):
    """Render the /verify result page for a single ``state`` string."""
    return tk.render('csunesco/verify_result.html',
                     extra_vars={'state': state})


def verify_citizen(token):
    """GET /verify/<token>: activate a pending Citizen Scientist account.

    Looks the token up, checks it has not expired, then flips the CKAN account to
    ``active`` and marks the profile verified (clearing the token so the link is
    single-use). Renders an ``ok`` / ``expired`` / ``invalid`` / ``error`` state
    and never reveals whether a given address exists.
    """
    from ckanext.csunesco import db
    profile = db.get_citizen_scientist_by_token(token)
    if profile is None:
        return _render_verify('invalid')

    created = getattr(profile, 'token_created', None)
    ttl = datetime.timedelta(hours=constants.VERIFICATION_TOKEN_TTL_HOURS)
    if created is None or (datetime.datetime.utcnow() - created) > ttl:
        return _render_verify('expired')

    try:
        user_obj = model.User.get(profile.user_id)
        if user_obj is not None:
            user_obj.activate()
            model.Session.commit()
        db.verify_citizen_scientist(profile)
    except Exception:
        model.Session.rollback()
        log.warning('csunesco: could not activate a verified citizen scientist')
        return _render_verify('error')

    return _render_verify('ok')


def resend_verification():
    """GET renders the resend form; POST re-issues a link (generic response).

    To avoid account enumeration the POST ALWAYS renders the same "if your
    account still needs verifying, we've sent a fresh link" confirmation, whether
    or not a matching pending account was found.
    """
    if request.method == 'GET':
        return tk.render('csunesco/resend_verification.html',
                         extra_vars={'sent': False})

    email = request.form.get('email', '').strip()
    if email:
        try:
            from ckanext.csunesco import db
            users = (model.Session.query(model.User)
                     .filter(model.User.email == email).all())
            for user_obj in users:
                profile = db.get_citizen_scientist(user_obj.id)
                if (profile is not None and not profile.email_verified
                        and user_obj.is_pending()):
                    token = secrets.token_urlsafe(32)
                    db.set_verification_token(user_obj.id, token)
                    _send_verification_email(
                        user_obj.fullname or user_obj.name, email, token)
                    break
        except Exception:
            log.warning('csunesco: resend verification could not be processed')

    return tk.render('csunesco/resend_verification.html',
                     extra_vars={'sent': True})
