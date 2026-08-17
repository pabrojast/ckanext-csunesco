# encoding: utf-8
"""Citizen Scientist registration redesign: contract and orchestration tests."""
import datetime

import pytest

try:
    from flask import Flask, g
    import ckan.model as model
    import ckan.plugins.toolkit as tk
    from ckanext.csunesco.logic import registration
    from ckanext.csunesco.logic.action import registration as registration_action
    HAVE_CKAN = True
except Exception:  # pragma: no cover - host without CKAN
    HAVE_CKAN = False

pytestmark = pytest.mark.skipif(not HAVE_CKAN, reason='requires CKAN')


@pytest.fixture
def app():
    app = Flask(__name__)
    app.secret_key = 'registration-test'
    return app


def test_optional_profile_normalizes_and_rejects_invalid_values():
    dob, nationality, gender = registration._parse_optional_profile({
        'date_of_birth': '1990-05-17',
        'nationality': 'cl',
        'gender': 'non_binary',
    })
    assert dob == datetime.date(1990, 5, 17)
    assert nationality == 'CL'
    assert gender == 'non_binary'

    # The two intentional-refusal sentinels (2026 mandatory-field rules).
    _dob, nationality, _gender = registration._parse_optional_profile(
        {'nationality': 'PREFER_NOT_TO_SAY'})
    assert nationality == 'PREFER_NOT_TO_SAY'
    assert registration._country_name('PREFER_NOT_TO_SAY') == \
        'Prefer not to say'

    tomorrow = datetime.date.today() + datetime.timedelta(days=1)
    with pytest.raises(tk.ValidationError):
        registration._parse_optional_profile({
            'date_of_birth': tomorrow.isoformat(),
        })
    with pytest.raises(tk.ValidationError):
        registration._parse_optional_profile({'nationality': 'XX'})
    with pytest.raises(tk.ValidationError):
        registration._parse_optional_profile({'gender': 'not-listed'})


def test_project_deep_link_accepts_slug_or_id_only():
    rows = [{'id': 'uuid-1', 'slug': 'river-x', 'title': 'River X'}]
    assert registration._selected_project(rows, 'river-x') == rows[0]
    assert registration._selected_project(rows, 'uuid-1') == rows[0]
    assert registration._selected_project(rows, 'unknown') is None


def test_limiter_counts_successful_consumptions_and_releases(monkeypatch):
    limiter = registration._RegistrationLimiter()
    clock = {'now': 10.0}
    monkeypatch.setattr(registration.time, 'monotonic', lambda: clock['now'])

    assert limiter.consume('ip', 2, 60) is None
    clock['now'] = 11.0
    assert limiter.consume('ip', 2, 60) is None
    clock['now'] = 12.0
    assert limiter.consume('ip', 2, 60) == 58
    clock['now'] = 71.0
    assert limiter.consume('ip', 2, 60) is None


def test_web_registration_creates_immediate_join_and_keeps_verification(
        app, monkeypatch):
    projects = [{'id': 'p1', 'slug': 'river-x', 'title': 'River X'}]
    captured = {'join': None, 'created': None, 'mail': None}

    monkeypatch.setattr(registration, '_registration_retry_after', lambda: None)
    monkeypatch.setattr(registration, '_recaptcha_configured', lambda: False)
    monkeypatch.setattr(registration, '_registration_projects', lambda: projects)
    monkeypatch.setattr(registration, '_render', lambda values: values)
    monkeypatch.setattr(registration.secrets, 'token_urlsafe', lambda size: 'token')
    monkeypatch.setattr(model.User, 'get', staticmethod(lambda value: type(
        'User', (), {'id': 'user-1', 'name': 'maria',
                    'is_anonymous': False, 'sysadmin': False})()))

    def create(context, data, verification_token=None):
        captured['created'] = (data, verification_token)
        return {'id': 'user-1', 'name': 'maria'}

    def get_action(name):
        assert name == 'csunesco_join_request_create'
        return lambda context, data: captured.update(join=data) or {
            'status': 'pending'}

    monkeypatch.setattr(registration, 'create_citizen_scientist', create)
    monkeypatch.setattr(tk, 'get_action', get_action)
    monkeypatch.setattr(
        registration, '_send_verification_email',
        lambda name, email, token: captured.update(mail=(name, email, token)))

    with app.test_request_context('/register', method='POST', data={
        'email': 'maria@example.org',
        'username': 'Maria',
        'password': 'long-enough',
        'confirm_password': 'long-enough',
        'terms': 'yes',
        'fullname': 'Maria Example',
        'date_of_birth': '1990-05-17',
        'nationality': 'cl',
        'gender': 'female',
        'project': 'river-x',
    }):
        g.user = ''
        out = registration.register_citizen()

    data, token = captured['created']
    assert token == 'token'
    assert data['date_of_birth'] == datetime.date(1990, 5, 17)
    assert data['nationality'] == 'CL'
    assert data['terms_accepted'] is True
    assert captured['join'] == {'project_id': 'p1'}
    assert captured['mail'] == ('Maria Example', 'maria@example.org', 'token')
    assert out['pending_verification'] is True
    assert out['join_project']['slug'] == 'river-x'


def test_rate_limited_post_is_generic_429(app, monkeypatch):
    monkeypatch.setattr(registration, '_registration_retry_after', lambda: 37)
    monkeypatch.setattr(registration, '_render', lambda values: values)
    with app.test_request_context('/register', method='POST'):
        out = registration.register_citizen()
    body, status, headers = out
    assert status == 429
    assert headers['Retry-After'] == '37'
    assert body['errors']['message'] == registration.GENERIC_ERROR


def test_ofform_legacy_action_payload_remains_valid(monkeypatch):
    captured = {}
    monkeypatch.setattr(tk, 'check_access', lambda *args, **kwargs: True)
    monkeypatch.setattr(model.User, 'get', staticmethod(lambda value: None))

    def create(context, data):
        captured.update(data)
        return {'name': 'maria', 'id': 'user-1'}

    monkeypatch.setattr(
        registration_action, 'create_citizen_scientist', create)
    out = registration_action.csunesco_register_citizen_scientist({}, {
        'email': 'maria@example.org',
        'username': 'maria',
        'password': 'long-enough',
        'fullname': 'Maria',
        'country': 'Chile',
    })
    assert out['status'] == 'success'
    assert captured['country'] == 'Chile'
    assert captured['date_of_birth'] is None
    assert captured['terms_accepted'] is False


def test_ofform_action_accepts_optional_profile_fields(monkeypatch):
    captured = {}
    monkeypatch.setattr(tk, 'check_access', lambda *args, **kwargs: True)
    monkeypatch.setattr(model.User, 'get', staticmethod(lambda value: None))
    monkeypatch.setattr(
        registration_action, 'create_citizen_scientist',
        lambda context, data: captured.update(data) or {
            'name': 'maria', 'id': 'user-1'})

    registration_action.csunesco_register_citizen_scientist({}, {
        'email': 'maria@example.org', 'username': 'maria',
        'password': 'long-enough', 'date_of_birth': '1990-05-17',
        'nationality': 'CL', 'gender': 'female', 'terms_accepted': True,
    })
    assert captured['nationality'] == 'CL'
    assert captured['gender'] == 'female'
    assert captured['terms_accepted'] is True


# --------------------------------------------------------------------------- #
# Username auto-generation (spec: optional, generated from the name)          #
# --------------------------------------------------------------------------- #

def test_generate_username_slugifies_and_dedupes(monkeypatch):
    taken = {'maria-perez', 'maria-perez-2'}
    monkeypatch.setattr(
        model.User, 'get',
        staticmethod(lambda name: object() if name in taken else None))
    assert registration._generate_username('José Núñez') == 'jose-nunez'
    assert registration._generate_username('María! Pérez') == 'maria-perez-3'
    # Fallback to the email local part, then to a generic base.
    assert registration._generate_username('', 'sam@example.org') == 'sam'
    assert registration._generate_username('', '') == 'citizen'


def test_create_citizen_scientist_generates_a_username_when_blank(monkeypatch):
    created = {}
    monkeypatch.setattr(registration, 'check_access', lambda *a, **k: True)
    monkeypatch.setattr(model.User, 'get', staticmethod(lambda name: None))
    monkeypatch.setattr(
        tk, 'get_action',
        lambda name: lambda context, data: created.update(data) or {
            'id': 'user-1', 'name': data['name']})
    out = registration.create_citizen_scientist({}, {
        'email': 'ana@example.org',
        'fullname': 'Ana Flores',
        'password': 'long-enough',
    })
    assert created['name'] == 'ana-flores'
    assert out['name'] == 'ana-flores'


# --------------------------------------------------------------------------- #
# Web form required-ness (view-only strictness; the API action stays lenient) #
# --------------------------------------------------------------------------- #

def test_web_registration_requires_the_demographic_block(app, monkeypatch):
    """fullname, DOB, gender AND nationality (2026 reporting rules: the
    Member-State disaggregation made nationality mandatory too)."""
    monkeypatch.setattr(registration, '_registration_retry_after', lambda: None)
    monkeypatch.setattr(registration, '_recaptcha_configured', lambda: False)
    monkeypatch.setattr(registration, '_render', lambda values: values)
    monkeypatch.setattr(
        registration, 'create_citizen_scientist',
        lambda *a, **k: pytest.fail('must not reach account creation'))

    complete = {
        'email': 'maria@example.org',
        'password': 'long-enough',
        'confirm_password': 'long-enough',
        'terms': 'yes',
        'fullname': 'Maria Example',
        'date_of_birth': '1990-05-17',
        'gender': 'female',
        'nationality': 'PREFER_NOT_TO_SAY',
    }
    for missing in ('fullname', 'date_of_birth', 'gender', 'nationality'):
        data = dict(complete)
        data[missing] = ''
        with app.test_request_context('/register', method='POST', data=data):
            g.user = ''
            out = registration.register_citizen()
        assert out['errors']['message'] == registration.GENERIC_ERROR, missing


# --------------------------------------------------------------------------- #
# Project Manager registration (spec section 3)                               #
# --------------------------------------------------------------------------- #

_MANAGER_FORM = {
    'email': 'pm@example.org',
    'password': 'long-enough',
    'confirm_password': 'long-enough',
    'fullname': 'Paula Manager',
    'date_of_birth': '1985-02-03',
    'gender': 'female',
    'nationality': 'CL',
    'org_type': 'university',
    'org_name': '__new__',
    'new_org_name': 'Hydrology Lab',
    'org_title': 'Research lead',
    'responsibilities': 'yes',
}


def _manager_post(app, monkeypatch, overrides=None):
    captured = {}
    monkeypatch.setattr(registration, '_registration_retry_after', lambda: None)
    monkeypatch.setattr(registration, '_recaptcha_configured', lambda: False)
    monkeypatch.setattr(registration, '_render_manager', lambda values: values)
    monkeypatch.setattr(registration, '_organization_options',
                        lambda: [{'name': 'existing-org',
                                  'title': 'Existing Org'}])
    monkeypatch.setattr(registration.secrets, 'token_urlsafe',
                        lambda size: 'token')
    monkeypatch.setattr(
        registration, '_send_verification_email',
        lambda name, email, token: captured.update(mail=(email, token)))

    def create(context, data, verification_token=None):
        captured.update(created=data, token=verification_token)
        return {'id': 'user-1', 'name': 'paula'}

    monkeypatch.setattr(registration, 'create_citizen_scientist', create)
    form = dict(_MANAGER_FORM)
    form.update(overrides or {})
    with app.test_request_context('/register-pm', method='POST', data=form):
        g.user = ''
        out = registration.register_manager()
    return out, captured


def test_manager_registration_new_org_derives_admin(app, monkeypatch):
    out, captured = _manager_post(app, monkeypatch)
    manager = captured['created']['manager']
    assert manager['org_name_requested'] == 'Hydrology Lab'
    assert manager['org_id'] is None
    assert manager['org_role'] == 'admin'
    assert manager['org_type'] == 'university'
    assert captured['token'] == 'token'
    assert captured['mail'] == ('pm@example.org', 'token')
    assert out['pending_verification'] is True


def test_manager_registration_existing_org_derives_editor(app, monkeypatch):
    out, captured = _manager_post(app, monkeypatch, {
        'org_name': 'existing-org', 'new_org_name': ''})
    manager = captured['created']['manager']
    assert manager['org_id'] == 'existing-org'
    assert manager['org_name_requested'] is None
    assert manager['org_role'] == 'editor'


def test_manager_registration_requires_the_org_block(app, monkeypatch):
    for missing, value in (('org_type', ''), ('org_name', ''),
                           ('org_title', ''), ('responsibilities', ''),
                           ('new_org_name', ''), ('nationality', '')):
        out, captured = _manager_post(app, monkeypatch, {missing: value})
        assert out['errors']['message'] == registration.GENERIC_ERROR, missing
        assert 'created' not in captured, missing


def test_manager_registration_rejects_an_unknown_existing_org(app, monkeypatch):
    out, captured = _manager_post(app, monkeypatch, {
        'org_name': 'forged-org', 'new_org_name': ''})
    assert out['errors']['message'] == registration.GENERIC_ERROR
    assert 'created' not in captured


# --------------------------------------------------------------------------- #
# The manager double gate at /verify                                          #
# --------------------------------------------------------------------------- #

def _verify_profile(profile_type):
    import datetime as _dt
    return type('Profile', (), {
        'user_id': 'user-1',
        'profile_type': profile_type,
        'token_created': _dt.datetime.utcnow(),
    })()


def test_verify_activates_a_citizen_account(monkeypatch):
    from ckanext.csunesco import db as cs_db
    profile = _verify_profile('citizen')
    activated = {'called': False}
    user = type('User', (), {
        'activate': lambda self: activated.update(called=True)})()
    monkeypatch.setattr(cs_db, 'get_citizen_scientist_by_token',
                        lambda token: profile)
    monkeypatch.setattr(cs_db, 'verify_citizen_scientist', lambda p: p)
    monkeypatch.setattr(model.User, 'get', staticmethod(lambda uid: user))
    monkeypatch.setattr(model.Session, 'commit', lambda: None)
    monkeypatch.setattr(registration, '_render_verify', lambda state: state)
    assert registration.verify_citizen('tok') == 'ok'
    assert activated['called'] is True


def test_verify_keeps_a_manager_account_pending(monkeypatch):
    from ckanext.csunesco import db as cs_db
    profile = _verify_profile('manager')
    monkeypatch.setattr(cs_db, 'get_citizen_scientist_by_token',
                        lambda token: profile)
    monkeypatch.setattr(cs_db, 'verify_citizen_scientist', lambda p: p)
    monkeypatch.setattr(
        model.User, 'get',
        staticmethod(lambda uid: pytest.fail('manager must not be activated')))
    monkeypatch.setattr(registration, '_render_verify', lambda state: state)
    assert registration.verify_citizen('tok') == 'manager_pending'
