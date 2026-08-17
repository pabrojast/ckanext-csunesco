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
