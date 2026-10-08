"""Lost responses must not create another project or partial receipt."""
import pytest
import sqlalchemy as sa
import ckan.plugins.toolkit as tk
from ckanext.csunesco import db
from ckanext.csunesco.logic import deliveries, portal
from ckanext.csunesco.logic.action import projects
from ckanext.csunesco.tests.test_db_behavior import session  # noqa: F401


@pytest.fixture
def bridge(monkeypatch):
    monkeypatch.setattr(portal, 'require_service', lambda context: None)
    monkeypatch.setattr(projects.auth, '_is_sysadmin', lambda context: True)
    return {'user': 'bridge'}


def test_project_response_loss_returns_existing_creation(session, bridge, monkeypatch):
    monkeypatch.setattr(tk, 'check_access', lambda *a: None)
    monkeypatch.setattr(tk, 'navl_validate', lambda incoming, *a: (incoming, {}))
    monkeypatch.setattr(projects, '_resolve_organization', lambda data: None)
    monkeypatch.setattr(projects, '_resolve_lead_organisation', lambda data: None)
    monkeypatch.setattr(projects, '_resolve_creator', lambda *a: ('creator', None))
    from ckanext.csunesco.logic import approval_events
    monkeypatch.setattr(approval_events, 'record', lambda *a: None)
    payload = {'title': 'Retry project', 'slug': 'retry-project', 'delivery_id': 'a'*64}
    first = projects.csunesco_project_request_create(bridge, payload)
    second = projects.csunesco_project_request_create(bridge, payload)
    assert first == second
    assert session.query(db.CsProject).count() == 1
    assert session.execute(sa.select(sa.func.count()).select_from(db.cs_delivery_table)).scalar() == 1
    with pytest.raises(tk.ValidationError):
        projects.csunesco_project_request_create(bridge, dict(payload, title='Changed'))


def test_receipt_rolls_back_with_creation(session, bridge):
    payload = {'delivery_id': 'b'*64}
    receipt, result = deliveries.begin(bridge, payload, 'create')
    assert result is None
    project = db.CsProject(slug='rollback', title='Rollback')
    session.add(project); session.flush()
    deliveries.complete(receipt, {'id': project.id})
    session.rollback()
    assert session.query(db.CsProject).count() == 0
    assert deliveries.begin(bridge, payload, 'create')[1] is None


def test_delivery_key_requires_service_identity(session, monkeypatch):
    def deny(context): raise tk.NotAuthorized('Service required')
    monkeypatch.setattr(portal, 'require_service', deny)
    with pytest.raises(tk.NotAuthorized):
        deliveries.begin({'user': 'person'}, {'delivery_id': 'c'*64}, 'create')


def test_live_map_does_not_refresh_project_statistics(monkeypatch):
    from flask import Flask
    from ckanext.csunesco.logic import views_data, data_access, ofform
    from ckanext.csunesco.logic.action import data
    monkeypatch.setattr(views_data, '_approved_source', lambda key: {'id': key, 'project_id': 'project'})
    monkeypatch.setattr(data_access, 'bundle', lambda source: {'dashboard': {'rows': []}})
    monkeypatch.setattr(ofform, 'rows_to_geojson', lambda value: {'type': 'FeatureCollection', 'features': []})
    def unexpected(*a): pytest.fail('Reading a map must not refresh other sources')
    monkeypatch.setattr(data, 'refresh_project_stats', unexpected)
    with Flask(__name__).test_request_context('/'):
        response = views_data.data_source_geojson('source')
    assert response.status_code == 200
