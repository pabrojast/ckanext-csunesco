"""Public project entry and CS-only contextual login."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Blueprint, Flask, session, url_for
from flask_babel import Babel
from jinja2 import DictLoader, Environment
import ckan.plugins.toolkit as tk
from ckanext.csunesco import db
from ckanext.csunesco.logic import helpers, auth, editorial_owner, views, portal, registration, validators
from ckanext.csunesco.logic.action import projects
from ckanext.csunesco.tests.test_project_portal import store


@pytest.fixture
def app():
    application = Flask(__name__)
    application.secret_key = 'registration-test-session'
    Babel(application)
    cs = Blueprint('csunesco', __name__)
    cs.add_url_rule('/citizen-science/', 'home', lambda: '')
    cs.add_url_rule('/es/citizen-science/project/river', 'project', lambda: '')
    cs.add_url_rule('/citizen-science/project/new', 'project_new', views.project_new, methods=['GET', 'POST'])
    cs.add_url_rule('/citizen-science/project/<slug>', 'project_landing', lambda slug: '')
    cs.add_url_rule('/citizen-science/project/<slug>/edit', 'project_edit', lambda slug: '')
    application.register_blueprint(cs)
    application.add_url_rule('/citizen-science-portal', 'portal', lambda: '')
    application.add_url_rule('/dataset', 'dataset', lambda: '')
    return application


@pytest.mark.parametrize('path,expected', [
    ('/citizen-science/', '/citizen-science/'),
    ('/es/citizen-science/project/river?tab=data', '/es/citizen-science/project/river?tab=data'),
    ('/citizen-science/?next=https://outside.test', '/citizen-science/?next=https://outside.test'),
    ('/citizen-science-portal', '/citizen-science-portal'),
    ('/dataset', None), ('/user/login', None),
])
def test_login_return_is_contextual_local_path(app, path, expected):
    with app.test_request_context(path):
        assert helpers.csunesco_login_return_url() == expected
    assert helpers.csunesco_login_return_url() is None


@pytest.fixture(params=[('ckan', ''), ('ckan', 'app'), ('app', 'app')])
def ownership(request, monkeypatch):
    editorial, intake = request.param
    monkeypatch.setitem(tk.config, 'ckanext.csunesco.editorial_owner', editorial)
    monkeypatch.setitem(tk.config, 'ckanext.csunesco.project_intake_owner', intake)
    monkeypatch.setitem(tk.config, 'ckanext.csunesco.ofform_app_url', 'https://app.example/cstoolbox')


@pytest.fixture
def registration_form(store, monkeypatch):
    # Use the real view, form parsing, schema, action, authorization and DB.
    # Isolate CKAN identity lookups and the remote Toolbox transport only.
    from ckan import authz, model
    _, users = store
    org = model.Group(name='test-organization', title='Test organization', is_organization=True)
    db.Session.add(org)
    db.Session.commit()
    monkeypatch.setattr(model.Group, 'get', lambda key: org if key in (org.id, org.name) else None)
    monkeypatch.setattr(authz, 'has_user_permission_for_group_or_org',
                        lambda org_id, name, permission: name == 'author' and org_id == org.id)
    monkeypatch.setattr(registration, '_organization_options',
                        lambda: [{'name': org.name, 'title': org.title}])
    monkeypatch.setattr(views, '_member_state_choices', lambda: ([], True))
    monkeypatch.setattr(validators, '_member_state_names', lambda model: {'chile'})
    monkeypatch.setattr(tk, '_', lambda text: text)
    monkeypatch.setattr(tk, 'g', SimpleNamespace(user='author'))
    monkeypatch.setattr(tk, 'url_for', url_for)
    monkeypatch.setattr(tk, 'redirect_to', lambda endpoint, **kw: ('redirect', url_for(endpoint, **kw)))
    monkeypatch.setattr(tk, 'render', lambda template, extra_vars: dict(extra_vars, template=template))
    monkeypatch.setattr(tk, 'h', SimpleNamespace(flash_success=lambda *a: None, flash_notice=lambda *a: None))
    monkeypatch.setattr(tk, 'get_action', lambda name: projects.csunesco_project_request_create)
    def check_access(name, context, data):
        assert name == 'csunesco_project_request_create'
        if not auth.csunesco_project_request_create(context, data)['success']:
            raise tk.NotAuthorized('Organization editor required')
    monkeypatch.setattr(tk, 'check_access', check_access)
    monkeypatch.setattr(views, '_resolve_cover', lambda *a: (SimpleNamespace(
        _written=[], urls={}, rollback=lambda: None), {}))
    monkeypatch.setattr(views, '_apply_image_urls', lambda *a: None)
    synced = []
    monkeypatch.setattr(portal, 'send_initial_request', lambda row: synced.append(row.id))
    return org, users, synced


@pytest.mark.parametrize('user,allowed', [(None, False), ('outsider', False), ('author', True), ('reviewer', True)])
def test_ckan_roles_control_navigation_and_form(app, registration_form, ownership, monkeypatch, user, allowed):
    monkeypatch.setattr(tk, 'g', SimpleNamespace(user=user))
    with app.test_request_context('/citizen-science/project/new'):
        assert helpers.csunesco_can_propose_project() is allowed
        result = views.project_new()
        assert result['template'] == ('csunesco/project_request.html' if allowed else 'csunesco/project_eligibility.html')
        if allowed:
            assert [step['key'] for step in result['steps']] == [
                'identity', 'classification', 'location', 'participation',
                'dataAccess', 'leadership', 'funding', 'brand']
        else:
            assert result['logged_in'] is bool(user)


@pytest.mark.parametrize('configured', [False, True])
def test_new_project_link_is_local(app, ownership, monkeypatch, configured):
    if not configured:
        monkeypatch.setitem(tk.config, 'ckanext.csunesco.ofform_app_url', '')
    monkeypatch.setattr(tk, 'url_for', url_for)
    with app.test_request_context('/'):
        assert editorial_owner.editor_link('csunesco.project_new') == '/citizen-science/project/new'
        assert editorial_owner.editor_link('csunesco.project_new', submitted=1).endswith('?submitted=1')


def _valid_proposal(org):
    return {'title': 'A CKAN proposal', 'organization_id': org.id,
            'short_description': 'Water monitoring', 'keywords': 'water, river',
            'water_type': 'River', 'water_data_type': 'Water quantity',
            'geographic_extent': 'Global', 'countries_present': '1', 'countries': 'chile',
            'participation_mode': 'open', 'data_access': 'public',
            'activity_status': 'Active', 'lead_partner_type': 'University',
            'lead_organisation': org.name, 'request_nonce': 'test-once'}


def test_submission_confirmation_and_retry_stay_in_ckan(app, registration_form, ownership):
    org, _, synced = registration_form
    with app.test_request_context('/citizen-science/project/new', method='POST', data=_valid_proposal(org)):
        result = views.project_new()
        assert result == ('redirect', '/citizen-science/project/new?submitted=1')
        saved_nonce = session['cs_project_request_saved_nonce']
    project = db.Session.query(db.CsProject).filter_by(title='A CKAN proposal').one()
    assert project.status == 'pending'
    assert project.created_by == 'author'
    assert synced == [project.id]
    with app.test_request_context('/citizen-science/project/new', method='POST', data=_valid_proposal(org)):
        session['cs_project_request_saved_nonce'] = saved_nonce
        assert views.project_new() == result
    assert db.Session.query(db.CsProject).filter_by(title='A CKAN proposal').count() == 1
    with app.test_request_context('/citizen-science/project/new?submitted=1'):
        assert views.project_new()['success'] is True


def test_invalid_submission_preserves_input(app, registration_form, ownership):
    org, _, synced = registration_form
    data = _valid_proposal(org)
    data['title'] = ''
    with app.test_request_context('/citizen-science/project/new', method='POST', data=data):
        result = views.project_new()
        assert 'title' in result['errors']
        assert result['data']['short_description'] == data['short_description']
    assert not synced
    assert db.Session.query(db.CsProject).count() == 1  # only the fixture project


def test_draft_is_saved_and_app_outage_does_not_lose_proposal(app, registration_form, ownership, monkeypatch):
    org, _, _ = registration_form
    def unavailable(row):
        raise RuntimeError('App unavailable')
    monkeypatch.setattr(portal, 'send_initial_request', unavailable)
    for draft in (False, True):
        data = {'title': 'Draft', 'organization_id': org.id, 'save_draft': '1'} if draft else _valid_proposal(org)
        with app.test_request_context('/citizen-science/project/new', method='POST', data=data):
            assert views.project_new()[0] == 'redirect'
        project = db.Session.query(db.CsProject).filter_by(title=data['title']).one()
        assert project.status == ('draft' if draft else 'pending')
        assert db._load_json(project.extras, {})['_portal_intake']['pending'] is True


@pytest.mark.parametrize('user,organization', [('outsider', 'own'), ('author', 'other'), (None, 'own')])
def test_action_rejects_unprivileged_or_wrong_organization(registration_form, ownership, user, organization):
    org, _, _ = registration_form
    with pytest.raises(tk.NotAuthorized):
        projects.csunesco_project_request_create({'user': user}, {
            'title': 'Forbidden', 'organization_id': org.id if organization == 'own' else 'other-org'})


@pytest.mark.parametrize('user', ['author', 'reviewer'])
def test_humans_cannot_fetch_private_app_assets(registration_form, ownership, user):
    org, _, _ = registration_form
    with pytest.raises(tk.NotAuthorized):
        projects.csunesco_project_request_create({'user': user}, {
            'title': 'Forbidden asset', 'organization_id': org.id, 'logo_url': 'asset:19'})


def test_header_keeps_other_pages_and_logged_in_users_unchanged():
    source = (Path(__file__).parents[1] / 'templates/header.html').read_text()
    source = source.replace('{% ckan_extends %}', '{% extends "parent.html" %}')
    env = Environment(autoescape=True, loader=DictLoader({
        'header.html': source,
        'parent.html': '{% block header_account_notlogged %}ORIGINAL{% endblock %}',
    }))
    calls = []
    def url_for(endpoint, **kwargs):
        calls.append((endpoint, kwargs))
        return '/user/login?came_from=/citizen-science/'
    for user, destination in [(None, None), (object(), '/citizen-science/')]:
        html = env.get_template('header.html').render(c=SimpleNamespace(userobj=user),
            h=SimpleNamespace(csunesco_login_url=lambda: destination), _=lambda s: s)
        assert 'ORIGINAL' in html
    html = env.get_template('header.html').render(c=SimpleNamespace(userobj=None),
        h=SimpleNamespace(csunesco_login_url=lambda: '/user/login?came_from=/citizen-science/', url_for=url_for), _=lambda s: s)
    assert 'ORIGINAL' not in html
    assert '/user/login?came_from=/citizen-science/' in html
    assert calls == []


@pytest.mark.parametrize('path,next_path', [
    ('/citizen-science/', '/projects'),
    ('/citizen-science-portal', '/projects'),
    ('/citizen-science/project/river', '/explorer/projects/river'),
    ('/citizen-science/?next=https://outside.test', '/projects'),
])
def test_app_login_is_scoped_and_retains_project_context(app, monkeypatch, path, next_path):
    from urllib.parse import urlsplit, parse_qs
    monkeypatch.setitem(tk.config, 'ckanext.csunesco.ofform_app_url', 'https://app.example/cstoolbox')
    with app.test_request_context(path):
        target = urlsplit(helpers.csunesco_login_url())
        assert target.netloc == 'app.example' and target.path == '/cstoolbox/login'
        assert parse_qs(target.query) == {'next': [next_path]}


@pytest.mark.parametrize('path', ['/citizen-science/project/new', '/citizen-science/project/river/edit'])
def test_ckan_editor_login_stays_in_ckan(app, monkeypatch, path):
    monkeypatch.setitem(tk.config, 'ckanext.csunesco.ofform_app_url', 'https://app.example')
    monkeypatch.setattr(tk, 'url_for', lambda endpoint, **kw: (endpoint, kw))
    with app.test_request_context(path):
        assert helpers.csunesco_login_url() == ('user.login', {'came_from': path})
    with app.test_request_context('/dataset'):
        assert helpers.csunesco_login_url() is None


def test_approval_event_is_durable_private_and_retains_concurrent_events(store, monkeypatch):
    from ckanext.csunesco.logic import approval_events, snapshots
    project, users = store
    monkeypatch.setitem(tk.config, 'ckanext.csunesco.ofform_callback_token', 'test-token')
    users['author'].email = 'author@example.org'
    approval_events.record(project)
    db.Session.commit()
    event = db._load_json(project.extras, {})[approval_events.KEY][0]
    assert event['actor_email'] == 'author@example.org'
    assert approval_events.KEY not in db.project_dictize(project)
    def unavailable(*args):
        raise RuntimeError('transport unavailable')
    monkeypatch.setattr(snapshots, 'app_request', unavailable)
    approval_events.flush(project)
    assert db._load_json(project.extras, {})[approval_events.KEY] == [event]
    def receive(path, payload):
        assert path == '/internal/ckan/approval-events' and payload == event
        approval_events.record(project, 'join_request', 'outsider')
        db.Session.commit()
    monkeypatch.setattr(snapshots, 'app_request', receive)
    approval_events.flush(project)
    remaining = db._load_json(project.extras, {})[approval_events.KEY]
    assert len(remaining) == 1 and remaining[0]['actor_username'] == 'outsider'


def test_registration_errors_never_expose_unknown_backend_details(monkeypatch):
    from ckanext.csunesco.logic.registration_errors import from_validation
    monkeypatch.setattr(tk, '_', lambda message: message)
    result = from_validation(tk.ValidationError({'database': 'private credentials'}))
    assert result['code'] == 'registration_service_unavailable'
    assert 'private credentials' not in str(result)
    assert from_validation(tk.ValidationError({'name': ['already exists']}))['code'] == 'registration_username_taken'


@pytest.mark.parametrize('email,password,state,success', [
    ('author@example.org', 'original-password', 'active', True),
    ('other@example.org', 'original-password', 'active', False),
    ('author@example.org', 'another-password', 'active', False),
    ('author@example.org', 'original-password', 'pending', False),
])
def test_registration_retry_requires_original_active_identity(store, monkeypatch, email, password, state, success):
    from ckanext.csunesco.logic.action import registration as action
    project, users = store
    user = users['author']
    user.email = 'author@example.org'
    user.state = state
    user.validate_password = lambda value: value == 'original-password'
    profile = db.CsCitizenScientist()
    profile.user_id = user.id
    db.Session.add(profile)
    db.Session.commit()
    monkeypatch.setattr(tk, '_', lambda value: value)
    body = {'username':'author', 'email':email, 'password':password}
    if success:
        assert action.csunesco_register_citizen_scientist({}, body)['existed'] is True
    else:
        with pytest.raises(tk.ValidationError) as error:
            action.csunesco_register_citizen_scientist({}, body)
        assert error.value.error_dict['code'] == 'registration_username_taken'
