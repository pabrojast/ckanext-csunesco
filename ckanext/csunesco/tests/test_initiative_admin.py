# encoding: utf-8
"""Behavioral tests for the initiative-admin (ADM) helpers (P1, matriz CST).

Same harness as ``test_db_behavior``: a fresh in-memory SQLite engine bound to
the plugin's scoped ``Session``. Because the plugin declares its tables on
CKAN's SHARED metadata (``ckan.model.meta``), the fixture can also create the
core ``group`` / ``member`` / ``user`` tables and exercise
``admin_initiative_groups`` / ``initiative_admin_user_ids`` — the queries that
resolve the ADM role from CKAN group capacities — against real CKAN models.
"""
import pytest

try:
    import sqlalchemy as sa  # noqa: F401
    from ckan.model.group import group_table, member_table
    from ckanext.csunesco import db
    HAVE_CKAN = True
except Exception:  # pragma: no cover - environment without CKAN
    HAVE_CKAN = False

pytestmark = pytest.mark.skipif(
    not HAVE_CKAN, reason="requires CKAN (ckan.model + sqlalchemy)")


@pytest.fixture
def session():
    """In-memory SQLite with the plugin tables + the core group/member tables
    (all on the shared metadata), bound to the module Session.

    The core ``user`` table is NOT creatable on SQLite (JSONB column), so tests
    that need an acting user pass a plain stub object through the context —
    ``_resolve_user`` only reads attributes, never the table.
    """
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


def _project(session, slug, initiative, status='pending'):
    project = db.CsProject()
    project.slug = slug
    project.title = slug.title()
    project.initiative_group = initiative
    project.status = status
    session.add(project)
    session.commit()
    return project


def _data_source(session, project_id, form_id, status='pending'):
    source = db.CsDataSource()
    source.project_id = project_id
    source.form_id = form_id
    source.title = 'DS %s' % form_id
    source.status = status
    session.add(source)
    session.commit()
    return source


# ---------------------------------------------------------------------------
# Initiative scoping over the plugin's own tables
# ---------------------------------------------------------------------------

def test_initiative_project_ids_scopes_by_group(session):
    river_a = _project(session, 'river-a', 'riverwatch')
    river_b = _project(session, 'river-b', 'riverwatch', status='approved')
    island = _project(session, 'island-a', 'islandwatch')

    ids = set(db.initiative_project_ids(['riverwatch']))
    assert ids == {river_a.id, river_b.id}
    assert set(db.initiative_project_ids(['islandwatch'])) == {island.id}
    assert db.initiative_project_ids([]) == []
    assert db.initiative_project_ids(None) == []


def test_pending_projects_scoped_and_stripped(session):
    pending = _project(session, 'river-a', 'riverwatch')
    pending.region_geojson = '{"type": "FeatureCollection"}'
    session.commit()
    _project(session, 'river-b', 'riverwatch', status='approved')
    _project(session, 'island-a', 'islandwatch')

    total, rows = db.pending_projects(['riverwatch'])
    assert total == 1
    assert [r['slug'] for r in rows] == ['river-a']
    # The heavy blob never rides in list rows (same contract as project_list).
    assert 'region_geojson' not in rows[0]

    assert db.pending_projects([]) == (0, [])
    assert db._count_pending_projects(['riverwatch']) == 1
    assert db._count_pending_projects(['islandwatch']) == 1
    assert db._count_pending_projects([]) == 0
    # None keeps the historical "count everything" (sysadmin) semantics.
    assert db._count_pending_projects(None) == 2


def test_pending_data_sources_initiative_scope(session):
    river = _project(session, 'river-a', 'riverwatch', status='approved')
    island = _project(session, 'island-a', 'islandwatch', status='approved')
    _data_source(session, river.id, 1)
    _data_source(session, island.id, 2)
    _data_source(session, island.id, 3, status='approved')

    total, rows = db.pending_data_sources(initiative_groups=['islandwatch'])
    assert total == 1
    assert [r['form_id'] for r in rows] == [2]
    assert db.pending_data_sources(initiative_groups=[]) == (0, [])
    # None keeps the unrestricted (sysadmin) semantics.
    total_all, _rows = db.pending_data_sources()
    assert total_all == 2

    assert db._count_pending_data_sources(['riverwatch']) == 1
    assert db._count_pending_data_sources([]) == 0
    assert db._count_pending_data_sources(None) == 2


def test_project_admin_user_ids(session):
    project = _project(session, 'river-a', 'riverwatch', status='approved')
    for user_id, role, status in [
        ('u-pm', 'admin', 'active'),
        ('u-pending-pm', 'admin', 'pending'),
        ('u-cs', 'scientist', 'active'),
    ]:
        member = db.CsProjectMember()
        member.project_id = project.id
        member.user_id = user_id
        member.role = role
        member.status = status
        session.add(member)
    session.commit()

    assert db.project_admin_user_ids(project.id) == ['u-pm']
    assert db.project_admin_user_ids(None) == []


# ---------------------------------------------------------------------------
# ADM resolution from CKAN group capacities (core group/member/user tables)
# ---------------------------------------------------------------------------

def _group(session, name, state='active'):
    """Insert a core ``group`` row directly (no ORM: CKAN's session hooks would
    touch tables this fixture does not create). Returns the generated id."""
    group_id = 'grp-%s' % name
    session.execute(group_table.insert().values(
        id=group_id, name=name, title=name.title(), type='group',
        state=state, is_organization=False, approval_status='approved'))
    session.commit()
    return group_id


def _member(session, group_id, user_id, capacity, state='active'):
    """Insert a core ``member`` row directly (same rationale as ``_group``)."""
    session.execute(member_table.insert().values(
        id='mem-%s-%s-%s' % (group_id, user_id, capacity),
        group_id=group_id, table_id=user_id, table_name='user',
        capacity=capacity, state=state))
    session.commit()


def test_admin_initiative_groups_resolves_admin_capacity(session):
    riverwatch = _group(session, 'riverwatch')
    islandwatch = _group(session, 'islandwatch')
    other = _group(session, 'member-states')          # not an initiative

    _member(session, riverwatch, 'u-adm', 'admin')
    _member(session, islandwatch, 'u-adm', 'member')  # plain member: no ADM
    _member(session, other, 'u-adm', 'admin')          # non-initiative group
    _member(session, islandwatch, 'u-deleted', 'admin', state='deleted')

    assert db.admin_initiative_groups('u-adm') == ['riverwatch']
    assert db.admin_initiative_groups('u-deleted') == []
    assert db.admin_initiative_groups('nobody') == []
    assert db.admin_initiative_groups(None) == []


def test_initiative_admin_user_ids(session):
    riverwatch = _group(session, 'riverwatch')
    _member(session, riverwatch, 'u-adm-1', 'admin')
    _member(session, riverwatch, 'u-adm-2', 'admin')
    _member(session, riverwatch, 'u-cs', 'member')

    assert sorted(db.initiative_admin_user_ids('riverwatch')) == [
        'u-adm-1', 'u-adm-2']
    assert db.initiative_admin_user_ids(None) == []
    assert db.initiative_admin_user_ids('') == []


def test_resolve_owner_org_ignores_suggestion_for_non_sysadmin(
        session, monkeypatch):
    """Una sugerencia plantada (extras.owner_org) NUNCA decide la organización
    cuando el aprobador no es sysadmin: con ``honor_suggestion=False`` la
    resolución salta al organization_id del proyecto o al default configurado.
    (Cierra la escalada: ADM crea el source con owner_org ajeno y se
    auto-aprueba — el package correría con ignore_auth.)"""
    import json as _json
    import ckan.plugins.toolkit as tk
    from ckanext.csunesco.logic import package_sync

    monkeypatch.setitem(
        tk.config, 'ckanext.csunesco.dataset_owner_org', 'default-org')
    project = _project(session, 'river-a', 'riverwatch', status='approved')
    source = _data_source(session, project.id, 9)
    source.extras = _json.dumps({'owner_org': 'org-ajena'})
    session.commit()

    # Aprobación sysadmin (honor_suggestion=True): la sugerencia manda.
    assert package_sync.resolve_owner_org(project, source) == 'org-ajena'
    # Aprobación no-sysadmin: la sugerencia se ignora -> default configurado.
    assert package_sync.resolve_owner_org(
        project, source, honor_suggestion=False) == 'default-org'
    # Con organization_id curado en el proyecto, ese gana sobre el default.
    project.organization_id = 'proj-org'
    session.commit()
    assert package_sync.resolve_owner_org(
        project, source, honor_suggestion=False) == 'proj-org'
    # El override explícito (solo llega desde un sysadmin) sigue mandando.
    assert package_sync.resolve_owner_org(
        project, source, override_org='chosen') == 'chosen'


class _StubUser(object):
    """Duck-typed acting user for ``_resolve_user`` (attributes only)."""

    is_anonymous = False
    sysadmin = False

    def __init__(self, user_id):
        self.id = user_id


def test_pending_counts_for_initiative_admin(session):
    """An ADM's counts cover their initiatives' projects/content/joins/data."""
    riverwatch = _group(session, 'riverwatch')
    user = _StubUser('u-adm')
    _member(session, riverwatch, user.id, 'admin')

    pending_project = _project(session, 'river-a', 'riverwatch')
    approved = _project(session, 'river-b', 'riverwatch', status='approved')
    _project(session, 'island-a', 'islandwatch')      # fuera de su scope
    _data_source(session, approved.id, 7)

    join = db.CsProjectMember()
    join.project_id = approved.id
    join.user_id = 'u-cs'
    session.add(join)                                  # status default: pending
    content = db.CsContent()
    content.slug = 'news-1'
    content.content_type = 'cs-news'
    content.project_id = approved.id
    content.initiative_group = 'riverwatch'
    content.title = 'Hello'
    content.status = 'pending'
    session.add(content)
    session.commit()

    counts = db.pending_counts({'auth_user_obj': user})
    assert counts['project_requests'] == 1     # river-a (no island-a)
    assert counts['join_requests'] == 1
    assert counts['content_requests'] == 1
    assert counts['data_requests'] == 1
    assert counts['total'] == 4
    assert pending_project.status == 'pending'
