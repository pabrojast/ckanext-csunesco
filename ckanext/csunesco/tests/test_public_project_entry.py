"""Public project entry and CS-only contextual login."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Blueprint, Flask
from jinja2 import DictLoader, Environment
import ckan.plugins.toolkit as tk
from ckanext.csunesco.logic import helpers, auth


@pytest.fixture
def app():
    application = Flask(__name__)
    cs = Blueprint('csunesco', __name__)
    cs.add_url_rule('/citizen-science/', 'home', lambda: '')
    cs.add_url_rule('/es/citizen-science/project/river', 'project', lambda: '')
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


def test_proposal_navigation_uses_app_eligibility_without_changing_legacy_auth(monkeypatch):
    monkeypatch.setattr(tk, 'g', SimpleNamespace(user='citizen'))
    monkeypatch.setitem(tk.config, 'ckanext.csunesco.editorial_owner', 'app')
    monkeypatch.setattr(auth, 'can_propose_project', lambda *a: False)
    assert helpers.csunesco_can_propose_project()
    monkeypatch.setitem(tk.config, 'ckanext.csunesco.editorial_owner', 'ckan')
    assert not helpers.csunesco_can_propose_project()
    monkeypatch.setattr(tk, 'g', SimpleNamespace(user=None))
    assert not helpers.csunesco_can_propose_project()


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
            h=SimpleNamespace(csunesco_login_return_url=lambda: destination), _=lambda s: s)
        assert 'ORIGINAL' in html
    html = env.get_template('header.html').render(c=SimpleNamespace(userobj=None),
        h=SimpleNamespace(csunesco_login_return_url=lambda: '/citizen-science/', url_for=url_for), _=lambda s: s)
    assert 'ORIGINAL' not in html
    assert calls == [('user.login', {'came_from': '/citizen-science/'})]
