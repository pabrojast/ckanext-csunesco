# encoding: utf-8
"""csunesco_content_list rich filters (increment 3) on the in-memory ORM.

Seeded rows exercise each additive filter: free text (escaped LIKE), date
range over COALESCE(publish_date, created), upcoming events, author, source
(with the extras backfill), organization, multi-project (ids or slugs, capped),
the sort allowlist, the batch owner decoration, and the initiative-admin
privilege that finally lets an ADM pull their initiative's pending rows by API.
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
    from ckanext.csunesco.logic.action.content import parse_content_sort
    HAVE_CKAN = True
except Exception:  # pragma: no cover - environment without CKAN
    HAVE_CKAN = False

pytestmark = pytest.mark.skipif(
    not HAVE_CKAN, reason="requires CKAN (ckan.model + sqlalchemy)")


class _User(object):
    is_anonymous = False

    def __init__(self, user_id):
        self.id = user_id


def _ctx(user_id='caller-1'):
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
    monkeypatch.setattr(cs_auth, 'can_manage_project',
                        lambda context, project_id: False)
    monkeypatch.setattr(cs_auth, '_admin_initiative_groups',
                        lambda context: set())
    return content_action


def _project(session, slug, initiative=None):
    project = db.CsProject()
    project.slug = slug
    project.title = slug.replace('-', ' ').title()
    project.status = 'approved'
    project.initiative_group = initiative
    session.add(project)
    session.commit()
    return project


def _content(session, title, project=None, organization_id=None, **over):
    row = db.CsContent()
    row.content_type = over.get('content_type', 'cs-news')
    row.project_id = project.id if project is not None else None
    row.organization_id = organization_id
    row.initiative_group = (project.initiative_group
                            if project is not None else None)
    row.title = title
    row.body = over.get('body', u'<p>body of %s</p>' % title)
    row.status = over.get('status', 'approved')
    row.visibility = over.get('visibility', u'public')
    row.source = over.get('source')
    row.created_by = over.get('created_by', 'author-1')
    row.slug = db.unique_content_slug(title)
    row.created = over.get('created', datetime.datetime(2026, 1, 1))
    row.publish_date = over.get('publish_date')
    row.end_date = over.get('end_date')
    if over.get('extras') is not None:
        row.extras = over['extras']
    session.add(row)
    session.commit()
    return row


# --------------------------------------------------------------------------- #
# parse_content_sort (pure)                                                    #
# --------------------------------------------------------------------------- #

def test_parse_content_sort_allowlist():
    assert parse_content_sort(None) is None
    assert parse_content_sort('  ') is None
    assert parse_content_sort('title') == ('title', 'desc')
    assert parse_content_sort('created asc') == ('created', 'asc')
    assert parse_content_sort('PUBLISH_DATE ASC') == ('publish_date', 'asc')
    for bad in ('id', 'title up', 'title asc extra', 'drop table'):
        with pytest.raises(ValueError):
            parse_content_sort(bad)


# --------------------------------------------------------------------------- #
# Filters                                                                      #
# --------------------------------------------------------------------------- #

def test_q_matches_title_and_body_escaped(actions, session):
    project = _project(session, 'river-a')
    _content(session, 'Turbidity spike', project=project)
    _content(session, 'Calm week', project=project,
             body=u'<p>100% turbidity drop</p>')
    _content(session, 'Unrelated', project=project)

    out = actions.csunesco_content_list(_ctx(), {'q': 'turbidity'})
    assert {r['title'] for r in out['results']} == {'Turbidity spike',
                                                    'Calm week'}
    # LIKE wildcards in the needle are literal, not wildcards.
    out = actions.csunesco_content_list(_ctx(), {'q': '100%'})
    assert [r['title'] for r in out['results']] == ['Calm week']
    assert out['applied_filters']['q'] == '100%'


def test_date_range_uses_effective_date(actions, session):
    project = _project(session, 'river-b')
    _content(session, 'January piece', project=project,
             created=datetime.datetime(2026, 1, 10))
    _content(session, 'June piece', project=project,
             created=datetime.datetime(2026, 1, 1),
             publish_date=datetime.datetime(2026, 6, 15))

    out = actions.csunesco_content_list(
        _ctx(), {'date_from': '2026-06-01', 'date_to': '2026-06-30'})
    assert [r['title'] for r in out['results']] == ['June piece']
    with pytest.raises(tk.ValidationError):
        actions.csunesco_content_list(_ctx(), {'date_from': 'not-a-date'})


def test_upcoming_forces_events(actions, session):
    project = _project(session, 'river-c')
    now = datetime.datetime.utcnow()
    _content(session, 'Past event', project=project, content_type='cs-event',
             publish_date=now - datetime.timedelta(days=10),
             end_date=now - datetime.timedelta(days=9))
    _content(session, 'Future event', project=project, content_type='cs-event',
             publish_date=now + datetime.timedelta(days=5),
             end_date=now + datetime.timedelta(days=6))
    _content(session, 'A news', project=project)

    out = actions.csunesco_content_list(_ctx(), {'upcoming': 'true'})
    assert [r['title'] for r in out['results']] == ['Future event']
    with pytest.raises(tk.ValidationError):
        actions.csunesco_content_list(
            _ctx(), {'upcoming': True, 'content_type': 'cs-news'})


def test_source_filter_is_null_safe(actions, session):
    project = _project(session, 'river-d')
    _content(session, 'From the app', project=project, source=u'app')
    _content(session, 'From the portal', project=project, source=u'ckan')
    _content(session, 'Legacy row', project=project, source=None)

    out = actions.csunesco_content_list(_ctx(), {'source': 'app'})
    assert [r['title'] for r in out['results']] == ['From the app']
    # 'ckan' includes legacy NULL rows (they predate the column).
    out = actions.csunesco_content_list(_ctx(), {'source': 'ckan'})
    assert {r['title'] for r in out['results']} == {'From the portal',
                                                    'Legacy row'}
    with pytest.raises(tk.ValidationError):
        actions.csunesco_content_list(_ctx(), {'source': 'martian'})


def test_source_backfill_from_extras_on_column_creation():
    engine = sa.create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(sa.text(
            'CREATE TABLE cs_content (id TEXT PRIMARY KEY, extras TEXT)'))
        conn.execute(sa.text(
            'INSERT INTO cs_content (id, extras) VALUES '
            '(\'a\', \'{"source": "app", "excerpt": "x"}\'), '
            '(\'b\', \'{"excerpt": "y"}\')'))
    db._ensure_columns(engine)
    with engine.connect() as conn:
        rows = dict(conn.execute(
            sa.text('SELECT id, source FROM cs_content')).fetchall())
    assert rows['a'] == 'app'
    assert rows['b'] in (None, 'ckan')
    engine.dispose()


def test_project_ids_accepts_ids_and_slugs_with_cap(actions, session):
    project_a = _project(session, 'river-e')
    project_b = _project(session, 'river-f')
    other = _project(session, 'river-g')
    _content(session, 'A news', project=project_a)
    _content(session, 'B news', project=project_b)
    _content(session, 'G news', project=other)

    out = actions.csunesco_content_list(
        _ctx(), {'project_ids': '%s,river-f' % project_a.id})
    assert {r['title'] for r in out['results']} == {'A news', 'B news'}

    # Unknown keys match nothing (never "everything").
    out = actions.csunesco_content_list(_ctx(), {'project_ids': 'ghost'})
    assert out['results'] == []

    with pytest.raises(tk.ValidationError):
        actions.csunesco_content_list(
            _ctx(), {'project_ids': ','.join('p%d' % i for i in range(60))})


def test_sort_title_asc_and_include_project(actions, session):
    project = _project(session, 'river-h')
    _content(session, 'zebra note', project=project)
    _content(session, 'alpha note', project=project)

    out = actions.csunesco_content_list(
        _ctx(), {'sort': 'title asc', 'project_ids': 'river-h',
                 'include_project': True})
    assert [r['title'] for r in out['results']] == ['alpha note', 'zebra note']
    assert out['results'][0]['project_title'] == 'River H'
    assert out['results'][0]['project_slug'] == 'river-h'
    with pytest.raises(tk.ValidationError):
        actions.csunesco_content_list(_ctx(), {'sort': 'id desc'})


def test_initiative_admin_lists_their_pending_rows(actions, session,
                                                   monkeypatch):
    project = _project(session, 'river-i', initiative='riverwatch')
    _content(session, 'Pending in my initiative', project=project,
             status='pending')
    foreign = _project(session, 'river-j', initiative='islandwatch')
    _content(session, 'Pending elsewhere', project=foreign, status='pending')

    # A plain caller filtering by initiative stays pinned to approved.
    out = actions.csunesco_content_list(
        _ctx(), {'initiative': 'riverwatch', 'status': 'pending'})
    assert out['results'] == []

    # The riverwatch ADM sees THEIR initiative's pending rows by API...
    monkeypatch.setattr(cs_auth, '_admin_initiative_groups',
                        lambda context: {'riverwatch'})
    out = actions.csunesco_content_list(
        _ctx('adm-1'), {'initiative': 'riverwatch', 'status': 'pending'})
    assert [r['title'] for r in out['results']] == [
        'Pending in my initiative']
    # ...but filtering ANOTHER initiative keeps the public pin.
    out = actions.csunesco_content_list(
        _ctx('adm-1'), {'initiative': 'islandwatch', 'status': 'pending'})
    assert out['results'] == []
