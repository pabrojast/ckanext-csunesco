import json
from types import SimpleNamespace

import pytest
import ckan.plugins.toolkit as tk

from ckanext.csunesco.logic import data_access, managed_data, ofform, snapshots


@pytest.mark.parametrize('level,metadata,preview,download', [
    ('public', True, True, True), ('confidential', False, False, False),
    ('findable', True, False, False), ('viewable', True, True, False),
    ('restricted', True, False, False),
])
def test_source_caps_follow_datashare(monkeypatch, level, metadata, preview, download):
    caps = dict(can_read_metadata=metadata, can_view_resources=preview, can_download=download)
    calls = []
    def action(context, data):
        calls.append((context, data))
        return caps
    monkeypatch.setattr(tk, 'get_action', lambda name: action)
    source = {'status': 'approved', 'ckan_package_id': 'dataset', 'access_level': level}
    for capability, expected in caps.items():
        assert data_access.permitted({'user': 'viewer'}, source, capability) is expected
    assert all(call[0]['user'] == 'viewer' and call[1]['id'] == 'dataset' for call in calls)
    source['status'] = 'pending'
    assert not data_access.permitted({'user': 'admin'}, source, 'can_download')


def test_partition_fetch_binds_form_level_revision_and_refuses_stale_data(monkeypatch):
    source = dict(id='source', form_id=12, partition_id=7, policy_revision=3, access_level='confidential')
    paths = []
    def fetch(path, payload=None):
        paths.append(path)
        return dict(partition_id=7, policy_revision=3, visibility='confidential', dashboard={'rows': []}, csv='id\n')
    monkeypatch.setattr(snapshots, 'app_request', fetch)
    assert data_access.bundle(source, materialize=False)['dashboard'] == {'rows': []}
    assert paths == ['/internal/ckan/forms/12/snapshot?partition_id=7&policy_revision=3']
    source['policy_revision'] = 4
    with pytest.raises(ofform.OfformError):
        data_access.bundle(source, materialize=False)
    monkeypatch.setattr(snapshots, 'app_request', lambda *args: (_ for _ in ()).throw(ValueError('offline')))
    with pytest.raises(ofform.OfformError):
        data_access.bundle(source, materialize=False)


def test_policy_gate_never_uses_an_old_success(monkeypatch):
    source = object()
    monkeypatch.setattr(managed_data.db, 'data_source_dictize', lambda obj: {
        'partition_id': 9, 'policy_revision': 8, 'access_level': 'restricted'})
    monkeypatch.setattr(snapshots, 'app_request', lambda path: {'access_level': 'restricted'})
    managed_data.check_current(source)
    monkeypatch.setattr(snapshots, 'app_request', lambda path: {'access_level': 'public'})
    with pytest.raises(tk.ObjectNotFound):
        managed_data.check_current(source)


def test_manual_privacy_drift_and_datastore_copies_are_rejected(monkeypatch):
    source = SimpleNamespace(access_level='confidential')
    monkeypatch.setattr(managed_data, 'source_for', lambda package_id: source)
    context = {'model': SimpleNamespace(
        Package=SimpleNamespace(get=lambda key: SimpleNamespace(id='dataset')),
        Resource=SimpleNamespace(get=lambda key: SimpleNamespace(package_id='dataset')))}
    with pytest.raises(tk.ValidationError):
        managed_data.package_patch(lambda *args: {}, context, {'id': 'dataset', 'access_level': 'public'})
    with pytest.raises(tk.ValidationError):
        managed_data.package_update(lambda *args: {}, context, {'id': 'dataset', 'private': False})
    with pytest.raises(tk.ValidationError):
        managed_data.datastore_create(lambda *args: {}, context, {'resource_id': 'resource'})
    result = managed_data.package_patch(lambda ctx, data: data, context, {'id': 'dataset', 'title': 'Updated title'})
    assert result['title'] == 'Updated title'


def test_policy_check_is_shared_only_within_one_http_request(monkeypatch):
    from flask import Flask
    app = Flask(__name__)
    source = object()
    monkeypatch.setattr(managed_data.db, 'data_source_dictize', lambda obj: {
        'partition_id': 9, 'policy_revision': 8, 'access_level': 'restricted'})
    calls = []
    def fetch(path):
        calls.append(path)
        return {'access_level': 'restricted'}
    monkeypatch.setattr(snapshots, 'app_request', fetch)
    with app.test_request_context('/'):
        managed_data.check_current(source)
        managed_data.check_current(source)
        assert len(calls) == 1
    # An outage on the next request must never reuse the previous success.
    monkeypatch.setattr(snapshots, 'app_request', lambda path: (_ for _ in ()).throw(ValueError('offline')))
    with app.test_request_context('/'):
        with pytest.raises(tk.ObjectNotFound):
            managed_data.check_current(source)


def test_nested_searches_share_check_and_recheck_on_next_request(monkeypatch):
    from flask import Flask
    app = Flask(__name__)
    row = SimpleNamespace(ckan_package_id='dataset')
    monkeypatch.setattr(managed_data.db.CsDataSource, 'access_level', 'legacy', raising=False)
    query = SimpleNamespace(filter=lambda *a: SimpleNamespace(all=lambda: [row]))
    monkeypatch.setattr(managed_data.db.Session, 'query', lambda *a: query)
    monkeypatch.setattr(managed_data.db, 'data_source_dictize', lambda obj: {
        'partition_id': 9, 'policy_revision': 8, 'ckan_package_id': 'dataset'})
    calls = []
    def fetch(path, data):
        calls.append(data)
        return {'available': [9]}
    monkeypatch.setattr(snapshots, 'app_request', fetch)
    original = lambda context, data: data
    with app.test_request_context('/'):
        for _ in range(20):
            assert managed_data.package_search(original, {}, {'rows': 0}) == {'rows': 0}
        assert len(calls) == 1
    monkeypatch.setattr(snapshots, 'app_request', lambda *a: {'available': []})
    with app.test_request_context('/'):
        result = managed_data.package_search(original, {}, {'rows': 0})
        assert '-id:"dataset"' in result['fq']
