import copy
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
