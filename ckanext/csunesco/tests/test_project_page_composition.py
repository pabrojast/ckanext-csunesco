"""The actual landing and its snippets render each shared field once."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import Environment, ChoiceLoader, DictLoader, FileSystemLoader
from markupsafe import Markup
from ckan.lib.jinja_extensions import _get_extensions
from ckan.lib import base
from ckanext.csunesco.logic import blocks, page_render, portal, helpers


@pytest.fixture
def render_project(monkeypatch):
    root = Path(__file__).parents[1] / 'templates'
    env = Environment(autoescape=True, extensions=_get_extensions(), loader=ChoiceLoader([
        DictLoader({'csunesco/base.html': '{% block cs_content %}{% endblock %}'}),
        FileSystemLoader(str(root)),
    ]))
    helper = SimpleNamespace(
        url_for=lambda *a, **kw: '/project', check_access=lambda *a: False,
        csunesco_member_state_titles=lambda values: [SimpleNamespace(title=v) for v in values],
        csunesco_portal_field_value=helpers.csunesco_portal_field_value,
        csunesco_portal_public_field=helpers.csunesco_portal_public_field,
        csunesco_field_audience_ok=lambda field, project: field not in ('workplan', 'local_govt_engagement', 'indigenous_knowledge', 'indigenous_knowledge_notes', 'duration_of_involvement'),
        csunesco_block_type=lambda kind: blocks.BLOCK_TYPES.get(kind),
        csunesco_icon=lambda *a: '',
    )
    env.globals.update(_=lambda text, **kw: text % kw if kw else text, h=helper)
    monkeypatch.setattr(base, 'render_snippet', lambda name, **kw: Markup(env.get_template(name).render(**kw)))

    def render(project, candidate, participant=False):
        project = dict(id='river', slug='river', title='River', **project)
        candidate = page_render.visible_blocks(page_render.project_blocks(project, candidate))
        data = page_render.project_display_data(project)
        ctx = dict(project=data, contacts={'contact_email': 'contact@example.test'} if participant else {},
                   can_manage=False, preview=False, section_types={b['type'] for b in candidate})
        if participant:
            helper.csunesco_field_audience_ok = lambda *a: True
        return env.get_template('csunesco/project_landing.html').render(
            project=project, blocks=candidate, ctx=ctx, is_draft_preview=False)
    return render


def test_legacy_landing_has_one_about_and_preserves_distinct_details(render_project):
    project = dict(short_description='A unique river description', countries=['Belgium'],
                   open_participation=True, start_date='2026-10-01',
                   target_group='Students', how_to_participate='Take a reading',
                   project_document_url='https://example.test/report',
                   contact_email='contact@example.test', structure={'aim': 'Protect rivers'},
                   workplan=[{'title': 'Private milestone', 'status': 'upcoming'}])
    candidate = [blocks.normalize_block({'id': 'about', 'type': 'builtin_about'}),
                 blocks.normalize_block({'id': 'custom', 'type': 'rich_text', 'html': '<p>Keep custom text</p>'})]
    html = render_project(project, candidate)
    assert html.count('>About this project<') == 1
    for value in ('A unique river description', 'Belgium', '2026-10-01', 'Students', 'Take a reading', 'Protect rivers', 'Keep custom text'):
        assert html.count(value) == 1, value
    assert 'https://example.test/report' in html
    assert 'contact@example.test' not in html and 'Private milestone' not in html
    participant = render_project(project, candidate, participant=True)
    assert 'contact@example.test' in participant and participant.count('Private milestone') == 1


def test_hidden_sections_and_empty_compositions_do_not_grow_fallbacks(render_project):
    project = dict(short_description='Hidden description', project_document_url='https://example.test/hidden',
                   structure={'aim': 'Hidden objective'})
    candidate = [{'id': kind, 'type': kind, 'hidden': True} for kind in ('builtin_about', 'project_facts', 'project_structure')]
    for value in (candidate, []):
        html = render_project(project, value)
        assert 'Hidden description' not in html and 'Hidden objective' not in html
        assert 'https://example.test/hidden' not in html
        assert 'About this project' not in html
    assert len(candidate) == 3


def test_app_sections_do_not_reintroduce_legacy_values(render_project):
    project = dict(portal_managed=True, short_description='', landing_content='Old description',
                   start_date='2026-10-01', target_group='Old target', how_to_participate='Old instructions',
                   structure={'timeframe_start': None, 'target_groups': [], 'how_to_participate': ''})
    candidate = [{'id': 'about', 'type': 'builtin_about', 'html': ''},
                 {'id': 'structure', 'type': 'project_structure'}]
    html = render_project(project, candidate)
    for value in ('Old description', '2026-10-01', 'Old target', 'Old instructions'):
        assert value not in html
    assert 'Project structure' not in html  # no empty card


def test_presentation_does_not_modify_saved_blocks_or_project():
    project = {'project_document_url': 'https://example.test/doc', 'start_date': '2026-10-01'}
    candidate = [{'id': 'about', 'type': 'builtin_about', 'title': 'Our river'}]
    composed = page_render.project_blocks(project, candidate)
    assert len(composed) == 2 and len(candidate) == 1
    data = page_render.project_display_data(project)
    assert data['structure']['timeframe_start'] == '2026-10-01'
    assert 'structure' not in project
    assert composed[0]['title'] == 'Our river'


def test_pending_first_app_publication_still_renders_old_approved_page(render_project):
    html = render_project(dict(portal_managed=True, portal_published=False,
                               short_description='Approved legacy description',
                               project_document_url='https://example.test/approved-document'),
                          [blocks.normalize_block({'type': 'builtin_about'})])
    assert html.count('Approved legacy description') == 1
    assert 'https://example.test/approved-document' in html
