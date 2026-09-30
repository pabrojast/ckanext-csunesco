"""Public discovery must not inherit a moderator's unpublished catalogue."""
import datetime
import json
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from flask import Flask, g
import ckan.plugins.toolkit as tk

from ckanext.csunesco import db
from ckanext.csunesco.logic import auth, page_render, registration
from ckanext.csunesco.logic import views, views_content, views_data
from ckanext.csunesco.logic.action import content, data, projects


@pytest.fixture
def catalogue(monkeypatch):
    engine = sa.create_engine('sqlite://')
    db.ensure_mappers()
    from ckan.model.group import group_table, member_table
    db.metadata.create_all(bind=engine, tables=list(db._ALL_TABLES) + [group_table, member_table])
    db.Session.remove()
    db.Session.configure(bind=engine)
    users = {name: SimpleNamespace(id=name, name=name, sysadmin=name == 'admin',
                                  is_anonymous=False)
             for name in ('admin', 'member')}
    monkeypatch.setattr(auth.model.User, 'get', lambda name: users.get(name))
    monkeypatch.setattr(tk, 'check_access', lambda *a, **k: True)
    monkeypatch.setattr(tk, 'g', g)
    monkeypatch.setattr(tk, 'render', lambda template, extra_vars: extra_vars)
    monkeypatch.setattr(auth, '_admin_initiative_groups', lambda context: set())
    monkeypatch.setattr(views, '_member_state_choices', lambda: ([], True))
    actions = {
        'csunesco_project_list': projects.csunesco_project_list,
        'csunesco_content_list': content.csunesco_content_list,
        'csunesco_data_source_list': data.csunesco_data_source_list,
        'csunesco_aggregate_stats': projects.csunesco_aggregate_stats,
        'datashare_access_check': lambda *a: {'can_read_metadata': True},
    }
    monkeypatch.setattr(tk, 'get_action', lambda name: actions[name])
    rows = {}
    for i, state in enumerate(('approved', 'pending', 'draft', 'rejected', 'archived', 'withdrawn')):
        p = db.CsProject()
        p.slug = state; p.title = state; p.status = 'approved' if state == 'withdrawn' else state
        p.initiative_group = 'riverwatch'; p.countries = '[]'
        p.created = datetime.datetime(2026, 1, i + 1)
        p.extras = json.dumps({'_portal_withdrawn': True}) if state == 'withdrawn' else '{}'
        db.Session.add(p); db.Session.flush(); rows[state] = p
        for j, item_status in enumerate(('approved', 'pending', 'rejected')):
            source = db.CsDataSource()
            source.project_id = p.id; source.form_id = i * 10 + j
            source.title = state + '-' + item_status; source.status = item_status
            source.ckan_package_id = source.title
            db.Session.add(source)
            for kind in ('cs-news', 'cs-event', 'cs-publication', 'cs-map'):
                c = db.CsContent()
                c.project_id = p.id; c.title = state + '-' + kind + '-' + item_status
                c.slug = c.title; c.content_type = kind; c.status = item_status
                c.visibility = 'public'; c.body = 'Published text'
                c.initiative_group = 'riverwatch'
                db.Session.add(c)
    private = db.CsContent()
    private.project_id = rows['approved'].id; private.title = 'Private news'
    private.slug = 'private'; private.content_type = 'cs-news'; private.status = 'approved'
    private.visibility = 'private'; private.body = 'Private text'
    db.Session.add(private)
    db.Session.commit()
    yield rows, actions
    db.Session.remove(); engine.dispose()


@pytest.mark.parametrize('user', [None, 'member', 'admin'])
@pytest.mark.parametrize('initiative', [None, 'riverwatch'])
def test_home_and_initiative_projects_are_public_before_limit(catalogue, user, initiative):
    rows, _ = catalogue
    context = {'user': user}
    result = page_render._recent_projects(context, 1, initiative)
    assert [p['id'] for p in result] == [rows['approved'].id]
    assert context == {'user': user}


@pytest.mark.parametrize('user', [None, 'admin'])
def test_public_project_search_and_registration_exclude_unpublished(catalogue, user):
    rows, _ = catalogue
    with Flask(__name__).test_request_context('/?initiative=riverwatch'):
        g.user = user
        result = views.project_list()
        assert result['count'] == 1
        assert [p['id'] for p in result['projects']] == [rows['approved'].id]
        assert [p['id'] for p in registration._registration_projects()] == [rows['approved'].id]


@pytest.mark.parametrize('user', [None, 'admin'])
@pytest.mark.parametrize('kind', ['cs-news', 'cs-event', 'cs-publication', 'cs-map'])
def test_public_content_indexes_require_published_parent(catalogue, user, kind):
    rows, _ = catalogue
    with Flask(__name__).test_request_context('/'):
        g.user = user
        result = views_content._content_index(kind)
        assert result['count'] == 1
        assert {c['project_id'] for c in result['items']} == {rows['approved'].id}
        assert all(c['status'] == 'approved' and c['visibility'] == 'public' for c in result['items'])
        hub = views_content.cs_content_index()
        assert hub['count'] == 4


@pytest.mark.parametrize('user', [None, 'admin'])
def test_public_data_viewer_requires_approved_source_and_project(catalogue, user):
    rows, _ = catalogue
    with Flask(__name__).test_request_context('/'):
        g.user = user
        result = views_data.data_viewer()
        assert result['count'] == 1
        assert [(r['project_id'], r['status']) for r in result['sources']] == [(rows['approved'].id, 'approved')]


def test_review_apis_still_list_unapproved_projects(catalogue):
    rows, _ = catalogue
    result = projects.csunesco_project_list({'user': 'admin'}, {'limit': 100})
    assert {p['id'] for p in result['results']} == {p.id for p in rows.values()}
    result = content.csunesco_content_list({'user': 'admin'}, {'status': 'pending'})
    assert result['count'] == 24
    result = content.csunesco_content_list({'user': 'admin'}, {'project_id': rows['approved'].id})
    assert any(c['visibility'] == 'private' for c in result['results'])


def test_public_blocks_hide_private_and_unapproved_content(catalogue):
    rows, _ = catalogue
    result = page_render.build_context({'user': 'admin'}, None, [
        {'type': 'site_projects', 'limit': 6},
        {'type': 'content_list', 'scope': 'site', 'limit': 100},
    ])
    assert [p['id'] for p in result['recent_projects']] == [rows['approved'].id]
    assert len(next(iter(result['content_lists'].values()))) == 4


def test_approved_content_on_pending_project_is_not_public(catalogue):
    rows, _ = catalogue
    with pytest.raises(tk.ObjectNotFound):
        content.csunesco_content_show({'user': None}, {'slug': 'pending-cs-news-approved'})
    assert content.csunesco_content_show({'user': 'admin'}, {'slug': 'pending-cs-news-approved'})['project_id'] == rows['pending'].id


def test_public_sources_stay_separate_from_editor_preview(catalogue):
    rows, _ = catalogue
    project = db.project_dictize(rows['approved'])
    blocks = [{'type': 'builtin_data'}]
    published = page_render.build_context({'user': 'admin'}, project, blocks)
    preview = page_render.build_context({'user': 'admin'}, project, blocks, preview=True)
    assert [s['status'] for s in published['data_sources']] == ['approved']
    assert {s['status'] for s in preview['data_sources']} == {'approved', 'pending', 'rejected'}


@pytest.mark.parametrize('state', ['pending', 'draft', 'rejected', 'archived', 'withdrawn'])
def test_source_proxy_cannot_read_an_unpublished_project(catalogue, state):
    from ckanext.csunesco.logic import data_access
    rows, _ = catalogue
    source = {'project_id': rows[state].id, 'status': 'approved', 'ckan_package_id': 'dataset'}
    assert not data_access.permitted({'user': 'admin'}, source, 'can_read_metadata')


def test_public_dataset_blocks_do_not_use_admin_access(monkeypatch):
    from ckanext.csunesco.logic.public_view import public_context
    def show(context, data):
        if data['id'] == 'secret' and not context.get('user'):
            raise tk.NotAuthorized('Private dataset')
        return {'id': data['id'], 'private': data['id'] == 'secret', 'state': 'active'}
    monkeypatch.setattr(tk, 'get_action', lambda name: show)
    context = {'user': 'admin', 'auth_user_obj': SimpleNamespace(sysadmin=True), 'ignore_auth': True}
    assert [p['id'] for p in page_render._show_datasets(public_context(context), ['secret', 'public'])] == ['public']
    assert len(page_render._show_datasets(context, ['secret', 'public'])) == 2
    assert context['user'] == 'admin' and context['ignore_auth'] is True


def test_home_totals_exclude_unpublished_and_withdrawn_projects(catalogue):
    rows, _ = catalogue
    for p in rows.values():
        stats = db.CsProjectStats()
        stats.project_id = p.id; stats.observations = 7; stats.sites_monitored = 2
        db.Session.add(stats)
    db.Session.commit()
    for initiative in (None, 'riverwatch'):
        result = db.aggregate_stats(initiative)
        assert result['observations'] == 7
        assert result['sites_monitored'] == 2


def test_public_content_retains_the_logged_in_visibility_tier(catalogue):
    rows, _ = catalogue
    item = db.get_content('approved-cs-news-approved')
    item.visibility = 'logged-in'; db.Session.commit()
    with Flask(__name__).test_request_context('/'):
        g.user = None
        assert views_content._content_index('cs-news')['count'] == 0
        g.user = 'member'
        assert views_content._content_index('cs-news')['count'] == 1
        g.user = 'admin'
        assert views_content._content_index('cs-news')['count'] == 1


def test_public_content_can_still_be_owned_by_an_organization(catalogue):
    item = db.CsContent()
    item.title = 'Organization news'; item.slug = 'org-news'; item.organization_id = 'org'
    item.status = 'approved'; item.visibility = 'public'; item.content_type = 'cs-news'
    db.Session.add(item); db.Session.commit()
    with Flask(__name__).test_request_context('/'):
        g.user = 'admin'
        result = views_content._content_index('cs-news')
        assert {r['slug'] for r in result['items']} == {'org-news', 'approved-cs-news-approved'}


def test_dataset_search_excludes_unpublished_parents_before_solr(catalogue):
    from ckanext.csunesco.logic import managed_data
    from ckanext.csunesco.logic.public_view import public_context
    rows, _ = catalogue
    for source in db.Session.query(db.CsDataSource).all():
        source.access_level = 'legacy'
    db.Session.commit()
    filters = managed_data.package_search(lambda ctx, data: data,
        public_context({'user': 'admin'}), {'fq': 'owner_org:"org" OR organization:"org"', 'rows': 1})
    assert filters['fq'].startswith('(owner_org:"org" OR organization:"org") AND (')
    assert '-id:"approved-approved"' not in filters['fq']
    for state in ('pending', 'draft', 'rejected', 'archived', 'withdrawn'):
        assert '-id:"%s-approved"' % state in filters['fq']
    assert '-id:"approved-pending"' in filters['fq']
    # Direct references in a dataset block follow the same publication rule.
    original = lambda ctx, data: {'id': data['id']}
    with pytest.raises(tk.ObjectNotFound):
        managed_data.package_show(original, public_context({'user': 'admin'}), {'id': 'pending-approved'})
    assert managed_data.package_show(original, public_context({'user': 'admin'}), {'id': 'approved-approved'})['id'] == 'approved-approved'
