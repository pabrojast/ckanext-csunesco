"""Public project entry and CS-only contextual login."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Blueprint, Flask
from jinja2 import DictLoader, Environment
import ckan.plugins.toolkit as tk
from ckanext.csunesco.logic import helpers, auth, editorial_owner


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
    monkeypatch.setitem(tk.config, 'ckanext.csunesco.project_intake_owner', 'app')
    assert helpers.csunesco_can_propose_project()
    assert not editorial_owner.enabled()
    monkeypatch.setattr(tk, 'g', SimpleNamespace(user=None))
    assert not helpers.csunesco_can_propose_project()


def test_app_only_intake_redirects_legacy_cta_without_global_editorial_migration(app, monkeypatch):
    from ckanext.csunesco.logic import views
    monkeypatch.setitem(tk.config, 'ckanext.csunesco.editorial_owner', 'ckan')
    monkeypatch.setitem(tk.config, 'ckanext.csunesco.project_intake_owner', 'app')
    monkeypatch.setitem(tk.config, 'ckanext.csunesco.ofform_app_url', 'https://portal.test/cstoolbox/')
    monkeypatch.setattr(tk, 'redirect_to', lambda url: url)
    monkeypatch.setattr(views, '_organization_choices', lambda **kw: pytest.fail('App intake must not require CKAN organization membership'))
    destination = 'https://portal.test/cstoolbox/explorer/start'
    with app.test_request_context('/citizen-science/project/new'):
        assert views.project_new() == destination
    assert editorial_owner.editor_link('csunesco.project_new') == destination
    assert not editorial_owner.enabled()
    monkeypatch.setattr(tk, 'url_for', lambda endpoint, **kw: '/legacy-editor')
    assert editorial_owner.editor_link('csunesco.site_page_edit') == '/legacy-editor'


@pytest.mark.parametrize('method,configured,status', [('POST', True, 405), ('GET', False, 503)])
def test_app_only_intake_fails_closed(app, monkeypatch, method, configured, status):
    from flask import abort
    from werkzeug.exceptions import HTTPException
    from ckanext.csunesco.logic import views
    monkeypatch.setitem(tk.config, 'ckanext.csunesco.project_intake_owner', 'app')
    monkeypatch.setitem(tk.config, 'ckanext.csunesco.ofform_app_url', 'https://portal.test/cstoolbox' if configured else '')
    monkeypatch.setattr(tk, 'abort', abort)
    with app.test_request_context('/citizen-science/project/new', method=method):
        with pytest.raises(HTTPException) as error:
            views.project_new()
        assert error.value.code == status


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
