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
    # pending_counts resolves the initiative-admin (ADM) role from CKAN's own
    # group/member tables (shared metadata), so they must exist here too.
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


def test_project_dictize_includes_image_url(session):
    project = db.CsProject()
    project.slug = 'img-proj'
    project.title = 'Img'
    project.image_url = '/csunesco/images/project-ghana.jpg'
    session.add(project)
    session.commit()

    result = db.project_dictize(db.get_project('img-proj'))
    assert result['image_url'] == '/csunesco/images/project-ghana.jpg'


# ---------------------------------------------------------------------------
# merge_legacy_fields (the seed-legacy-projects merge rule)
# ---------------------------------------------------------------------------

def _seed_fields(**overrides):
    seed = {
        'title': 'Ghana',
        'short_description': 'Seed description.',
        'initiative_group': 'riverwatch',
        'countries': json.dumps(['ghana']),
        'biosphere_reserve': '',
        'image_url': '/csunesco/images/project-ghana.jpg',
    }
    seed.update(overrides)
    return seed


def test_merge_legacy_fields_fills_only_empty():
    project = db.CsProject()
    project.title = 'Custom title set by an admin'
    project.countries = '[]'      # JSON-empty counts as empty -> filled
    changed = db.merge_legacy_fields(project, _seed_fields())
    assert 'title' not in changed                    # non-empty -> kept
    assert project.title == 'Custom title set by an admin'
    assert 'countries' in changed
    assert project.countries == json.dumps(['ghana'])
    assert project.image_url == '/csunesco/images/project-ghana.jpg'


def test_merge_legacy_fields_force_overwrites_but_never_blanks():
    project = db.CsProject()
    project.title = 'Custom title'
    project.biosphere_reserve = 'Admin-set reserve'
    changed = db.merge_legacy_fields(project, _seed_fields(), force=True)
    assert 'title' in changed
    assert project.title == 'Ghana'
    # An EMPTY seed value must not blank an admin-set field, even with force.
    assert project.biosphere_reserve == 'Admin-set reserve'
    assert 'biosphere_reserve' not in changed


def test_merge_legacy_fields_idempotent():
    project = db.CsProject()
    assert db.merge_legacy_fields(project, _seed_fields(), force=True)
    # Second run: nothing changes either way.
    assert db.merge_legacy_fields(project, _seed_fields()) == []
    assert db.merge_legacy_fields(project, _seed_fields(), force=True) == []


def test_merge_legacy_fields_never_touches_unmanaged_fields():
    project = db.CsProject()
    project.status = 'approved'
    project.trusted = True
    project.landing_content = '<p>legacy</p>'
    seed = dict(_seed_fields(), status='pending', trusted=False,
                landing_content='<p>evil</p>', extras='{"x": 1}')
    db.merge_legacy_fields(project, seed, force=True)
    assert project.status == 'approved'
    assert project.trusted is True
    assert project.landing_content == '<p>legacy</p>'


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


# ---------------------------------------------------------------------------
# P2: join audit trail (reviewed_by/reviewed_at) + trusted flag
# ---------------------------------------------------------------------------

def test_set_member_status_persists_reviewer(session):
    member = db.CsProjectMember()
    member.project_id = 'p9'
    member.user_id = 'u9'
    session.add(member)
    session.commit()

    got = db.set_member_status('p9', 'u9', 'active', reviewed_by='reviewer-1')
    session.commit()
    assert got.status == 'active'
    assert got.reviewed_by == 'reviewer-1'
    assert got.reviewed_at is not None

    row = db.member_dictize(db.project_member('p9', 'u9'))
    assert row['reviewed_by'] == 'reviewer-1'
    assert row['reviewed_at'] is not None

    # Without a reviewer the previous audit values are preserved.
    db.set_member_status('p9', 'u9', 'rejected')
    session.commit()
    kept = db.project_member('p9', 'u9')
    assert kept.status == 'rejected'
    assert kept.reviewed_by == 'reviewer-1'


def test_project_trusted_default_false_and_dictized(session):
    project = db.CsProject()
    project.slug = 'trusty'
    project.title = 'Trusty'
    project.status = 'approved'
    session.add(project)
    session.commit()

    got = session.query(db.CsProject).filter_by(slug='trusty').one()
    assert bool(got.trusted) is False           # column default
    assert db.project_dictize(got)['trusted'] is False

    got.trusted = True
    session.commit()
    assert db.project_dictize(got)['trusted'] is True


# ---------------------------------------------------------------------------
# Project pages (block-composed landing)
# ---------------------------------------------------------------------------

def test_project_page_roundtrip_and_defaults(session):
    page = db.CsProjectPage()
    page.project_id = 'p-page'
    page.draft_json = '[{"id":"aaaaaaaa","type":"rich_text","html":"<p>x</p>"}]'
    session.add(page)
    session.commit()

    got = session.query(db.CsProjectPage).get('p-page')
    assert got.status == 'draft', 'status column default'
    assert got.published_json is None, 'a new page is not published'
    assert got.created is not None and got.modified is not None


def test_page_dictize_separates_never_published_from_emptied(session):
    """``None`` (fall back to the default layout) and ``[]`` (deliberately
    emptied) must not collapse -- ``_load_json`` would merge them."""
    page = db.CsProjectPage()
    page.project_id = 'p-empty'
    session.add(page)
    session.commit()
    assert db.page_dictize(page)['published_blocks'] is None

    page.published_json = '[]'
    session.commit()
    assert db.page_dictize(page)['published_blocks'] == []


def test_page_dictize_parses_and_normalizes_blocks(session):
    page = db.CsProjectPage()
    page.project_id = 'p-blocks'
    page.published_json = ('[{"id":"aaaaaaaa","type":"callout","tone":"info"},'
                           '{"type":"from_the_future"}]')
    page.draft_json = '[{"id":"bbbbbbbb","type":"rich_text"}]'
    session.add(page)
    session.commit()

    public = db.page_dictize(page)
    # The unknown type is dropped on read, so one stale row cannot 500 a page.
    assert [b['type'] for b in public['published_blocks']] == ['callout']
    # The draft is withheld unless the caller explicitly asks for it.
    assert 'draft_blocks' not in public
    assert [b['type'] for b in
            db.page_dictize(page, include_draft=True)['draft_blocks']] \
        == ['rich_text']


def test_page_dictize_survives_corrupt_json(session):
    page = db.CsProjectPage()
    page.project_id = 'p-corrupt'
    page.published_json = '{not json at all'
    session.add(page)
    session.commit()
    assert db.page_dictize(page)['published_blocks'] == []


def test_get_or_create_project_page_is_idempotent(session):
    first = db.get_or_create_project_page('p-once', created_by='u1')
    session.commit()
    second = db.get_or_create_project_page('p-once', created_by='u2')
    session.commit()
    assert first.project_id == second.project_id == 'p-once'
    assert second.created_by == 'u1', 'the existing row is reused, not reset'
    assert session.query(db.CsProjectPage).count() == 1


def test_pending_pages_lists_only_pending_rows(session):
    project = db.CsProject()
    project.slug = 'page-proj'
    project.title = 'Page Proj'
    project.initiative_group = 'riverwatch'
    project.status = 'approved'
    session.add(project)
    session.commit()

    for project_id, status in (('other', 'draft'), (project.id, 'pending')):
        page = db.CsProjectPage()
        page.project_id = project_id
        page.status = status
        session.add(page)
    session.commit()

    total, rows = db.pending_pages()
    assert total == 1
    assert rows[0]['project_id'] == project.id
    # The listing carries what the review tab needs without loading the JSON.
    assert rows[0]['project_title'] == 'Page Proj'
    assert rows[0]['project_slug'] == 'page-proj'
    assert 'draft_json' not in rows[0]


def test_pending_pages_never_lists_the_site_page(session):
    """The site (hub) page never enters 'pending' by design, but if a bug
    ever left it there the review tab must not show a NULL-titled row."""
    page = db.CsProjectPage()
    page.project_id = db.SITE_PAGE_ID
    page.status = 'pending'          # forced by hand: cannot happen via actions
    session.add(page)
    session.commit()

    total, rows = db.pending_pages()
    assert total == 0
    assert rows == []
    assert db._count_pending_pages() == 0


def test_site_page_row_round_trips(session):
    """The sentinel row reuses the whole draft/published lifecycle."""
    page = db.get_or_create_project_page(db.SITE_PAGE_ID, created_by='u1')
    page.draft_json = '[{"type": "site_hero", "id": "abcdef01"}]'
    session.commit()

    again = db.get_or_create_project_page(db.SITE_PAGE_ID)
    assert again is page
    result = db.page_dictize(db.get_project_page(db.SITE_PAGE_ID),
                             include_draft=True)
    assert result['project_id'] == db.SITE_PAGE_ID
    assert result['published_blocks'] is None
    assert result['draft_blocks'][0]['type'] == 'site_hero'


def test_pending_counts_key_set_never_drifts(session):
    """The guard: a new queue must reach the zero dict, BOTH branches and the
    total. Anything half-wired shows up here first."""
    expected = {'project_requests', 'join_requests', 'content_requests',
                'data_requests', 'page_requests', 'total'}

    page = db.CsProjectPage()
    page.project_id = 'p-count'
    page.status = 'pending'
    session.add(page)
    session.commit()

    contexts = {
        'anonymous': {},
        'sysadmin': {'auth_user_obj': _FakeUser('u-sys', True)},
        'plain_user': {'auth_user_obj': _FakeUser('u-nobody', False)},
    }
    for label, context in contexts.items():
        counts = db.pending_counts(context)
        assert set(counts) == expected, label
        assert counts['total'] == sum(
            value for key, value in counts.items() if key != 'total'), label

    assert db.pending_counts(contexts['sysadmin'])['page_requests'] == 1
    # A user with no role sees zeros, not somebody else's queue.
    assert db.pending_counts(contexts['plain_user'])['page_requests'] == 0


# ---------------------------------------------------------------------------
# Data-chat quota counter
# ---------------------------------------------------------------------------

def test_chat_usage_starts_at_zero_and_counts_up(session):
    assert db.chat_usage_count('user-1', '2026-07-25') == 0
    assert db.bump_chat_usage('user-1', '2026-07-25') == 1
    assert db.bump_chat_usage('user-1', '2026-07-25') == 2
    session.commit()
    assert db.chat_usage_count('user-1', '2026-07-25') == 2


def test_chat_usage_is_per_user_and_per_day(session):
    db.bump_chat_usage('user-1', '2026-07-25')
    db.bump_chat_usage('user-1', '2026-07-25')
    db.bump_chat_usage('user-2', '2026-07-25')
    db.bump_chat_usage('user-1', '2026-07-26')
    session.commit()
    assert db.chat_usage_count('user-1', '2026-07-25') == 2
    assert db.chat_usage_count('user-2', '2026-07-25') == 1
    # Yesterday's spend must not follow the user into today.
    assert db.chat_usage_count('user-1', '2026-07-26') == 1


def test_chat_usage_keeps_one_row_per_user_day(session):
    for _ in range(5):
        db.bump_chat_usage('user-1', '2026-07-25')
    session.commit()
    rows = (session.query(db.CsChatUsage)
            .filter_by(user_id='user-1', day='2026-07-25').all())
    assert len(rows) == 1
    assert rows[0].calls == 5


def test_chat_usage_ignores_a_missing_user_or_day(session):
    """A caller with no user id must not silently share one anonymous bucket."""
    assert db.bump_chat_usage(None, '2026-07-25') == 0
    assert db.bump_chat_usage('user-1', None) == 0
    assert db.chat_usage_count(None, '2026-07-25') == 0
    session.commit()
    assert session.query(db.CsChatUsage).count() == 0


# ---------------------------------------------------------------------------
# Auto-heal: el modelo y la lista de columnas curables no pueden divergir
# ---------------------------------------------------------------------------

def test_every_declared_column_is_creatable_on_an_existing_deployment():
    """``create_all`` sólo crea tablas que faltan: nunca añade una columna a una
    tabla que ya existe. Por eso una columna nueva DEBE estar también en
    ``_AUTO_HEAL_COLUMNS`` — si no, el despliegue de producción arranca con la
    tabla vieja y revienta al primer SELECT que la nombre.

    Este test no exige que TODA columna esté en la lista (las del release
    inicial no hacen falta): exige que la lista siga cubriendo lo que cubre, y
    que no nombre columnas que ya no existen. Lo segundo es lo que se pudre en
    silencio, porque un ALTER sobre una columna inexistente sólo deja un
    log.error genérico.
    """
    declared = {table.name: {c.name for c in table.columns}
                for table in db._ALL_TABLES}

    unknown = []
    for table_name, column_name, _type in db._AUTO_HEAL_COLUMNS:
        if table_name not in declared:
            unknown.append('%s (tabla desconocida)' % table_name)
        elif column_name not in declared[table_name]:
            unknown.append('%s.%s' % (table_name, column_name))
    assert not unknown, (
        'auto-heal nombra columnas que el modelo ya no declara: %s' % unknown)


def test_auto_heal_covers_the_columns_added_after_each_table_shipped():
    """Guarda de regresión con nombres explícitos.

    Cada entrada aquí se añadió a una tabla YA desplegada, así que sin su fila
    en ``_AUTO_HEAL_COLUMNS`` el portal en producción se queda sin ella. La
    lista se escribe a mano a propósito: si alguien añade una columna nueva al
    modelo y no la cura, este test no lo detecta — pero el día que alguien
    BORRE una de estas curaciones, sí.
    """
    healed = {(t, c) for t, c, _ in db._AUTO_HEAL_COLUMNS}
    for pair in [
        ('cs_project', 'trusted'),
        ('cs_project_member', 'reviewed_by'),
        ('cs_content', 'slug'),
        ('cs_content', 'extras'),
        ('cs_project_stats', 'member_states'),
        ('cs_citizen_scientist', 'email_verified'),
    ]:
        assert pair in healed, '%s dejó de auto-curarse' % (pair,)


# ---------------------------------------------------------------------------
# "Your projects" (the PM's way back to their own project)
# ---------------------------------------------------------------------------

def _project(session, slug, title, status='approved', initiative='riverwatch'):
    project = db.CsProject()
    project.slug = slug
    project.title = title
    project.status = status
    project.initiative_group = initiative
    session.add(project)
    session.flush()
    return project


def _member(session, project_id, user_id, role='admin', status='active'):
    member = db.CsProjectMember()
    member.project_id = project_id
    member.user_id = user_id
    member.role = role
    member.status = status
    session.add(member)
    session.flush()
    return member


def test_projects_administered_lists_only_active_admin_memberships(session):
    mine = _project(session, 'mine', 'Mine')
    as_scientist = _project(session, 'sci', 'As scientist')
    not_yet = _project(session, 'pend', 'Membership pending')
    someone_else = _project(session, 'other', 'Someone else')
    _member(session, mine.id, 'u1')
    _member(session, as_scientist.id, 'u1', role='scientist')
    _member(session, not_yet.id, 'u1', status='pending')
    _member(session, someone_else.id, 'u2')
    session.commit()

    got = db.projects_administered('u1')
    assert [p['slug'] for p in got] == ['mine']
    assert got[0]['title'] == 'Mine'
    assert got[0]['status'] == 'approved'
    assert got[0]['initiative_group'] == 'riverwatch'
    assert set(got[0]) == {'id', 'slug', 'title', 'status', 'initiative_group'}


def test_projects_administered_includes_pending_and_rejected(session):
    """The queued request is exactly when a manager has no other way in."""
    for slug, status in (('a', 'pending'), ('b', 'rejected'), ('c', 'approved')):
        project = _project(session, slug, slug.upper(), status=status)
        _member(session, project.id, 'u1')
    session.commit()

    got = db.projects_administered('u1')
    assert {p['status'] for p in got} == {'pending', 'rejected', 'approved'}


def test_projects_administered_is_ordered_by_title(session):
    for slug, title in (('c', 'Charlie'), ('a', 'Alpha'), ('b', 'Bravo')):
        project = _project(session, slug, title)
        _member(session, project.id, 'u1')
    session.commit()
    assert [p['title'] for p in db.projects_administered('u1')] == \
        ['Alpha', 'Bravo', 'Charlie']


def test_projects_administered_is_empty_without_a_user(session):
    project = _project(session, 'mine', 'Mine')
    _member(session, project.id, 'u1')
    session.commit()
    assert db.projects_administered(None) == []
    assert db.projects_administered('') == []
    assert db.projects_administered('nobody') == []


def test_projects_administered_honours_the_limit(session):
    for index in range(5):
        project = _project(session, 'p%d' % index, 'Project %d' % index)
        _member(session, project.id, 'u1')
    session.commit()
    assert len(db.projects_administered('u1', limit=2)) == 2


# ---------------------------------------------------------------------------
# Rejected content: the author has to be able to find it AND read why
# ---------------------------------------------------------------------------

def test_rejection_reason_survives_the_summary_dictize(session):
    """The landing card is a SUMMARY row, so the reason has to ride in extras.

    ``content_dictize`` merges extras in both modes; if that ever changed, the
    rejection chip on the project page would silently render blank.
    """
    content = db.CsContent()
    content.slug = 'n1'
    content.content_type = 'cs-news'
    content.title = 'N'
    content.status = 'rejected'
    content.body = '<b>hello</b>'
    content.extras = json.dumps({'excerpt': 'teaser',
                                 'rejection_reason': 'Please add a source.'})
    session.add(content)
    session.commit()

    summary = db.content_dictize(db.get_content('n1'), summary=True)
    assert 'body' not in summary
    assert summary['status'] == 'rejected'
    assert summary['rejection_reason'] == 'Please add a source.'


def test_list_content_unfiltered_returns_every_status(session):
    """What a project manager gets for their OWN project; the public call
    passes status='approved' and must still get only that."""
    for slug, status in (('a', 'approved'), ('b', 'pending'), ('c', 'rejected')):
        content = db.CsContent()
        content.slug = slug
        content.content_type = 'cs-news'
        content.title = slug.upper()
        content.project_id = 'p1'
        content.status = status
        session.add(content)
    session.commit()

    total, rows = db.list_content(project_id='p1')
    assert total == 3
    assert {row.status for row in rows} == {'approved', 'pending', 'rejected'}

    total, rows = db.list_content(project_id='p1', status='approved')
    assert total == 1
    assert rows[0].slug == 'a'
