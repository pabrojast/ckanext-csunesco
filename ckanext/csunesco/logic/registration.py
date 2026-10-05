# encoding: utf-8
"""Citizen Scientist self-registration view logic.

This blueprint-backed view (parallel to CKAN's ``/user/register``) creates a
PENDING CKAN account for an individual Citizen Scientist, persists its private
profile fields and can immediately file a request to join an approved project.
Email verification remains the activation gate. This module is pure HTTP
orchestration: it reads and validates the form, calls the core ``user_create``
action and marks the new user as a Citizen Scientist profile.

Design notes (from advisors, see .mix/plan.md):
  * CKAN validates account creation. Only allowlisted, actionable validation
    messages reach the public form; unexpected exceptions stay private.
  * Registration relies on CKAN's own ``user_create`` auth
    (``ckan.auth.create_user_via_web``); we add no bespoke auth function.
  * reCAPTCHA v3 is OPTIONAL: enforced only when BOTH the public and private
    keys are configured, verified SERVER-SIDE (score > 0.5); skipped silently
    otherwise.
"""
import datetime
import logging
import math
import re
import secrets
import threading
import time
import unicodedata
from collections import defaultdict, deque

from flask import request
from babel import Locale, UnknownLocaleError

import ckan.plugins.toolkit as tk
import ckan.model as model
from ckan.logic import check_access, NotAuthorized, ValidationError

from ckanext.csunesco import constants
from ckanext.csunesco.logic.registration_errors import problem, from_validation

log = logging.getLogger(__name__)

MIN_PASSWORD_LENGTH = 8

GENDER_VALUES = frozenset((
    'female', 'male', 'non_binary', 'prefer_not_to_say',
))
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+$')

RATE_LIMIT_ENABLED_OPTION = (
    'ckanext.csunesco.registration_rate_limit_enabled')
RATE_LIMIT_MAX_OPTION = 'ckanext.csunesco.registration_rate_limit_max'
RATE_LIMIT_WINDOW_OPTION = 'ckanext.csunesco.registration_rate_limit_window'
DEFAULT_RATE_LIMIT_MAX = 10
DEFAULT_RATE_LIMIT_WINDOW = 300
MAX_REGISTRATION_PROJECTS = 500

# Single generic message for every validation/creation failure. We deliberately
# never surface per-field internals (e.g. "username already taken") so the form
# cannot be used to enumerate accounts.
GENERIC_ERROR = 'Registration data invalid, please review your details.'


class _RegistrationLimiter(object):
    """Small thread-safe, per-worker sliding-window limiter.

    CKAN has no shared rate-limit service available to extensions.  This is a
    best-effort first line of defence (matching ofform's limiter); reCAPTCHA and
    the deployment proxy may add stronger/shared controls.  Blocked attempts do
    not extend the window, so a client is always released after the advertised
    Retry-After period.
    """

    def __init__(self):
        self._events = defaultdict(deque)
        self._lock = threading.Lock()

    def consume(self, key, maximum, window):
        now = time.monotonic()
        cutoff = now - window
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= maximum:
                return max(1, int(math.ceil(events[0] + window - now)))
            events.append(now)
            # Prevent an unbounded dictionary of one-shot IPs. Empty/old keys
            # are cheap to identify because each deque is time-ordered.
            if len(self._events) > 10000:
                stale = [name for name, values in self._events.items()
                         if not values or values[-1] <= cutoff]
                for name in stale:
                    self._events.pop(name, None)
            return None

    def clear(self):
        """Test/support hook; production never needs to reset the limiter."""
        with self._lock:
            self._events.clear()


registration_limiter = _RegistrationLimiter()


def _positive_config_int(name, default):
    try:
        value = int(tk.config.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _registration_retry_after():
    """Consume one POST allowance; return retry seconds when limited."""
    try:
        enabled = tk.asbool(tk.config.get(RATE_LIMIT_ENABLED_OPTION, True))
    except (TypeError, ValueError):
        enabled = True
    if not enabled:
        return None
    maximum = _positive_config_int(
        RATE_LIMIT_MAX_OPTION, DEFAULT_RATE_LIMIT_MAX)
    window = _positive_config_int(
        RATE_LIMIT_WINDOW_OPTION, DEFAULT_RATE_LIMIT_WINDOW)
    # ``remote_addr`` has already passed through CKAN/Flask's configured proxy
    # handling. Never trust a caller-supplied X-Forwarded-For header here.
    key = request.remote_addr or 'unknown'
    return registration_limiter.consume(key, maximum, window)


def _parse_optional_profile(data):
    """Normalize and validate the optional profile fields.

    Raises the same generic ValidationError used by account creation.  The
    nationality label is canonical English in storage; the form localizes only
    its presentation.
    """
    raw_dob = data.get('date_of_birth')
    if isinstance(raw_dob, datetime.datetime):
        date_of_birth = raw_dob.date()
    elif isinstance(raw_dob, datetime.date):
        date_of_birth = raw_dob
    elif raw_dob:
        try:
            date_of_birth = datetime.datetime.strptime(
                str(raw_dob).strip(), '%Y-%m-%d').date()
        except (TypeError, ValueError):
            raise ValidationError(problem('date_invalid', 'date_of_birth'))
    else:
        date_of_birth = None
    if date_of_birth and date_of_birth > datetime.date.today():
        raise ValidationError(problem('date_invalid', 'date_of_birth'))

    nationality = (data.get('nationality') or '').strip().upper()
    # Two non-ISO sentinels, stored as themselves so profiles stay
    # comparable: 'OTHER' for nationalities the ISO list cannot express
    # (stateless, unrecognized territories) and 'PREFER_NOT_TO_SAY' -- the
    # 2026 reporting rules make the field mandatory, so declining to answer
    # must be an explicit choice rather than a skipped input.
    if (nationality
            and nationality not in ('OTHER', 'PREFER_NOT_TO_SAY')
            and nationality not in constants.ISO_3166_ALPHA2):
        raise ValidationError(problem('nationality_invalid', 'nationality'))

    gender = (data.get('gender') or '').strip()
    if gender and gender not in GENDER_VALUES:
        raise ValidationError(problem('gender_invalid', 'gender'))

    return date_of_birth, nationality or None, gender or None


def _generate_username(fullname, email=None):
    """Derive an available CKAN username from a person's name (spec: username
    is optional and "generated based on their name" when left blank).

    Slugifies the full name (fallback: the email local part), then probes CKAN
    for availability, appending ``-2``, ``-3``... on collision. A final random
    suffix guarantees termination even against an adversarial namespace. The
    result always satisfies CKAN's name rules (lowercase ``a-z0-9-_``, >= 2
    chars).
    """
    base_source = (fullname or '').strip() or (email or '').split('@')[0]
    # Deaccent first ('María Pérez' -> 'Maria Perez') so common Latin names
    # produce readable handles instead of hyphen soup.
    base_source = unicodedata.normalize('NFKD', base_source)
    base_source = base_source.encode('ascii', 'ignore').decode('ascii')
    base = re.sub(r'[^a-z0-9_-]+', '-', base_source.lower()).strip('-_')
    base = re.sub(r'-{2,}', '-', base)[:80]
    if len(base) < 2:
        base = 'citizen'
    candidate = base
    for suffix in range(2, 200):
        if model.User.get(candidate) is None:
            return candidate
        candidate = '%s-%d' % (base, suffix)
    return '%s-%s' % (base, secrets.token_hex(4))


def _country_name(code, locale='en'):
    """Canonical/localized country label for an ISO code, with safe fallback."""
    if not code:
        return None
    if code == 'OTHER':
        # The non-ISO sentinel the form offers; Babel knows no territory for
        # it, and 'OTHER' as a stored country label would read as shouting.
        return 'Other'
    if code == 'PREFER_NOT_TO_SAY':
        return 'Prefer not to say'
    try:
        parsed = Locale.parse(locale or 'en', sep='_')
    except (UnknownLocaleError, ValueError):
        parsed = Locale.parse('en')
    return parsed.territories.get(code) or code


def _country_options():
    """Localized nationality options sorted in the request's language."""
    try:
        language = tk.h.lang() or 'en'
    except Exception:
        language = 'en'
    rows = [
        {'code': code, 'label': _country_name(code, language)}
        for code in constants.ISO_3166_ALPHA2
    ]
    return sorted(rows, key=lambda row: row['label'].casefold())


def _registration_projects():
    """All approved projects for sign-up, fail-soft and capped at 500."""
    projects = []
    try:
        offset = 0
        while offset < MAX_REGISTRATION_PROJECTS:
            listing = tk.get_action('csunesco_project_list')({
                'model': model, 'session': model.Session,
                'user': getattr(tk.g, 'user', None),
                'csunesco_public_view': True,
            }, {'limit': 100, 'offset': offset, 'status': 'approved'})
            batch = listing.get('results') or []
            projects.extend(batch)
            offset += len(batch)
            if not batch or offset >= listing.get('count', 0):
                break
    except Exception:
        log.warning('csunesco: registration project list unavailable')
        return []
    return sorted(projects[:MAX_REGISTRATION_PROJECTS],
                  key=lambda row: (row.get('title') or '').casefold())


def _selected_project(projects, value):
    wanted = (value or '').strip()
    if not wanted:
        return None
    for project in projects:
        if wanted in (str(project.get('id') or ''),
                      str(project.get('slug') or '')):
            return project
    return None


def _ofform_register_url():
    """Configured CS Toolbox registration URL, or None when links are off."""
    try:
        from ckanext.csunesco.logic import ofform
        base = (tk.config.get(ofform.APP_URL_OPTION) or '').strip().rstrip('/')
    except Exception:
        return None
    return (base + '/register-citizen') if base else None


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
    """Render registration with stable choices and optional integration links."""
    extra_vars.setdefault('recaptcha_publickey',
                          tk.config.get('ckan.recaptcha.publickey'))
    extra_vars.setdefault('country_options', _country_options())
    extra_vars.setdefault('projects', _registration_projects())
    extra_vars.setdefault('ofform_register_url', _ofform_register_url())
    extra_vars.setdefault('today', datetime.date.today().isoformat())
    selected_value = (extra_vars.get('data') or {}).get('project')
    extra_vars.setdefault(
        'selected_project',
        _selected_project(extra_vars.get('projects') or [], selected_value))
    return tk.render('csunesco/register_citizen.html', extra_vars=extra_vars)


def create_citizen_scientist(context, data, verification_token=None):
    """Core create-user + CS-profile flow, shared by the web view and the API.

    ``data`` is a plain dict with keys ``email``, ``username``, ``password`` and
    (optional) ``fullname`` / profile fields. It creates a CKAN account via the
    ``user_create`` action (using the passed ``context``) and idempotently
    inserts the ``cs_citizen_scientist`` profile row (persisting ``country``).

    When ``verification_token`` is given (web and current app registrations) the new
    account is held in CKAN ``pending`` state -- it cannot log in until the
    emailed ``/verify`` link activates it -- and the token is stored on its
    profile. With no token (legacy trusted API callers) the account is active
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
    date_of_birth, nationality, gender = _parse_optional_profile(data)
    if nationality:
        # Store a stable human compatibility value alongside the ISO code.
        country = _country_name(nationality, 'en')
    terms_accepted = bool(data.get('terms_accepted'))
    motivation = str(data.get('motivation') or '').strip()
    language = str(data.get('language') or '').strip()
    language_other = str(data.get('language_other') or '').strip()
    if motivation and not 20 <= len(motivation) <= 500:
        raise ValidationError(problem('motivation_invalid', 'motivation'))
    if language and language not in ('en', 'es', 'fr', 'ar', 'pt', 'uk', 'quh') or len(language_other) > 64:
        raise ValidationError(problem('language_invalid', 'language', 'language_other'))
    project_slug = data.get('registration_project_slug')
    if project_slug:
        from ckanext.csunesco import db
        selected = db.get_project(project_slug)
        project_slug = selected.slug if selected and selected.status == 'approved' else None

    # Username is optional (spec: generated from the name when blank). This is
    # ADDITIVE for the API path: a payload that sends one behaves exactly as
    # before.
    if not username and (fullname or email):
        username = _generate_username(fullname, email)

    # Validaciones públicas que indican el campo a corregir.
    if not username or not email or not password:
        raise ValidationError(problem('required', 'username', 'email', 'password'))
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationError(problem('password_short', 'password'))
    if not EMAIL_RE.match(email):
        raise ValidationError(problem('email_invalid', 'email'))

    # user_create runs its own auth check; NotAuthorized (self-registration
    # disabled / non-sysadmin token) is left to propagate to the caller.
    check_access('user_create', context)

    try:
        new_user = tk.get_action('user_create')(dict(context, defer_commit=True), {
            'name': username,
            'email': email,
            'password': password,
            'fullname': fullname,
        })
    except ValidationError as exc:
        # Solo mensajes permitidos; las excepciones internas no se publican.
        log.warning('csunesco: citizen scientist account creation rejected')
        raise ValidationError(from_validation(exc))
    except NotAuthorized:
        raise
    except Exception:
        # Any unexpected error (DB, mailer, ...) -> still generic, never leaked.
        # Roll back first: a DB error leaves the session in an aborted
        # transaction and the error page itself would 500 on the next query.
        model.Session.rollback()
        log.warning('csunesco: unexpected error creating citizen scientist')
        raise ValidationError(problem('service_unavailable'))

    # Verified-registration path: hold the account in ``pending`` state until the emailed link is
    # opened. Both CKAN core login and the custom authenticator gate on
    # ``user.is_active``, so a pending account cannot sign in.
    if verification_token:
        try:
            user_obj = model.User.get(new_user['id'])
            if user_obj is not None:
                user_obj.set_pending()
        except Exception:
            model.Session.rollback()
            raise ValidationError(problem('service_unavailable'))

    # Commit the CKAN account, pending state and private profile together.
    # A failed profile write must not leave an active account without a dossier.
    try:
        from ckanext.csunesco import db
        db.get_or_create_citizen_scientist(
            new_user['id'], country=country,
            verification_token=verification_token,
            date_of_birth=date_of_birth,
            nationality=nationality,
            gender=gender,
            terms_accepted=terms_accepted,
            manager=data.get('manager'), motivation=motivation,
            language=language, language_other=language_other,
            registration_project_slug=project_slug,
            defer_commit=True)
        model.Session.commit()
    except Exception:
        model.Session.rollback()
        log.warning('csunesco: registration transaction failed')
        raise ValidationError(problem('service_unavailable'))

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

    try:
        verify_url = tk.url_for('csunesco.verify_citizen', token=token,
                                _external=True)
    except Exception:
        # BuildError / malformed ckan.site_url must not abort a registration
        # that already created the account.
        log.warning('csunesco: could not build verification URL; email skipped')
        return False
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
    except Exception as e:
        # CKAN's mailer leaks raw smtplib errors: its ``finally: quit()`` on a
        # dead connection raises SMTPServerDisconnected, replacing the
        # MailerException in flight. Best-effort means catching everything.
        log.warning('csunesco: verification email failed: %s',
                    type(e).__name__)
        return False


def register_citizen():
    """GET renders the form; POST creates a pending Citizen Scientist account."""
    if request.method == 'GET':
        return _render({
            'data': {'project': request.args.get('project', '').strip()},
            'errors': {},
        })

    # --- POST ---------------------------------------------------------------
    retry_after = _registration_retry_after()

    # Read form fields. Username is forced lowercase + stripped (CKAN name
    # rules). Profile and project fields are optional.
    email = request.form.get('email', '').strip()
    username = request.form.get('username', '').lower().strip()
    fullname = request.form.get('fullname', '').strip()
    password = request.form.get('password', '')
    confirm_password = request.form.get('confirm_password', '')
    date_of_birth = request.form.get('date_of_birth', '').strip()
    nationality = request.form.get('nationality', '').strip().upper()
    gender = request.form.get('gender', '').strip()
    motivation = request.form.get('motivation', '').strip()
    language = request.form.get('language', '').strip()
    language_other = request.form.get('language_other', '').strip()
    project_value = request.form.get('project', '').strip()
    terms = request.form.get('terms')

    # Non-sensitive values we echo back on error (NEVER the password).
    data = {
        'email': email,
        'username': username,
        'fullname': fullname,
        'date_of_birth': date_of_birth,
        'nationality': nationality,
        'gender': gender,
        'motivation': motivation, 'language': language, 'language_other': language_other,
        'project': project_value,
    }

    def _fail(status=200, headers=None, errors=None):
        """Re-render with actionable errors and the entered values."""
        rendered = _render({
            'data': data,
            'errors': errors or problem('required'),
        })
        if status == 200 and not headers:
            return rendered
        return rendered, status, (headers or {})

    if retry_after is not None:
        return _fail(429, {'Retry-After': str(retry_after)}, problem('too_many_attempts'))

    # Terms acceptance is mandatory (server-side, truthy).
    if not terms:
        return _fail(errors=problem('terms_required', 'terms'))

    # Required identity/demographics -- enforced in the WEB form only (the
    # API action, ofform's frozen payload, stays lenient on purpose; the
    # per-caller strictness split lives here in the view). Nationality joined
    # the list with the 2026 reporting rules: beneficiaries must be
    # disaggregated by gender, age class and Member State, and an optional
    # field yields ~30% completion (the OpenLearning experience).
    if not fullname or not date_of_birth or not gender or not nationality:
        missing = [k for k in ('fullname', 'date_of_birth', 'gender', 'nationality') if not data[k]]
        return _fail(errors=problem('required', *missing))

    if not 20 <= len(motivation) <= 500:
        return _fail(errors=problem('motivation_invalid', 'motivation'))

    # Password: required, min length, must match confirmation.
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        return _fail(errors=problem('password_short', 'password'))
    if password != confirm_password:
        return _fail(errors=problem('password_mismatch', 'confirm_password'))

    # Parse profile values now so invalid dates/codes fail before user_create.
    try:
        parsed_dob, parsed_nationality, parsed_gender = _parse_optional_profile({
            'date_of_birth': date_of_birth,
            'nationality': nationality,
            'gender': gender,
        })
    except ValidationError as exc:
        return _fail(errors=from_validation(exc))

    # reCAPTCHA only enforced when configured.
    if _recaptcha_configured():
        if not _verify_recaptcha(request.form.get('recaptcha_response')):
            return _fail(errors=problem('captcha_failed'))

    context = {
        'model': model,
        'session': model.Session,
        'user': tk.g.user,
    }

    # Single-use, unguessable token that gates activation. The account is created
    # in ``pending`` state (cannot log in) until the emailed link is opened.
    verification_token = secrets.token_urlsafe(32)

    try:
        new_user = create_citizen_scientist(context, {
            'email': email,
            'username': username,
            'fullname': fullname,
            'password': password,
            'date_of_birth': parsed_dob,
            'nationality': parsed_nationality,
            'gender': parsed_gender,
            'motivation': motivation, 'language': language, 'language_other': language_other,
            'registration_project_slug': project_value,
            'terms_accepted': True,
        }, verification_token=verification_token)
    except NotAuthorized:
        # Self-registration via the web is disabled
        # (ckan.auth.create_user_via_web = false). Show the same generic error.
        log.warning('csunesco: user_create not authorized for citizen register')
        return _fail(errors=problem('registration_disabled'))
    except ValidationError as exc:
        return _fail(errors=from_validation(exc))

    # Join is deliberately immediate, even though the account remains pending.
    # The reviewer queue already exposes the verification flag. As in ofform,
    # a bad/unknown project or a join failure never rolls back account creation.
    projects = _registration_projects()
    selected_project = _selected_project(projects, project_value)
    join_requested = False
    if selected_project is not None:
        try:
            user_obj = model.User.get(new_user['id'])
            result = tk.get_action('csunesco_join_request_create')({
                'model': model,
                'session': model.Session,
                'user': new_user['name'],
                'auth_user_obj': user_obj,
            }, {'project_id': selected_project['id']})
            join_requested = bool(result)
        except Exception:
            model.Session.rollback()
            log.warning('csunesco: registration join request failed')

    # Best-effort activation email (the resend form is the fallback).
    _send_verification_email(fullname or username, email, verification_token)

    # Confirmation state: the account is PENDING -> invite the user to check
    # their inbox rather than to log in. A ``pending_verification`` flag keeps the
    # confirmation inside this plugin's template (no separate success endpoint).
    return _render({
        'data': {},
        'errors': {},
        'pending_verification': True,
        'email': email,
        'join_project': selected_project if join_requested else None,
    })


def _organization_options():
    """Existing CKAN organizations for the PM form, fail-soft and sorted."""
    try:
        try:
            from ckanext.colab.controller import get_all_organizations_cached
        except ImportError:
            get_all_organizations_cached = None
        if get_all_organizations_cached:
            # Form validation needs a fresh catalog after another PM was approved.
            get_all_organizations_cached.cache_clear()
        rows = get_all_organizations_cached() if get_all_organizations_cached else tk.get_action('organization_list')({
            'model': model, 'session': model.Session,
            'user': getattr(tk.g, 'user', None),
        }, {'all_fields': True, 'limit': 1000})
    except Exception:
        log.warning('csunesco: organization list unavailable for PM form')
        return []
    options = [{'name': row.get('name'),
                'title': row.get('title') or row.get('name')}
               for row in rows if row.get('name')]
    return sorted(options, key=lambda row: row['title'].casefold())


def _render_manager(extra_vars):
    """Render the PM registration form with its stable choice lists."""
    extra_vars.setdefault('recaptcha_publickey',
                          tk.config.get('ckan.recaptcha.publickey'))
    extra_vars.setdefault('country_options', _country_options())
    extra_vars.setdefault('org_types', constants.ORG_TYPES)
    extra_vars.setdefault('organizations', _organization_options())
    extra_vars.setdefault('today', datetime.date.today().isoformat())
    return tk.render('csunesco/register_manager.html', extra_vars=extra_vars)


def register_manager():
    """GET/POST: Project Manager self-registration (spec section 3).

    Same hardening as the Citizen Scientist form (rate limit, reCAPTCHA,
    generic errors, email verification), plus the Organization block. The
    account is double-gated: after the email is verified it STAYS pending
    until a sysadmin approves it (``csunesco_manager_approve``), which is when
    the declared organization is created/joined -- never at sign-up, so an
    unvetted visitor cannot spam the org registry.
    """
    if request.method == 'GET':
        return _render_manager({'data': {}, 'errors': {}})

    # --- POST ---------------------------------------------------------------
    retry_after = _registration_retry_after()

    email = request.form.get('email', '').strip()
    username = request.form.get('username', '').lower().strip()
    fullname = request.form.get('fullname', '').strip()
    password = request.form.get('password', '')
    confirm_password = request.form.get('confirm_password', '')
    date_of_birth = request.form.get('date_of_birth', '').strip()
    nationality = request.form.get('nationality', '').strip().upper()
    gender = request.form.get('gender', '').strip()
    motivation = request.form.get('motivation', '').strip()
    language = request.form.get('language', '').strip()
    language_other = request.form.get('language_other', '').strip()
    org_type = request.form.get('org_type', '').strip()
    org_name = request.form.get('org_name', '').strip()
    new_org_name = request.form.get('new_org_name', '').strip()
    org_title = request.form.get('org_title', '').strip()
    responsibilities = request.form.get('responsibilities')
    org_role = request.form.get('org_role', 'member').strip()
    org_description = request.form.get('org_description', '').strip()

    data = {
        'email': email,
        'username': username,
        'fullname': fullname,
        'date_of_birth': date_of_birth,
        'nationality': nationality,
        'gender': gender,
        'org_type': org_type,
        'org_name': org_name,
        'new_org_name': new_org_name,
        'org_title': org_title,
    }

    data.update(motivation=motivation, language=language, language_other=language_other,
                org_role=org_role, org_description=org_description)

    def _fail(status=200, headers=None, errors=None):
        rendered = _render_manager({
            'data': data,
            'errors': errors or problem('required'),
        })
        if status == 200 and not headers:
            return rendered
        return rendered, status, (headers or {})

    if retry_after is not None:
        return _fail(429, {'Retry-After': str(retry_after)}, problem('too_many_attempts'))

    # The responsibilities acknowledgement is this form's terms checkbox.
    if not responsibilities:
        return _fail(errors=problem('terms_required', 'responsibilities'))

    # Required fields: identity, demographics (incl. nationality -- the 2026
    # Member-State reporting rule, same as the citizen form) and the whole
    # org block.
    if not fullname or not date_of_birth or not gender or not nationality:
        missing = [k for k in ('fullname', 'date_of_birth', 'gender', 'nationality') if not data[k]]
        return _fail(errors=problem('required', *missing))
    if org_type not in {row['name'] for row in constants.ORG_TYPES}:
        return _fail(errors=problem('organization_invalid', 'org_name', 'org_title', 'org_type', 'new_org_name'))
    if not org_title:
        return _fail(errors=problem('organization_invalid', 'org_name', 'org_title', 'org_type', 'new_org_name'))

    if not 20 <= len(motivation) <= 500:
        return _fail(errors=problem('motivation_invalid', 'motivation'))
    if org_role not in ('member', 'admin') or len(org_description) > 5000:
        return _fail(errors=problem('organization_invalid', 'org_role', 'org_description'))

    # A new organization makes the applicant its admin after review.
    creating_org = org_name == '__new__'
    if creating_org and not new_org_name:
        return _fail(errors=problem('organization_invalid', 'org_name', 'org_title', 'org_type', 'new_org_name'))
    if not creating_org and not org_name:
        return _fail(errors=problem('organization_invalid', 'org_name', 'org_title', 'org_type', 'new_org_name'))
    org_id = None
    if not creating_org:
        # Validate against the live list so a forged value cannot smuggle an
        # arbitrary string into the approval flow.
        known = {row['name'] for row in _organization_options()}
        if org_name not in known:
            return _fail(errors=problem('organization_invalid', 'org_name', 'org_title', 'org_type', 'new_org_name'))
        org_id = org_name

    if not password or len(password) < MIN_PASSWORD_LENGTH:
        return _fail(errors=problem('password_short', 'password'))
    if password != confirm_password:
        return _fail(errors=problem('password_mismatch', 'confirm_password'))

    try:
        parsed_dob, parsed_nationality, parsed_gender = _parse_optional_profile({
            'date_of_birth': date_of_birth,
            'nationality': nationality,
            'gender': gender,
        })
    except ValidationError as exc:
        return _fail(errors=from_validation(exc))

    if _recaptcha_configured():
        if not _verify_recaptcha(request.form.get('recaptcha_response')):
            return _fail(errors=problem('captcha_failed'))

    context = {
        'model': model,
        'session': model.Session,
        'user': tk.g.user,
    }
    org_image_url = None
    logo_upload = None
    if creating_org:
        from ckanext.csunesco.logic.onboarding import store_org_logo, exact_org_match
        if exact_org_match(new_org_name):
            return _fail(errors=problem('organization_invalid', 'new_org_name'))
        try:
            org_image_url, logo_upload = store_org_logo(request.files.get('org_logo'))
        except ValidationError as exc:
            return _fail(errors=from_validation(exc))
    verification_token = secrets.token_urlsafe(32)

    try:
        create_citizen_scientist(context, {
            'email': email,
            'username': username,
            'fullname': fullname,
            'password': password,
            'date_of_birth': parsed_dob,
            'nationality': parsed_nationality,
            'gender': parsed_gender,
            'motivation': motivation, 'language': language, 'language_other': language_other,
            'terms_accepted': True,
            'manager': {
                'org_id': org_id,
                'org_name_requested': new_org_name if creating_org else None,
                'org_type': org_type,
                'org_title': org_title,
                # New organizations start with the requester as admin;
                # existing organizations use the requested Member/Admin role.
                'org_role': 'admin' if creating_org else org_role,
                'org_description': org_description if creating_org else None,
                'org_image_url': org_image_url,
            },
        }, verification_token=verification_token)
    except NotAuthorized:
        if logo_upload:
            logo_upload.rollback()
        log.warning('csunesco: user_create not authorized for PM register')
        return _fail(errors=problem('registration_disabled'))
    except ValidationError as exc:
        if logo_upload:
            logo_upload.rollback()
        return _fail(errors=from_validation(exc))
    except Exception:
        if logo_upload:
            logo_upload.rollback()
        raise

    _send_verification_email(fullname or username, email, verification_token)

    return _render_manager({
        'data': {},
        'errors': {},
        'pending_verification': True,
        'email': email,
    })


def _render_verify(state, project_slug=None):
    """Render the /verify result page for a single ``state`` string."""
    return tk.render('csunesco/verify_result.html',
                     extra_vars={'state': state, 'project_slug': project_slug})


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

    # Project Manager accounts have a SECOND gate: verifying the email proves
    # the address but the account stays CKAN-pending until a sysadmin approves
    # it (csunesco_manager_approve) -- the spec's "IHP Admin approves/declines
    # the user account" step. Citizens activate right here as before.
    is_manager = getattr(profile, 'profile_type', None) == 'manager'

    try:
        if not is_manager:
            user_obj = model.User.get(profile.user_id)
            if user_obj is not None:
                user_obj.activate()
                model.Session.commit()
        db.verify_citizen_scientist(profile)
    except Exception:
        model.Session.rollback()
        log.warning('csunesco: could not activate a verified citizen scientist')
        return _render_verify('error')

    return _render_verify('manager_pending' if is_manager else 'ok', getattr(profile, 'registration_project_slug', None))


def resend_verification():
    """GET renders the resend form; POST re-issues a link (generic response).

    To avoid account enumeration the POST ALWAYS renders the same "if your
    account still needs verifying, we've sent a fresh link" confirmation, whether
    or not a matching pending account was found.
    """
    if request.method == 'GET':
        return tk.render('csunesco/resend_verification.html',
                         extra_vars={'sent': False})

    # MISMO throttle que el alta (`register_citizen` / `register_manager`).
    # Sin él este POST era anónimo e ilimitado, y cada llamada rotaba el token
    # de la víctima: bastaba un bucle contra una dirección pendiente conocida
    # para (a) mandar correo sin tope y (b) invalidar una y otra vez el enlace
    # que esa persona tenía abierto, dejándola sin poder verificarse nunca.
    # La respuesta 429 es idéntica a la del alta, así que no añade un oráculo:
    # depende del llamante, no de si la cuenta existe.
    retry_after = _registration_retry_after()
    if retry_after is not None:
        response = tk.render('csunesco/resend_verification.html',
                             extra_vars={'sent': True})
        return (response, 429, {'Retry-After': str(retry_after)})

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
