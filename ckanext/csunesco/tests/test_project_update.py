# encoding: utf-8
"""``csunesco_project_update`` and the project ``extras`` round-trip.

What these pin down, in contract order:

* the staged form's seven extra fields survive a create/read round-trip, and
  DATES come back as ISO strings -- they are validated into ``datetime``
  objects and stored in a JSON column, so a missing ``.isoformat()`` would
  raise "Object of type datetime is not JSON serializable" on the first save;
* ``open_participation=False`` PERSISTS as False rather than being dropped as
  "empty" (the shape of the silent un-feature bug that content already fixed);
* a partial update touches only the keys it was sent;
* editing NEVER changes moderation state -- an approved project stays
  approved, which is the deliberate divergence from ``content_update``;
* ``slug`` in the payload is ignored: a project's URL is permanent;
* a caller who cannot manage the project is refused even when ``check_access``
  lets them past (the action's own re-check).

Same environment as ``test_content_lifecycle``: fresh in-memory SQLite bound
to the plugin's scoped ``Session``, ``tk.check_access`` neutralized, the
authorization helper monkeypatched per test.
"""
import json

import pytest

try:
    import sqlalchemy as sa
    import ckan.plugins.toolkit as tk
    import ckan  # noqa: F401
    from ckanext.csunesco import db
    from ckanext.csunesco.logic import auth as cs_auth
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


def _ctx(user_id='pm-1'):
    return {'user': user_id, 'auth_user_obj': _User(user_id)}


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
    """Neutralize the web-stack pieces so the actions run on the bare ORM."""
    monkeypatch.setattr(tk, 'check_access', lambda *a, **k: True)
    monkeypatch.setattr(cs_auth, '_is_sysadmin', lambda context: False)
    monkeypatch.setattr(cs_auth, 'can_manage_project',
                        lambda context, project_id: True)
    # The country validator checks membership of the member-states group; this
    # module is about extras and moderation state, so keep countries empty and
    # let the dedicated test_member_states.py cover that relationship.
    return projects_action


FULL = {
    'title': 'Douro Basin',
    'initiative': 'riverwatch',
    'short_description': 'Freshwater monitoring.',
    'how_to_participate': 'Take a sample, photograph the label.',
    'start_date': '2026-07-16',
    'end_date': '2026-09-30',
    'open_participation': True,
    'target_group': 'Secondary schools along the river.',
    'contact_person': 'Ana Silva',
    'contact_email': 'ana@example.org',
}


def _create(actions, payload=None):
    return actions.csunesco_project_request_create(
        _ctx(), dict(payload if payload is not None else FULL))


def _row(project_id):
    return db.get_project(project_id)


def test_extras_round_trip(actions, session):
    created = _create(actions)
    out = db.project_dictize(_row(created['id']))
    assert out['how_to_participate'] == 'Take a sample, photograph the label.'
    assert out['target_group'] == 'Secondary schools along the river.'
    assert out['contact_person'] == 'Ana Silva'
    assert out['contact_email'] == 'ana@example.org'
    assert out['open_participation'] is True


def test_dates_are_stored_as_iso_strings_not_datetimes(actions, session):
    """The validator hands back ``datetime``; the column is JSON."""
    created = _create(actions)
    stored = json.loads(_row(created['id']).extras)
    assert stored['start_date'] == '2026-07-16'
    assert stored['end_date'] == '2026-09-30'
    assert isinstance(stored['start_date'], str)
    # And the whole blob must be re-serializable -- no datetime smuggled in.
    json.dumps(stored)


def test_open_participation_false_persists(actions, session):
    """False is a VALUE, not "empty": an unticked box must save as No."""
    payload = dict(FULL, open_participation=False)
    created = _create(actions, payload)
    out = db.project_dictize(_row(created['id']))
    assert out['open_participation'] is False


def test_create_still_accepts_a_payload_with_no_extras(actions, session):
    """The ofform outbox sends none of the seven; that must keep working."""
    created = _create(actions, {'title': 'Bare', 'initiative': 'riverwatch'})
    assert created['status'] == 'pending'
    assert json.loads(_row(created['id']).extras) == {}


def test_partial_update_leaves_other_extras_alone(actions, session):
    created = _create(actions)
    actions.csunesco_project_update(
        _ctx(), {'id': created['id'], 'title': 'Douro Basin (2027)'})
    out = db.project_dictize(_row(created['id']))
    assert out['title'] == 'Douro Basin (2027)'
    assert out['contact_email'] == 'ana@example.org'
    assert out['start_date'] == '2026-07-16'


def test_update_records_actor_time_and_changed_fields(actions, session):
    created = _create(actions)
    actions.csunesco_project_update(
        _ctx(), {'id': created['id'], 'title': 'Renamed',
                 'contact_person': 'A. Silva'})
    history = json.loads(_row(created['id']).extras)['edit_history']
    assert len(history) == 1
    assert history[0]['user_id'] == 'pm-1'
    assert history[0]['user_name'] == 'pm-1'
    assert history[0]['timestamp'].endswith('Z')
    assert history[0]['fields'] == ['contact_person', 'title']


def test_noop_update_does_not_add_audit_noise(actions, session):
    created = _create(actions)
    actions.csunesco_project_update(
        _ctx(), {'id': created['id'], 'title': created['title']})
    assert 'edit_history' not in json.loads(_row(created['id']).extras)


def test_initiative_change_cascades_to_project_content(actions, session):
    created = _create(actions)
    content = db.CsContent()
    content.id = 'content-1'
    content.slug = 'content-1'
    content.content_type = 'cs-news'
    content.project_id = created['id']
    content.initiative_group = 'riverwatch'
    content.title = 'Update'
    content.status = 'approved'
    content.extras = '{}'
    session.add(content)
    session.commit()

    actions.csunesco_project_update(
        _ctx(), {'id': created['id'], 'initiative': 'islandwatch'})

    session.expire_all()
    assert db.get_content('content-1').initiative_group == 'islandwatch'


def test_update_can_clear_an_extra(actions, session):
    created = _create(actions)
    actions.csunesco_project_update(
        _ctx(), {'id': created['id'], 'contact_email': ''})
    out = db.project_dictize(_row(created['id']))
    assert 'contact_email' not in json.loads(_row(created['id']).extras)
    assert not out.get('contact_email')


def test_update_keeps_an_approved_project_approved(actions, session):
    """The deliberate divergence from content_update.

    Sending an approved project back to pending because someone fixed a typo
    would unpublish its landing page and stall its news queue.
    """
    created = _create(actions)
    project = _row(created['id'])
    project.status = 'approved'
    project.reviewed_by = 'adm-9'
    session.commit()

    actions.csunesco_project_update(
        _ctx(), {'id': created['id'], 'title': 'Renamed'})
    after = _row(created['id'])
    assert after.status == 'approved'
    assert after.reviewed_by == 'adm-9'


def test_update_keeps_a_pending_project_pending(actions, session):
    created = _create(actions)
    actions.csunesco_project_update(
        _ctx(), {'id': created['id'], 'title': 'Renamed'})
    assert _row(created['id']).status == 'pending'


def test_update_ignores_slug(actions, session):
    """The URL is permanent: links, QR codes and the ofform mirror depend on
    it, and none of them would be told it moved."""
    created = _create(actions)
    original = created['slug']
    actions.csunesco_project_update(
        _ctx(), {'id': created['id'], 'slug': 'somewhere-else'})
    assert _row(created['id']).slug == original


def test_update_rejects_an_empty_title(actions, session):
    """Narrowing the schema to the keys sent loosens WHICH fields are
    required, never HOW a field that was sent is checked."""
    created = _create(actions)
    with pytest.raises(tk.ValidationError):
        actions.csunesco_project_update(
            _ctx(), {'id': created['id'], 'title': ''})


def test_update_rejects_end_before_start(actions, session):
    created = _create(actions)
    with pytest.raises(tk.ValidationError):
        actions.csunesco_project_update(
            _ctx(), {'id': created['id'],
                     'start_date': '2026-07-16', 'end_date': '2026-07-15'})


def test_update_accepts_a_single_day_project(actions, session):
    created = _create(actions)
    actions.csunesco_project_update(
        _ctx(), {'id': created['id'],
                 'start_date': '2026-07-16', 'end_date': '2026-07-16'})
    out = db.project_dictize(_row(created['id']))
    assert out['start_date'] == out['end_date'] == '2026-07-16'


def test_update_refuses_a_non_manager(actions, session, monkeypatch):
    """check_access is neutralized here, so this proves the ACTION's own
    re-check against the resolved project bites.

    Acts as a DIFFERENT user from the one who filed the request: the author of
    an unapproved project is allowed to edit it on purpose (see the authorship
    tests below), so using ``_ctx()`` here would be testing that rule instead.
    """
    created = _create(actions)
    monkeypatch.setattr(cs_auth, 'can_manage_project',
                        lambda context, project_id: False)
    monkeypatch.setattr(cs_auth, '_is_project_initiative_admin',
                        lambda context, project_id: False)
    with pytest.raises(tk.NotAuthorized):
        actions.csunesco_project_update(
            _ctx('somebody-else'), {'id': created['id'], 'title': 'Nope'})


def test_update_on_a_missing_project_is_not_found(actions, session):
    with pytest.raises(tk.ObjectNotFound):
        actions.csunesco_project_update(_ctx(), {'id': 'no-such-project'})


def test_update_sanitizes_free_text(actions, session):
    created = _create(actions)
    actions.csunesco_project_update(
        _ctx(), {'id': created['id'],
                 'how_to_participate': 'Hi <script>alert(1)</script>'})
    out = db.project_dictize(_row(created['id']))
    assert '<script>' not in out['how_to_participate']


# --------------------------------------------------------------------------- #
# Rejected projects: ownership, findability and the way back into the queue.   #
#                                                                              #
# The gap these close: the admin MEMBERSHIP row is inserted by                 #
# csunesco_project_approve, so until a request is approved its own author      #
# administers nothing. They could not edit it, could not find it in "Your      #
# projects", and a rejection was terminal.                                     #
# --------------------------------------------------------------------------- #

@pytest.fixture
def as_author(session, monkeypatch):
    """The acting user is NOT a manager by membership -- only the author."""
    monkeypatch.setattr(tk, 'check_access', lambda *a, **k: True)
    monkeypatch.setattr(cs_auth, '_is_sysadmin', lambda context: False)
    monkeypatch.setattr(cs_auth, 'can_manage_project',
                        lambda context, project_id: False)
    monkeypatch.setattr(cs_auth, '_is_project_initiative_admin',
                        lambda context, project_id: False)
    return projects_action


def _reject(session, project_id, reason='Wrong initiative.'):
    project = _row(project_id)
    project.status = 'rejected'
    project.rejection_reason = reason
    project.reviewed_by = 'adm-9'
    project.reviewed_at = _dt()
    session.commit()
    return project


def _dt():
    import datetime
    return datetime.datetime(2026, 7, 16, 9, 0, 0)


def test_author_may_edit_their_own_pending_request(actions, session,
                                                   monkeypatch):
    """The membership row does not exist yet -- authorship has to carry it."""
    created = _create(actions)
    monkeypatch.setattr(cs_auth, 'can_manage_project',
                        lambda context, project_id: False)
    monkeypatch.setattr(cs_auth, '_is_project_initiative_admin',
                        lambda context, project_id: False)
    actions.csunesco_project_update(
        _ctx(), {'id': created['id'], 'title': 'Fixed typo'})
    assert _row(created['id']).title == 'Fixed typo'


def test_a_stranger_may_not_edit_someone_elses_request(as_author, session):
    # Only as_author -- pulling in the `actions` fixture too would re-patch
    # can_manage_project back to True after it and grant everyone everything.
    created = _create(as_author)
    with pytest.raises(tk.NotAuthorized):
        # A DIFFERENT user: not the author, not a manager.
        as_author.csunesco_project_update(
            _ctx('somebody-else'), {'id': created['id'], 'title': 'Mine now'})


def test_authorship_grants_nothing_once_approved(actions, session,
                                                 monkeypatch):
    """After approval the membership row exists and the ordinary rule owns
    the decision -- authorship must not be a permanent back door."""
    created = _create(actions)
    project = _row(created['id'])
    project.status = 'approved'
    session.commit()
    monkeypatch.setattr(cs_auth, 'can_manage_project',
                        lambda context, project_id: False)
    monkeypatch.setattr(cs_auth, '_is_project_initiative_admin',
                        lambda context, project_id: False)
    with pytest.raises(tk.NotAuthorized):
        actions.csunesco_project_update(
            _ctx(), {'id': created['id'], 'title': 'Still mine?'})


def test_resubmit_returns_a_rejected_project_to_the_queue(actions, session):
    created = _create(actions)
    _reject(session, created['id'])
    out = actions.csunesco_project_resubmit(_ctx(), {'id': created['id']})
    assert out['status'] == 'pending'


def test_resubmit_clears_the_reason_and_the_stale_review_stamp(actions,
                                                               session):
    """A re-queued row must not tell the reviewer it was already reviewed."""
    created = _create(actions)
    _reject(session, created['id'])
    actions.csunesco_project_resubmit(_ctx(), {'id': created['id']})
    after = _row(created['id'])
    assert after.rejection_reason is None
    assert after.reviewed_by is None
    assert after.reviewed_at is None


def test_resubmit_works_for_the_author_without_a_membership_row(as_author,
                                                                session):
    created = _create(as_author)
    _reject(session, created['id'])
    out = as_author.csunesco_project_resubmit(_ctx(), {'id': created['id']})
    assert out['status'] == 'pending'


def test_resubmit_refuses_a_stranger(as_author, session):
    created = _create(as_author)
    _reject(session, created['id'])
    with pytest.raises(tk.NotAuthorized):
        as_author.csunesco_project_resubmit(
            _ctx('somebody-else'), {'id': created['id']})


def test_resubmit_rejects_a_pending_project(actions, session):
    """Re-queueing an already queued request would just reorder the queue."""
    created = _create(actions)
    with pytest.raises(tk.ValidationError):
        actions.csunesco_project_resubmit(_ctx(), {'id': created['id']})


def test_resubmit_cannot_unpublish_an_approved_project(actions, session):
    """The dangerous one: pending would take a live landing page down."""
    created = _create(actions)
    project = _row(created['id'])
    project.status = 'approved'
    session.commit()
    with pytest.raises(tk.ValidationError):
        actions.csunesco_project_resubmit(_ctx(), {'id': created['id']})
    assert _row(created['id']).status == 'approved'


def test_resubmit_on_a_missing_project_is_not_found(actions, session):
    with pytest.raises(tk.ObjectNotFound):
        actions.csunesco_project_resubmit(_ctx(), {'id': 'no-such-project'})


def test_your_projects_lists_a_request_with_no_membership_row(actions,
                                                              session):
    """Findability: without this the resubmit control has no page to live on.

    projects_administered's docstring already promised pending and rejected
    requests would appear; joined on membership alone, it could not keep that
    promise for the person who filed them.
    """
    created = _create(actions)
    _reject(session, created['id'])
    mine = db.projects_administered('pm-1')
    slugs = {row['slug'] for row in mine}
    assert created['slug'] in slugs
    row = [r for r in mine if r['slug'] == created['slug']][0]
    assert row['status'] == 'rejected'
    # The card needs the reason, so the query has to carry it.
    assert row['rejection_reason'] == 'Wrong initiative.'


def test_your_projects_does_not_leak_other_peoples_requests(actions, session):
    _create(actions)
    assert db.projects_administered('someone-else') == []


# --------------------------------------------------------------------------- #
# Country clearing semantics: OMITTED preserves, [] clears.                    #
#                                                                              #
# The web form now proves it rendered its picker (`countries_present`) before  #
# the view sends the key at all, because an empty multi-select and a picker    #
# that failed to draw are the same nothing on the wire -- and the update reads #
# an empty list as "clear every country".                                      #
# --------------------------------------------------------------------------- #

def _country_ctx(slug='chile'):
    """A context carrying `model`: csunesco_valid_country_list reads it and
    falls back to an EMPTY valid-set when it is absent, which would reject
    every country before the patch below is ever consulted."""
    import ckan.model as model
    ctx = _ctx()
    ctx['model'] = model
    return ctx


def _with_country(actions, session, monkeypatch, slug='chile'):
    monkeypatch.setattr(
        'ckanext.csunesco.logic.validators._member_state_names',
        lambda model: {slug})
    created = actions.csunesco_project_request_create(
        _country_ctx(), {'title': 'Has countries', 'initiative': 'riverwatch',
                         'countries': [slug]})
    assert db.project_dictize(_row(created['id']))['countries'] == [slug]
    return created


def test_update_without_countries_preserves_them(actions, session, monkeypatch):
    created = _with_country(actions, session, monkeypatch)
    actions.csunesco_project_update(
        _country_ctx(), {'id': created['id'], 'title': 'Renamed only'})
    assert db.project_dictize(_row(created['id']))['countries'] == ['chile']


def test_update_with_an_empty_list_clears_them(actions, session, monkeypatch):
    """The deliberate half: an empty selection from a picker that DID render
    means the user removed everything."""
    created = _with_country(actions, session, monkeypatch)
    actions.csunesco_project_update(
        _country_ctx(), {'id': created['id'], 'countries': []})
    assert db.project_dictize(_row(created['id']))['countries'] == []


def test_a_country_already_stored_survives_a_member_state_outage(
        actions, session, monkeypatch):
    """Keeping a country you already declared is not the same act as adding
    one, and must not need the member-state list to be reachable.

    Otherwise the edit form is unsavable during an outage: the picker re-offers
    the stored value (so it is not silently wiped) and the validator then
    rejects it, blocking even a title change until someone repairs the group.
    """
    created = _with_country(actions, session, monkeypatch)
    # The whole member-state list is now unavailable.
    monkeypatch.setattr(
        'ckanext.csunesco.logic.validators._member_state_names',
        lambda model: set())
    actions.csunesco_project_update(
        _country_ctx(), {'id': created['id'], 'title': 'Renamed mid-outage',
                         'countries': ['chile']})
    out = db.project_dictize(_row(created['id']))
    assert out['title'] == 'Renamed mid-outage'
    assert out['countries'] == ['chile']


def test_an_unknown_new_country_is_still_rejected(actions, session,
                                                  monkeypatch):
    """Grandfathering covers what the project already had, nothing else."""
    created = _with_country(actions, session, monkeypatch)
    with pytest.raises(tk.ValidationError):
        actions.csunesco_project_update(
            _country_ctx(), {'id': created['id'],
                             'countries': ['chile', 'atlantis']})


# --------------------------------------------------------------------------- #
# The derived member-states counter (fed on approve and on country edits)     #
# --------------------------------------------------------------------------- #

def test_approve_seeds_the_member_states_counter(actions, session,
                                                 monkeypatch):
    """The per-project counter is DERIVED from the declared countries.

    Regression: it was never fed by anything, so every landing page's
    At-a-Glance band showed Member States = 0 forever.
    """
    monkeypatch.setattr(
        'ckanext.csunesco.logic.validators._member_state_names',
        lambda model: {'chile', 'peru'})
    created = actions.csunesco_project_request_create(
        _country_ctx(), {'title': 'Two states', 'initiative': 'riverwatch',
                         'countries': ['chile', 'peru']})
    actions.csunesco_project_approve(_ctx('reviewer-1'),
                                     {'id': created['id']})
    stats = db.get_stats(created['id'])
    assert stats.member_states == 2


def test_editing_countries_recomputes_the_counter(actions, session,
                                                  monkeypatch):
    created = _with_country(actions, session, monkeypatch)
    actions.csunesco_project_approve(_ctx('reviewer-1'), {'id': created['id']})
    assert db.get_stats(created['id']).member_states == 1
    actions.csunesco_project_update(
        _country_ctx(), {'id': created['id'], 'countries': []})
    assert db.get_stats(created['id']).member_states == 0


def test_editing_countries_on_a_pending_project_creates_no_counter_row(
        actions, session, monkeypatch):
    """Pending requests get their counter seeded at approval, not before."""
    created = _with_country(actions, session, monkeypatch)
    actions.csunesco_project_update(
        _country_ctx(), {'id': created['id'], 'countries': []})
    assert db.get_stats(created['id']) is None


# --------------------------------------------------------------------------- #
# csunesco_aggregate_stats with an initiative filter                          #
# --------------------------------------------------------------------------- #

def test_aggregate_stats_accepts_an_initiative(actions, session):
    """Regression: the action referenced ``constants.CS_INITIATIVES`` without
    importing ``constants``, so EVERY initiative-scoped call raised NameError
    -- swallowed by page_render, which is why initiative pages rendered zeros
    forever instead of crashing."""
    out = actions.csunesco_aggregate_stats(_ctx(), {'initiative': 'riverwatch'})
    assert out == {'citizen_scientists': 0, 'observations': 0,
                   'sites_monitored': 0, 'member_states': 0}


def test_aggregate_stats_rejects_an_unknown_initiative(actions, session):
    with pytest.raises(tk.ValidationError):
        actions.csunesco_aggregate_stats(_ctx(), {'initiative': 'atlantis'})
