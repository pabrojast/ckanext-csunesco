# encoding: utf-8
"""Pure unit tests for ckanext-csunesco (NO database, NO web stack).

Exercises every field-level validator in ``logic/validators.py``, the shared
``logic/sanitize.sanitize_html`` allowlist and the navl schema builders in
``logic/schema.py``. These import ``ckan.plugins.toolkit`` (validators raise
``toolkit.Invalid``), so the whole module skips cleanly when CKAN is not
installed but MUST run and pass inside the ckan-dev container.
"""
import datetime
import json

import pytest

try:
    import ckan.plugins.toolkit as tk
    from ckanext.csunesco.logic import validators as v
    from ckanext.csunesco.logic import sanitize
    from ckanext.csunesco.logic import schema
    HAVE_CKAN = True
except Exception:  # pragma: no cover - environment without CKAN
    HAVE_CKAN = False

try:
    import bleach  # noqa: F401
    HAVE_BLEACH = True
except Exception:  # pragma: no cover
    HAVE_BLEACH = False

pytestmark = pytest.mark.skipif(
    not HAVE_CKAN, reason="requires CKAN (ckan.plugins.toolkit)")


# ---------------------------------------------------------------------------
# csunesco_valid_initiative
# ---------------------------------------------------------------------------

def test_valid_initiative_accepts_known_name():
    assert v.csunesco_valid_initiative('be-resilient') == 'be-resilient'
    assert v.csunesco_valid_initiative('riverwatch') == 'riverwatch'


def test_valid_initiative_normalizes_hyphenated_aliases():
    # The CS Toolbox app sends hyphenated slugs; they must normalize to the
    # canonical group names (never bounce, never store the alias).
    assert v.csunesco_valid_initiative('river-watch') == 'riverwatch'
    assert v.csunesco_valid_initiative('island-watch') == 'islandwatch'
    assert v.csunesco_valid_initiative('beresilient') == 'be-resilient'


def test_valid_initiative_passes_empty_through():
    assert v.csunesco_valid_initiative('') == ''
    assert v.csunesco_valid_initiative(None) is None


def test_valid_initiative_rejects_unknown():
    with pytest.raises(tk.Invalid):
        v.csunesco_valid_initiative('not-an-initiative')


# ---------------------------------------------------------------------------
# csunesco_valid_slug
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('slug', ['river-watch', 'abc', 'a1-b2-c3', 'x'])
def test_valid_slug_accepts_url_safe(slug):
    assert v.csunesco_valid_slug(slug) == slug


def test_valid_slug_passes_empty_through():
    assert v.csunesco_valid_slug('') == ''
    assert v.csunesco_valid_slug(None) is None


@pytest.mark.parametrize('slug', [
    'River', 'UPPER', '-leading', 'trailing-', 'dou--ble', 'has space',
    'under_score', 'slash/es',
])
def test_valid_slug_rejects_bad(slug):
    with pytest.raises(tk.Invalid):
        v.csunesco_valid_slug(slug)


# ---------------------------------------------------------------------------
# csunesco_valid_geojson (size guard + type allowlist)
# ---------------------------------------------------------------------------

def test_valid_geojson_accepts_allowlisted_types():
    for gtype in ('FeatureCollection', 'Feature', 'Polygon', 'MultiPolygon'):
        raw = json.dumps({'type': gtype, 'coordinates': []})
        assert v.csunesco_valid_geojson(raw) == raw


def test_valid_geojson_passes_empty_through():
    assert v.csunesco_valid_geojson('') == ''
    assert v.csunesco_valid_geojson(None) is None


def test_valid_geojson_rejects_oversized_before_parsing():
    # A payload larger than the 1MB cap must be rejected by the size guard,
    # never handed to json.loads.
    oversized = 'a' * (v.MAX_GEOJSON_BYTES + 1)
    with pytest.raises(tk.Invalid):
        v.csunesco_valid_geojson(oversized)


def test_valid_geojson_rejects_invalid_json():
    with pytest.raises(tk.Invalid):
        v.csunesco_valid_geojson('this is not json')


def test_valid_geojson_rejects_disallowed_type():
    raw = json.dumps({'type': 'Point', 'coordinates': [0, 0]})
    with pytest.raises(tk.Invalid):
        v.csunesco_valid_geojson(raw)


# ---------------------------------------------------------------------------
# country list (coercion helper + fail-closed membership check)
# ---------------------------------------------------------------------------

def test_coerce_country_list_from_json_and_list():
    assert v._coerce_country_list('["Chile", "Peru"]') == ['Chile', 'Peru']
    assert v._coerce_country_list(['Chile', ' Peru ']) == ['Chile', 'Peru']
    assert v._coerce_country_list('') == []
    assert v._coerce_country_list(None) == []


def test_coerce_country_list_rejects_non_list():
    with pytest.raises(tk.Invalid):
        v._coerce_country_list('5')
    with pytest.raises(tk.Invalid):
        v._coerce_country_list('{ not json')


def test_country_list_empty_returns_empty_json():
    assert v.csunesco_valid_country_list('', {}) == json.dumps([])
    assert v.csunesco_valid_country_list([], {}) == json.dumps([])


def test_country_list_accepts_known_member_states(monkeypatch):
    monkeypatch.setattr(v, '_member_state_names',
                        lambda model: {'Chile', 'Peru'})
    ctx = {'model': object()}
    out = v.csunesco_valid_country_list(['Chile', 'Peru'], ctx)
    assert json.loads(out) == ['Chile', 'Peru']
    out = v.csunesco_valid_country_list('["Chile"]', ctx)
    assert json.loads(out) == ['Chile']


def test_country_list_rejects_unknown_member_state(monkeypatch):
    monkeypatch.setattr(v, '_member_state_names', lambda model: {'Chile'})
    ctx = {'model': object()}
    with pytest.raises(tk.Invalid):
        v.csunesco_valid_country_list(['Atlantis'], ctx)


def test_country_list_fails_closed_without_model():
    # No model in context -> valid set is empty -> any country is rejected.
    with pytest.raises(tk.Invalid):
        v.csunesco_valid_country_list(['Chile'], {})


# ---------------------------------------------------------------------------
# csunesco_valid_document_url (http/https only)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('url', [
    'http://example.org/doc.pdf', 'https://example.org/doc.pdf',
])
def test_document_url_accepts_http_https(url):
    assert v.csunesco_valid_document_url(url) == url


def test_document_url_passes_empty_through():
    assert v.csunesco_valid_document_url('') == ''
    assert v.csunesco_valid_document_url(None) is None


@pytest.mark.parametrize('url', [
    'javascript:alert(1)', 'data:text/html,x', 'ftp://host/file',
    'file:///etc/passwd',
])
def test_document_url_rejects_other_schemes(url):
    with pytest.raises(tk.Invalid):
        v.csunesco_valid_document_url(url)


# ---------------------------------------------------------------------------
# csunesco_valid_content_type
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('ctype', [
    'cs-news', 'cs-event', 'cs-publication', 'cs-map'])
def test_content_type_accepts_known(ctype):
    assert v.csunesco_valid_content_type(ctype) == ctype


@pytest.mark.parametrize('ctype', ['cs-media', 'news', '', 'CS-NEWS'])
def test_content_type_rejects_unknown(ctype):
    with pytest.raises(tk.Invalid):
        v.csunesco_valid_content_type(ctype)


def test_content_types_in_sync_with_action_module():
    # CONTENT_TYPES is deliberately duplicated in the action module (so the
    # action layer never imports validators just for the set) -- this test is
    # the guard that keeps both copies identical.
    content = pytest.importorskip('ckanext.csunesco.logic.action.content')
    assert content.CONTENT_TYPES == v.CONTENT_TYPES


# ---------------------------------------------------------------------------
# csunesco_valid_terria_url (base allowlist, fail closed)
# ---------------------------------------------------------------------------

def test_terria_url_passes_empty_through():
    assert v.csunesco_valid_terria_url('') == ''
    assert v.csunesco_valid_terria_url(None) is None


def test_terria_url_fails_closed_without_config(monkeypatch):
    monkeypatch.setattr(v, 'terria_allowed_bases', lambda: [])
    with pytest.raises(tk.Invalid):
        v.csunesco_valid_terria_url('https://maps.example/terria/#share=abc')


def test_terria_url_accepts_configured_base(monkeypatch):
    monkeypatch.setattr(
        v, 'terria_allowed_bases', lambda: ['https://maps.example/terria'])
    for url in (
        'https://maps.example/terria',                 # exact
        'https://maps.example/terria/#share=g-abc',    # share link
        'https://maps.example/terria#share=g-abc',     # no trailing slash
        'https://maps.example/terria?start=1',         # query form
    ):
        assert v.csunesco_valid_terria_url(url) == url


@pytest.mark.parametrize('url', [
    'https://evil.example/#share=x',
    # Base-prefix trick: the base followed by a dot is a DIFFERENT host.
    'https://maps.example.evil.com/#share=x',
    'javascript:alert(1)',
    'ftp://maps.example/terria/#share=x',
])
def test_terria_url_rejects_bad(monkeypatch, url):
    monkeypatch.setattr(
        v, 'terria_allowed_bases', lambda: ['https://maps.example'])
    with pytest.raises(tk.Invalid):
        v.csunesco_valid_terria_url(url)


def test_terria_allowed_bases_parses_and_normalizes(monkeypatch):
    monkeypatch.setitem(
        tk.config, v.TERRIA_BASE_URL_OPTION,
        'https://a.example/terria/  https://b.example')
    assert v.terria_allowed_bases() == [
        'https://a.example/terria', 'https://b.example']


# ---------------------------------------------------------------------------
# csunesco_nonempty_media_list
# ---------------------------------------------------------------------------

def test_nonempty_media_list_rejects_empty():
    # '[]' is a truthy string, so not_empty alone cannot catch it.
    with pytest.raises(tk.Invalid):
        v.csunesco_nonempty_media_list('[]')
    with pytest.raises(tk.Invalid):
        v.csunesco_nonempty_media_list('not json either')


def test_nonempty_media_list_accepts_nonempty():
    value = '["https://a.example/doc.pdf"]'
    assert v.csunesco_nonempty_media_list(value) == value


# ---------------------------------------------------------------------------
# csunesco_valid_iso_date
# ---------------------------------------------------------------------------

def test_iso_date_parses_date():
    result = v.csunesco_valid_iso_date('2026-07-16')
    assert result == datetime.datetime(2026, 7, 16)


def test_iso_date_parses_datetime():
    result = v.csunesco_valid_iso_date('2026-07-16T09:30')
    assert result == datetime.datetime(2026, 7, 16, 9, 30)


def test_iso_date_empty_normalizes_to_none():
    assert v.csunesco_valid_iso_date('') is None
    assert v.csunesco_valid_iso_date(None) is None


def test_iso_date_passes_datetime_through():
    dt = datetime.datetime(2026, 1, 1, 12, 0)
    assert v.csunesco_valid_iso_date(dt) is dt


def test_iso_date_rejects_garbage():
    with pytest.raises(tk.Invalid):
        v.csunesco_valid_iso_date('not-a-date')


# ---------------------------------------------------------------------------
# csunesco_end_after_start (navl full-schema validator)
# ---------------------------------------------------------------------------

def test_end_after_start_flags_end_before_start():
    key = ('end_date',)
    data = {
        ('publish_date',): datetime.datetime(2026, 7, 16, 9, 0),
        key: datetime.datetime(2026, 7, 16, 8, 0),
    }
    errors = {key: []}
    v.csunesco_end_after_start(key, data, errors, {})
    assert errors[key], 'expected an error when end <= start'


def test_end_after_start_flags_equal():
    key = ('end_date',)
    same = datetime.datetime(2026, 7, 16, 9, 0)
    data = {('publish_date',): same, key: same}
    errors = {key: []}
    v.csunesco_end_after_start(key, data, errors, {})
    assert errors[key]


def test_end_after_start_ok_when_end_later():
    key = ('end_date',)
    data = {
        ('publish_date',): datetime.datetime(2026, 7, 16, 9, 0),
        key: datetime.datetime(2026, 7, 16, 10, 0),
    }
    errors = {key: []}
    v.csunesco_end_after_start(key, data, errors, {})
    assert errors[key] == []


def test_end_after_start_noop_when_missing():
    key = ('end_date',)
    data = {key: None}
    errors = {key: []}
    v.csunesco_end_after_start(key, data, errors, {})
    assert errors[key] == []


# ---------------------------------------------------------------------------
# csunesco_valid_media_list
# ---------------------------------------------------------------------------

def test_media_list_empty_returns_empty_json():
    assert v.csunesco_valid_media_list('') == json.dumps([])
    assert v.csunesco_valid_media_list(None) == json.dumps([])


def test_media_list_accepts_http_urls():
    out = v.csunesco_valid_media_list(['http://a/x.png', 'https://b/y.png'])
    assert json.loads(out) == ['http://a/x.png', 'https://b/y.png']
    out = v.csunesco_valid_media_list('["http://a/x.png"]')
    assert json.loads(out) == ['http://a/x.png']


def test_media_list_strips_empty_items():
    out = v.csunesco_valid_media_list(['', 'http://a/x.png', '  '])
    assert json.loads(out) == ['http://a/x.png']


def test_media_list_rejects_bad_scheme():
    with pytest.raises(tk.Invalid):
        v.csunesco_valid_media_list(['javascript:alert(1)'])


def test_media_list_rejects_non_list():
    with pytest.raises(tk.Invalid):
        v.csunesco_valid_media_list('{"a": 1}')


# ---------------------------------------------------------------------------
# sanitize_html
# ---------------------------------------------------------------------------

def test_sanitize_passes_falsy_through():
    assert sanitize.sanitize_html('') == ''
    assert sanitize.sanitize_html(None) is None


def test_sanitize_strips_script_tag():
    out = sanitize.sanitize_html('<script>alert(1)</script><p>hi</p>')
    assert '<script' not in out.lower()


def test_sanitize_strips_onerror_and_img():
    out = sanitize.sanitize_html('<img src=x onerror="alert(1)">text')
    assert 'onerror' not in out.lower()
    assert '<img' not in out.lower()
    assert 'text' in out


def test_sanitize_drops_javascript_url():
    out = sanitize.sanitize_html('<a href="javascript:alert(1)">click</a>')
    assert 'javascript:' not in out.lower()


@pytest.mark.skipif(not HAVE_BLEACH, reason="requires bleach for allowlist")
def test_sanitize_keeps_allowlisted_tags():
    html = ('<b>bold</b> <em>em</em> <strong>s</strong> '
            '<a href="http://x.org" rel="noopener">link</a>'
            '<ul><li>one</li></ul><h3>head</h3><blockquote>q</blockquote>')
    out = sanitize.sanitize_html(html)
    for fragment in ('<b>bold</b>', '<em>em</em>', '<strong>s</strong>',
                     '<li>one</li>', '<h3>head</h3>',
                     '<blockquote>q</blockquote>'):
        assert fragment in out
    assert 'href="http://x.org"' in out


@pytest.mark.skipif(not HAVE_BLEACH, reason="requires bleach for allowlist")
def test_sanitize_drops_disallowed_tag_keeps_text():
    out = sanitize.sanitize_html('<div class="x"><p>kept</p></div>')
    assert '<div' not in out
    assert '<p>kept</p>' in out


# ---------------------------------------------------------------------------
# schema builders return the expected navl dicts
# ---------------------------------------------------------------------------

def test_project_request_schema_shape():
    s = schema.project_request_schema()
    expected = {
        'title', 'initiative', 'countries', 'slug', 'biosphere_reserve',
        'region_geojson', 'short_description', 'project_document_url',
    }
    assert expected <= set(s)
    assert v.csunesco_valid_initiative in s['initiative']
    assert v.csunesco_valid_slug in s['slug']
    assert v.csunesco_valid_geojson in s['region_geojson']
    assert v.csunesco_valid_country_list in s['countries']
    assert v.csunesco_valid_document_url in s['project_document_url']


def test_content_schema_news_dates_optional():
    s = schema.content_schema('cs-news')
    assert v.csunesco_valid_content_type in s['content_type']
    assert v.csunesco_valid_media_list in s['media']
    assert v.csunesco_valid_iso_date in s['publish_date']
    not_empty = tk.get_validator('not_empty')
    # News dates are optional -> not_empty must NOT be required.
    assert not_empty not in s['publish_date']


def test_content_schema_event_requires_end_after_start():
    s = schema.content_schema('cs-event')
    not_empty = tk.get_validator('not_empty')
    assert not_empty in s['publish_date']
    assert not_empty in s['end_date']
    assert v.csunesco_end_after_start in s['end_date']


def test_content_schema_publication_requires_documents():
    s = schema.content_schema('cs-publication')
    not_empty = tk.get_validator('not_empty')
    assert not_empty in s['media']
    assert v.csunesco_valid_media_list in s['media']
    assert v.csunesco_nonempty_media_list in s['media']
    # DOI / authors are optional publication metadata.
    assert 'doi' in s and 'authors' in s


def test_content_schema_map_requires_terria_url():
    s = schema.content_schema('cs-map')
    not_empty = tk.get_validator('not_empty')
    assert not_empty in s['terria_url']
    assert v.csunesco_valid_terria_url in s['terria_url']


def test_content_schema_news_keeps_new_fields_optional():
    s = schema.content_schema('cs-news')
    not_empty = tk.get_validator('not_empty')
    assert not_empty not in s['terria_url']
    assert not_empty not in s['media']


# ---------------------------------------------------------------------------
# ofform client (pure parts: geojson conversion + SSRF guards)
# ---------------------------------------------------------------------------

def test_rows_to_geojson_skips_invalid_rows_and_flattens_answers():
    ofform = pytest.importorskip('ckanext.csunesco.logic.ofform')
    data = {'rows': [
        {'id': 1, 'date': '2026-07-01T10:00:00', 'lat': -33.45, 'lng': -70.66,
         'source': 'native',
         'answers': {'ph': 7.123456789, 'tags': ['a', 'b'], 'site': 'X',
                     'nested': {'k': 1}}},
        {'id': 2, 'lat': None, 'lng': -70},       # missing lat -> skipped
        {'id': 3, 'lat': 95, 'lng': 10},          # out of range -> skipped
        {'id': 4, 'lat': 'nan', 'lng': 'inf'},    # non-finite -> skipped
        'not-a-dict',                             # malformed row -> skipped
    ]}
    result = ofform.rows_to_geojson(data)
    assert result['type'] == 'FeatureCollection'
    assert len(result['features']) == 1
    feature = result['features'][0]
    assert feature['geometry'] == {
        'type': 'Point', 'coordinates': [-70.66, -33.45]}
    props = feature['properties']
    # ``date`` is the per-feature time key Terria's time slider uses.
    assert props['date'] == '2026-07-01T10:00:00'
    assert props['ph'] == 7.123457            # floats rounded to 6 decimals
    assert props['tags'] == 'a|b'             # lists joined
    assert props['site'] == 'X'
    assert json.loads(props['nested']) == {'k': 1}   # dicts JSON-dumped


def test_observation_stats_counts_totals_and_distinct_sites():
    ofform = pytest.importorskip('ckanext.csunesco.logic.ofform')
    data = {
        'total': 500,                       # upstream total wins (truncation)
        'rows': [
            {'lat': -33.4501, 'lng': -70.66},
            {'lat': -33.45012, 'lng': -70.66004},   # same site at 4 decimals
            {'lat': -33.5, 'lng': -70.7},
            {'lat': None, 'lng': -70},              # no coords -> no site
        ],
    }
    stats = ofform.observation_stats(data)
    assert stats['observations'] == 500
    assert stats['sites'] == 2
    # Without an upstream total, fall back to the row count.
    assert ofform.observation_stats({'rows': [{'a': 1}]})['observations'] == 1
    assert ofform.observation_stats(None) == {'observations': 0, 'sites': 0}


def test_rows_to_geojson_tolerates_empty_input():
    ofform = pytest.importorskip('ckanext.csunesco.logic.ofform')
    empty = {'type': 'FeatureCollection', 'features': []}
    assert ofform.rows_to_geojson(None) == empty
    assert ofform.rows_to_geojson({}) == empty


def test_summarize_dashboard_review_context():
    ofform = pytest.importorskip('ckanext.csunesco.logic.ofform')
    data = {
        'total': 3, 'truncated': False,
        'rows': [
            {'id': 1, 'date': '2026-03-02T10:00:00', 'lat': -33.4, 'lng': -70.6},
            {'id': 2, 'date': '2026-01-15T08:00:00', 'lat': None, 'lng': -70},
            {'id': 3, 'date': '2026-07-01T12:00:00', 'lat': -33.5, 'lng': -70.7},
        ],
    }
    summary = ofform.summarize_dashboard(data)
    assert summary['ok'] is True
    assert summary['total'] == 3
    assert summary['first_date'] == '2026-01-15'
    assert summary['last_date'] == '2026-07-01'
    assert summary['with_coords'] == 2
    # Empty/missing payloads degrade to zeros, never raise.
    empty = ofform.summarize_dashboard(None)
    assert empty['ok'] is True and empty['total'] == 0


def test_public_form_url_requires_config(monkeypatch):
    ofform = pytest.importorskip('ckanext.csunesco.logic.ofform')
    monkeypatch.setitem(tk.config, ofform.APP_URL_OPTION, '')
    assert ofform.public_form_url(7) is None
    monkeypatch.setitem(
        tk.config, ofform.APP_URL_OPTION, 'https://app.example/')
    assert ofform.public_form_url(7) == 'https://app.example/public/forms/7'
    assert ofform.public_form_url('nope') is None


def test_ofform_form_id_coercion_guards_the_path():
    ofform = pytest.importorskip('ckanext.csunesco.logic.ofform')
    assert ofform._coerce_form_id('7') == 7
    for bad in ('../../etc/passwd', 'abc', 0, -3, None):
        with pytest.raises(ofform.OfformError):
            ofform._coerce_form_id(bad)


def test_ofform_fetch_fails_closed_without_base_url(monkeypatch):
    ofform = pytest.importorskip('ckanext.csunesco.logic.ofform')
    monkeypatch.setitem(tk.config, ofform.BASE_URL_OPTION, '')
    with pytest.raises(ofform.OfformError):
        ofform._fetch('/public/forms/1/export.csv')


def test_is_sysadmin_tolerates_flask_login_anonymous_user():
    # On portals with flask-login-style auth plugins (IHP-WINS), anonymous API
    # calls carry an AnonymousUser (no .sysadmin/.id) in auth_user_obj; the
    # helpers must treat it as "no user", never AttributeError (500).
    auth = pytest.importorskip('ckanext.csunesco.logic.auth')
    action_pkg = pytest.importorskip('ckanext.csunesco.logic.action')

    class _AnonymousUser:
        is_anonymous = True
        is_authenticated = False

    context = {'auth_user_obj': _AnonymousUser(), 'user': ''}
    assert auth._is_sysadmin(context) is False
    assert auth._user_obj(context) is None
    assert action_pkg.current_user_id(context) is None


def test_package_name_is_munged_and_bounded():
    package_sync = pytest.importorskip(
        'ckanext.csunesco.logic.package_sync')

    class _Project:
        slug = 'x' * 200

    class _DataSource:
        form_id = 42

    name = package_sync.package_name(_Project, _DataSource)
    assert len(name) <= package_sync.MAX_NAME_LENGTH
    assert name.startswith('cs-data-')
    assert name.endswith('-42')


def test_resolve_owner_org_priority(monkeypatch):
    # override > app-suggested (extras) > project org > configured default.
    package_sync = pytest.importorskip(
        'ckanext.csunesco.logic.package_sync')
    monkeypatch.setitem(
        tk.config, package_sync.OWNER_ORG_OPTION, 'default-org')

    class _Project:
        organization_id = None

    class _DataSource:
        extras = json.dumps({'owner_org': 'app-org'})

    class _BareSource:
        extras = '{}'

    resolve = package_sync.resolve_owner_org
    assert resolve(_Project, _DataSource, 'chosen-org') == 'chosen-org'
    assert resolve(_Project, _DataSource) == 'app-org'
    assert resolve(_Project, _BareSource) == 'default-org'
    project_with_org = _Project()
    project_with_org.organization_id = 'project-org'
    assert resolve(project_with_org, _BareSource) == 'project-org'
    # App suggestion still beats the project org (it is more specific).
    assert resolve(project_with_org, _DataSource) == 'app-org'


# ---------------------------------------------------------------------------
# P2: content_initial_status (trusted projects publish news/events unreviewed)
# ---------------------------------------------------------------------------

def test_content_initial_status_matrix():
    from ckanext.csunesco.logic.action.content import content_initial_status

    # Sysadmin portal-authored publishes; app-pushed queues even for sysadmin.
    assert content_initial_status(True, 'ckan', 'cs-news', False) == 'approved'
    assert content_initial_status(True, 'app', 'cs-news', False) == 'pending'
    # Non-trusted project: everything from non-sysadmins queues.
    assert content_initial_status(False, 'ckan', 'cs-news', False) == 'pending'
    assert content_initial_status(False, 'app', 'cs-event', False) == 'pending'
    # Trusted project: news/events skip review on BOTH surfaces...
    assert content_initial_status(False, 'ckan', 'cs-news', True) == 'approved'
    assert content_initial_status(False, 'app', 'cs-event', True) == 'approved'
    # ...but publications/maps ALWAYS queue (external links/embeds).
    assert content_initial_status(False, 'ckan', 'cs-publication', True) == 'pending'
    assert content_initial_status(False, 'app', 'cs-map', True) == 'pending'
