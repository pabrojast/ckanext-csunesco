"""Existing identities request PM access without a second account or lost access."""
import datetime
from types import SimpleNamespace

import pytest
from flask import g
import ckan.model as model
import ckan.plugins.toolkit as tk
from ckanext.csunesco import db
from ckanext.csunesco.logic import auth, registration
from ckanext.csunesco.logic.action import registration as decisions
from ckanext.csunesco.tests.test_registration import app, _MANAGER_FORM
from ckanext.csunesco.tests.test_manager_approval import session, harness, _ctx


@pytest.fixture
def existing(harness, monkeypatch):
    user, calls = harness
    user.state = 'active'
    user.password = 'existing-password-hash'
    monkeypatch.setattr(registration, '_lock_manager_account', lambda user_id: user)
    monkeypatch.setattr(auth, '_user_obj', lambda ctx: user if ctx.get('user') == user.name else None)
    monkeypatch.setattr(auth, 'can_propose_project', lambda ctx: False)
    monkeypatch.setattr(registration, '_render_manager', lambda values: values)
    monkeypatch.setattr(registration, '_registration_retry_after', lambda: None)
    monkeypatch.setattr(registration, '_organization_options', lambda: [
        {'name': 'existing-org', 'title': 'Existing organization'}])
    monkeypatch.setattr(tk, 'redirect_to', lambda endpoint, **kwargs: ('redirect', endpoint, kwargs))
    monkeypatch.setattr(tk, 'url_for', lambda endpoint, **kwargs: '/register-pm')
    monkeypatch.setattr(registration, '_recaptcha_configured', lambda: True)

    def unexpected(*args, **kwargs):
        pytest.fail('Existing accounts must not create users, verify email or use registration CAPTCHA')

    monkeypatch.setattr(registration, 'create_citizen_scientist', unexpected)
    monkeypatch.setattr(registration, '_send_verification_email', unexpected)
    monkeypatch.setattr(registration, '_verify_recaptcha', unexpected)
    profile = db.get_or_create_citizen_scientist(user.id,
        date_of_birth=datetime.date(1985, 2, 3), nationality='CL', gender='female',
        language='fr', motivation='Protect local rivers together.', terms_accepted=True)
    profile.email_verified = True
    model.Session.commit()
    return user, calls


def post(app, user, **overrides):
    form = dict(_MANAGER_FORM, org_name='existing-org', new_org_name='',
                existing_account='1', password='', confirm_password='')
    form.update(overrides)
    with app.test_request_context('/register-pm', method='POST', data=form):
        g.user = user.name if user else ''
        return registration.register_manager()


def test_request_reuses_identity_and_preserves_citizen_data(app, existing, session):
    has_profile = True
    user, calls = existing
    if has_profile:
        profile = db.get_or_create_citizen_scientist(user.id,
            country='Chile', registration_project_slug='river', terms_accepted=True)
        profile.country = 'Chile'
        profile.registration_project_slug = 'river'
        session.commit()
        original_id, accepted_at = profile.id, profile.terms_accepted_at
    out = post(app, user, username='someone-else', email='other@example.org', fullname='Forged name', password='overwrite-attempt')
    assert out[:2] == ('redirect', 'csunesco.register_manager')
    profile = db.get_citizen_scientist(user.id)
    assert session.query(db.CsCitizenScientist).count() == 1
    assert profile.manager_application_origin == 'existing_account'
    assert profile.profile_type == 'manager' and profile.manager_decision is None
    assert profile.email_verified and not profile.verification_token
    assert profile.org_id == 'existing-org' and profile.org_role == 'member'
    assert (user.name, user.email, user.fullname, user.password, user.state) == (
        'paula', 'pm@example.org', 'Paula Manager', 'existing-password-hash', 'active')
    assert not calls
    if has_profile:
        assert (profile.id, profile.country, profile.registration_project_slug,
                profile.terms_accepted_at) == (original_id, 'Chile', 'river', accepted_at)


def test_existing_data_prefilled_and_no_password_required(app, existing, session):
    user, _ = existing
    db.get_or_create_citizen_scientist(user.id, nationality='CL',
        date_of_birth=datetime.date(1985, 2, 3), gender='female', motivation='Protect local rivers together.')
    with app.test_request_context('/register-pm'):
        g.user = user.name
        out = registration.register_manager()
    assert out['existing_account'] is True and out['recaptcha_publickey'] == ''
    assert out['data']['date_of_birth'] == '1985-02-03'
    assert out['data']['email'] == user.email
    assert out['data']['motivation'] == 'Protect local rivers together.'


def test_pending_duplicate_and_rejection_keep_account_active(app, existing, session):
    user, _ = existing
    post(app, user)
    profile = db.get_citizen_scientist(user.id)
    stamp = profile.responsibilities_accepted_at
    assert post(app, user)['application_status'] == 'pending'
    assert profile.responsibilities_accepted_at == stamp
    decisions.csunesco_manager_reject(_ctx(), {'username': user.name, 'reason': 'Confirm affiliation'})
    out = post(app, user)
    assert out['application_status'] == 'rejected' and out['review_reason'] == 'Confirm affiliation'
    assert user.state == 'active' and not user.activated
    assert session.query(db.CsCitizenScientist).count() == 1


def test_approval_does_not_reactivate_existing_account(app, existing, session):
    user, calls = existing
    post(app, user)
    result = decisions.csunesco_manager_approve(_ctx(), {'username': user.name})
    assert result['manager_decision'] == 'approved' and not user.activated
    assert dict(calls)['organization_member_create']['role'] == 'member'
    before = list(calls)
    assert decisions.csunesco_manager_approve(_ctx(), {'username': user.name})['existed']
    assert calls == before


def test_disabled_after_application_cannot_be_reactivated_by_pm_approval(app, existing, session):
    user, calls = existing
    post(app, user)
    user.state = 'deleted'
    with pytest.raises(tk.ValidationError):
        decisions.csunesco_manager_approve(_ctx(), {'username': user.name})
    assert not calls and not user.activated
    assert db.get_citizen_scientist(user.id).manager_decision is None


def test_eligible_user_goes_directly_to_project_form(app, existing, monkeypatch):
    from ckanext.csunesco.logic import registration_profile
    monkeypatch.setattr(registration_profile, 'completeness', lambda *args: {'profile_complete': True})
    user, _ = existing
    monkeypatch.setattr(auth, 'can_propose_project', lambda ctx: True)
    assert post(app, user)[:2] == ('redirect', 'csunesco.project_new')
    assert db.get_citizen_scientist(user.id).profile_type == 'citizen'


def test_expired_session_never_falls_through_to_new_registration(app, existing):
    assert post(app, None)[:2] == ('redirect', 'user.login')
    from ckanext.csunesco import blueprint
    with app.test_request_context('/register-pm', method='POST', data={'existing_account': '1'}):
        g.user = ''
        assert blueprint.register_manager()[:2] == ('redirect', 'user.login')


def test_invalid_organization_keeps_existing_identity_and_selection(app, existing):
    user, _ = existing
    out = post(app, user, org_name='forged-org')
    assert out['errors']['code'] == 'registration_organization_invalid'
    assert out['data']['org_name'] == 'forged-org'
    assert out['existing_account'] and db.get_citizen_scientist(user.id).profile_type == 'citizen'


def test_disabled_during_submission_creates_no_profile(existing, monkeypatch):
    user, _ = existing
    user.state = 'deleted'
    with pytest.raises(tk.NotAuthorized):
        registration._request_manager_access(user.id, {}, {})
    assert db.get_citizen_scientist(user.id).profile_type == 'citizen'


def test_concurrent_duplicate_does_not_change_reviewed_application(app, existing, session):
    user, _ = existing
    post(app, user)
    assert not registration._request_manager_access(user.id, {}, {'org_id': 'other'})
    assert db.get_citizen_scientist(user.id).org_id == 'existing-org'


def test_existing_account_template_has_no_credentials_or_signup_call_to_action():
    import re
    from pathlib import Path
    from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader
    templates = Path(__file__).parents[1] / 'templates'
    source = (templates / 'csunesco/register_manager.html').read_text()
    source = re.sub(r"{%\s*(?:asset|snippet)\b.*?%}", '', source, flags=re.S)
    env = Environment(loader=ChoiceLoader([DictLoader({
        'csunesco/base.html': '{% block cs_content %}{% endblock %}',
        'form.html': source,
    }), FileSystemLoader(str(templates))]), autoescape=True)
    helpers = SimpleNamespace(url_for=lambda *a, **kw: '/route',
        url_for_static=lambda p: p, csunesco_icon=lambda *a: '', csrf_input=lambda: '',
        csunesco_editor_link=lambda *a: '/new', csunesco_login_url=lambda: '/login')
    values = dict(_=lambda text: text, h=helpers, existing_account=True,
        account=SimpleNamespace(name='paula', email='pm@example.org', fullname='Paula'),
        data={'fullname':'Paula'}, errors={}, country_options=[], org_types=[], organizations=[])
    html = env.get_template('form.html').render(**values)
    assert 'Submit PM request' in html and 'name="existing_account"' in html
    assert not any('name="' + key + '"' in html for key in ('email','username','password','confirm_password'))
    assert 'Create account' not in html and 'Already have an account?' not in html
    pending = env.get_template('form.html').render(**dict(values, application_status='pending'))
    assert 'awaiting review' in pending and '<form' not in pending
    rejected = env.get_template('form.html').render(**dict(values, application_status='rejected', review_reason='<script>bad</script>'))
    assert '&lt;script&gt;' in rejected and '<script>bad' not in rejected


def test_manager_route_enforces_csrf_even_when_extensions_are_exempt(monkeypatch):
    from flask import Flask
    from flask_wtf.csrf import CSRFProtect, generate_csrf
    from ckanext.csunesco import blueprint
    web = Flask(__name__)
    web.config.update(SECRET_KEY='test-only', WTF_CSRF_FIELD_NAME='_csrf_token')
    csrf = CSRFProtect(web)
    csrf.exempt(blueprint.register_manager)
    web.add_url_rule('/manager', view_func=blueprint.register_manager, methods=['GET', 'POST'])
    web.add_url_rule('/token', view_func=lambda: generate_csrf())
    @web.before_request
    def signed_in():
        g.user = 'existing-user'
    monkeypatch.setattr(registration, 'register_manager', lambda: 'accepted')
    client = web.test_client()
    token = client.get('/token', base_url='https://example.test').get_data(as_text=True)
    assert client.get('/manager').status_code == 200
    for data in ({}, {'_csrf_token': 'invalid'}, {'existing_account': '1'}):
        assert client.post('/manager', data=data).status_code == 400
    # A token belongs to its browser session and requires a same-origin referrer.
    assert web.test_client().post('/manager', data={'_csrf_token': token}).status_code == 400
    assert client.post('/manager', base_url='https://example.test',
        data={'_csrf_token': token}, headers={'Referer': 'https://other.test/'}).status_code == 400
    accepted = client.post('/manager', base_url='https://example.test',
        data={'_csrf_token': token}, headers={'Referer': 'https://example.test/manager'})
    assert accepted.status_code == 200 and accepted.get_data(as_text=True) == 'accepted'


def test_incomplete_account_must_register_before_pm(app, existing, session):
    user, _ = existing
    profile = db.get_citizen_scientist(user.id)
    profile.terms_accepted_at = None
    session.commit()
    assert post(app, user)[:2] == ('redirect', 'csunesco.complete_profile')
    with pytest.raises(tk.ValidationError):
        registration._request_manager_access(user.id, {}, {'org_id': 'existing-org'})
    assert profile.profile_type == 'citizen' and profile.org_id is None


def test_pm_application_does_not_overwrite_citizen_fields(app, existing):
    user, _ = existing
    profile = db.get_citizen_scientist(user.id)
    before = (profile.motivation, profile.language, profile.date_of_birth, profile.terms_accepted_at)
    post(app, user, motivation='Forged replacement', language='ar', date_of_birth='2000-01-01')
    assert before == (profile.motivation, profile.language, profile.date_of_birth, profile.terms_accepted_at)


def test_pm_entry_requires_registration_and_verification(app, existing, monkeypatch):
    user, _ = existing
    with app.test_request_context('/register-pm'):
        g.user = ''
        assert registration.register_manager()[:2] == ('redirect', 'csunesco.register_citizen')
    profile = db.get_citizen_scientist(user.id)
    profile.email_verified = False
    assert post(app, user)[:2] == ('redirect', 'csunesco.resend_verification')
    assert profile.profile_type == 'citizen'
