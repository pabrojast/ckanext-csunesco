"""Wizard checks, affiliation round trips and collaborator identity."""
from types import SimpleNamespace

import pytest
import ckan.model as model
import ckan.plugins.toolkit as tk
from flask import Flask

from ckanext.csunesco import db
from ckanext.csunesco.logic import project_form, validators, views
from ckanext.csunesco.tests.test_project_form_phase1 import actions, session, _ctx, _fake_users, _editor_rows


@pytest.fixture
def form_context(monkeypatch):
    monkeypatch.setattr(validators, '_member_state_names', lambda model: {'chile'})
    monkeypatch.setattr(model.Group, 'get', lambda name: SimpleNamespace(id=name, is_organization=True, state='active'))
    monkeypatch.setattr(project_form.auth, 'can_propose_for_org', lambda *args: True)
    return _ctx()


def test_hidden_and_group_fields_are_checked_before_navigation(form_context):
    _, errors = project_form.validate({'title': 'River', 'short_description': '<p><br></p>',
        'keywords': 'water', 'countries': [], 'point_lat': '0',
        'data_access': 'private'}, form_context)
    assert {'short_description', 'keywords', 'countries', 'water_type', 'water_data_type',
            'point_lng', 'point_radius_km', 'data_access_justification'} <= errors.keys()


def test_external_affiliation_requires_name_and_is_metadata(form_context):
    data, errors = project_form.validate({'title': 'River', 'initiative': '__external__'}, form_context, strict=False)
    assert 'external_initiative_name' in errors
    data, errors = project_form.validate({'title': 'River', 'initiative': '__external__',
        'external_initiative_name': 'Community Rivers'}, form_context, strict=False)
    assert not errors
    assert not data.get('initiative') and data['external_initiative_name'] == 'Community Rivers'


def test_slug_explains_address_ending(form_context):
    _, errors = project_form.validate({'title': 'River', 'slug': 'https://example.org'}, form_context, strict=False)
    assert 'river-monitoring' in errors['slug'][0]
    assert 'https://' in errors['slug'][0]


def test_external_name_roundtrips_without_adding_a_group(actions, session):
    created = actions.csunesco_project_request_create(_ctx(), {
        'title': 'Community project', 'external_initiative_name': 'Community Rivers'})
    assert not created['initiative_group']
    assert created['external_initiative_name'] == 'Community Rivers'
    with pytest.raises(tk.ValidationError):
        actions.csunesco_project_update(_ctx(), {'id': created['id'], 'initiative': 'riverwatch'})
    updated = actions.csunesco_project_update(_ctx(), {'id': created['id'],
        'external_initiative_name': 'Updated community'})
    assert views._project_to_form(updated)['initiative'] == '__external__'
    cleared = actions.csunesco_project_update(_ctx(), {'id': created['id'], 'external_initiative_name': ''})
    assert not cleared.get('external_initiative_name')


def test_owner_is_not_added_as_editor_and_inactive_users_are_rejected(actions, session, monkeypatch):
    _fake_users(monkeypatch, {'owner', 'other'})
    created = actions.csunesco_project_request_create(_ctx('uid-owner'), {
        'title': 'River', 'editors': ['owner', 'other']})
    assert created['editors'] == ['other']
    assert _editor_rows(session, created['id']) == ['uid-other']
    monkeypatch.setattr(model.User, 'get', lambda name: SimpleNamespace(id='inactive', name=name, state='deleted'))
    with pytest.raises(tk.ValidationError):
        actions.csunesco_project_request_create(_ctx(), {'title': 'River', 'editors': ['removed']})


def test_step_endpoint_filters_errors_and_performs_no_creation(form_context, monkeypatch):
    app = Flask(__name__)
    from flask_babel import Babel
    Babel(app)
    monkeypatch.setattr(project_form, 'authorized_context', lambda project_id: (form_context, None))
    with app.test_request_context('/project/validate', method='POST', data={'step': '2', 'title': 'River', 'keywords': 'water'}):
        result = views.project_validate().get_json()
    assert result['valid'] is False and result['step'] == '2'
    assert 'keywords' in result['errors']
    assert 'organization_id' not in result['errors'] and 'short_description' not in result['errors']


def test_validation_endpoint_requires_csrf(monkeypatch):
    from flask_wtf import CSRFProtect
    from ckanext.csunesco import blueprint
    app = Flask(__name__)
    app.secret_key = 'only-for-tests'
    csrf = CSRFProtect(app)
    app.add_url_rule('/project/validate', view_func=blueprint.project_validate, methods=['POST'])
    csrf.exempt(blueprint.project_validate)  # CKAN exempts plugin blueprints globally.
    monkeypatch.setattr(views, 'project_validate', lambda: 'must not be called')
    assert app.test_client().post('/project/validate').status_code == 400
