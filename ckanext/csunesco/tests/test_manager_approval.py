# encoding: utf-8
"""The Project Manager double gate: sysadmin approve/decline actions.

What these pin down:

* approve activates the user, CREATES the requested organization (new-org
  path, capacity admin) or JOINS the existing one (capacity editor), stamps
  the decision and is idempotent on re-approve;
* approve refuses an email-unverified manager -- the first gate cannot be
  skipped by the second;
* reject records the decision and leaves the account pending (reversible);
* ``pending_managers`` lists exactly the verified-but-undecided profiles.

Same harness as ``test_join_requests``: in-memory SQLite bound to the plugin's
scoped ``Session``, ``tk.check_access`` neutralized, CKAN's user/group lookups
monkeypatched (their tables cannot be created on SQLite).
"""
import pytest

try:
    import sqlalchemy as sa
    import ckan.plugins.toolkit as tk
    import ckan.model as model
    import ckan  # noqa: F401
    from ckanext.csunesco import db
    from ckanext.csunesco.logic.action import registration as reg_action
    HAVE_CKAN = True
except Exception:  # pragma: no cover - environment without CKAN
    HAVE_CKAN = False

pytestmark = pytest.mark.skipif(
    not HAVE_CKAN, reason="requires CKAN (ckan.model + sqlalchemy)")


class _FakeUser(object):
    def __init__(self, user_id='user-1', name='paula'):
        self.id = user_id
        self.name = name
        self.fullname = 'Paula Manager'
        self.email = 'pm@example.org'
        self.activated = False

    def activate(self):
        self.activated = True


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
def harness(session, monkeypatch):
    """(user, calls) plus the neutralized web-stack pieces."""
    user = _FakeUser()
    calls = []
    monkeypatch.setattr(tk, 'check_access', lambda *a, **k: True)
    monkeypatch.setattr(model.User, 'get', staticmethod(
        lambda key: user if key in (user.id, user.name) else None))
    monkeypatch.setattr(model.Group, 'get', staticmethod(lambda key: None))
    monkeypatch.setattr(
        reg_action, '_send_decision_email',
        lambda u, approved, reason=None: calls.append(
            ('email', (approved, reason))))

    def get_action(name):
        def call(context, data):
            calls.append((name, data))
            if name == 'organization_create':
                return {'id': 'org-uuid-1', 'name': data['name']}
            return {}
        return call

    monkeypatch.setattr(tk, 'get_action', get_action)
    return user, calls


def _manager_profile(session, email_verified=True, org_name_requested=None,
                     org_id=None):
    profile = db.CsCitizenScientist()
    profile.user_id = 'user-1'
    profile.profile_type = 'manager'
    profile.email_verified = email_verified
    profile.org_name_requested = org_name_requested
    profile.org_id = org_id
    profile.org_type = 'university'
    profile.org_title = 'Research lead'
    profile.org_role = 'admin' if org_name_requested else 'editor'
    session.add(profile)
    session.commit()
    return profile


def _ctx():
    return {'user': 'admin', 'auth_user_obj': type(
        'Admin', (), {'id': 'admin-1', 'is_anonymous': False})()}


def test_approve_new_org_creates_it_and_grants_admin(harness, session):
    user, calls = harness
    _manager_profile(session, org_name_requested='Hydrology Lab')

    out = reg_action.csunesco_manager_approve(_ctx(), {'username': 'paula'})

    assert user.activated is True
    created = dict(calls)['organization_create']
    assert created['title'] == 'Hydrology Lab'
    assert created['name'] == 'hydrology-lab'
    membership = dict(calls)['organization_member_create']
    assert membership == {'id': 'org-uuid-1', 'username': 'paula',
                          'role': 'admin'}
    assert ('email', (True, None)) in calls
    assert out['manager_decision'] == 'approved'
    assert out['org_id'] == 'org-uuid-1'
    assert out['existed'] is False


def test_approve_existing_org_grants_editor(harness, session):
    user, calls = harness
    _manager_profile(session, org_id='existing-org')

    out = reg_action.csunesco_manager_approve(_ctx(), {'username': 'paula'})

    assert user.activated is True
    names = [name for (name, _payload) in calls if name != 'email']
    assert 'organization_create' not in names
    membership = dict(calls)['organization_member_create']
    assert membership['role'] == 'editor'
    assert out['org_role'] == 'editor'


def test_approve_is_idempotent(harness, session):
    user, calls = harness
    profile = _manager_profile(session, org_id='existing-org')
    profile.manager_decision = 'approved'
    session.commit()

    out = reg_action.csunesco_manager_approve(_ctx(), {'username': 'paula'})
    assert out['existed'] is True
    # No side effects on re-approve: no membership call, no email.
    assert calls == []


def test_approve_refuses_an_unverified_email(harness, session):
    _manager_profile(session, email_verified=False,
                     org_name_requested='Hydrology Lab')
    with pytest.raises(tk.ValidationError):
        reg_action.csunesco_manager_approve(_ctx(), {'username': 'paula'})


def test_reject_records_and_notifies_but_deletes_nothing(harness, session):
    user, calls = harness
    _manager_profile(session, org_name_requested='Hydrology Lab')

    out = reg_action.csunesco_manager_reject(
        _ctx(), {'username': 'paula', 'reason': 'Unknown organization'})

    assert user.activated is False
    assert out['manager_decision'] == 'rejected'
    assert ('email', (False, 'Unknown organization')) in calls


def test_a_citizen_profile_is_not_a_manager(harness, session):
    profile = db.CsCitizenScientist()
    profile.user_id = 'user-1'
    profile.profile_type = 'citizen'
    session.add(profile)
    session.commit()
    with pytest.raises(tk.ObjectNotFound):
        reg_action.csunesco_manager_approve(_ctx(), {'username': 'paula'})


def test_pending_managers_lists_verified_undecided_only(harness, session):
    verified = _manager_profile(session, org_name_requested='Hydrology Lab')

    unverified = db.CsCitizenScientist()
    unverified.user_id = 'user-2'
    unverified.profile_type = 'manager'
    unverified.email_verified = False
    session.add(unverified)

    decided = db.CsCitizenScientist()
    decided.user_id = 'user-3'
    decided.profile_type = 'manager'
    decided.email_verified = True
    decided.manager_decision = 'rejected'
    session.add(decided)

    citizen = db.CsCitizenScientist()
    citizen.user_id = 'user-4'
    citizen.profile_type = 'citizen'
    citizen.email_verified = True
    session.add(citizen)
    session.commit()

    rows = db.pending_managers()
    assert [row.user_id for row in rows] == [verified.user_id]
