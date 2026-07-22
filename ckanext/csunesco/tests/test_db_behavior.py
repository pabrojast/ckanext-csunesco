# encoding: utf-8
"""Behavioral ORM tests for ckanext-csunesco against a REAL SQLAlchemy engine.

No web stack, no Postgres/Solr: every test builds a FRESH in-memory SQLite
engine, creates the plugin's ``cs_*`` tables on CKAN's shared metadata and binds the
plugin's module-level scoped ``Session`` to that engine. This proves the classic
``Table`` + ``mapper`` wiring, the column defaults, the ``UniqueConstraint`` and
the pure/data helpers (dictize, unique-slug, stats SQL) all produce a working
ORM -- not just that the modules import.

Import-safe under real CKAN and skips cleanly when CKAN is absent, but it MUST
actually run and pass inside the ckan-dev container.
"""
import json

import pytest

try:
    import sqlalchemy as sa
    from sqlalchemy.exc import IntegrityError
    import ckan  # noqa: F401  -- ensure the real CKAN model layer is importable
    from ckanext.csunesco import db
    HAVE_CKAN = True
except Exception:  # pragma: no cover - environment without CKAN
    HAVE_CKAN = False

pytestmark = pytest.mark.skipif(
    not HAVE_CKAN, reason="requires CKAN (ckan.model + sqlalchemy)")


@pytest.fixture
def session():
    """A fresh in-memory SQLite DB with the plugin's cs_* tables + bound Session.

    Wires the classic mappers once, creates ONLY the plugin's tables on the
    shared metadata against a throwaway engine, and reconfigures the plugin's
    module-level scoped ``Session`` to that engine so helpers that hard-code
    ``db.Session`` (unique_slug, stats_increment, dictize fetches, ...) run
    against this isolated database. Torn down per test for full isolation.
    """
    engine = sa.create_engine('sqlite://')
    db.ensure_mappers()
    db.metadata.create_all(bind=engine, tables=db._ALL_TABLES)
    db.Session.remove()
    db.Session.configure(bind=engine)
    try:
        yield db.Session
    finally:
        db.Session.remove()
        engine.dispose()


# ---------------------------------------------------------------------------
# Mapper wiring + column defaults: insert + query each mapped class
# ---------------------------------------------------------------------------

def test_project_roundtrip_and_defaults(session):
    project = db.CsProject()
    project.slug = 'river-x'
    project.title = 'River X'
    project.status = 'approved'
    session.add(project)
    session.commit()

    got = session.query(db.CsProject).filter_by(slug='river-x').one()
    assert got.id, 'uuid primary-key default should populate on insert'
    assert got.title == 'River X'
    assert got.created is not None, '_utcnow default should populate'
    assert got.modified is not None


def test_project_member_defaults(session):
    member = db.CsProjectMember()
    member.project_id = 'p1'
    member.user_id = 'u1'
    session.add(member)
    session.commit()

    got = session.query(db.CsProjectMember).one()
    assert got.role == 'scientist'      # column default
    assert got.status == 'pending'      # column default
    assert got.source == 'ckan'         # column default


def test_content_roundtrip_and_boolean_default(session):
    content = db.CsContent()
    content.slug = 'news-1'
    content.content_type = 'cs-news'
    content.title = 'Hello'
    content.body = '<b>hi</b>'
    session.add(content)
    session.commit()

    got = session.query(db.CsContent).filter_by(slug='news-1').one()
    assert got.featured is False        # Boolean default
    assert got.status == 'draft'        # column default
    assert got.body == '<b>hi</b>'


def test_stats_roundtrip_zero_defaults(session):
    stats = db.CsProjectStats()
    stats.project_id = 'p1'
    session.add(stats)
    session.commit()

    got = session.query(db.CsProjectStats).one()
    assert got.citizen_scientists == 0
    assert got.observations == 0
    assert got.sites_monitored == 0
    assert got.member_states == 0


def test_citizen_scientist_roundtrip(session):
    profile = db.CsCitizenScientist()
    profile.user_id = 'user-9'
    session.add(profile)
    session.commit()

    got = session.query(db.CsCitizenScientist).filter_by(user_id='user-9').one()
    assert got.id
    assert got.created is not None


# ---------------------------------------------------------------------------
# UniqueConstraint(project_id, user_id) on cs_project_member
# ---------------------------------------------------------------------------

def test_project_member_unique_constraint(session):
    first = db.CsProjectMember()
    first.project_id = 'p1'
    first.user_id = 'u1'
    session.add(first)
    session.commit()

    dup = db.CsProjectMember()
    dup.project_id = 'p1'
    dup.user_id = 'u1'
    session.add(dup)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # A different user for the same project is fine.
    other = db.CsProjectMember()
    other.project_id = 'p1'
    other.user_id = 'u2'
    session.add(other)
    session.commit()
    assert session.query(db.CsProjectMember).count() == 2


def test_citizen_scientist_unique_user(session):
    first = db.CsCitizenScientist()
    first.user_id = 'u1'
    session.add(first)
    session.commit()

    dup = db.CsCitizenScientist()
    dup.user_id = 'u1'
    session.add(dup)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


# ---------------------------------------------------------------------------
# Dictize helpers (parse extras/countries/media, native keys win)
# ---------------------------------------------------------------------------

def test_project_dictize_parses_and_merges(session):
    project = db.CsProject()
    project.slug = 'rio'
    project.title = 'Rio'
    project.status = 'approved'
    project.countries = json.dumps(['Chile', 'Peru'])
    project.extras = json.dumps({'website': 'http://x.org',
                                 'status': 'SHOULD_NOT_CLOBBER'})
    session.add(project)
    session.commit()

    row = db.get_project('rio')
    result = db.project_dictize(row)
    assert result['slug'] == 'rio'
    assert result['countries'] == ['Chile', 'Peru']
    assert result['website'] == 'http://x.org'   # merged from extras
    # extras must NOT clobber a native column (setdefault semantics).
    assert result['status'] == 'approved'


def test_project_dictize_none_returns_none():
    assert db.project_dictize(None) is None


def test_content_dictize_summary_vs_full(session):
    content = db.CsContent()
    content.slug = 'n1'
    content.content_type = 'cs-news'
    content.title = 'N'
    content.body = '<b>hello</b>'
    content.media = json.dumps(['http://img/a.png'])
    content.extras = json.dumps({'excerpt': 'teaser'})
    session.add(content)
    session.commit()

    row = db.get_content('n1')
    full = db.content_dictize(row, summary=False)
    assert full['body'] == '<b>hello</b>'
    assert full['media'] == ['http://img/a.png']
    assert full['excerpt'] == 'teaser'
    assert full['featured'] is False

    summary = db.content_dictize(row, summary=True)
    assert 'body' not in summary          # deferred/omitted for list rows
    assert summary['media'] == ['http://img/a.png']


def test_data_source_defaults_and_unique_project_form(session):
    ds = db.CsDataSource()
    ds.project_id = 'p1'
    ds.form_id = 7
    ds.title = 'Water quality'
    session.add(ds)
    session.commit()

    got = session.query(db.CsDataSource).one()
    assert got.id, 'uuid primary-key default should populate on insert'
    assert got.status == 'pending'      # column default: ALWAYS reviewed
    assert got.source == 'ckan'         # column default
    assert got.created is not None

    dup = db.CsDataSource()
    dup.project_id = 'p1'
    dup.form_id = 7
    dup.title = 'Duplicate'
    session.add(dup)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_data_source_dictize_merges_extras(session):
    ds = db.CsDataSource()
    ds.project_id = 'p1'
    ds.form_id = 3
    ds.title = 'T'
    ds.extras = json.dumps({'resource_ids': ['r1', 'r2']})
    session.add(ds)
    session.commit()

    result = db.data_source_dictize(session.query(db.CsDataSource).one())
    assert result['resource_ids'] == ['r1', 'r2']
    assert result['status'] == 'pending'
    assert result['form_id'] == 3
    assert db.data_source_dictize(None) is None


class _FakeUser:
    def __init__(self, user_id, sysadmin):
        self.id = user_id
        self.sysadmin = sysadmin


def test_pending_counts_includes_data_requests_for_sysadmin(session):
    ds = db.CsDataSource()
    ds.project_id = 'p1'
    ds.form_id = 1
    ds.title = 'T'
    session.add(ds)
    session.commit()

    counts = db.pending_counts({'auth_user_obj': _FakeUser('u1', True)})
    assert counts['data_requests'] == 1
    assert counts['total'] == counts['project_requests'] \
        + counts['join_requests'] + counts['content_requests'] + 1


def test_pending_counts_hides_data_requests_from_project_admin(session):
    member = db.CsProjectMember()
    member.project_id = 'p1'
    member.user_id = 'u2'
    member.role = 'admin'
    member.status = 'active'
    session.add(member)
    ds = db.CsDataSource()
    ds.project_id = 'p1'
    ds.form_id = 2
    ds.title = 'T'
    session.add(ds)
    session.commit()

    counts = db.pending_counts({'auth_user_obj': _FakeUser('u2', False)})
    assert counts['data_requests'] == 0


def test_content_dictize_promotes_type_extras(session):
    # Publication / map metadata lives in extras (no dedicated columns) and
    # must surface at the top level via the setdefault merge.
    content = db.CsContent()
    content.slug = 'm1'
    content.content_type = 'cs-map'
    content.title = 'M'
    content.extras = json.dumps({
        'terria_url': 'https://maps.example/terria/#share=g-1',
        'doi': '10.1234/abcd',
        'authors': 'A. Author',
    })
    session.add(content)
    session.commit()

    result = db.content_dictize(db.get_content('m1'))
    assert result['terria_url'] == 'https://maps.example/terria/#share=g-1'
    assert result['doi'] == '10.1234/abcd'
    assert result['authors'] == 'A. Author'


# ---------------------------------------------------------------------------
# unique_slug / unique_content_slug (collision -> suffixed)
# ---------------------------------------------------------------------------

def test_unique_slug_suffixes_on_collision(session):
    project = db.CsProject()
    project.slug = 'river'
    project.title = 'River'
    session.add(project)
    session.commit()

    assert db.unique_slug('River') == 'river-2'
    assert db.unique_slug('Brand New Project') == 'brand-new-project'


def test_unique_content_slug_suffixes_on_collision(session):
    content = db.CsContent()
    content.slug = 'news'
    content.content_type = 'cs-news'
    content.title = 'n'
    session.add(content)
    session.commit()

    assert db.unique_content_slug('News') == 'news-2'
    assert db.unique_content_slug('Fresh Item') == 'fresh-item'


# ---------------------------------------------------------------------------
# stats_increment: atomic SET x = x + :delta, validated field whitelist
# ---------------------------------------------------------------------------

def test_stats_increment_accumulates(session):
    stats = db.CsProjectStats()
    stats.project_id = 'p1'
    stats.citizen_scientists = 0
    stats.observations = 0
    stats.sites_monitored = 0
    stats.member_states = 0
    session.add(stats)
    session.commit()

    # The helper's own SELECT is the source of truth for the freshly written
    # value (SET x = x + :delta, then read back).
    assert db.stats_increment('p1', 'observations', 5) == 5
    assert db.stats_increment('p1', 'observations', 3) == 8
    session.commit()

    # CKAN's Session is expire_on_commit=False, so the identity-mapped instance
    # still holds its stale attribute after the raw UPDATE. Expire it to force a
    # reload from the DB and prove the increment actually persisted.
    session.expire_all()
    got = session.query(db.CsProjectStats).one()
    assert got.observations == 8


def test_stats_increment_rejects_unknown_field(session):
    with pytest.raises(ValueError):
        db.stats_increment('p1', 'not_a_real_field', 1)


# ---------------------------------------------------------------------------
# aggregate_stats: single JOIN'd SELECT restricted to approved projects
# ---------------------------------------------------------------------------

def test_aggregate_stats_only_counts_approved(session):
    approved = db.CsProject()
    approved.slug = 'a'
    approved.title = 'A'
    approved.status = 'approved'
    approved.countries = json.dumps(['Chile', 'Peru'])
    pending = db.CsProject()
    pending.slug = 'b'
    pending.title = 'B'
    pending.status = 'pending'
    pending.countries = json.dumps(['France'])   # excluded (not approved)
    session.add_all([approved, pending])
    session.flush()

    for project, observations in ((approved, 10), (pending, 99)):
        stats = db.CsProjectStats()
        stats.project_id = project.id
        stats.observations = observations
        stats.sites_monitored = 2
        session.add(stats)
    # Citizen scientists = registered profiles UNION active members of
    # approved projects (u1 is both -> counted once; pending member ignored).
    profile = db.CsCitizenScientist()
    profile.user_id = 'u1'
    active = db.CsProjectMember()
    active.project_id = approved.id
    active.user_id = 'u1'
    active.status = 'active'
    other = db.CsProjectMember()
    other.project_id = approved.id
    other.user_id = 'u2'
    other.status = 'active'
    waiting = db.CsProjectMember()
    waiting.project_id = approved.id
    waiting.user_id = 'u3'
    waiting.status = 'pending'
    session.add_all([profile, active, other, waiting])
    session.commit()

    agg = db.aggregate_stats()
    assert agg['observations'] == 10       # pending project excluded
    assert agg['sites_monitored'] == 2
    assert agg['citizen_scientists'] == 2  # u1 (deduped) + u2
    assert agg['member_states'] == 2       # Chile + Peru (France excluded)


def test_stats_set_writes_absolute_values(session):
    project = db.CsProject()
    project.slug = 'abs'
    project.title = 'Abs'
    project.status = 'approved'
    session.add(project)
    session.flush()

    db.stats_set(project.id, observations=123, sites_monitored=7)
    session.commit()
    stats = db.get_stats(project.id)
    assert stats.observations == 123
    assert stats.sites_monitored == 7

    # Absolute semantics: a second refresh REPLACES, never accumulates.
    db.stats_set(project.id, observations=50)
    session.commit()
    stats = db.get_stats(project.id)
    assert stats.observations == 50
    assert stats.sites_monitored == 7      # untouched field preserved


def test_aggregate_stats_zero_when_empty(session):
    agg = db.aggregate_stats()
    assert agg == {
        'citizen_scientists': 0, 'observations': 0,
        'sites_monitored': 0, 'member_states': 0,
    }
