"""Unión privada, estado de cuentas, aislamiento de ámbitos y fallos de caché."""
import time

import pytest
from ckan.model import User
from ckanext.csunesco import db
from ckanext.csunesco.logic import helpers, participation, snapshots
from ckanext.csunesco.tests.test_db_behavior import session


def test_inputs_filter_inactive_members_and_profiles_and_scope(session):
    for uid, state in [('active', 'active'), ('inactive', 'deleted'), ('manager', 'active'), ('only-profile', 'active')]:
        session.add(User(id=uid, name=uid, state=state))
        profile = db.CsCitizenScientist(); profile.user_id = uid; session.add(profile)
    for slug, visibility in [('public', 'public'), ('hidden', 'unlisted')]:
        p = db.CsProject(); p.slug = slug; p.title = slug; p.status = 'approved'
        p.initiative_group = 'riverwatch'; p.discovery_visibility = visibility
        session.add(p); session.flush()
        for uid, role in [('active', 'scientist'), ('inactive', 'scientist'), ('manager', 'admin')]:
            m = db.CsProjectMember(); m.project_id = p.id; m.user_id = uid; m.role = role; m.status = 'active'; session.add(m)
    session.commit()
    scoped = db.participation_inputs('riverwatch')
    assert scoped == {'project_slugs': ['public'], 'ckan_user_ids': ['active'], 'excluded_ckan_user_ids': ['inactive']}
    assert db.participation_inputs()['ckan_user_ids'] == ['active', 'manager', 'only-profile']
    assert db.participation_inputs('islandwatch')['project_slugs'] == []


def test_cache_union_outage_expiry_and_scope_changes(monkeypatch, tmp_path):
    monkeypatch.setattr(snapshots, 'root', lambda: tmp_path)
    payload = dict(project_slugs=['p'], ckan_user_ids=['one'], excluded_ckan_user_ids=[])
    monkeypatch.setattr(db, 'participation_inputs', lambda *args, **kwargs: payload)
    calls = []
    def app(path, body):
        calls.append((path, body))
        return {'citizen_scientists': 7}
    monkeypatch.setattr(snapshots, 'app_request', app)
    assert participation.cached_count(payload) is None
    assert participation.refresh() == 7
    assert calls == [('/internal/ckan/participation-summary', payload)]
    assert participation.cached_count(payload) == 7
    assert participation.cached_count(dict(payload, project_slugs=['other'])) is None
    assert participation.cached_count(dict(payload, excluded_ckan_user_ids=['one'])) is None
    def unavailable(*args):
        raise OSError('outage')
    monkeypatch.setattr(snapshots, 'app_request', unavailable)
    with pytest.raises(OSError): participation.refresh()
    assert participation.cached_count(payload) == 7
    now = time.time()
    monkeypatch.setattr(participation.time, 'time', lambda: now + participation.MAX_AGE + 1)
    assert participation.cached_count(payload) is None
    # La caché contiene sólo fecha y total, no identificadores de personas.
    record = snapshots.read_private('participation', participation._key(payload))
    assert set(record) == {'created_at', 'citizen_scientists'}


def _counting_writes(monkeypatch, tmp_path, total):
    """One scope whose upstream total is total[0]; returns (payload, clock, writes)."""
    monkeypatch.setattr(snapshots, 'root', lambda: tmp_path)
    payload = dict(project_slugs=['p'], ckan_user_ids=['one'], excluded_ckan_user_ids=[])
    monkeypatch.setattr(db, 'participation_inputs', lambda *args, **kwargs: payload)
    monkeypatch.setattr(snapshots, 'app_request', lambda *args: {'citizen_scientists': total[0]})
    writes, real = [], snapshots.write_private
    def write(kind, key, data):
        writes.append(data['citizen_scientists'])
        real(kind, key, data)
    monkeypatch.setattr(snapshots, 'write_private', write)
    clock = [1_000_000.0]
    monkeypatch.setattr(participation.time, 'time', lambda: clock[0])
    return payload, clock, writes


def test_unchanged_total_is_renewed_once_per_repair_period(monkeypatch, tmp_path):
    # Every uWSGI worker of every pod runs the repair pass. They used to rewrite
    # each total on every pass, which failed the Toolbox backup that reads the
    # same share ("upload changed during backup", 2026-11-23 to 2026-11-25).
    total = [7]
    payload, clock, writes = _counting_writes(monkeypatch, tmp_path, total)
    start = clock[0]
    for _ in range(20):  # twenty workers, ten seconds apart
        assert participation.refresh() == 7
        clock[0] += 10
    assert writes == [7]
    clock[0] = start + participation.RENEW_AFTER + 1
    assert participation.refresh() == 7
    assert writes == [7, 7]
    # A total that changed is written at once, however new the file is.
    total[0] = 8
    clock[0] += 1
    assert participation.refresh() == 8
    assert writes == [7, 7, 8]
    assert participation.cached_count(payload) == 8


def test_single_worker_renews_on_every_pass_and_the_total_never_expires(monkeypatch, tmp_path):
    total = [7]
    payload, clock, writes = _counting_writes(monkeypatch, tmp_path, total)
    for _ in range(12):  # an hour of passes: 300 s of sleep plus the pass itself
        participation.refresh()
        clock[0] += 300 + 2
        assert participation.cached_count(payload) == 7
    assert writes == [7] * 12
    # A pass that fails leaves the last total readable until MAX_AGE, as before.
    clock[0] += 300 + 2
    assert participation.cached_count(payload) == 7


@pytest.mark.parametrize('content', ['{"created_at": 1000000', '{"created_at": "today", "citizen_scientists": 7}', '[]'])
def test_stored_total_that_cannot_be_read_is_written_again(monkeypatch, tmp_path, content):
    total = [7]
    payload, clock, writes = _counting_writes(monkeypatch, tmp_path, total)
    participation.refresh()
    snapshots.path_for('participation', participation._key(payload)).write_text(content)
    assert participation.refresh() == 7
    assert writes == [7, 7]
    assert participation.cached_count(payload) == 7


@pytest.mark.parametrize('value', [None, True, -1, '7'])
def test_invalid_upstream_is_never_cached(monkeypatch, tmp_path, value):
    monkeypatch.setattr(snapshots, 'root', lambda: tmp_path)
    payload = dict(project_slugs=['p'], ckan_user_ids=[], excluded_ckan_user_ids=[])
    monkeypatch.setattr(db, 'participation_inputs', lambda *args, **kwargs: payload)
    monkeypatch.setattr(snapshots, 'app_request', lambda *args: {'citizen_scientists': value})
    with pytest.raises(ValueError): participation.refresh()
    assert participation.cached_count(payload) is None


def test_unknown_values_render_as_unavailable_not_zero(monkeypatch):
    def unavailable(*args): raise RuntimeError('database unavailable')
    monkeypatch.setattr(helpers.tk, 'get_action', unavailable)
    assert set(helpers.csunesco_aggregate_stats().values()) == {None}
    assert helpers.csunesco_format_number(None) == '—'
    assert str(helpers.csunesco_format_number(0)) == '0'


def test_project_refresh_persists_union_without_changing_measurements(session, monkeypatch, tmp_path):
    monkeypatch.setattr(snapshots, 'root', lambda: tmp_path)
    project = db.CsProject(); project.slug = 'p'; project.title = 'P'; project.status = 'approved'
    session.add(project); session.commit()
    db.stats_set(project.id, observations=6485, sites_monitored=49, citizen_scientists=1)
    session.commit()
    monkeypatch.setattr(snapshots, 'app_request', lambda *args: {'citizen_scientists': 14})
    assert participation.refresh(project_id=project.id) == 14
    stats = db.get_stats(project.id)
    assert (stats.citizen_scientists, stats.observations, stats.sites_monitored) == (14, 6485, 49)
