"""Project publication boundary: service credentials, private revisions and snapshots."""
import copy
import json
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
import ckan.plugins.toolkit as tk
from ckanext.csunesco import db
from ckanext.csunesco.logic import portal, snapshots, auth, blocks
from ckanext.csunesco.logic.action import page as page_actions


@pytest.fixture
def store(monkeypatch, tmp_path):
    engine = sa.create_engine('sqlite://')
    db.ensure_mappers()
    from ckan.model.group import group_table, member_table
    db.metadata.create_all(bind=engine, tables=list(db._ALL_TABLES) + [group_table, member_table])
    db.Session.remove()
    db.Session.configure(bind=engine)
    monkeypatch.setattr(tk, 'check_access', lambda *a, **k: True)
    monkeypatch.setitem(tk.config, 'ckanext.csunesco.portal_storage_path', str(tmp_path))
    monkeypatch.setitem(tk.config, 'ckanext.csunesco.portal_service_user', 'transport')
    monkeypatch.setattr(portal, 'callback', lambda *a: None)
    users = {name: SimpleNamespace(id=name, name=name, state='active', sysadmin=name in ('transport', 'reviewer'), is_anonymous=False) for name in ('transport', 'author', 'reviewer', 'outsider')}
    monkeypatch.setattr(portal.model.User, 'get', lambda key: users.get(key))
    monkeypatch.setattr(auth, 'can_manage_project', lambda ctx, pid: ctx['user'] in ('author', 'reviewer', 'transport'))
    project = db.CsProject()
    project.slug = 'river'; project.title = 'Published title'; project.status = 'approved'
    project.created_by = 'author'; project.extras = '{}'
    db.Session.add(project); db.Session.commit()
    yield project, users
    db.Session.remove(); engine.dispose()


def ctx(name='transport'):
    return {'user': name}


def envelope(project, **overrides):
    payload = {'project': {'title': 'Candidate title', 'short_description': '<b>Safe</b>', 'contact_email': 'private@example.test'},
               'structure': {'aim': 'Monitor rivers', 'indigenous_knowledge_notes': 'private field'},
               'workplan': [{'title': 'Private workplan'}],
               'blocks': blocks.default_blocks() + [blocks.normalize_block({'type': 'project_facts', 'fields': ['short_description']})],
               'contents': [], 'media': []}
    data = {'schema_version': 1, 'project': {'id': project.id}, 'app_project_id': 42,
            'revision': 1, 'actor': {'username': 'author'}, 'intent': 'submit',
            'payload': payload, 'checksum': portal.checksum(payload)}
    data.update(overrides)
    return data


def test_transport_never_autoapproves(store):
    project, _ = store
    out = portal.apply(ctx(), envelope(project))
    assert out['status'] == 'pending'
    assert project.title == 'Published title'
    page = db.get_project_page(project.id)
    assert page.submitted_by == 'author'
    assert page.published_json is None
    assert 'Candidate title' not in json.dumps(db.project_dictize(project))
    assert 'private field' not in json.dumps(db.page_dictize(page))


def test_effective_actor_capability_and_identity_checked(store):
    project, _ = store
    for actor in ({'username': 'outsider'}, {'username': 'author', 'ckan_id': 'reviewer'}, {'username': 'missing'}):
        with pytest.raises(tk.NotAuthorized):
            portal.apply(ctx(), envelope(project, actor=actor))
    with pytest.raises(tk.NotAuthorized):
        portal.apply(ctx('reviewer'), envelope(project))


def test_revision_and_checksum_protect_retries(store):
    project, _ = store
    data = envelope(project)
    first = portal.apply(ctx(), data)
    assert portal.apply(ctx(), data)['draft_hash'] == first['draft_hash']
    with pytest.raises(tk.ValidationError):
        portal.apply(ctx(), envelope(project, checksum='bad'))
    newer = envelope(project, revision=2)
    portal.apply(ctx(), newer)
    with pytest.raises(tk.ValidationError):
        portal.apply(ctx(), data)


def test_submit_retry_recovers_staged_draft(store, monkeypatch):
    project, _ = store
    original = page_actions.csunesco_project_page_submit
    def fail(*a):
        raise RuntimeError('temporary failure')
    monkeypatch.setattr(page_actions, 'csunesco_project_page_submit', fail)
    data = envelope(project)
    with pytest.raises(RuntimeError):
        portal.apply(ctx(), data)
    monkeypatch.setattr(page_actions, 'csunesco_project_page_submit', original)
    assert portal.apply(ctx(), data)['status'] == 'pending'


def test_exact_approval_freezes_public_fields_without_private_values(store):
    project, _ = store
    result = portal.apply(ctx(), envelope(project))
    with pytest.raises(tk.ValidationError):
        page_actions.csunesco_project_page_approve(ctx('reviewer'), {'project_id': project.id})
    with pytest.raises(tk.ValidationError):
        page_actions.csunesco_project_page_approve(ctx('reviewer'), {'project_id': project.id, 'draft_hash': 'old'})
    page_actions.csunesco_project_page_approve(ctx('reviewer'), {'project_id': project.id, 'draft_hash': result['draft_hash']})
    assert project.title == 'Candidate title'
    public = db.project_dictize(project)
    assert public['structure'] == {'aim': 'Monitor rivers'}
    assert 'contact_email' not in public
    assert 'workplan' not in public
    assert portal.status(project)['published_revision'] == 1
    assert portal.status(project)['status'] == 'approved'


def test_rejection_preserves_last_published_page(store):
    project, _ = store
    result = portal.apply(ctx(), envelope(project))
    page_actions.csunesco_project_page_approve(ctx('reviewer'), {'project_id': project.id, 'draft_hash': result['draft_hash']})
    page = db.get_project_page(project.id)
    previous = page.published_json
    data = envelope(project, revision=2)
    data['payload']['project']['title'] = 'Rejected title'
    data['checksum'] = portal.checksum(data['payload'])
    portal.apply(ctx(), data)
    page_actions.csunesco_project_page_reject(ctx('reviewer'), {'project_id': project.id, 'reason': 'Fix title'})
    assert page.published_json == previous
    assert project.title == 'Candidate title'
    assert portal.status(project)['status'] == 'rejected'
    assert portal.status(project)['published_revision'] == 1


def test_unknown_or_private_block_fields_are_rejected(store):
    project, _ = store
    for block in ({'type': 'not-installed'}, {'type': 'site_hero'}, {'type': 'project_facts', 'fields': ['contact_email']}, {'type': 'text', 'future_design': {'keep': 'me'}}):
        payload = envelope(project)['payload']; payload['blocks'] = [block]
        with pytest.raises(tk.ValidationError):
            portal.validate_payload(payload)


def test_draft_export_is_service_only_and_preserves_snapshot(store):
    project, _ = store
    portal.apply(ctx(), envelope(project, intent='draft'))
    result = portal.export(ctx(), {'project_id': project.id})
    assert result['page']['draft_portal_payload']['project']['title'] == 'Candidate title'
    with pytest.raises(tk.NotAuthorized):
        portal.export(ctx('author'), {'project_id': project.id})


def test_persisted_bundle_survives_upstream_failure_and_gates_revocation(store, monkeypatch):
    project, _ = store
    source = db.CsDataSource(); source.project_id = project.id; source.form_id = 99; source.status = 'approved'
    db.Session.add(source); db.Session.commit()
    data = {'rows': [{'id': 1, 'answers': {'ph': 7}}], 'schema': {}, 'total': 1}
    snapshots.write_private('forms', '99', {'dashboard': data, 'csv': 'id,ph\n1,7\n', 'created_at': 0})
    from ckanext.csunesco.logic import ofform
    monkeypatch.setattr(ofform, '_fetch', lambda *a, **k: (_ for _ in ()).throw(ofform.OfformError('offline')))
    assert ofform.fetch_dashboard_data(99) == data
    assert ofform.fetch_csv(99) == 'id,ph\n1,7\n'
    source.status = 'rejected'; db.Session.commit()
    assert snapshots.saved_form(99) is None


def test_public_field_registry_does_not_advertise_private_fields(store):
    project, _ = store
    registry = portal.capabilities(ctx(), {})
    assert not set(portal.FACT_FIELDS).intersection(portal.constants.FIELD_AUDIENCE)
    assert {'project_facts', 'project_structure'} <= {item['key'] for item in registry['blocks']}


def test_asset_fetch_auth_is_pinned_to_app_endpoint(store, monkeypatch):
    project, _ = store
    monkeypatch.setitem(tk.config, 'ckanext.csunesco.ofform_base_url', 'http://app.example')
    monkeypatch.setitem(tk.config, 'ckanext.csunesco.ofform_callback_token', 'test-callback-secret')
    with pytest.raises(tk.ValidationError):
        snapshots._copy_media(project, 'http://outside.example/image.png', 'http://evil.example/internal/ckan/project-assets/1')
    with pytest.raises(tk.ValidationError):
        snapshots._copy_media(project, 'http://outside.example/image.png', '/admin/users')
    calls = []
    class Reply:
        headers = SimpleNamespace(get_content_type=lambda: 'image/png')
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, limit): return b'fixture-image-bytes'
    class Opener:
        def open(self, request, timeout):
            calls.append((request.full_url, dict(request.header_items())))
            return Reply()
    monkeypatch.setattr(snapshots.urllib.request, 'build_opener', lambda *args: Opener())
    candidate = {'project': {'logo_url': 'http://outside.example/logo.png'}, 'blocks': [],
                 'media': [{'url': 'http://outside.example/logo.png', 'fetch_url': '/internal/ckan/project-assets/1'}]}
    stored = snapshots.materialize_media(project, candidate)
    assert stored['project']['logo_url'].startswith('/citizen-science/portal/media/' + project.id)
    assert calls[0][0] == 'http://app.example/internal/ckan/project-assets/1'
    assert calls[0][1]['Authorization'] == 'Bearer test-callback-secret'
    assert portal.metadata(project).get('media_hashes') is None
    snapshots.publish_media_manifest(project, stored)
    assert portal.metadata(project)['media_hashes']


def test_initial_intake_idempotency_links_without_publishing(store, monkeypatch):
    project, _ = store
    project.extras = json.dumps({'_portal_intake': {'pending': True}})
    db.Session.commit()
    called = []
    def request(path, payload):
        called.append((path, payload))
        return {'app_project_id': 42}
    monkeypatch.setattr(snapshots, 'app_request', request)
    portal.send_initial_request(project)
    portal.send_initial_request(project)
    assert len(called) == 1
    assert called[0][1]['idempotency_key'] == 'ckan-project:' + project.id
    assert called[0][1]['initial_setup'] == {}
    assert called[0][1]['actor']['username'] == 'author'
    assert portal.metadata(project)['app_project_id'] == 42
    assert portal.metadata(project)['status'] == 'draft'
    assert db.get_project_page(project.id) is None


def test_real_ckan_configuration_declaration_accepts_options():
    from pathlib import Path
    import yaml
    from ckan.config.declaration import Declaration
    declaration = Declaration()
    source = Path(portal.__file__).parents[1] / 'config_declaration.yaml'
    declaration.load_dict(yaml.safe_load(source.read_text()))


def test_empty_app_brand_values_use_defaults_and_keep_explicit_zero(store):
    project, _ = store
    payload = envelope(project)['payload']
    payload['project'].update(logo_url=None, heading_image_url=None,
                              logo_focal_x=None, logo_focal_y=0, logo_zoom=None,
                              heading_focal_x=None, heading_focal_y=None, heading_zoom=None,
                              countries=None, region_geojson=None)
    candidate = portal.validate_payload(payload)
    assert candidate['project']['logo_focal_x'] == 50
    assert candidate['project']['logo_focal_y'] == 0
    assert candidate['project']['logo_zoom'] == 100
    assert candidate['project']['heading_zoom'] == 100
    assert candidate['project']['region_geojson'] is None


def test_observation_media_shares_snapshot_and_rejects_unrelated_rows(store, monkeypatch):
    project, _ = store
    source = SimpleNamespace(project_id=project.id, form_id=99)
    digest = 'a' * 64
    monkeypatch.setattr(snapshots, '_copy_media', lambda *a, **k: '/citizen-science/portal/media/' + project.id + '/' + digest)
    media = [{'id': 7, 'submission_id': 1, 'field_name': 'photo', 'mime': 'image/jpeg',
              'fetch_url': '/internal/ckan/submission-files/7', 'sha256': digest}]
    original = {'rows': [{'id': 1, 'answers': {'photo': 'remote-expiring-url'}}]}
    dashboard, hashes = snapshots.materialize_observation_media(source, original, media)
    assert hashes == [digest]
    url = dashboard['rows'][0]['answers']['photo'][0]
    assert url == '/citizen-science/portal/data-media/99/' + digest
    assert dashboard['rows'][0]['media'][0]['url'] == url
    assert original['rows'][0]['answers']['photo'] == 'remote-expiring-url'
    media[0]['submission_id'] = 2
    with pytest.raises(ValueError):
        snapshots.materialize_observation_media(source, original, media)


def test_initial_upload_moves_out_of_public_storage_and_binds_after_retry(store, monkeypatch, tmp_path):
    project, _ = store
    monkeypatch.setitem(tk.config, 'ckan.storage_path', str(tmp_path))
    directory = tmp_path / 'storage' / 'uploads' / 'csunesco'
    directory.mkdir(parents=True)
    original = directory / 'new-logo.png'
    original.write_bytes(b'new-brand-image')
    batch = SimpleNamespace(_written=[str(original)])
    data = {'logo_url': '/uploads/csunesco/new-logo.png'}
    refs = snapshots.privatize_intake_uploads(data, batch, 'author')
    assert not original.exists()
    assert data['logo_url'].startswith('/citizen-science/portal/intake-media/')
    assert snapshots.read_private('intake', refs[0])['project_id'] is None
    # Form validation rerender keeps the private URL and binds it on retry.
    retried = snapshots.privatize_intake_uploads(data, SimpleNamespace(_written=[]), 'author')
    assert retried == refs
    snapshots.bind_intake_media(project, refs)
    assert snapshots.read_private('intake', refs[0])['project_id'] == project.id


def test_intake_media_requires_owner_or_service_until_approval(store, monkeypatch):
    from flask import Flask, g, abort
    from werkzeug.exceptions import NotFound
    project, _ = store
    project.status = 'pending'
    token, digest = 't' * 43, 'b' * 64
    directory = snapshots.root() / 'assets'
    directory.mkdir(exist_ok=True)
    (directory / digest).write_bytes(b'private-logo')
    snapshots.write_private('asset-types', digest, {'type': 'image/png'})
    snapshots.write_private('intake', token, {'actor': 'author', 'project_id': project.id,
                                            'digest': digest, 'field': 'logo_url'})
    project.logo_url = '/citizen-science/portal/intake-media/' + token + '/' + digest
    monkeypatch.setattr(tk, 'g', g)
    monkeypatch.setitem(tk.config, 'ckanext.csunesco.ofform_callback_token', 'callback-test')
    monkeypatch.setattr(tk, 'abort', abort)
    app = Flask(__name__)
    with app.test_request_context('/'):
        g.user = None
        with pytest.raises(NotFound):
            snapshots.intake_asset_view(token, digest)
        g.user = 'author'
        assert snapshots.intake_asset_view(token, digest).status_code == 200
    with app.test_request_context('/', headers={'Authorization': 'Bearer callback-test'}):
        g.user = None
        assert snapshots.intake_asset_view(token, digest).headers['Cache-Control'] == 'no-store'
    with app.test_request_context('/'):
        g.user = None
        project.status = 'approved'
        assert snapshots.intake_asset_view(token, digest).status_code == 200
        portal.set_metadata(project, {'status': 'withdrawn'})
        with pytest.raises(NotFound):
            snapshots.intake_asset_view(token, digest)


def test_project_stats_refresh_uses_aggregate_contract_without_touching_cms(store, monkeypatch):
    project, _ = store
    portal.set_metadata(project, {'app_project_id': 42})
    called = []
    monkeypatch.setattr(snapshots, 'app_request', lambda path: {'observations': 3, 'sites_monitored': 2, 'citizen_scientists': 1, 'member_states': 1})
    monkeypatch.setattr(db, 'stats_set', lambda pid, **kw: called.append((pid, kw)))
    snapshots.refresh_project_stats(project)
    # App observations are not public without a CKAN-approved source snapshot.
    assert called == [(project.id, {'observations': 0, 'sites_monitored': 2, 'citizen_scientists': 1})]
    assert project.title == 'Published title'
    assert db.get_project_page(project.id) is None


def test_video_provider_metadata_stays_link_without_downloading_html(store, monkeypatch):
    project, _ = store
    monkeypatch.setattr(snapshots, '_copy_media', lambda *a, **k: pytest.fail('Provider link must not be downloaded'))
    video = {'kind': 'video', 'url': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'}
    candidate = {'project': {}, 'blocks': [], 'media': [video]}
    assert snapshots.materialize_media(project, candidate)['media'] == [video]


def test_withdrawal_hides_catalog_and_survives_replacement_draft_until_approval(store, monkeypatch):
    from ckanext.csunesco.logic.action import projects as project_actions
    project, _ = store
    portal.apply(ctx(), envelope(project, intent='withdraw'))
    assert portal.withdrawn(project)
    assert project_actions.csunesco_project_list({'user': None}, {})['count'] == 0
    assert project_actions.csunesco_project_list(ctx(), {})['count'] == 1
    with pytest.raises(tk.ObjectNotFound):
        project_actions.csunesco_project_show({'user': None}, {'id': project.id})
    # Upgrade/import from metadata written before the sticky marker existed.
    legacy_extras = db._load_json(project.extras, {})
    legacy_extras.pop('_portal_withdrawn', None)
    project.extras = portal.canonical(legacy_extras)
    result = portal.apply(ctx(), envelope(project, revision=2))
    assert result['status'] == 'pending'
    assert result['withdrawn'] is True
    assert portal.withdrawn(project)
    page_actions.csunesco_project_page_approve(ctx('reviewer'), {'project_id': project.id, 'draft_hash': result['draft_hash']})
    assert not portal.withdrawn(project)
    assert project_actions.csunesco_project_list({'user': None}, {})['count'] == 1


def test_migration_asset_ref_can_resolve_original_ckan_url_before_app_hydration(store, monkeypatch):
    project, _ = store
    monkeypatch.setitem(tk.config, 'ckan.site_url', 'http://ckan.example')
    payload = envelope(project)['payload']
    payload['project']['logo_url'] = 'asset:7'
    payload['media'] = [{'id': 7, 'kind': 'image', 'source_url': 'http://ckan.example/uploads/csunesco/legacy.png'}]
    assert portal.validate_payload(payload)['project']['logo_url'] == '/uploads/csunesco/legacy.png'


def test_legacy_local_alias_is_private_after_withdrawal_without_breaking_shared_home(store):
    project, _ = store
    path = '/uploads/csunesco/legacy-project.png'
    digest = 'c' * 64
    snapshots.register_legacy_alias(project, path, digest)
    record = snapshots.read_private('legacy-aliases', portal.hashlib.sha256(path.encode()).hexdigest())
    project.logo_url = path
    assert snapshots.legacy_alias_public(record)
    portal.set_metadata(project, {'app_project_id': 42, 'status': 'withdrawn'})
    assert not snapshots.legacy_alias_public(record)
    home = db.CsProjectPage()
    home.project_id = db.SITE_PAGE_ID
    home.published_json = json.dumps([{'type': 'image', 'items': [{'url': path}]}])
    db.Session.add(home); db.Session.commit()
    assert snapshots.legacy_alias_public(record)
