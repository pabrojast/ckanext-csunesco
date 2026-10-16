"""The two public routes and the identity-bound Toolbox PM intake."""
import datetime
from types import SimpleNamespace
from urllib.parse import urlsplit, parse_qs
import pytest
from flask import Flask, g
import ckan.plugins.toolkit as tk
import ckan.model as model
from ckanext.csunesco import db
from ckanext.csunesco.logic import registration, manager_application as managers, registration_profile as profiles
from ckanext.csunesco.tests.test_registration_completion import identity, BODY
from ckanext.csunesco.tests.test_manager_approval import session, harness


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setitem(tk.config,'ckanext.csunesco.toolbox_registration',True)
    monkeypatch.setitem(tk.config,'ckanext.csunesco.ofform_app_url','https://toolbox.example/cstoolbox')
    monkeypatch.setattr(tk,'_',lambda text:text)
    monkeypatch.setattr(tk,'redirect_to',lambda url,**kwargs:(url,kwargs))
    return Flask(__name__)


def test_citizen_post_never_creates_portal_account(app,monkeypatch):
    monkeypatch.setattr(registration,'create_citizen_scientist',lambda *a,**k:pytest.fail('local signup must leave CKAN'))
    with app.test_request_context('/citizen-science/register-citizen',method='POST',data={'password':'private','project':'river','lng':'fr'}):
        url,_=registration.register_citizen()
        assert url.startswith('https://toolbox.example/cstoolbox/register-citizen?')
        assert parse_qs(urlsplit(url).query)=={'project':['river'],'lng':['fr']}
        assert 'private' not in url


def test_old_pm_route_goes_to_toolbox(app):
    with app.test_request_context('/citizen-science/register/project-manager'):
        assert registration.register_manager()[0]=='https://toolbox.example/cstoolbox/register-project-manager'


def test_dedicated_identity_form_does_not_redirect_to_local_registration(app,monkeypatch):
    monkeypatch.setattr(registration,'_render',lambda values:values)
    with app.test_request_context('/citizen-science/register/ihp-wins'):
        g.user=None
        assert 'errors' in registration.register_ihpwins()


def test_verified_pm_continuation_uses_stored_path_not_query(app,monkeypatch):
    monkeypatch.setattr(tk,'render',lambda template,extra_vars:extra_vars)
    with app.test_request_context('/citizen-science/verify/used', environ_overrides={'CKAN_LANG':'es'}):
        values=registration._render_verify('ok',next_path='/register-project-manager')
        assert urlsplit(values['toolbox_login_url']).path == '/cstoolbox/register-project-manager'
        assert parse_qs(urlsplit(values['toolbox_login_url']).query)=={'resume':['ihpwins'],'lng':['es']}


@pytest.mark.parametrize('language', ['en', 'es', 'fr', 'pt', 'uk', 'ar', 'quh'])
def test_existing_portal_session_has_explicit_handoff(app, monkeypatch, language):
    monkeypatch.setattr(tk, 'render', lambda template, extra_vars: (template, extra_vars))
    # Portuguese, Ukrainian and Qhichwa share the supported English URL locale.
    with app.test_request_context('/citizen-science/register/ihp-wins?lng=' + language, environ_overrides={'CKAN_LANG': 'en'}):
        g.user = 'existing-citizen'
        template, values = registration.register_ihpwins()
        assert template == 'csunesco/identity_handoff.html'
        assert values['account_name'] == 'existing-citizen'
        assert '/login' not in values['continue_url']
        assert parse_qs(urlsplit(values['continue_url']).query) == {'resume': ['ihpwins'], 'lng': [language]}
        assert values['copy']['signed_in']


@pytest.mark.parametrize('language', ['pt', 'uk', 'quh'])
def test_verification_continuation_keeps_profile_language(app, monkeypatch, language):
    monkeypatch.setattr(tk, 'render', lambda template, extra_vars: extra_vars)
    with app.test_request_context('/citizen-science/verify/used', environ_overrides={'CKAN_LANG': 'en'}):
        values = registration._render_verify('ok', next_path='/register-project-manager', language=language)
        assert parse_qs(urlsplit(values['toolbox_login_url']).query)['lng'] == [language]


def test_manager_submission_is_self_service_and_idempotent(session,identity,monkeypatch):
    user,ctx=identity
    profiles.csunesco_registration_profile_update(ctx,BODY)
    monkeypatch.setattr(registration,'_organization_options',lambda:[{'name':'org','title':'Organization'}])
    data={'org_type':'ngo','org_name':'org','org_title':'Coordinator','org_role':'member','responsibilities':True}
    result=managers.csunesco_manager_application_submit(ctx,data)
    assert result['profile']['profile_type']=='manager'
    profile=db.get_citizen_scientist(user.id);stamp=profile.responsibilities_accepted_at
    managers.csunesco_manager_application_submit(ctx,{**data,'org_name':'different'})
    assert profile.org_id=='org' and profile.responsibilities_accepted_at==stamp
    assert profile.manager_decision is None and profile.email_verified


@pytest.mark.parametrize('extra',[{'responsibilities':False},{'org_type':'invented'},{'org_name':'missing'},{'org_role':'sysadmin'}])
def test_manager_rejects_invalid_fields_without_promoting(session,identity,monkeypatch,extra):
    user,ctx=identity
    profiles.csunesco_registration_profile_update(ctx,BODY)
    monkeypatch.setattr(registration,'_organization_options',lambda:[{'name':'org','title':'Organization'}])
    with pytest.raises(tk.ValidationError):
        managers.csunesco_manager_application_submit(ctx,{'org_type':'ngo','org_name':'org','org_title':'Coordinator','org_role':'member','responsibilities':True,**extra})
    assert db.get_citizen_scientist(user.id).profile_type=='citizen'


def test_manager_cannot_spoof_other_identity(session,identity):
    user,ctx=identity
    with pytest.raises(tk.NotAuthorized):
        managers.csunesco_manager_application_submit(ctx,{'actor_username':'another','actor_id':'another-id'})
