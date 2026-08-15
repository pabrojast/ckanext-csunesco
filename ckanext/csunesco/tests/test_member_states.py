# encoding: utf-8
"""Behavioral tests for the member-state (country) lookup.

The bug these pin down: the project form used to read its country options from
``group_show(id='member-states', include_groups=True)`` and fall back to
``child['title'] or child['name']``. CKAN's child-group dictization returns
``title: None``, so the fallback fired for EVERY option and the live portal
listed ``afghanistan``, ``-land-islands``, ``albania`` -- raw slugs, lowercase
-- instead of ``Afghanistan``, ``Åland Islands``, ``Albania``.

Same harness as ``test_initiative_admin``: an in-memory SQLite engine bound to
the plugin's scoped Session, with CKAN's core ``group`` / ``member`` tables
created alongside the plugin's own (they all live on the shared metadata).
"""
import pytest

try:
    import sqlalchemy as sa  # noqa: F401
    from ckan.model.group import group_table, member_table
    from ckanext.csunesco import db
    from ckanext.csunesco.logic import validators as v
    HAVE_CKAN = True
except Exception:  # pragma: no cover - environment without CKAN
    HAVE_CKAN = False

pytestmark = pytest.mark.skipif(
    not HAVE_CKAN, reason="requires CKAN (ckan.model + sqlalchemy)")


@pytest.fixture
def session():
    import sqlalchemy
    engine = sqlalchemy.create_engine('sqlite://')
    db.ensure_mappers()
    tables = list(db._ALL_TABLES) + [group_table, member_table]
    db.metadata.create_all(bind=engine, tables=tables)
    db.Session.remove()
    db.Session.configure(bind=engine)
    try:
        yield db.Session
    finally:
        db.Session.remove()
        engine.dispose()


def _group(session, name, title, state='active'):
    """Insert a core ``group`` row directly (no ORM: CKAN's session hooks
    would touch tables this fixture does not create)."""
    group_id = 'grp-%s' % name
    session.execute(group_table.insert().values(
        id=group_id, name=name, title=title, type='group',
        state=state, is_organization=False, approval_status='approved'))
    session.commit()
    return group_id


def _child(session, parent_id, child_id, state='active'):
    """Make ``child_id`` a child GROUP of ``parent_id`` (table_name='group')."""
    session.execute(member_table.insert().values(
        id='mem-%s-%s' % (parent_id, child_id),
        group_id=parent_id, table_id=child_id, table_name='group',
        capacity='public', state=state))
    session.commit()


def _seed(session):
    parent = _group(session, 'member-states', 'Member States')
    for name, title in (('afghanistan', 'Afghanistan'),
                        ('-land-islands', u'\xc5land Islands'),
                        ('albania', 'Albania'),
                        ('zimbabwe', 'Zimbabwe')):
        _child(session, parent, _group(session, name, title))
    return parent


def test_returns_real_titles_not_slugs(session):
    """THE bug: the picker must say "Afghanistan", never "afghanistan"."""
    _seed(session)
    titles = [choice['title'] for choice in db.member_state_choices()]
    assert 'Afghanistan' in titles
    assert u'\xc5land Islands' in titles
    assert 'afghanistan' not in titles
    assert '-land-islands' not in titles


def test_sorts_accent_folded(session):
    """Plain .lower() sorts by code point, which puts "Åland Islands" after
    "Zimbabwe" -- at the very bottom of a 200-entry list, where nobody would
    look for the first country alphabetically."""
    _seed(session)
    titles = [choice['title'] for choice in db.member_state_choices()]
    assert titles == ['Afghanistan', u'\xc5land Islands', 'Albania',
                      'Zimbabwe']


def test_excludes_deleted_children_and_memberships(session):
    parent = _seed(session)
    # A child group that was deleted...
    _child(session, parent, _group(session, 'atlantis', 'Atlantis',
                                   state='deleted'))
    # ...and one whose MEMBERSHIP was revoked but whose group still exists.
    _child(session, parent, _group(session, 'narnia', 'Narnia'),
           state='deleted')
    names = {choice['name'] for choice in db.member_state_choices()}
    assert 'atlantis' not in names
    assert 'narnia' not in names


def test_missing_parent_group_returns_empty_without_raising(session):
    """An un-seeded portal must not break the form -- countries are optional.

    The view turns this into a "not configured on this portal" message rather
    than the bare empty box it used to render.
    """
    assert db.member_state_choices() == []


def test_agrees_with_the_validator(session):
    """The picker can never offer a value the schema would reject.

    ``db.member_state_choices`` and ``validators._member_state_names`` run
    separate queries over the same relationship -- the validator takes its
    ``model`` from the navl context and cannot reuse the db helper. This is
    what keeps that duplication honest.
    """
    import ckan.model as model
    _seed(session)
    offered = {choice['name'] for choice in db.member_state_choices()}
    accepted = v._member_state_names(model)
    assert offered == accepted


def test_falls_back_to_the_slug_when_a_title_is_missing(session):
    """A group with no title still renders something, not a blank row."""
    parent = _seed(session)
    _child(session, parent, _group(session, 'nowhere', None))
    match = [c for c in db.member_state_choices() if c['name'] == 'nowhere']
    assert match and match[0]['title'] == 'nowhere'
