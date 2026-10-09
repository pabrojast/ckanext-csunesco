"""Registration entry preserves intent and only offers permitted next steps."""
import re
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode, urlsplit, parse_qs

import pytest
from flask import g
from jinja2 import DictLoader, Environment
import ckan.model as model
import ckan.plugins.toolkit as tk
from ckanext.csunesco import db
from ckanext.csunesco.logic import registration, registration_profile
from ckanext.csunesco.tests.test_registration import app


def url_for(endpoint, **params):
    path = '/citizen-science/' + endpoint.split('.')[-1].replace('_', '-')
    return path + ('?' + urlencode(params) if params else '')


@pytest.fixture
def entry(monkeypatch):
    monkeypatch.setattr(tk, 'url_for', url_for)
    monkeypatch.setattr(tk, 'render', lambda template, extra_vars: extra_vars)
    monkeypatch.setattr(registration, '_registration_projects', lambda: [
        {'id': 'project-id', 'slug': 'river-watch', 'title': 'River watch'}])


def test_anonymous_common_entry_keeps_invitation_and_pm_intent(app, entry):
    with app.test_request_context('/get-on-board?role=manager&project=river-watch'):
        g.user = ''
        out = registration.get_on_board()
    query = parse_qs(urlsplit(out['registration_url']).query)
    assert out['registration_stage'] == 'register'
    assert query == {'next': [url_for('csunesco.register_manager')], 'project': ['river-watch']}
    resume = parse_qs(urlsplit(out['login_url']).query)['came_from'][0]
    assert parse_qs(urlsplit(resume).query) == query
    resume_completion = parse_qs(urlsplit(out['completion_url']).query)['next'][0]
    assert parse_qs(urlsplit(resume_completion).query) == query
    assert out['join_url'].endswith('?slug=river-watch')


def test_untrusted_destination_is_not_carried_into_registration(app, entry):
    with app.test_request_context('/get-on-board?next=https://example.org'):
        g.user = ''
        out = registration.get_on_board()
    assert 'next=' not in out['registration_url']


@pytest.mark.parametrize('complete,verified,stage', [
    (False, False, 'complete'), (False, True, 'complete'),
    (True, False, 'verify'), (True, True, 'ready'),
])
def test_existing_identity_resumes_registration(app, entry, monkeypatch, complete, verified, stage):
    user = SimpleNamespace(id='existing-id', name='existing')
    monkeypatch.setattr(model.User, 'get', lambda name: user)
    monkeypatch.setattr(db, 'get_citizen_scientist', lambda uid: SimpleNamespace(email_verified=verified))
    monkeypatch.setattr(registration_profile, 'completeness', lambda *args: {'profile_complete': complete})
    with app.test_request_context('/get-on-board'):
        g.user = user.name
        out = registration.get_on_board()
    assert out['registration_stage'] == stage
    assert out['logged_in']


@pytest.mark.parametrize('stage,can_propose', [
    ('register', False), ('complete', False), ('verify', False), ('ready', False), ('ready', True),
])
def test_rendered_entry_has_one_signup_and_state_appropriate_actions(stage, can_propose):
    path = Path(__file__).parents[1] / 'templates/csunesco/get_on_board.html'
    source = re.sub(r"{% asset .*?%}", '', path.read_text())
    env = Environment(autoescape=True, loader=DictLoader({
        'entry': source, 'csunesco/base.html': '{% block cs_content %}{% endblock %}',
    }))
    h = SimpleNamespace(url_for=url_for, csunesco_icon=lambda *a: '',
                        csunesco_can_propose_project=lambda: can_propose,
                        csunesco_editor_link=lambda endpoint: '/proposal')
    html = env.get_template('entry').render(_=lambda s: s, h=h,
        registration_stage=stage, registration_url='/register',
        completion_url='/complete', login_url='/login', join_url='/citizen-science/project-list')
    assert html.count('href="/register"') == (1 if stage == 'register' else 0)
    assert ('href="/complete"' in html) == (stage == 'complete')
    assert ('href="/citizen-science/resend-verification"' in html) == (stage == 'verify')
    assert ('href="/citizen-science/project-list"' in html) == (stage == 'ready')
    assert ('href="/proposal"' in html) == (stage == 'ready' and can_propose)
    assert ('href="/citizen-science/register-manager"' in html) == (stage == 'ready' and not can_propose)
