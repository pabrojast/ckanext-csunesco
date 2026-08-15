# encoding: utf-8
"""Join requests: what the applicant says, and what the reviewer is shown.

Before these, a join request carried NOTHING. The landing page offered a bare
"Request to join" button and the approval panel printed a username, so the
project admin approved or rejected people with no information at all -- while
the CS Toolbox app had been sending a ``note`` on every join since its join
endpoint existed, only for the action to drop it on the floor.

What these pin down:

* the note is stored, tag-stripped and length-capped;
* the EXACT ofform join payload keeps working and its note now survives;
* an empty note normalizes to NULL rather than an empty string;
* the review rows carry the note and the requester's CS profile;
* re-requesting stays idempotent and does not wipe the original note.

Same harness as ``test_content_lifecycle``: in-memory SQLite bound to the
plugin's scoped ``Session``, ``tk.check_access`` neutralized.

NOTE ON COVERAGE: CKAN's ``user`` table cannot be created on SQLite (its
``plugin_extras`` is JSONB), so the CKAN-side decoration of a review row
(display name, email) is exercised only for its fail-soft path here. The
fields that come from OUR tables -- the note, the declared country, the
verification flag -- are asserted for real.
"""
import pytest

try:
    import sqlalchemy as sa
    import ckan.plugins.toolkit as tk
    import ckan  # noqa: F401
    from ckanext.csunesco import db
    from ckanext.csunesco.logic import auth as cs_auth
    from ckanext.csunesco.logic.action import members as members_action
    HAVE_CKAN = True
except Exception:  # pragma: no cover - environment without CKAN
    HAVE_CKAN = False

pytestmark = pytest.mark.skipif(
    not HAVE_CKAN, reason="requires CKAN (ckan.model + sqlalchemy)")


class _User(object):
    is_anonymous = False

    def __init__(self, user_id):
        self.id = user_id


def _ctx(user_id='citizen-1'):
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
    monkeypatch.setattr(tk, 'check_access', lambda *a, **k: True)
    monkeypatch.setattr(cs_auth, '_is_sysadmin', lambda context: False)
    return members_action


@pytest.fixture
def project(session):
    """Joining requires an APPROVED project."""
    row = db.CsProject()
    row.slug = 'river-x'
    row.title = 'River X'
    row.status = 'approved'
    session.add(row)
    session.commit()
    return row


def _profile(session, user_id, country='chile', verified=True):
    row = db.CsCitizenScientist()
    row.user_id = user_id
    row.country = country
    row.email_verified = verified
    session.add(row)
    session.commit()
    return row


def test_the_note_is_stored(actions, session, project):
    out = actions.csunesco_join_request_create(
        _ctx(), {'project_id': project.id,
                 'note': 'I teach nearby and my class can sample weekly.'})
    assert out['note'] == 'I teach nearby and my class can sample weekly.'
    assert out['status'] == 'pending'


def test_the_ofform_join_payload_keeps_working_and_its_note_survives(
        actions, session, project):
    """Regression for the drop.

    This is the shape ofform's outbox pushes (backend cs_projects.py). It sent
    a note long before there was anywhere to put it.
    """
    payload = {
        'programme_id': 'prog-1',
        'project_slug': 'river-x',
        'project_id': project.id,
        'username': 'koen',
        'note': 'Sent from the app.',
    }
    out = actions.csunesco_join_request_create(_ctx(), payload)
    assert out['note'] == 'Sent from the app.'


def test_markup_is_stripped_not_stored(actions, session, project):
    out = actions.csunesco_join_request_create(
        _ctx(), {'project_id': project.id,
                 'note': 'Hi <script>alert(1)</script><b>there</b>'})
    assert '<script>' not in out['note']
    assert '<b>' not in out['note']


def test_a_long_note_is_capped_not_rejected(actions, session, project):
    """Losing a join request over an over-long paragraph would be worse."""
    out = actions.csunesco_join_request_create(
        _ctx(), {'project_id': project.id, 'note': 'x' * 5000})
    assert len(out['note']) == members_action.MAX_NOTE_LENGTH


def test_an_empty_note_is_null(actions, session, project):
    """The note stays optional -- the button-only path must still work."""
    out = actions.csunesco_join_request_create(
        _ctx(), {'project_id': project.id, 'note': '   '})
    assert out['note'] is None


def test_a_join_with_no_note_at_all_still_works(actions, session, project):
    out = actions.csunesco_join_request_create(
        _ctx(), {'project_id': project.id})
    assert out['status'] == 'pending'
    assert out['note'] is None


def test_re_requesting_is_idempotent_but_the_note_can_be_improved(
        actions, session, project):
    """Still one membership row -- but a PENDING applicant may rewrite their
    case. Discarding the second note was the old behaviour and it was silent:
    the reply is a friendly "already requested", so nothing told them their
    words had gone nowhere."""
    actions.csunesco_join_request_create(
        _ctx(), {'project_id': project.id, 'note': 'First words.'})
    again = actions.csunesco_join_request_create(
        _ctx(), {'project_id': project.id, 'note': 'Second thoughts.'})
    assert again['already_requested'] is True
    assert again['note'] == 'Second thoughts.'


def test_the_review_row_carries_the_note_and_the_profile(
        actions, session, project):
    """What the reviewer actually gets handed."""
    _profile(session, 'citizen-1', country='chile', verified=True)
    actions.csunesco_join_request_create(
        _ctx(), {'project_id': project.id, 'note': 'Weekly sampling.'})

    total, rows = db.pending_joins(project_ids=[project.id])
    assert total == 1
    row = rows[0]
    assert row['note'] == 'Weekly sampling.'
    assert row['user_country'] == 'chile'
    assert row['email_verified'] is True
    assert row['project_title'] == 'River X'


def test_an_unverified_requester_is_flagged(actions, session, project):
    """The panel shows a warning chip; the flag has to reach it."""
    _profile(session, 'citizen-1', verified=False)
    actions.csunesco_join_request_create(_ctx(), {'project_id': project.id})
    _total, rows = db.pending_joins(project_ids=[project.id])
    assert rows[0]['email_verified'] is False


def test_a_requester_with_no_cs_profile_does_not_break_the_queue(
        actions, session, project):
    """Accounts created before the profile existed, or by other routes."""
    actions.csunesco_join_request_create(_ctx(), {'project_id': project.id})
    _total, rows = db.pending_joins(project_ids=[project.id])
    assert rows[0]['user_country'] is None
    assert rows[0]['email_verified'] is False


def test_unresolvable_accounts_do_not_take_down_the_queue(
        actions, session, project):
    """A reviewer must get their queue even when a requester's CKAN account
    cannot be read -- ids where names would be, never a 500.

    On this harness the ``user`` table does not exist at all, which is exactly
    the failure being guarded against.
    """
    actions.csunesco_join_request_create(
        _ctx(), {'project_id': project.id, 'note': 'Still visible.'})
    _total, rows = db.pending_joins(project_ids=[project.id])
    assert rows[0]['user_name'] == 'citizen-1'
    assert rows[0]['user_email'] is None
    assert rows[0]['note'] == 'Still visible.'


def test_member_dictize_exposes_the_note(session, project):
    member = db.CsProjectMember()
    member.project_id = project.id
    member.user_id = 'citizen-1'
    member.status = 'pending'
    member.note = 'Because rivers.'
    session.add(member)
    session.commit()
    assert db.member_dictize(member)['note'] == 'Because rivers.'
