# encoding: utf-8
"""The phase-2 structure mirror (csunesco_project_structure_upsert).

What these pin down:

* the snapshot lands under ``extras['structure']`` / ``extras['workplan']``
  and ``project_dictize`` merges both top-level;
* REPLACE semantics: a second push without a field removes it;
* free text is tag-stripped and capped; unknown keys are dropped;
* the workplan keeps unscheduled steps (no ``starts_on``) and normalizes bad
  kinds/statuses instead of failing;
* an unknown project is ObjectNotFound.

Same SQLite harness as ``test_ofform_contract``.
"""
import json

import pytest

try:
    import sqlalchemy as sa
    import ckan.plugins.toolkit as tk
    import ckan  # noqa: F401
    from ckanext.csunesco import db
    from ckanext.csunesco.logic.action import structure as structure_action
    HAVE_CKAN = True
except Exception:  # pragma: no cover - environment without CKAN
    HAVE_CKAN = False

pytestmark = pytest.mark.skipif(
    not HAVE_CKAN, reason="requires CKAN (ckan.model + sqlalchemy)")


class _User(object):
    is_anonymous = False

    def __init__(self, user_id):
        self.id = user_id


def _ctx():
    return {'user': 'cs-toolbox-service',
            'auth_user_obj': _User('cs-toolbox-service')}


@pytest.fixture
def session(monkeypatch):
    engine = sa.create_engine('sqlite://')
    db.ensure_mappers()
    from ckan.model.group import group_table, member_table
    db.metadata.create_all(
        bind=engine, tables=list(db._ALL_TABLES) + [group_table, member_table])
    db.Session.remove()
    db.Session.configure(bind=engine)
    monkeypatch.setattr(tk, 'check_access', lambda *a, **k: True)
    try:
        yield db.Session
    finally:
        db.Session.remove()
        engine.dispose()


@pytest.fixture
def project(session):
    row = db.CsProject()
    row.slug = 'douro-basin'
    row.title = 'Douro Basin'
    row.status = 'approved'
    session.add(row)
    session.commit()
    return row


def test_snapshot_lands_in_extras_and_dictizes(session, project):
    out = structure_action.csunesco_project_structure_upsert(_ctx(), {
        'project_slug': 'douro-basin',
        'structure': {
            'aim': 'Track river health with schools.',
            'focus_areas': ['Community Engagement and Awareness'],
            'engagement_level': 'Participatory',
            'water_parameters': {'Physical water quality': ['pH']},
            'local_govt_engagement': True,
            'timeframe_start': '2026-09-01',
        },
        'workplan': [
            {'title': '1. Understand the Context and Challenges',
             'kind': 'milestone', 'status': 'upcoming', 'position': 0},
        ],
    })
    assert out['structure']['aim'] == 'Track river health with schools.'

    dictized = db.project_dictize(db.get_project(project.id))
    assert dictized['structure']['engagement_level'] == 'Participatory'
    assert dictized['structure']['local_govt_engagement'] is True
    assert dictized['workplan'][0]['title'].startswith('1. Understand')
    # Unscheduled: no starts_on key at all.
    assert 'starts_on' not in dictized['workplan'][0]


def test_replace_semantics_drop_cleared_fields(session, project):
    structure_action.csunesco_project_structure_upsert(_ctx(), {
        'project_slug': 'douro-basin',
        'structure': {'aim': 'First version.', 'training_level': 'Other'},
        'workplan': [{'title': 'Step A'}],
    })
    structure_action.csunesco_project_structure_upsert(_ctx(), {
        'project_slug': 'douro-basin',
        'structure': {'aim': 'Second version.'},
        'workplan': [],
    })
    dictized = db.project_dictize(db.get_project(project.id))
    assert dictized['structure'] == {'aim': 'Second version.'}
    assert 'workplan' not in dictized


def test_text_is_tag_stripped_and_capped(session, project):
    structure_action.csunesco_project_structure_upsert(_ctx(), {
        'project_slug': 'douro-basin',
        'structure': {
            'aim': '<script>alert(1)</script>Legit text ' + 'x' * 5000,
            'unknown_key': 'dropped',
        },
    })
    dictized = db.project_dictize(db.get_project(project.id))
    aim = dictized['structure']['aim']
    assert '<script>' not in aim
    assert aim.startswith('alert(1)Legit text') or aim.startswith('Legit')
    assert len(aim) <= structure_action.MAX_TEXT
    assert 'unknown_key' not in dictized['structure']


def test_bad_workplan_values_normalize(session, project):
    structure_action.csunesco_project_structure_upsert(_ctx(), {
        'project_slug': 'douro-basin',
        'workplan': [
            {'title': 'Odd one', 'kind': 'sprint', 'status': 'someday',
             'starts_on': 'not-a-date'},
            {'title': ''},                       # dropped: no title
            'not-a-dict',                        # dropped: wrong shape
        ],
    })
    dictized = db.project_dictize(db.get_project(project.id))
    assert len(dictized['workplan']) == 1
    step = dictized['workplan'][0]
    assert step['kind'] == 'step'
    assert step['status'] == 'upcoming'
    assert 'starts_on' not in step


def test_unknown_project_is_not_found(session):
    with pytest.raises(tk.ObjectNotFound):
        structure_action.csunesco_project_structure_upsert(
            _ctx(), {'project_slug': 'nowhere', 'structure': {'aim': 'x'}})


def test_existing_extras_survive_the_push(session, project):
    project.extras = json.dumps({'keywords': ['water', 'schools']})
    session.commit()
    structure_action.csunesco_project_structure_upsert(_ctx(), {
        'project_slug': 'douro-basin',
        'structure': {'aim': 'Keep my keywords.'},
    })
    dictized = db.project_dictize(db.get_project(project.id))
    assert dictized['keywords'] == ['water', 'schools']
    assert dictized['structure']['aim'] == 'Keep my keywords.'
