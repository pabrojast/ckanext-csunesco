import json
from types import SimpleNamespace

import pytest
import ckan.plugins.toolkit as tk

from ckanext.csunesco.logic import data_access, managed_data, ofform, snapshots


@pytest.mark.parametrize('level,metadata,preview,download', [
    ('public', True, True, True), ('private', True, False, False), ('confidential', False, False, False),
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


def test_completed_partition_migration_never_requests_an_exclusive_lock(monkeypatch):
    db = managed_data.db
    engine = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
    inspector = SimpleNamespace(
        get_unique_constraints=lambda table: [],
        get_indexes=lambda table: [{'name': 'uq_cs_data_source_project_form_level'}])
    monkeypatch.setattr(db.sa, 'inspect', lambda _: inspector)
    # No begin() exists: an already-migrated database must never request DDL.
    db._ensure_data_source_partitions(engine)


def test_private_is_findable_in_datashare_without_changing_partition_identity(monkeypatch):
    source = SimpleNamespace(access_level='private')
    monkeypatch.setattr(managed_data, 'source_for', lambda package_id: source)
    model = SimpleNamespace(Package=SimpleNamespace(get=lambda key: SimpleNamespace(id='dataset')))
    context = {'model': model}
    assert data_access.dataset_level('private') == 'findable'
    managed_data._check_update(context, {'id': 'dataset', 'access_level': 'findable', 'private': False})
    with pytest.raises(tk.ValidationError):
        managed_data._check_update(context, {'id': 'dataset', 'access_level': 'public'})
    assert source.access_level == 'private'


@pytest.fixture
def render_data(monkeypatch):
    """Render the shipped snippets, including nested CKAN snippet calls."""
    from pathlib import Path
    from jinja2 import Environment, FileSystemLoader
    from markupsafe import Markup
    from ckan.lib import jinja_extensions
    env = Environment(autoescape=True,
        loader=FileSystemLoader(str(Path(__file__).parents[1] / 'templates')),
        extensions=[jinja_extensions.SnippetExtension])
    env.globals.update(_=lambda text: text, h=SimpleNamespace(
        url_for=lambda endpoint, **params: '/' + endpoint + '/' + str(params.get('id', '')),
        csunesco_block_type=lambda name: SimpleNamespace(label=name),
        check_access=lambda *a: False))
    def render(template, **values):
        return Markup(env.get_template(template).render(**values))
    monkeypatch.setattr(jinja_extensions.base, 'render_snippet', render)
    return render


@pytest.mark.parametrize('level,view,download', [
    ('public', True, True), ('private', False, False),
    ('findable', False, False), ('viewable', True, False), ('restricted', False, False),
])
@pytest.mark.parametrize('authorized', [False, True])
def test_data_blocks_render_only_the_viewers_allowed_operations(render_data, level, view, download, authorized):
    view, download = view or authorized, download or authorized
    source = dict(id='source', status='approved', title='Dataset', ckan_package_id='package',
                  can_view_resources=view, can_download=download, access_level=level)
    ctx = dict(project={'structure': {}}, data_sources=[source], approved_sources={'source': source})
    block = dict(id='data', type='builtin_data')
    html = render_data('csunesco/blocks/builtin_data.html', block=block, ctx=ctx)
    assert ('data-series-url=' in html) is view
    assert ('data-observations-url=' in html) is download
    assert ('href="/csunesco.data_source_csv/' in html) is download
    assert ('href="/csunesco.data_source_geojson/' in html) is download
    assert '/dataset.read/package' in html  # Findable metadata stays discoverable.
    assert ('Measurements from this dataset are restricted' in html) is not view
    chart = dict(id='chart', type='chart', data_source_id='source', height=260)
    html = render_data('csunesco/blocks/chart.html', block=chart, ctx=ctx)
    assert ('data-series-url=' in html) is view
    assert ('href="/csunesco.data_source_csv/' in html) is download
    html = render_data('csunesco/blocks/observation_map.html', block=chart, ctx=ctx)
    assert ('data-observations-url=' in html) is download
    html = render_data('csunesco/blocks/data_chat.html', block=chart, ctx=ctx)
    assert ('class="cs-chat-card"' in html) is view
    assert ('href="/csunesco.data_source_csv/' in html) is download


def test_app_configured_charts_keep_settings_and_respect_download_switch(render_data):
    source = dict(id='source', status='approved', title='Dataset', can_view_resources=True,
                  can_download=True, ckan_package_id='package')
    ctx = dict(project={'structure': {}}, data_sources=[source], approved_sources={'source': source})
    chart = dict(id='configured', type='chart', data_source_id='source', title='Water quality',
                 mode='numeric', field='ph', chart='bar', agg='max', group_by='site',
                 bucket='week', range='90d', height=320)
    block = dict(id='data', type='builtin_data', source_ids=['source'], charts=[chart],
                 show_downloads=False, show_maps=False)
    html = render_data('csunesco/blocks/builtin_data.html', block=block, ctx=ctx)
    for attr, value in {'mode':'numeric', 'field':'ph', 'type':'bar', 'agg':'max',
                        'group-by':'site', 'bucket':'week', 'range':'90d'}.items():
        assert 'data-%s="%s"' % (attr, value) in html
    assert 'data-observations-url=' not in html
    assert 'href="/csunesco.data_source_csv/' not in html
    block['source_ids'] = []
    html = render_data('csunesco/blocks/builtin_data.html', block=block, ctx=ctx)
    assert 'data-series-url=' not in html


@pytest.mark.parametrize('level,metadata,view,download', [
    ('public', True, True, True), ('private', True, False, False),
    ('confidential', False, False, False), ('viewable', True, True, False),
    ('restricted', True, False, False),
])
@pytest.mark.parametrize('user', [None, 'outsider', 'authorized'])
def test_chart_and_download_endpoints_enforce_current_dataset_rights(monkeypatch, level, metadata, view, download, user):
    from flask import Flask, g, abort
    from werkzeug.exceptions import NotFound
    from ckanext.csunesco.logic import views_data
    from ckanext.csunesco.logic.action import data
    authorized = user == 'authorized'
    caps = dict(can_read_metadata=metadata or authorized,
                can_view_resources=view or authorized, can_download=download or authorized)
    source = SimpleNamespace(id='source', status='approved', form_id=1, title='Dataset')
    monkeypatch.setattr(data.db, 'get_data_source', lambda _: source)
    monkeypatch.setattr(data.db, 'data_source_dictize', lambda _: dict(id='source', status='approved',
        form_id=1, ckan_package_id='package', access_level=level))
    monkeypatch.setattr(tk, 'check_access', lambda *a: True)
    monkeypatch.setattr(tk, '_', lambda text: text)
    monkeypatch.setattr(tk, 'abort', abort)
    actions = {'datashare_access_check': lambda context, _: caps,
               'csunesco_data_source_show': data.csunesco_data_source_show,
               'csunesco_data_source_series': data.csunesco_data_source_series,
               'csunesco_data_source_fields': data.csunesco_data_source_fields}
    monkeypatch.setattr(tk, 'get_action', lambda name: actions[name])
    monkeypatch.setattr(tk, 'g', g)
    reads = []
    def bundle(*args, **kwargs):
        reads.append(True)
        return {'dashboard': {'rows': [], 'schema': {}}, 'csv': 'id\n'}
    monkeypatch.setattr(data_access, 'bundle', bundle)
    monkeypatch.setattr(data, 'refresh_project_stats', lambda *a: None)
    with Flask(__name__).test_request_context('/'):
        g.user = user
        for endpoint, allowed in [(views_data.data_source_fields, caps['can_view_resources']),
                                  (views_data.data_source_series, caps['can_view_resources']),
                                  (views_data.data_source_csv, caps['can_download']),
                                  (views_data.data_source_geojson, caps['can_download'])]:
            reads.clear()
            if allowed:
                response = endpoint('source')
                assert response.status_code == 200
                assert response.headers['Cache-Control'] == 'no-store'
                assert reads
            else:
                with pytest.raises(NotFound):
                    endpoint('source')
                assert not reads
