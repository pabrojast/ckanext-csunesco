"""A browser receives display settings, never inherited private selectors.

Feeds resolve the published revision or frozen preview server-side. They ignore
caller selectors and recheck anonymous source rights, including during previews.
Legacy raw-source charts and authorized editing DTOs keep their full contract.
"""
import copy
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask, url_for
import ckan.plugins.toolkit as tk

from ckanext.csunesco.logic import chart_definition, chart_feeds, helpers, portal, snapshots
from ckanext.csunesco.logic.action import data, page as page_actions


def chart():
    definition = chart_definition.normalize({'version': 2, 'field': 'ph', 'title': 'River quality',
        'sites': ['PRIVATE_SITE_EXAMPLE'], 'filters': {'sites': ['PRIVATE_SITE_EXAMPLE'],
            'facets': {'weather': ['PRIVATE_FACET_EXAMPLE']}, 'search': 'PRIVATE_SEARCH_EXAMPLE'}})
    return dict(id='river-chart', type='chart', title='River quality', data_source_id='source',
        height=320, mode='numeric', chart='line', field='ph', group_by='auto', bucket='month',
        definition=definition, history_sources=[], saved_chart_revision=1)


@pytest.fixture
def feed_case(monkeypatch):
    project = SimpleNamespace(id='project-id', slug='river', status='approved', extras='{}')
    page = SimpleNamespace(project_id=project.id, published_json=json.dumps([chart()]))
    source = SimpleNamespace(id='source', project_id=project.id, status='approved')
    state = dict(project=project, page=page, source=source, calls=[], rights=True,
        visible=True, preview_authorized=True, saved=None)
    monkeypatch.setattr(chart_feeds.db, 'get_project', lambda key: project if key in (project.id, project.slug) else None)
    monkeypatch.setattr(chart_feeds.db, 'get_project_page', lambda key: state['page'])
    monkeypatch.setattr(chart_feeds.db, 'get_data_source', lambda key: source if key == source.id else None)
    monkeypatch.setattr(tk, 'check_access', lambda *a: None)
    monkeypatch.setattr(tk, '_', lambda value: value)
    def require(context, current):
        assert context['user'] is None and 'ignore_auth' not in context and 'auth_user_obj' not in context
        if not state['rights']:
            raise tk.ObjectNotFound('Restricted source')
    monkeypatch.setattr(data.data_access, 'require', require)
    monkeypatch.setattr(data.data_access, 'dashboard', lambda current: {
        'schema': {'fields': [{'name': 'ph', 'type': 'number'}, {'name': 'site', 'type': 'site'},
                              {'name': 'weather', 'type': 'single_select'}]},
        'rows': [{'date': '2026-11-01', 'answers': {'ph': 7, 'site': 'Public A', 'weather': 'Sunny'}}]})
    def get_action(name):
        if name == 'csunesco_project_show':
            def show(context, args):
                assert context['user'] is None and args == {'id': project.id}
                if not state['visible']:
                    raise tk.ObjectNotFound('Hidden project')
                return {'id': project.id, 'slug': project.slug}
            return show
        assert name == 'csunesco_data_source_series'
        def series(context, args):
            state['calls'].append(copy.deepcopy(args))
            return data.csunesco_data_source_series(context, args)
        return series
    monkeypatch.setattr(tk, 'get_action', get_action)
    monkeypatch.setattr(snapshots, 'read_private', lambda kind, key: state['saved'])
    def authorize(saved, current):
        assert saved is state['saved'] and current is project
        if not state['preview_authorized']:
            raise tk.NotAuthorized('Revoked preview')
    monkeypatch.setattr(portal, 'authorize_saved_preview', authorize)
    monkeypatch.setattr(tk, 'url_for', url_for)
    app = Flask(__name__)
    app.add_url_rule('/project/<slug>/chart/<block_id>/series', 'csunesco.project_chart_series', chart_feeds.project_chart_series)
    app.add_url_rule('/portal/preview/<ticket>/chart/<block_id>/series', 'csunesco.preview_chart_series', chart_feeds.preview_chart_series)
    state.update(app=app, client=app.test_client())
    return state


def public_url(state):
    return '/project/river/chart/river-chart/series?revision=' + chart_feeds.revision(state['page'].published_json)


def test_published_feed_uses_stored_filters_and_anonymous_rights_even_with_extra_input(feed_case):
    s = feed_case
    response = s['client'].get(public_url(s) + '&definition={}&id=other&project_id=other&sites=Public+A',
        json={'definition': {'mode': 'count'}, 'history_sources': [{'id': 'other'}]},
        headers={'Authorization': 'caller-must-not-be-forwarded'})
    assert response.status_code == 200
    assert response.json['used_rows'] == 0  # Keeping the stored filters never broadens the view.
    assert s['calls'][0]['definition']['filters']['search'] == 'PRIVATE_SEARCH_EXAMPLE'
    assert s['calls'][0]['project_id'] == s['project'].id
    assert s['calls'][0]['history_sources'] == []
    assert response.headers['Cache-Control'] == 'no-store'
    assert 'PRIVATE_' not in response.get_data(as_text=True)
    assert s['client'].post(public_url(s), json={}).status_code == 405


@pytest.mark.parametrize('reason', ['missing_revision', 'old_revision', 'unpublished', 'unapproved',
                                  'withdrawn', 'project_hidden', 'source_revoked', 'source_unapproved', 'wrong_project'])
def test_published_feed_fails_closed(feed_case, reason):
    s = feed_case; url = public_url(s)
    if reason == 'missing_revision': url = url.split('?')[0]
    if reason == 'old_revision': s['page'].published_json = json.dumps([dict(chart(), title='A newly published chart')])
    if reason == 'unpublished': s['page'].published_json = None
    if reason == 'unapproved': s['project'].status = 'pending'
    if reason == 'withdrawn': s['project'].extras = '{"_portal_withdrawn":true}'
    if reason == 'project_hidden': s['visible'] = False
    if reason == 'source_revoked': s['rights'] = False
    if reason == 'source_unapproved': s['source'].status = 'rejected'
    if reason == 'wrong_project': s['source'].project_id = 'another-project'
    response = s['client'].get(url)
    assert response.status_code == 404
    assert response.headers['Cache-Control'] == 'no-store'


@pytest.mark.parametrize('reason', ['chart_hidden', 'parent_hidden', 'source_deselected', 'duplicate_id', 'not_chart'])
def test_nested_block_checks_match_public_rendering(feed_case, reason):
    s = feed_case; item = chart()
    parent = dict(id='data', type='builtin_data', source_ids=['source'], charts=[item])
    items = [parent]
    if reason == 'chart_hidden': item['hidden'] = True
    if reason == 'parent_hidden': parent['hidden'] = True
    if reason == 'source_deselected': parent['source_ids'] = []
    if reason == 'duplicate_id': items.append(chart())
    if reason == 'not_chart': parent['charts'] = []; items.append(dict(id='river-chart', type='rich_text'))
    s['page'].published_json = json.dumps(items)
    assert s['client'].get(public_url(s)).status_code == 404


def test_nested_chart_and_frozen_preview_reuse_the_same_source_checks(feed_case):
    s = feed_case
    nested = [dict(id='data', type='builtin_data', source_ids=['source'], charts=[chart()])]
    s['page'].published_json = json.dumps(nested)
    assert s['client'].get(public_url(s)).status_code == 200
    s['saved'] = dict(project_id=s['project'].id, expires=time.time() + 60,
        candidate={'blocks': copy.deepcopy(nested)})
    s['page'].published_json = '[]'
    response = s['client'].get('/portal/preview/ticket/chart/river-chart/series?definition={}&id=other')
    assert response.status_code == 200 and response.json['used_rows'] == 0
    assert response.headers['Cache-Control'] == 'no-store'
    assert s['calls'][-1]['definition'] == {key: value for key, value in chart()['definition'].items()
        if key not in {'presentation', 'template', 'title', 'caption'}}
    assert s['saved']['candidate']['blocks'] == nested


@pytest.mark.parametrize('mode', ['numeric', 'count', 'category'])
def test_block_feed_matches_the_app_calculation_query_without_mutating_snapshot(feed_case, mode):
    s = feed_case; item = chart()
    item['definition'] = chart_definition.normalize(dict(version=2, title='Date (UTC)', caption='Display only',
        mode=mode, chart='bar', field='weather' if mode == 'category' else 'ph',
        sites=['Public A'], filters={'sites': ['Public A'], 'facets': {'weather': ['Sunny']}, 'search': 'Public'},
        presentation={'axisTitles': {'x': 'Date (UTC)'}}, template={'id': 'trend', 'version': 1}))
    s['page'].published_json = original = json.dumps([item])
    query = {key: value for key, value in item['definition'].items()
             if key not in {'presentation', 'template', 'title', 'caption'}}
    expected = chart_definition.series(query, data.data_access.dashboard(s['source']))
    expected['data_source_id'] = s['source'].id
    response = s['client'].get(public_url(s))
    assert response.status_code == 200 and response.json == expected
    assert response.json['used_rows'] == 1
    assert s['calls'][-1]['definition'] == query
    assert s['calls'][-1]['history_sources'] == item['history_sources']
    assert s['page'].published_json == original


@pytest.mark.parametrize('reason', ['expired', 'revoked_grant', 'source_revoked', 'hidden_nested', 'wrong_scope', 'missing_ticket'])
def test_preview_ticket_does_not_authorize_nonpublic_measurements(feed_case, reason):
    s = feed_case
    s['saved'] = dict(project_id=s['project'].id, expires=time.time() + 60,
        candidate={'blocks': [dict(id='data', type='builtin_data', charts=[chart()])]})
    if reason == 'expired': s['saved']['expires'] = time.time() - 1
    if reason == 'revoked_grant': s['preview_authorized'] = False
    if reason == 'source_revoked': s['rights'] = False
    if reason == 'hidden_nested': s['saved']['candidate']['blocks'][0]['charts'][0]['hidden'] = True
    if reason == 'wrong_scope': s['saved']['scope'] = 'site'
    if reason == 'missing_ticket': s['saved'] = None
    response = s['client'].get('/portal/preview/ticket/chart/river-chart/series')
    assert response.status_code == 404 and response.headers['Cache-Control'] == 'no-store'


def test_public_read_dto_hides_selectors_but_authorized_editor_round_trips(feed_case, monkeypatch):
    s = feed_case; original = s['page'].published_json
    monkeypatch.setattr(page_actions.auth, 'can_manage_project', lambda context, project_id: context.get('user') == 'manager')
    monkeypatch.setattr(page_actions.db, 'page_dictize', lambda page, **kw: {
        'published_blocks': json.loads(page.published_json), 'project_id': page.project_id})
    public = page_actions.csunesco_project_page_show({'user': None}, {'project_id': s['project'].id})
    editor = page_actions.csunesco_project_page_show({'user': 'manager'}, {'project_id': s['project'].id})
    assert 'PRIVATE_' not in json.dumps(public)
    assert public['published_revision_hash'] == editor['published_revision_hash'] == chart_feeds.revision(original)
    assert editor['published_blocks'][0]['definition'] == chart()['definition']
    assert s['page'].published_json == original
    assert all(not key.startswith('_chart') for key in editor['published_blocks'][0])
    assert chart_definition.normalize(editor['published_blocks'][0]['definition']) == chart()['definition']


def test_native_preview_creates_one_frozen_ticket_for_v2_only(feed_case, monkeypatch):
    s = feed_case; tickets = []
    def create(project, candidate, actor, revision):
        tickets.append(copy.deepcopy((candidate, actor)))
        return {'ticket': 'native-preview-ticket'}
    monkeypatch.setattr(portal, 'create_preview_ticket', create)
    ctx = dict(project={'id': s['project'].id, 'slug': 'river'}, preview=True, can_manage=True)
    items = [chart()]
    with s['app'].test_request_context():
        chart_feeds.prepare_context({'user': 'manager'}, ctx, items)
        feed = helpers.csunesco_chart_feed(items[0], ctx)
    assert '/preview/native-preview-ticket/' in feed['url']
    assert 'PRIVATE_' not in json.dumps(feed)
    assert tickets[0][1] == {'username': 'manager'}
    items[0]['definition']['sites'] = []
    assert tickets[0][0]['blocks'][0]['definition']['sites'] == ['PRIVATE_SITE_EXAMPLE']
    # The same ticket also remains valid at the existing base preview URL.
    s['saved'] = dict(project_id=s['project'].id, expires=time.time() + 60,
        actor=tickets[0][1], candidate=tickets[0][0])
    from ckanext.csunesco.logic import page_render
    from ckanext.csunesco.logic.action import projects
    monkeypatch.setattr(chart_feeds.db, 'project_dictize', lambda project: {'id': project.id, 'slug': project.slug})
    monkeypatch.setattr(projects, '_stats_dict', lambda project_id: {})
    monkeypatch.setattr(page_render, 'build_context', lambda *args, **kw: {})
    monkeypatch.setattr(tk, 'render', lambda *args, **kw: 'Authorized chart preview')
    monkeypatch.setitem(tk.config, 'ckanext.csunesco.portal_preview_origin', 'https://app.example.test')
    with s['app'].test_request_context():
        response = portal.preview_view('native-preview-ticket')
    assert response.status_code == 200 and response.get_data(as_text=True) == 'Authorized chart preview'
    legacy = chart(); legacy['definition']['version'] = 1
    chart_feeds.prepare_context({'user': 'manager'}, ctx, [legacy])
    assert len(tickets) == 1 and helpers.csunesco_chart_feed(legacy, ctx) is None


def test_actual_chart_html_contains_only_display_settings_and_revision_url(feed_case, monkeypatch):
    from jinja2 import Environment, FileSystemLoader
    from markupsafe import Markup
    from ckan.lib import jinja_extensions
    s = feed_case; item = chart()
    item['history_sources'] = [{'id': 'SECRET_HISTORY_ID', 'mappings': {'PRIVATE_COLUMN': 'ph'}}]
    ctx = dict(project={'id': s['project'].id, 'slug': 'river'}, preview=False,
        chart_revision=chart_feeds.revision(s['page'].published_json),
        approved_sources={'source': {'id': 'source', 'can_view_resources': True, 'can_download': False}})
    env = Environment(autoescape=True, loader=FileSystemLoader(str(Path(__file__).parents[1] / 'templates')),
        extensions=[jinja_extensions.SnippetExtension])
    env.globals.update(_=lambda text: text, h=SimpleNamespace(url_for=url_for,
        csunesco_chart_feed=helpers.csunesco_chart_feed, csunesco_block_type=lambda name: SimpleNamespace(label=name)))
    monkeypatch.setattr(jinja_extensions.base, 'render_snippet', lambda template, **values: Markup(env.get_template(template).render(**values)))
    with s['app'].test_request_context():
        chart_feeds.prepare_context({'user': None}, ctx, [item])
        html = env.get_template('csunesco/blocks/chart.html').render(block=item, ctx=ctx)
    assert 'PRIVATE_' not in html and 'SECRET_HISTORY_ID' not in html
    assert 'data-history-sources' not in html and 'data-group-by' not in html
    assert 'data-server-resolved="true"' in html and 'revision=' in html
    assert 'River quality' in html
    assert 'PRIVATE_' in json.dumps(item)  # Rendering never mutates the stored configuration.


def test_v2_without_snapshot_url_never_returns_raw_selectors():
    feed = helpers.csunesco_chart_feed(chart(), {})
    assert feed['url'] is None and 'PRIVATE_' not in json.dumps(feed)


@pytest.mark.parametrize('identifier', ['A', '0', 'a_b-c', 'a' * 64, '_invalid', 'a' * 65])
def test_feed_identifiers_match_the_existing_block_contract(identifier):
    from ckanext.csunesco.logic import blocks
    assert bool(chart_feeds._ID.fullmatch(identifier)) == bool(blocks._ID_RE.fullmatch(identifier))


def test_legacy_public_block_definitions_stay_unchanged():
    item = chart(); item['definition']['version'] = 1
    assert chart_feeds.public_blocks([item]) == [item]
