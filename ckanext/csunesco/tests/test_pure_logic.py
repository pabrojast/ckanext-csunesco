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

@pytest.mark.parametrize('ctype', ['cs-news', 'cs-event'])
def test_content_type_accepts_known(ctype):
    assert v.csunesco_valid_content_type(ctype) == ctype


@pytest.mark.parametrize('ctype', ['cs-media', 'news', '', 'CS-NEWS'])
def test_content_type_rejects_unknown(ctype):
    with pytest.raises(tk.Invalid):
        v.csunesco_valid_content_type(ctype)


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
