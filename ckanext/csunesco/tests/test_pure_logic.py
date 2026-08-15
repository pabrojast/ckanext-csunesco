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
# csunesco_valid_image_url (https or internal path, charset-restricted)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('url', [
    'https://example.org/banner.jpg',
    'https://example.org/img.jpg?w=1600&fit=cover',
    '/csunesco/images/project-ghana.jpg',
])
def test_image_url_accepts_https_and_internal(url):
    assert v.csunesco_valid_image_url(url) == url


def test_image_url_passes_empty_through():
    assert v.csunesco_valid_image_url('') == ''
    assert v.csunesco_valid_image_url(None) is None


@pytest.mark.parametrize('url', [
    'http://example.org/banner.jpg',       # mixed content
    '//evil.example/banner.jpg',           # scheme-relative
    'javascript:alert(1)',
    'data:image/png;base64,x',
    'https://example.org/a\'.jpg',         # quote breaks url('...')
    'https://example.org/a).jpg',          # paren breaks url(...)
    'https://example.org/a b.jpg',         # whitespace
    'relative/path.jpg',                   # neither https nor /path
])
def test_image_url_rejects_unsafe(url):
    with pytest.raises(tk.Invalid):
        v.csunesco_valid_image_url(url)


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


def test_content_schema_event_requires_a_start_but_not_an_end():
    """An event needs a start date; the end is optional.

    Both used to be required, which rejected every open-ended event the CS
    Toolbox app creates -- it posts `end_date: null`.
    """
    s = schema.content_schema('cs-event')
    not_empty = tk.get_validator('not_empty')
    ignore_missing = tk.get_validator('ignore_missing')
    assert not_empty in s['publish_date']
    assert not_empty not in s['end_date']
    assert s['end_date'][0] is ignore_missing


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


def test_ofform_fetch_logs_which_form_failed(monkeypatch, caplog):
    """Un 404 aquí casi siempre significa que UN form dejó de ser público.

    Sin el path en el log, el operador ve "fetch failed" y no sabe cuál de los
    doce forms conectados se apagó -- que es justo el dato accionable.
    """
    import logging
    import urllib.error
    ofform = pytest.importorskip('ckanext.csunesco.logic.ofform')
    monkeypatch.setitem(
        tk.config, ofform.BASE_URL_OPTION, 'https://ofform.example')

    def boom(*args, **kwargs):
        raise urllib.error.HTTPError(
            'https://ofform.example', 404, 'Not Found', {}, None)

    monkeypatch.setattr(ofform.urllib.request, 'urlopen', boom)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(ofform.OfformError):
            ofform._fetch('/public/forms/11/dashboard-data')
    assert '/public/forms/11/dashboard-data' in caplog.text
    assert '404' in caplog.text


def test_ofform_fetch_logs_the_path_on_network_error(monkeypatch, caplog):
    import logging
    ofform = pytest.importorskip('ckanext.csunesco.logic.ofform')
    monkeypatch.setitem(
        tk.config, ofform.BASE_URL_OPTION, 'https://ofform.example')

    def boom(*args, **kwargs):
        raise OSError('connection reset')

    monkeypatch.setattr(ofform.urllib.request, 'urlopen', boom)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(ofform.OfformError):
            ofform._fetch('/public/forms/3/export.csv')
    assert '/public/forms/3/export.csv' in caplog.text


# --------------------------------------------------------------------------- #
# Proxy de datos: una fuente caída NO es un bug nuestro, y se dice distinto    #
# --------------------------------------------------------------------------- #

def test_an_upstream_outage_is_tagged_so_the_chart_can_say_so():
    """El chart JS elige la frase mirando ``reason``.

    Si esta clave desaparece, el visitante lee "the chart could not be loaded"
    (culpa nuestra) en vez de "the data is temporarily away".
    """
    views_data = pytest.importorskip('ckanext.csunesco.logic.views_data')
    response = views_data._upstream_error(views_data.UPSTREAM_UNAVAILABLE)
    # 502, no 503: el que falló fue el gateway, no ESTE servidor -- y el proxy
    # de GeoJSON lo consume Terria, cuyo reintento/caché sí depende del status.
    assert response.status_code == 502
    payload = json.loads(response.get_data(as_text=True))
    assert payload['reason'] == 'upstream_unavailable'
    assert payload['error'] == views_data.UNAVAILABLE_MESSAGE


def test_our_own_bug_is_not_blamed_on_the_upstream():
    views_data = pytest.importorskip('ckanext.csunesco.logic.views_data')
    payload = json.loads(
        views_data._upstream_error(
            views_data.INTERNAL_ERROR).get_data(as_text=True))
    assert payload['reason'] == 'internal_error'


def test_reason_codes_are_never_translated():
    """Se comparan literalmente en JS: traducirlos rompería la función en todos
    los locales que no sean inglés, en silencio."""
    views_data = pytest.importorskip('ckanext.csunesco.logic.views_data')
    for reason in (views_data.UPSTREAM_UNAVAILABLE, views_data.INTERNAL_ERROR):
        assert isinstance(reason, str)
        assert reason == reason.lower()
        assert ' ' not in reason


# --------------------------------------------------------------------------- #
# Panel de revisión: sondear fuentes caídas no puede costarle el panel a nadie #
# --------------------------------------------------------------------------- #

def test_probing_stops_at_the_row_cap(monkeypatch):
    views_admin = pytest.importorskip('ckanext.csunesco.logic.views_admin')
    ofform = pytest.importorskip('ckanext.csunesco.logic.ofform')
    calls = []
    monkeypatch.setattr(ofform, 'probe_form',
                        lambda form_id: calls.append(form_id) or {'ok': False})
    monkeypatch.setattr(ofform, 'public_form_url', lambda form_id: None)
    rows = [{'form_id': n} for n in range(views_admin.HEALTH_PROBE_MAX + 10)]

    views_admin._probe_rows(rows)

    assert len(calls) == views_admin.HEALTH_PROBE_MAX
    assert 'probe' not in rows[views_admin.HEALTH_PROBE_MAX]


def test_the_row_cap_never_truncates_a_full_page_of_pending_rows():
    """El tope de filas NO se auto-cura (a diferencia del presupuesto): si baja
    de la página del panel, la cola de pendientes pierde chips para siempre."""
    views_admin = pytest.importorskip('ckanext.csunesco.logic.views_admin')
    admin = pytest.importorskip('ckanext.csunesco.logic.action.admin')
    assert views_admin.HEALTH_PROBE_MAX >= views_admin.PANEL_PAGE_SIZE
    assert admin.CONNECTED_LIST_LIMIT <= views_admin.HEALTH_PROBE_MAX


def test_probing_stops_when_the_budget_is_spent(monkeypatch):
    """Ocho forms muertos x PROBE_TIMEOUT es un panel de 48 s. El presupuesto
    de reloj es el techo que lo impide."""
    import time as _time
    views_admin = pytest.importorskip('ckanext.csunesco.logic.views_admin')
    ofform = pytest.importorskip('ckanext.csunesco.logic.ofform')

    def slow(form_id):
        _time.sleep(0.02)
        return {'ok': False}

    monkeypatch.setattr(ofform, 'probe_form', slow)
    monkeypatch.setattr(ofform, 'public_form_url', lambda form_id: None)
    rows = [{'form_id': n} for n in range(12)]

    probed = views_admin._probe_rows(rows, budget=0.05)

    assert 0 < len(probed) < len(rows)


def test_a_bad_row_never_costs_the_panel(monkeypatch):
    views_admin = pytest.importorskip('ckanext.csunesco.logic.views_admin')
    ofform = pytest.importorskip('ckanext.csunesco.logic.ofform')

    def flaky(form_id):
        if form_id == 1:
            raise RuntimeError('upstream on fire')
        return {'ok': True, 'total': 3}

    monkeypatch.setattr(ofform, 'probe_form', flaky)
    monkeypatch.setattr(ofform, 'public_form_url', lambda form_id: None)
    rows = [{'form_id': 0}, {'form_id': 1}, {'form_id': 2}]

    probed = views_admin._probe_rows(rows)

    assert len(probed) == 2
    assert rows[2]['probe'] == {'ok': True, 'total': 3}


def test_unreachable_sources_sort_to_the_top():
    views_admin = pytest.importorskip('ckanext.csunesco.logic.views_admin')
    rows = [{'probe': {'ok': True}}, {}, {'probe': {'ok': False}}]
    rows.sort(key=views_admin._health_rank)
    assert rows[0]['probe'] == {'ok': False}
    assert rows[-1] == {}


# --------------------------------------------------------------------------- #
# El probe tiene su propia memoria: sin ella, cada render re-marca los muertos #
# --------------------------------------------------------------------------- #

def test_a_dark_form_is_probed_once_not_once_per_render(monkeypatch):
    ofform = pytest.importorskip('ckanext.csunesco.logic.ofform')
    ofform.cache_clear()
    calls = []

    def boom(path, timeout=None):
        calls.append(path)
        raise ofform.OfformError('HTTP 404')

    monkeypatch.setattr(ofform, '_fetch', boom)
    try:
        results = [ofform.probe_form(7) for _ in range(5)]
        assert all(r == {'ok': False} for r in results)
        assert len(calls) == 1
    finally:
        ofform.cache_clear()


def test_the_probe_memo_never_blocks_the_proxy(monkeypatch):
    """Clave de caché disjunta: un probe corto que falla no puede dejar sin
    datos a los proxys, que sí tienen tiempo de sobra."""
    ofform = pytest.importorskip('ckanext.csunesco.logic.ofform')
    ofform.cache_clear()
    calls = []

    def boom(path, timeout=None):
        calls.append(timeout)
        raise ofform.OfformError('HTTP 404')

    monkeypatch.setattr(ofform, '_fetch', boom)
    try:
        assert ofform.probe_form(7) == {'ok': False}
        with pytest.raises(ofform.OfformError):
            ofform.fetch_dashboard_data(7)
        # El proxy SÍ salió a la red: no heredó el veredicto del probe.
        assert len(calls) == 2
    finally:
        ofform.cache_clear()


def test_a_short_probe_cannot_poison_the_cache_with_garbage(monkeypatch):
    """Las ramas de JSON inválido ignoraban ``remember_failure``."""
    ofform = pytest.importorskip('ckanext.csunesco.logic.ofform')
    ofform.cache_clear()
    monkeypatch.setattr(ofform, '_fetch',
                        lambda path, timeout=None: b'not json at all')
    try:
        with pytest.raises(ofform.OfformError):
            ofform.fetch_dashboard_data(7, timeout=ofform.PROBE_TIMEOUT)
        assert ofform._cache_get(('dashboard', 7)) is None
    finally:
        ofform.cache_clear()


# ---------------------------------------------------------------------------
# ofform client: la caché tiene que proteger de verdad a los workers
# ---------------------------------------------------------------------------

def test_ofform_cache_enforces_its_entry_cap():
    """Purgar sólo lo EXPIRADO no bastaba: con más formularios calientes que el
    tope, nada estaba expirado y la caché crecía sin límite -- ~1,6 MB por
    payload parseado, justo la memoria de worker que este tope protege."""
    ofform = pytest.importorskip('ckanext.csunesco.logic.ofform')
    ofform.cache_clear()
    try:
        for i in range(ofform.MAX_CACHE_ENTRIES * 3):
            ofform._cache_set(('dashboard', i), {'rows': [], 'n': i})
        assert len(ofform._cache) == ofform.MAX_CACHE_ENTRIES
        # Se conservan las más recientes (TTL uniforme -> orden de inserción).
        newest = ('dashboard', ofform.MAX_CACHE_ENTRIES * 3 - 1)
        assert newest in ofform._cache
        assert ('dashboard', 0) not in ofform._cache
    finally:
        ofform.cache_clear()


def test_ofform_remembers_a_failure_so_the_next_caller_fails_fast(monkeypatch):
    """Sin caché negativa, un upstream caído convertía CADA bloque de gráfico,
    mapa y enlace CSV en una espera nueva de REQUEST_TIMEOUT, en cada visita."""
    ofform = pytest.importorskip('ckanext.csunesco.logic.ofform')
    ofform.cache_clear()
    calls = []

    def _boom(path, timeout=ofform.REQUEST_TIMEOUT):
        calls.append(path)
        raise ofform.OfformError('network error')

    monkeypatch.setattr(ofform, '_fetch', _boom)
    try:
        for _ in range(5):
            with pytest.raises(ofform.OfformError):
                ofform.fetch_dashboard_data(7)
        assert len(calls) == 1, 'solo el primer intento debe salir a la red'

        # El CSV tiene su propia clave: se comprueba que tambien recuerda.
        del calls[:]
        for _ in range(3):
            with pytest.raises(ofform.OfformError):
                ofform.fetch_csv(7)
        assert len(calls) == 1
    finally:
        ofform.cache_clear()


def test_ofform_failure_is_forgotten_when_it_expires(monkeypatch):
    ofform = pytest.importorskip('ckanext.csunesco.logic.ofform')
    ofform.cache_clear()
    monkeypatch.setattr(ofform, 'FAILURE_CACHE_TTL', -1)  # ya vencida
    calls = []

    def _boom(path, timeout=ofform.REQUEST_TIMEOUT):
        calls.append(path)
        raise ofform.OfformError('network error')

    monkeypatch.setattr(ofform, '_fetch', _boom)
    try:
        for _ in range(3):
            with pytest.raises(ofform.OfformError):
                ofform.fetch_dashboard_data(7)
        assert len(calls) == 3, 'una cache negativa vencida no debe bloquear'
    finally:
        ofform.cache_clear()


def test_the_review_probe_never_blacklists_a_merely_slow_upstream(monkeypatch):
    """El probe del panel usa un timeout mas corto; si falla por lentitud, los
    proxys (que si tienen tiempo) deben poder intentarlo igualmente."""
    ofform = pytest.importorskip('ckanext.csunesco.logic.ofform')
    ofform.cache_clear()
    calls = []

    def _boom(path, timeout=ofform.REQUEST_TIMEOUT):
        calls.append(timeout)
        raise ofform.OfformError('network error')

    monkeypatch.setattr(ofform, '_fetch', _boom)
    try:
        with pytest.raises(ofform.OfformError):
            ofform.fetch_dashboard_data(7, timeout=ofform.PROBE_TIMEOUT)
        with pytest.raises(ofform.OfformError):
            ofform.fetch_dashboard_data(7)
        assert calls == [ofform.PROBE_TIMEOUT, ofform.REQUEST_TIMEOUT]
    finally:
        ofform.cache_clear()


def test_a_recovered_upstream_is_served_again(monkeypatch):
    ofform = pytest.importorskip('ckanext.csunesco.logic.ofform')
    ofform.cache_clear()
    state = {'fail': True}

    def _flaky(path, timeout=ofform.REQUEST_TIMEOUT):
        if state['fail']:
            raise ofform.OfformError('network error')
        return b'{"rows": [], "total": 0}'

    monkeypatch.setattr(ofform, '_fetch', _flaky)
    try:
        with pytest.raises(ofform.OfformError):
            ofform.fetch_dashboard_data(7)
        # Vencida la marca (aqui se simula vaciando), el upstream vuelve.
        state['fail'] = False
        ofform.cache_clear()
        assert ofform.fetch_dashboard_data(7) == {'rows': [], 'total': 0}
    finally:
        ofform.cache_clear()


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


# ---------------------------------------------------------------------------
# Registro: toda acción necesita su función de auth
# ---------------------------------------------------------------------------

def test_every_registered_action_has_an_auth_function():
    """Una acción sin entrada en el registro de auth es una mina.

    CKAN no comprueba el permiso por su cuenta —lo hace la propia acción—, así
    que la falta no abre un agujero por sí sola. Pero el día que alguien llame
    a ``check_access``/``h.check_access`` con ese nombre, CKAN lanza
    ``ValueError: Authorization function not found``: un 500, no un 403. Es
    justo el modo de fallo que no se ve en una review.
    """
    actions = pytest.importorskip('ckanext.csunesco.logic.actions')
    auth = pytest.importorskip('ckanext.csunesco.logic.auth')
    missing = sorted(set(actions.get_actions()) - set(auth.get_auth_functions()))
    assert not missing, 'acciones sin función de auth: %s' % missing


def test_no_orphan_auth_functions():
    """Y al revés: una auth sin acción suele ser un typo en el nombre, que
    dejaría la acción real cubierta por el default en vez de por esta regla."""
    actions = pytest.importorskip('ckanext.csunesco.logic.actions')
    auth = pytest.importorskip('ckanext.csunesco.logic.auth')
    orphans = sorted(set(auth.get_auth_functions()) - set(actions.get_actions()))
    assert not orphans, 'auth sin acción (¿typo?): %s' % orphans


# --------------------------------------------------------------------------- #
# csunesco_content_image: portada de card desde media[] (heurística pura)      #
# --------------------------------------------------------------------------- #

def test_content_image_picks_first_image_url():
    from ckanext.csunesco.logic.helpers import csunesco_content_image
    assert csunesco_content_image(None) is None
    assert csunesco_content_image([]) is None
    # La primera URL de IMAGEN gana, aunque no sea la primera de la lista.
    media = ['https://example.org/report.pdf',
             'https://example.org/photo.JPG?w=800',
             'https://example.org/other.png']
    assert csunesco_content_image(media) == 'https://example.org/photo.JPG?w=800'
    # El querystring no engaña a la heurística (extensión sobre el PATH).
    assert csunesco_content_image(
        ['https://example.org/doc.pdf?name=x.png']) is None
    # Entradas basura se ignoran sin reventar.
    assert csunesco_content_image([None, 42, '  ', 'not a url']) is None


# --------------------------------------------------------------------------- #
# The staged project form: new fields, the step map and the ofform contract    #
# --------------------------------------------------------------------------- #

def _navl(data, sch):
    """Run navl over ``data`` and return ``(validated, errors)``."""
    return tk.navl_validate(data, sch, {'model': None, 'session': None})


def test_project_request_schema_new_fields_are_all_optional():
    """Every field the staged form added must be ignore_missing.

    Load-bearing: the CS Toolbox (ofform) outbox POSTs to this same action with
    a fixed payload that mentions none of them. A single required addition
    would break every project the app creates.
    """
    s = schema.project_request_schema()
    ignore_missing = tk.get_validator('ignore_missing')
    for field in schema.PROJECT_EXTRA_FIELDS:
        assert field in s, 'missing new field %r' % field
        assert s[field][0] is ignore_missing, \
            '%r must start with ignore_missing' % field


def test_project_request_schema_still_requires_a_title():
    """`title` is the only required field left.

    `initiative` was required until the app's programme-less projects showed
    that it must not be: it now normalizes an empty value instead of bouncing.
    """
    s = schema.project_request_schema()
    not_empty = tk.get_validator('not_empty')
    ignore_missing = tk.get_validator('ignore_missing')
    assert not_empty in s['title']
    assert not_empty not in s['initiative']
    assert s['initiative'][0] is ignore_missing


def test_project_update_schema_drops_slug():
    """A project's URL is permanent; an edit must not be able to move it."""
    assert 'slug' not in schema.project_update_schema()
    assert 'slug' in schema.project_request_schema()


def test_project_update_schema_narrows_to_present_keys():
    """A partial API update must not be rejected for omitting ``title``."""
    narrowed = schema.project_update_schema(['contact_email'])
    assert set(narrowed) == {'contact_email'}
    # ...but a key that IS sent still faces its whole rule list.
    assert tk.get_validator('not_empty') in \
        schema.project_update_schema(['title'])['title']


def test_ofform_payload_still_validates_unchanged():
    """Regression guard for the ofform contract.

    This is the exact payload ofform's outbox posts
    (backend/app/routers/cs_projects.py). It must keep validating clean, and
    must not acquire any of the staged form's fields by accident.
    """
    payload = {
        'programme_id': 'abc-123',
        'slug': 'douro-basin',
        'title': 'Douro Basin',
        'short_description': 'Freshwater monitoring.',
        'initiative': 'river-watch',
        'countries': [],
        'biosphere_reserve': 'Douro',
        'region_geojson': '',
        'project_document_url': 'https://example.org/doc.pdf',
        'requested_by': 'ana',
    }
    s = schema.project_request_schema()
    # The action whitelists to schema keys before validating; do the same here.
    incoming = {k: payload[k] for k in s if k in payload}
    data, errors = _navl(incoming, s)
    assert not errors, errors
    assert data['initiative'] == 'riverwatch'   # hyphenated alias normalized
    for field in schema.PROJECT_EXTRA_FIELDS:
        assert field not in data, \
            '%r appeared from a payload that never sent it' % field


def test_end_after_allows_equal_dates_for_a_project():
    """A one-day project legitimately has start_date == end_date."""
    s = schema.project_request_schema()
    same = '2026-07-16'
    data, errors = _navl(
        {'title': 'T', 'initiative': 'riverwatch',
         'start_date': same, 'end_date': same}, s)
    assert not errors, errors


def test_end_after_rejects_end_before_start():
    s = schema.project_request_schema()
    data, errors = _navl(
        {'title': 'T', 'initiative': 'riverwatch',
         'start_date': '2026-07-16', 'end_date': '2026-07-15'}, s)
    assert 'end_date' in errors


def test_a_single_day_event_is_accepted():
    """Was strict end > start, and rejected the commonest kind of event.

    The CS Toolbox app sends date-only ISO strings, so a one-day event arrives
    with end == start. That is not a data-entry mistake, it is Tuesday.
    """
    s = schema.content_schema('cs-event')
    same = '2026-07-16'
    data, errors = _navl(
        {'title': 'E', 'content_type': 'cs-event',
         'publish_date': same, 'end_date': same}, s)
    assert not errors, errors


def test_an_open_ended_event_is_accepted():
    s = schema.content_schema('cs-event')
    data, errors = _navl(
        {'title': 'E', 'content_type': 'cs-event',
         'publish_date': '2026-07-16'}, s)
    assert not errors, errors


def test_an_event_ending_before_it_starts_is_still_rejected():
    s = schema.content_schema('cs-event')
    data, errors = _navl(
        {'title': 'E', 'content_type': 'cs-event',
         'publish_date': '2026-07-16', 'end_date': '2026-07-15'}, s)
    assert 'end_date' in errors


def test_initiative_is_optional_for_a_programme_less_project():
    """The app offers "Choose a programme (optional)" and posts null."""
    s = schema.project_request_schema()
    data, errors = _navl({'title': 'No programme', 'initiative': None}, s)
    assert not errors, errors


def test_contact_email_rejects_a_second_recipient():
    """The stored value is rendered into a mailto: on the landing page."""
    s = schema.project_request_schema()
    data, errors = _navl(
        {'title': 'T', 'initiative': 'riverwatch',
         'contact_email': 'a@b.co,evil@x.y'}, s)
    assert 'contact_email' in errors


def test_contact_email_empty_is_accepted():
    """ignore_missing does NOT skip '', so the validator must tolerate it."""
    s = schema.project_request_schema()
    data, errors = _navl(
        {'title': 'T', 'initiative': 'riverwatch', 'contact_email': ''}, s)
    assert not errors, errors


def test_open_participation_false_survives_validation():
    s = schema.project_request_schema()
    data, errors = _navl(
        {'title': 'T', 'initiative': 'riverwatch',
         'open_participation': False}, s)
    assert not errors, errors
    assert data['open_participation'] is False


def test_project_form_steps_cover_the_schema_exactly():
    """The stage map and the schema must not drift apart.

    A field added to the schema but not placed on a stage would validate and
    then never render; a stage naming a field the schema dropped would render
    an input nothing reads.
    """
    from ckanext.csunesco import constants
    placed = set()
    for step in constants.PROJECT_FORM_STEPS:
        placed.update(step['fields'])
    expected = set(schema.project_request_schema()) - {'slug'}
    assert placed == expected, (
        'only in steps: %s / only in schema: %s'
        % (sorted(placed - expected), sorted(expected - placed)))


def test_project_form_steps_are_numbered_one_to_five():
    from ckanext.csunesco import constants
    numbers = [step['step'] for step in constants.PROJECT_FORM_STEPS]
    assert numbers == [1, 2, 3, 4, 5]
