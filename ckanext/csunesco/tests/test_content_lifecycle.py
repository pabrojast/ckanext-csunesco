# encoding: utf-8
"""Content lifecycle actions against a real (in-memory) ORM.

What these pin down, in contract order:

* the EXACT payload the CS Toolbox app pushes through the outbox keeps
  creating pending content (``source='app'`` queues even for a sysadmin token);
* ``withdraw`` only unpublishes APPROVED rows and stamps the audit trail;
* ``approve`` now also restores REJECTED rows (the undo), clearing the
  rejection reason and the withdrawn marker;
* ``reject`` deliberately keeps accepting any starting status (compat);
* ``delete`` is the only hard removal and it frees the slug;
* the ``featured`` flag only changes when the key was explicitly sent
  (the silent un-feature bug);
* rows without ``publish_date`` no longer pin to the top of listings
  (the NULLS-FIRST bug);
* the auto-heal whitelist grows the new audit columns on old databases.

Same environment as ``test_db_behavior``: fresh in-memory SQLite bound to the
plugin's scoped ``Session`` (which IS ``ckan.model.meta.Session``, the one the
actions commit through), ``tk.check_access`` neutralized, authorization
helpers monkeypatched per test.
"""
import datetime

import pytest

try:
    import sqlalchemy as sa
    import ckan.plugins.toolkit as tk
    import ckan  # noqa: F401
    from ckanext.csunesco import db
    from ckanext.csunesco.logic import auth as cs_auth
    from ckanext.csunesco.logic.action import content as content_action
    HAVE_CKAN = True
except Exception:  # pragma: no cover - environment without CKAN
    HAVE_CKAN = False

pytestmark = pytest.mark.skipif(
    not HAVE_CKAN, reason="requires CKAN (ckan.model + sqlalchemy)")


class _User(object):
    is_anonymous = False

    def __init__(self, user_id):
        self.id = user_id


def _ctx(user_id='reviewer-1'):
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
    # Default: a plain project manager (not sysadmin) who may manage projects.
    monkeypatch.setattr(cs_auth, '_is_sysadmin', lambda context: False)
    monkeypatch.setattr(cs_auth, 'can_manage_project',
                        lambda context, project_id: True)
    return content_action


def _approved_project(session, slug='river-x', initiative=None):
    project = db.CsProject()
    project.slug = slug
    project.title = slug.title()
    project.status = 'approved'
    project.initiative_group = initiative
    session.add(project)
    session.commit()
    return project


# The literal shape ofform's outbox pushes (services/cs_sync -> content kind).
APP_PAYLOAD = {
    'project_slug': 'river-x',
    'content_type': 'cs-news',
    'title': 'Sampling day announced',
    'body': '<p>We sampled the river.</p>',
    'publish_date': '2026-08-01',
    'end_date': None,
    'source': 'app',
    'author': 'koen',
}


def test_app_contract_payload_still_queues(actions, session, monkeypatch):
    _approved_project(session)
    # The app pushes with a SYSADMIN service token -- and must still queue.
    monkeypatch.setattr(cs_auth, '_is_sysadmin', lambda context: True)
    out = actions.csunesco_content_create(_ctx('service'), dict(APP_PAYLOAD))
    assert out['status'] == 'pending'
    assert out['source'] == 'app'
    assert out['app_author'] == 'koen'
    assert out['reviewed_by'] is None


def test_withdraw_only_approved_and_stamps_audit(actions, session, monkeypatch):
    _approved_project(session)
    monkeypatch.setattr(cs_auth, '_is_sysadmin', lambda context: True)
    created = actions.csunesco_content_create(
        _ctx('author-1'),
        {'project_slug': 'river-x', 'content_type': 'cs-news',
         'title': 'Published news', 'body': '<p>x</p>'})
    assert created['status'] == 'approved'

    out = actions.csunesco_content_withdraw(
        _ctx('mod-1'), {'id': created['id'], 'reason': 'outdated'})
    assert out['status'] == 'rejected'
    assert out['withdrawn'] is True
    assert out['rejection_reason'] == 'outdated'
    assert out['reviewed_by'] == 'mod-1'
    assert out['reviewed_at'] is not None

    # Already withdrawn (now rejected): a second withdraw must refuse.
    with pytest.raises(tk.ValidationError):
        actions.csunesco_content_withdraw(_ctx('mod-1'), {'id': created['id']})


def test_approve_restores_rejected_and_clears_marks(actions, session,
                                                    monkeypatch):
    _approved_project(session)
    monkeypatch.setattr(cs_auth, '_is_sysadmin', lambda context: True)
    created = actions.csunesco_content_create(
        _ctx('author-1'),
        {'project_slug': 'river-x', 'content_type': 'cs-news',
         'title': 'To be restored', 'body': '<p>x</p>'})
    actions.csunesco_content_withdraw(
        _ctx('mod-1'), {'id': created['id'], 'reason': 'oops'})

    out = actions.csunesco_content_approve(_ctx('mod-2'), {'id': created['id']})
    assert out['status'] == 'approved'
    assert 'rejection_reason' not in out
    assert 'withdrawn' not in out
    assert out['reviewed_by'] == 'mod-2'

    # Approving an already-approved row still refuses.
    with pytest.raises(tk.ValidationError):
        actions.csunesco_content_approve(_ctx('mod-2'), {'id': created['id']})


def test_reject_keeps_accepting_any_status(actions, session, monkeypatch):
    _approved_project(session)
    monkeypatch.setattr(cs_auth, '_is_sysadmin', lambda context: True)
    created = actions.csunesco_content_create(
        _ctx('author-1'),
        {'project_slug': 'river-x', 'content_type': 'cs-news',
         'title': 'Reject me', 'body': '<p>x</p>'})
    # Compat: reject works straight on an approved row (no state check).
    out = actions.csunesco_content_reject(
        _ctx('mod-1'), {'id': created['id'], 'reason': 'nope'})
    assert out['status'] == 'rejected'
    assert 'withdrawn' not in out
    assert out['reviewed_by'] == 'mod-1'


def test_delete_is_hard_and_frees_the_slug(actions, session, monkeypatch):
    _approved_project(session)
    monkeypatch.setattr(cs_auth, '_is_sysadmin', lambda context: True)
    first = actions.csunesco_content_create(
        _ctx('author-1'),
        {'project_slug': 'river-x', 'content_type': 'cs-news',
         'title': 'Unique headline', 'body': '<p>x</p>'})
    out = actions.csunesco_content_delete(_ctx('root'), {'id': first['id']})
    assert out == {'id': first['id'], 'deleted': True}
    assert db.get_content(first['id']) is None

    # The slug is reusable: a new row with the same title gets the BASE slug.
    second = actions.csunesco_content_create(
        _ctx('author-1'),
        {'project_slug': 'river-x', 'content_type': 'cs-news',
         'title': 'Unique headline', 'body': '<p>x</p>'})
    assert second['slug'] == first['slug']


def test_update_touches_featured_only_when_key_sent(actions, session,
                                                    monkeypatch):
    _approved_project(session)
    monkeypatch.setattr(cs_auth, '_is_sysadmin', lambda context: True)
    created = actions.csunesco_content_create(
        _ctx('root'),
        {'project_slug': 'river-x', 'content_type': 'cs-news',
         'title': 'Star item', 'body': '<p>x</p>', 'featured': True})
    assert created['featured'] is True

    # A sysadmin edit WITHOUT the key must not silently un-feature the row.
    out = actions.csunesco_content_update(
        _ctx('root'),
        {'id': created['id'], 'content_type': 'cs-news',
         'title': 'Star item (edited)', 'body': '<p>y</p>'})
    assert out['featured'] is True

    # An explicit False still un-features.
    out = actions.csunesco_content_update(
        _ctx('root'),
        {'id': created['id'], 'content_type': 'cs-news',
         'title': 'Star item (edited)', 'body': '<p>y</p>',
         'featured': False})
    assert out['featured'] is False


def test_undated_rows_no_longer_pin_to_the_top(actions, session):
    """COALESCE(publish_date, created): order is the natural timeline."""
    def _row(title, created, publish=None):
        row = db.CsContent()
        row.content_type = 'cs-news'
        row.project_id = 'p1'
        row.title = title
        row.status = 'approved'
        row.slug = db.unique_content_slug(title)
        row.created = created
        row.publish_date = publish
        session.add(row)

    _row('old undated', datetime.datetime(2020, 1, 1))
    _row('dated', datetime.datetime(2021, 1, 1),
         publish=datetime.datetime(2026, 6, 1))
    _row('new undated', datetime.datetime(2027, 1, 1))
    session.commit()

    _total, rows = db.list_content(content_type='cs-news')
    assert [r.title for r in rows] == ['new undated', 'dated', 'old undated']


def test_moderated_content_lists_decided_rows(actions, session, monkeypatch):
    _approved_project(session)
    monkeypatch.setattr(cs_auth, '_is_sysadmin', lambda context: True)
    kept = actions.csunesco_content_create(
        _ctx('root'), {'project_slug': 'river-x', 'content_type': 'cs-news',
                       'title': 'Stays pending', 'body': '<p>x</p>',
                       'source': 'app'})
    decided = actions.csunesco_content_create(
        _ctx('root'), {'project_slug': 'river-x', 'content_type': 'cs-news',
                       'title': 'Got published', 'body': '<p>x</p>'})
    assert kept['status'] == 'pending'

    total, rows = db.moderated_content()
    assert total == 1
    assert rows[0]['id'] == decided['id']
    assert rows[0]['project_title'] == 'River-X'

    # Scoping mirrors pending_content: empty scope -> nothing.
    assert db.moderated_content(project_ids=[]) == (0, [])


def test_auto_heal_adds_the_new_audit_columns():
    engine = sa.create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(sa.text('CREATE TABLE cs_content (id TEXT PRIMARY KEY)'))
    db._ensure_columns(engine)
    inspector = sa.inspect(engine)
    columns = {c['name'] for c in inspector.get_columns('cs_content')}
    assert {'reviewed_by', 'reviewed_at'} <= columns
    engine.dispose()
