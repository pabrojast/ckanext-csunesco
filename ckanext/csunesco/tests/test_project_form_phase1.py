# encoding: utf-8
"""Spec phase-1 form completion: strict-vs-lenient split, drafts, new fields.

What these pin down, in contract order:

* the STRICT form schema requires exactly the spec's starred fields, while
  the lenient action schema keeps accepting the CS Toolbox outbox payload
  (that half lives in ``test_ofform_contract.py``);
* keywords carry 2-3 entries in the strict schema and at most 3 anywhere;
* ``participation_mode`` and ``open_participation`` derive each other, both
  ways, so the web form and the app contract stay coherent;
* a point+radius triple materializes a region polygon the region validator
  itself accepts -- and an explicit region always wins;
* drafts: the view flag creates ``status='draft'``, drafts stay out of the
  public listing, and resubmit moves draft -> pending;
* the editors list syncs ``role='editor'`` member rows (add + remove) and an
  unknown username is a hard error;
* choice-list validators reject unknown options and honour ``allow_other``.

Same harness as ``test_project_update``: in-memory SQLite bound to the
plugin's scoped ``Session``, ``tk.check_access`` neutralized.
"""
import json

import pytest

try:
    import sqlalchemy as sa
    import ckan.plugins.toolkit as tk
    import ckan.model as model
    import ckan  # noqa: F401
    from ckanext.csunesco import constants, db
    from ckanext.csunesco.logic import auth as cs_auth
    from ckanext.csunesco.logic import schema as cs_schema
    from ckanext.csunesco.logic import validators as v
    from ckanext.csunesco.logic.action import projects as projects_action
    HAVE_CKAN = True
except Exception:  # pragma: no cover - environment without CKAN
    HAVE_CKAN = False

pytestmark = pytest.mark.skipif(
    not HAVE_CKAN, reason="requires CKAN (ckan.model + sqlalchemy)")


class _User(object):
    is_anonymous = False

    def __init__(self, user_id):
        self.id = user_id


def _ctx(user_id='pm-1', **extra):
    context = {'user': user_id, 'auth_user_obj': _User(user_id)}
    context.update(extra)
    return context


@pytest.fixture
def session():
    engine = sa.create_engine('sqlite://')
    db.ensure_mappers()
    from ckan.model.group import group_table, member_table
    db.metadata.create_all(
        bind=engine, tables=list(db._ALL_TABLES) + [group_table, member_table])
    db.Session.remove()
    db.Session.configure(bind=engine)
    try:
        yield db.Session
    finally:
        db.Session.remove()
        engine.dispose()


@pytest.fixture
def actions(session, monkeypatch):
    monkeypatch.setattr(tk, 'check_access', lambda *a, **k: True)
    monkeypatch.setattr(cs_auth, '_is_sysadmin', lambda context: False)
    monkeypatch.setattr(cs_auth, 'can_manage_project',
                        lambda context, project_id: True)
    return projects_action


# --------------------------------------------------------------------------- #
# The strict/lenient schema split                                             #
# --------------------------------------------------------------------------- #

STRICT_REQUIRED = (
    'title', 'organization_id', 'short_description', 'keywords', 'water_type',
    'water_data_type', 'geographic_extent', 'countries',
    'participation_mode', 'activity_status', 'lead_partner_type',
    'lead_organisation',
)


def test_the_form_schema_requires_the_spec_starred_fields():
    data, errors = tk.navl_validate(
        {}, cs_schema.project_request_form_schema(), {'model': model})
    for field in STRICT_REQUIRED:
        assert field in errors, 'form schema does not require %r' % field


def test_the_action_schema_requires_only_the_title():
    data, errors = tk.navl_validate(
        {}, cs_schema.project_request_schema(), {'model': model})
    assert set(errors) == {'title'}


def test_keywords_need_two_to_three_in_the_form_schema():
    schema = {'keywords': cs_schema.project_request_form_schema()['keywords']}
    _, errors = tk.navl_validate({'keywords': 'water'}, schema, {})
    assert 'keywords' in errors
    _, errors = tk.navl_validate({'keywords': 'water, river'}, schema, {})
    assert not errors
    _, errors = tk.navl_validate(
        {'keywords': 'a, b, c, d'}, schema, {})
    assert 'keywords' in errors


# --------------------------------------------------------------------------- #
# participation_mode <-> open_participation                                   #
# --------------------------------------------------------------------------- #

def test_participation_mode_derives_the_boolean():
    data = {'participation_mode': 'open'}
    projects_action._sync_participation(data)
    assert data['open_participation'] is True
    data = {'participation_mode': 'limited'}
    projects_action._sync_participation(data)
    assert data['open_participation'] is False


def test_the_apps_boolean_derives_the_mode():
    data = {'open_participation': False}
    projects_action._sync_participation(data)
    assert data['participation_mode'] == 'limited'
    data = {'open_participation': True}
    projects_action._sync_participation(data)
    assert data['participation_mode'] == 'open'


# --------------------------------------------------------------------------- #
# Point + radius                                                              #
# --------------------------------------------------------------------------- #

def test_point_radius_materializes_a_valid_region():
    data = {'point_lat': -33.45, 'point_lng': -70.66, 'point_radius_km': 25.0}
    projects_action._apply_point_radius(data)
    raw = data['region_geojson']
    # The synthesized polygon must satisfy the project's OWN region validator.
    assert v.csunesco_valid_geojson(raw) == raw
    parsed = json.loads(raw)
    assert parsed['geometry']['type'] == 'Polygon'
    assert parsed['properties']['csunesco_point_radius_km'] == 25.0
    ring = parsed['geometry']['coordinates'][0]
    assert ring[0] == ring[-1]          # closed ring
    assert len(ring) == 49              # 48 segments + closing vertex


def test_an_explicit_region_beats_the_point(session):
    explicit = json.dumps({'type': 'Polygon', 'coordinates': [[[0, 0]]]})
    data = {'point_lat': 1.0, 'point_lng': 2.0, 'point_radius_km': 3.0,
            'region_geojson': explicit}
    projects_action._apply_point_radius(data)
    assert data['region_geojson'] == explicit


# --------------------------------------------------------------------------- #
# Drafts                                                                      #
# --------------------------------------------------------------------------- #

def test_the_draft_flag_creates_a_draft(actions, session):
    created = actions.csunesco_project_request_create(
        _ctx(csunesco_draft=True), {'title': 'Half-finished idea'})
    row = db.get_project(created['id'])
    assert row.status == 'draft'


def test_the_api_path_still_files_pending(actions, session):
    created = actions.csunesco_project_request_create(
        _ctx(), {'title': 'Straight to review'})
    assert db.get_project(created['id']).status == 'pending'


def test_drafts_stay_out_of_the_public_listing(actions, session):
    actions.csunesco_project_request_create(
        _ctx(csunesco_draft=True), {'title': 'Hidden draft'})
    listing = actions.csunesco_project_list(_ctx('someone-else'), {})
    assert listing['count'] == 0


def test_resubmit_moves_a_draft_to_pending(actions, session, monkeypatch):
    monkeypatch.setattr(cs_auth, 'can_edit_project_details',
                        lambda context, project: True)
    created = actions.csunesco_project_request_create(
        _ctx(csunesco_draft=True), {'title': 'Ready now'})
    out = actions.csunesco_project_resubmit(_ctx(), {'id': created['id']})
    assert out['status'] == 'pending'


# --------------------------------------------------------------------------- #
# Editors                                                                     #
# --------------------------------------------------------------------------- #

def _fake_users(monkeypatch, known):
    monkeypatch.setattr(model.User, 'get', staticmethod(
        lambda name: (type('U', (), {'id': 'uid-' + name})()
                      if name in known else None)))


def _editor_rows(session, project_id):
    return sorted(
        member.user_id
        for member in session.query(db.CsProjectMember)
        .filter(db.CsProjectMember.project_id == project_id)
        .filter(db.CsProjectMember.role == 'editor').all())


def test_editors_create_member_rows(actions, session, monkeypatch):
    _fake_users(monkeypatch, {'ana', 'luis'})
    created = actions.csunesco_project_request_create(
        _ctx(), {'title': 'With editors', 'editors': ['ana', 'luis']})
    assert _editor_rows(session, created['id']) == ['uid-ana', 'uid-luis']


def test_editors_sync_removes_the_dropped_one(actions, session, monkeypatch):
    _fake_users(monkeypatch, {'ana', 'luis'})
    created = actions.csunesco_project_request_create(
        _ctx(), {'title': 'With editors', 'editors': ['ana', 'luis']})
    actions.csunesco_project_update(
        _ctx(), {'id': created['id'], 'editors': ['ana']})
    assert _editor_rows(session, created['id']) == ['uid-ana']


def test_an_unknown_editor_is_a_hard_error(actions, session, monkeypatch):
    _fake_users(monkeypatch, {'ana'})
    with pytest.raises(tk.ValidationError):
        actions.csunesco_project_request_create(
            _ctx(), {'title': 'Bad editor', 'editors': ['ana', 'ghost']})


# --------------------------------------------------------------------------- #
# Choice validators                                                           #
# --------------------------------------------------------------------------- #

def test_choice_list_rejects_unknown_options():
    validator = v.csunesco_choice_list(constants.ACTIVITY_STATUSES)
    assert validator(['Active']) == ['Active']
    with pytest.raises(tk.Invalid):
        validator(['Dormant'])


def test_allow_other_accepts_custom_entries():
    validator = v.csunesco_choice_list(constants.WATER_TYPES,
                                       allow_other=True)
    assert validator(['River', 'Glacial melt']) == ['River', 'Glacial melt']


def test_string_list_accepts_commas_json_and_lists():
    assert v.csunesco_valid_string_list('a, b') == ['a', 'b']
    assert v.csunesco_valid_string_list('["a", "b"]') == ['a', 'b']
    assert v.csunesco_valid_string_list(['a', 'a', '']) == ['a']
    assert v.csunesco_valid_string_list('') == []


def test_new_fields_round_trip_through_extras(actions, session):
    created = actions.csunesco_project_request_create(_ctx(), {
        'title': 'Fully specified',
        'keywords': 'water, quality',
        'water_type': ['River', 'Stream'],
        'water_data_type': ['Water quantity'],
        'geographic_extent': 'National',
        'locality': 'Valle del Maipo',
        'participation_mode': 'limited',
        'allowed_participants': 'ana@example.org, luis',
        'languages': 'Spanish, English',
        'stakeholders': ['Citizens', 'Researchers'],
        'activity_status': ['Active'],
        'lead_partner_type': 'University',
        'lead_organisation': 'Hydrology Lab',
        'funding_body': ['Flanders'],
        'funding_programme': 'CS 2026',
        'international_frameworks': ['SDG 6: Clean Water and Sanitation'],
    })
    out = db.project_dictize(db.get_project(created['id']))
    assert out['keywords'] == ['water', 'quality']
    assert out['water_type'] == ['River', 'Stream']
    assert out['geographic_extent'] == 'National'
    assert out['participation_mode'] == 'limited'
    assert out['open_participation'] is False
    assert out['allowed_participants'] == ['ana@example.org', 'luis']
    assert out['lead_organisation'] == 'Hydrology Lab'
    assert out['international_frameworks'] == [
        'SDG 6: Clean Water and Sanitation']


# --------------------------------------------------------------------------- #
# Explorer facets over extras (Fase 5)                                        #
# --------------------------------------------------------------------------- #

def _approved(actions, session, title, **fields):
    created = actions.csunesco_project_request_create(
        _ctx(), dict({'title': title}, **fields))
    row = db.get_project(created['id'])
    row.status = 'approved'
    session.commit()
    return created


def test_extras_facets_filter_the_listing(actions, session):
    _approved(actions, session, 'River project',
              water_type=['River'], activity_status=['Active'])
    _approved(actions, session, 'Pond project',
              water_type=['Pond'], activity_status=['On hold'])

    out = actions.csunesco_project_list(_ctx(), {'water_type': 'River'})
    assert [p['title'] for p in out['results']] == ['River project']
    assert out['count'] == 1
    assert out['applied_filters']['water_type'] == 'River'

    out = actions.csunesco_project_list(
        _ctx(), {'activity_status': 'On hold'})
    assert [p['title'] for p in out['results']] == ['Pond project']

    # Combined facets AND together.
    out = actions.csunesco_project_list(
        _ctx(), {'water_type': 'River', 'activity_status': 'On hold'})
    assert out['count'] == 0


def test_facet_filtering_never_leaks_the_region(actions, session):
    _approved(actions, session, 'Mapped project',
              water_type=['River'],
              region_geojson='{"type": "Polygon", "coordinates": []}')
    out = actions.csunesco_project_list(_ctx(), {'water_type': 'River'})
    assert out['results'] and 'region_geojson' not in out['results'][0]


# --------------------------------------------------------------------------- #
# Decision notifications (Fase 5): wired AFTER the commit, best-effort        #
# --------------------------------------------------------------------------- #

def test_approve_notifies_the_creator(actions, session, monkeypatch):
    from ckanext.csunesco.logic import notify
    calls = []
    monkeypatch.setattr(
        notify, 'notify_project_decision',
        lambda user_id, title, approved, reason=None: calls.append(
            (user_id, title, approved, reason)))
    created = actions.csunesco_project_request_create(
        _ctx('author-1'), {'title': 'Tell me when'})
    actions.csunesco_project_approve(_ctx('reviewer-1'),
                                     {'id': created['id']})
    assert calls == [('author-1', 'Tell me when', True, None)]


def test_reject_notifies_with_the_reason(actions, session, monkeypatch):
    from ckanext.csunesco.logic import notify
    calls = []
    monkeypatch.setattr(
        notify, 'notify_project_decision',
        lambda user_id, title, approved, reason=None: calls.append(
            (approved, reason)))
    created = actions.csunesco_project_request_create(
        _ctx('author-1'), {'title': 'Not this time'})
    actions.csunesco_project_reject(
        _ctx('reviewer-1'), {'id': created['id'], 'reason': 'Out of scope'})
    assert calls == [(False, 'Out of scope')]
