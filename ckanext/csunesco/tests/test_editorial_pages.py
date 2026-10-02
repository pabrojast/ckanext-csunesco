import copy
import json
import time
import pytest
import ckan.plugins.toolkit as tk
from ckanext.csunesco import db
from ckanext.csunesco.logic import editorial_pages as pages, editorial_owner, portal, snapshots, blocks
from ckanext.csunesco.logic.action import page as page_actions, projects
from ckanext.csunesco.tests.test_project_portal import store, ctx


def envelope(scope='site', key='home', revision=1):
    payload = {'blocks': blocks.default_site_blocks() if scope == 'site' else blocks.default_initiative_blocks(), 'layout': {}}
    return {'schema_version': 1, 'scope': scope, 'key': key, 'page_key': scope + ':' + key,
            'revision': revision, 'payload': payload, 'checksum': portal.checksum(payload),
            'actor': {'username': 'reviewer'}}


def test_publish_directly_checks_real_author_and_revision(store):
    data = envelope()
    result = pages.apply(ctx(), data)
    assert result['status'] == 'approved'
    row = db.get_project_page(db.SITE_PAGE_ID)
    first = row.published_json
    assert pages.apply(ctx(), data) == result
    bad = copy.deepcopy(data); bad['actor'] = {'username': 'transport'}
    with pytest.raises(tk.NotAuthorized): pages.apply(ctx(), bad)
    bad['actor'] = {'username': 'outsider'}
    with pytest.raises(tk.NotAuthorized): pages.apply(ctx(), bad)
    bad = copy.deepcopy(data); bad['payload']['blocks'][0]['heading'] = 'Conflicting'; bad['checksum'] = portal.checksum(bad['payload'])
    with pytest.raises(tk.ValidationError): pages.apply(ctx(), bad)
    assert row.published_json == first
    newer = copy.deepcopy(bad); newer['revision'] = 2
    pages.apply(ctx(), newer)
    with pytest.raises(tk.ValidationError): pages.apply(ctx(), data)


def test_global_preview_never_publishes_and_rechecks_app_scope(store, monkeypatch):
    data = envelope(); data['preview_grant'] = 'grant'; data['actor'] = {'username': 'app-admin-only'}
    claims = {k: data[k] for k in ('scope', 'key', 'revision', 'checksum')}
    claims.update(purpose='portal-preview', expires=int(time.time()) + 300)
    monkeypatch.setattr(snapshots, 'app_request', lambda *a: dict(claims))
    result = pages.preview(ctx(), data)
    assert result['url'].split('/')[-1]
    assert db.get_project_page(db.SITE_PAGE_ID) is None
    claims['scope'] = 'initiative'
    with pytest.raises(tk.NotAuthorized): pages.preview(ctx(), data)


def test_scope_rejects_project_only_blocks(store):
    data = envelope()
    data['payload']['blocks'].append(blocks.normalize_block({'type': 'builtin_join'}))
    data['checksum'] = portal.checksum(data['payload'])
    with pytest.raises(tk.ValidationError): pages.apply(ctx(), data)


def test_unavailable_stored_image_is_a_preview_warning_but_blocks_publish(store, monkeypatch):
    data = envelope(); data['preview_grant'] = 'grant'
    data['payload']['blocks'].append(blocks.normalize_block({'id': 'gallery', 'type': 'image', 'items': [{'url': '/uploads/csunesco/missing.png'}]}))
    data['checksum'] = portal.checksum(data['payload'])
    claims = {k: data[k] for k in ('scope', 'key', 'revision', 'checksum')}
    claims.update(purpose='portal-preview', expires=int(time.time()) + 300)
    monkeypatch.setattr(snapshots, 'app_request', lambda *a: dict(claims))
    def unavailable(*a, **kw): raise ValueError('Missing stored file')
    monkeypatch.setattr(snapshots, '_copy_media', unavailable)
    result = pages.preview(ctx(), data)
    assert result['warnings'][0]['block_id'] == 'gallery'
    with pytest.raises(ValueError): pages.apply(ctx(), data)
    assert db.get_project_page(db.SITE_PAGE_ID) is None


def test_legacy_editor_writes_blocked_even_for_human_sysadmin(store, monkeypatch):
    project, _ = store
    monkeypatch.setitem(tk.config, 'ckanext.csunesco.editorial_owner', 'app')
    for action, data in [
        (page_actions.csunesco_site_page_update, {'blocks': blocks.default_site_blocks()}),
        (page_actions.csunesco_site_page_publish, {}),
        (page_actions.csunesco_initiative_page_update, {'initiative': 'riverwatch', 'blocks': []}),
        (page_actions.csunesco_project_page_update, {'project_id': project.id, 'blocks': []}),
        (projects.csunesco_project_update, {'id': project.id, 'title': 'Forbidden'}),
    ]:
        with pytest.raises(tk.NotAuthorized): action(ctx('reviewer'), dict(data, csunesco_portal_sync=True))
    # Real internal bridge publication is still allowed after cutover.
    assert pages.apply(ctx(), envelope())['status'] == 'approved'
    assert project.title == 'Published title'


def test_editor_links_use_matching_app_sections(store, monkeypatch):
    project, _ = store
    monkeypatch.setitem(tk.config, 'ckanext.csunesco.editorial_owner', 'app')
    monkeypatch.setitem(tk.config, 'ckanext.csunesco.ofform_app_url', 'https://app.example')
    portal.set_metadata(project, {'app_project_id': 42}); db.Session.commit()
    assert editorial_owner.editor_link('csunesco.project_edit', slug=project.slug) == 'https://app.example/projects/42/space/details'
    assert editorial_owner.editor_link('csunesco.content_new', slug=project.slug).endswith('/space/news')
    assert editorial_owner.editor_link('csunesco.site_page_edit').endswith('/admin/portal-pages/site/home')


def test_institutional_preview_rechecks_native_role_without_nested_callback(store, monkeypatch):
    _, users = store
    claims = {'purpose': 'portal-preview', 'expires': time.time() + 120,
              'scope': 'site', 'key': 'home', 'institutional_ckan_id': 'reviewer',
              'institutional_username': 'reviewer'}
    calls = []
    monkeypatch.setattr(snapshots, 'app_request', lambda *args: calls.append(args) or claims)
    assert portal._app_preview_access('grant') == claims
    assert len(calls) == 1
    users['reviewer'].sysadmin = False
    with pytest.raises(tk.NotAuthorized): portal._app_preview_access('grant')
    users['reviewer'].sysadmin = True
    users['reviewer'].state = 'deleted'
    with pytest.raises(tk.NotAuthorized): portal._app_preview_access('grant')
    claims.update(institutional_ckan_id='transport', institutional_username='transport')
    with pytest.raises(tk.NotAuthorized): portal._app_preview_access('grant')


def test_project_link_uses_shared_editor_without_global_cutover(store, monkeypatch):
    project, _ = store
    monkeypatch.setitem(tk.config, 'ckanext.csunesco.editorial_owner', 'ckan')
    monkeypatch.setitem(tk.config, 'ckanext.csunesco.ofform_app_url', 'https://app.example/cstoolbox')
    monkeypatch.setattr(tk, 'url_for', lambda endpoint, **kwargs: 'native:' + endpoint)
    request = {'projects': [{'ckan_id': project.id, 'app_project_id': 42}]}
    assert pages.link_projects(ctx(), request) == {'linked': 1}
    assert pages.link_projects(ctx(), request) == {'linked': 1}
    assert not portal.managed(project)
    assert db.get_project_page(project.id) is None
    for endpoint, section in [('project_edit', 'details'), ('project_page_edit', 'portal'), ('content_new', 'news')]:
        assert editorial_owner.editor_link('csunesco.' + endpoint, slug=project.slug) == 'https://app.example/cstoolbox/projects/42/space/' + section
    assert editorial_owner.editor_link('csunesco.site_page_edit') == 'native:csunesco.site_page_edit'
    assert editorial_owner.editor_link('csunesco.initiative_page_edit', name='riverwatch') == 'native:csunesco.initiative_page_edit'
    assert editorial_owner.editor_link('csunesco.project_new') == 'native:csunesco.project_new'
    for action, data in [(projects.csunesco_project_update, {'id': project.id, 'title': 'Separate copy'}),
                         (page_actions.csunesco_project_page_update, {'project_id': project.id, 'blocks': []}),
                         (page_actions.csunesco_project_page_submit, {'project_id': project.id})]:
        with pytest.raises(tk.NotAuthorized):
            action(ctx('reviewer'), dict(data, csunesco_portal_sync=True))
    assert project.title == 'Published title'
    from ckanext.csunesco.tests.test_project_portal import envelope as project_envelope
    assert portal.apply(ctx(), project_envelope(project))['status'] == 'pending'
    assert project.title == 'Published title'


def test_link_batch_is_atomic_and_rejects_shared_app_record(store):
    project, _ = store
    other = db.CsProject()
    other.slug = 'other'; other.title = 'Other'; other.status = 'approved'; other.extras = '{}'
    db.Session.add(other); db.Session.commit()
    with pytest.raises(tk.ValidationError):
        pages.link_projects(ctx(), {'projects': [
            {'ckan_id': project.id, 'app_project_id': 42},
            {'ckan_id': other.id, 'app_project_id': 42},
        ]})
    assert editorial_owner.project_app_id(project) is None
    assert editorial_owner.project_app_id(other) is None


@pytest.mark.parametrize('view,section', [('details', 'details'), ('page', 'portal'), ('content', 'news')])
def test_legacy_get_redirects_to_shared_section_and_post_is_rejected(store, monkeypatch, view, section):
    from flask import Flask
    from ckanext.csunesco.logic import views, views_page, views_content
    project, _ = store
    monkeypatch.setitem(tk.config, 'ckanext.csunesco.editorial_owner', 'ckan')
    monkeypatch.setitem(tk.config, 'ckanext.csunesco.ofform_app_url', 'https://app.example')
    pages.link_projects(ctx(), {'projects': [{'ckan_id': project.id, 'app_project_id': 42}]})
    monkeypatch.setattr(tk, 'redirect_to', lambda url: url)
    function = {'details': views.project_edit, 'page': views_page.project_page_edit, 'content': views_content.content_new}[view]
    app = Flask(__name__)
    app.secret_key = "isolated-editor-route-test"
    with app.test_request_context('/'):
        assert function(project.slug) == 'https://app.example/projects/42/space/' + section
    if section == 'portal':
        with app.test_request_context('/?open=about&next=https://untrusted.example'):
            assert function(project.slug) == 'https://app.example/projects/42/space/portal?open=about'
        with app.test_request_context('/?open=https://untrusted.example'):
            assert function(project.slug) == 'https://app.example/projects/42/space/portal'
        saved = db.get_or_create_project_page(project.id)
        saved.published_json = json.dumps([{'id': 'legacy-about', 'type': 'builtin_about'}])
        db.Session.add(saved); db.Session.commit()
        with app.test_request_context('/?open=legacy-about'):
            assert function(project.slug) == 'https://app.example/projects/42/space/portal?open=builtin_about'
    from werkzeug.exceptions import MethodNotAllowed
    with app.test_request_context('/', method='POST'), pytest.raises(MethodNotAllowed):
        function(project.slug)
