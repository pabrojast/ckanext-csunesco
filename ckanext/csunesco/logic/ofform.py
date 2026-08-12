# encoding: utf-8
"""HTTP client for the CS Toolbox (ofform) public data endpoints + GeoJSON.

The ONLY origin this module ever fetches is the configured
``ckanext.csunesco.ofform_base_url``; form ids are coerced to ``int`` and
interpolated into a fixed path under that base, so no client-supplied string
can steer the request anywhere else (anti-SSRF). Responses are cached per
(form id, format) with a short TTL so a hot public dataset costs ofform at
most about one upstream fetch per TTL per format.

Upstream contract (see /home/pabrojast/ofform backend, ``routers/public.py``):
  * ``GET {base}/public/forms/{id}/dashboard-data`` -> ``{schema, total,
    truncated, rows: [{id, date, lat, lng, source, answers{}}]}``
  * ``GET {base}/public/forms/{id}/export.csv``     -> CSV text
Both return 404 unless the form is ``visibility=public`` AND ``published``.
"""
import json
import logging
import math
import threading
import time
import urllib.error
import urllib.request

import ckan.plugins.toolkit as tk

log = logging.getLogger(__name__)

BASE_URL_OPTION = 'ckanext.csunesco.ofform_base_url'
CACHE_TTL_OPTION = 'ckanext.csunesco.ofform_cache_ttl'
# The app's FRONTEND base (for "open in the app" links shown to reviewers) --
# distinct from the API base the proxy fetches from.
APP_URL_OPTION = 'ckanext.csunesco.ofform_app_url'

DEFAULT_CACHE_TTL = 60
REQUEST_TIMEOUT = 15
# The review-panel probe must never hold the admin page hostage: shorter
# timeout than the proxy, and results are TTL-cached like everything else.
PROBE_TIMEOUT = 6
# Hard cap on a proxied payload (bytes): protects CKAN worker memory from a
# runaway upstream response. ofform itself truncates dashboard data at 20k rows.
MAX_PROXY_BYTES = 20_000_000
# Kept deliberately small: a cached entry is a PARSED dashboard payload, and a
# real one runs to ~1.6 MB (form 3 on the dev portal: 1605 rows). At 128 that is
# ~200 MB of resident dicts per worker. Chart blocks keep this cache far hotter
# than the proxy alone did, so the ceiling has to be a size the workers can
# actually afford.
MAX_CACHE_ENTRIES = 32
# How long a FAILURE is remembered. Without this, an upstream that is down (or
# a form that was archived/unpublished after its dataset was created) turns
# every chart block, map and CSV link into a fresh REQUEST_TIMEOUT stall, on
# every single page view: a landing page fans out to one request per block, and
# each one holds a CKAN worker for 15 s. Short enough that recovery is quick,
# long enough that a dead upstream stops costing worker time.
FAILURE_CACHE_TTL = 30
# How long a review-panel PROBE result (ok or not) is remembered, under its own
# cache key. See :func:`probe_form` for why this exists and why it is short.
PROBE_CACHE_TTL = 45

_cache = {}
_cache_lock = threading.Lock()


class _Failure(object):
    """Marker stored in the cache so a known-bad fetch fails fast."""

    __slots__ = ('reason',)

    def __init__(self, reason):
        self.reason = reason


class OfformError(Exception):
    """Generic upstream failure. Details are logged, never surfaced raw."""


def get_base_url():
    """The configured CS Toolbox base URL (no trailing slash), or ``None``."""
    raw = tk.config.get(BASE_URL_OPTION) or ''
    return raw.strip().rstrip('/') or None


def cache_ttl():
    try:
        ttl = int(tk.config.get(CACHE_TTL_OPTION) or DEFAULT_CACHE_TTL)
    except (TypeError, ValueError):
        return DEFAULT_CACHE_TTL
    return ttl if ttl > 0 else DEFAULT_CACHE_TTL


def _coerce_form_id(form_id):
    """Return ``form_id`` as a positive int (the SSRF guard for the path)."""
    try:
        value = int(form_id)
    except (TypeError, ValueError):
        raise OfformError('invalid form id')
    if value <= 0:
        raise OfformError('invalid form id')
    return value


def _cache_get(key):
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at < time.time():
            _cache.pop(key, None)
            return None
        return value


def _cache_set(key, value, ttl=None):
    with _cache_lock:
        _cache[key] = (time.time() + (cache_ttl() if ttl is None else ttl), value)
        if len(_cache) <= MAX_CACHE_ENTRIES:
            return
        now = time.time()
        for stale in [k for k, (exp, _v) in _cache.items() if exp < now]:
            _cache.pop(stale, None)
        # Dropping the EXPIRED entries is not enough on its own: with more hot
        # forms than the cap, nothing is expired and the cache grew without
        # bound -- ~1.6 MB per parsed payload, which is exactly the worker
        # memory this cap exists to protect. Evict by nearest expiry (uniform
        # TTL, so that is insertion order) until we are back at the ceiling.
        while len(_cache) > MAX_CACHE_ENTRIES:
            oldest = min(_cache, key=lambda k: _cache[k][0])
            _cache.pop(oldest, None)


def _cache_failure(key, reason):
    """Remember a failed fetch briefly so the next caller fails fast."""
    _cache_set(key, _Failure(reason), ttl=FAILURE_CACHE_TTL)


def _cached_or_raise(key):
    """Cache hit for ``key``: the value, or None. Raises on a cached failure."""
    cached = _cache_get(key)
    if isinstance(cached, _Failure):
        raise OfformError(cached.reason)
    return cached


def cache_clear():
    """Drop every cached response (tests / admin tooling)."""
    with _cache_lock:
        _cache.clear()


def _fetch(path, timeout=REQUEST_TIMEOUT):
    """GET ``{base}{path}`` with a timeout + size cap. Returns raw bytes.

    ``path`` is ALWAYS built by this module from an int form id -- never from a
    client-supplied string.
    """
    base = get_base_url()
    if not base:
        raise OfformError('ofform base URL is not configured')
    url = base + path
    request = urllib.request.Request(
        url, headers={
            'Accept': 'application/json, text/csv, */*',
            # Explicit UA: Cloudflare's bot protection 403s the default
            # Python-urllib agent, silently breaking the whole proxy.
            'User-Agent': 'ckanext-csunesco (IHP-WINS data proxy)',
        })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            chunks = []
            total = 0
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_PROXY_BYTES:
                    log.warning('csunesco: ofform payload exceeded size cap')
                    raise OfformError('upstream payload too large')
                chunks.append(chunk)
            return b''.join(chunks)
    except OfformError:
        raise
    except urllib.error.HTTPError as error:
        # The path names the form and the endpoint. Without it the log says a
        # fetch failed but not WHICH form went dark -- and a 404 here almost
        # always means one specific form stopped being public+published in the
        # app, which is exactly the thing an operator needs to be told. Safe to
        # log: this module builds ``path`` from an int, never from client input.
        log.warning('csunesco: ofform fetch failed (HTTP %s) for %s',
                    error.code, path)
        raise OfformError('HTTP %s' % error.code)
    except Exception as error:
        log.warning('csunesco: ofform fetch failed (%s) for %s',
                    type(error).__name__, path)
        raise OfformError('network error')


def fetch_dashboard_data(form_id, timeout=REQUEST_TIMEOUT):
    """The public dashboard-data JSON for a form (TTL-cached dict)."""
    form_id = _coerce_form_id(form_id)
    key = ('dashboard', form_id)
    cached = _cached_or_raise(key)
    if cached is not None:
        return cached
    # El probe del panel de revisión usa un timeout más corto: si falla por
    # lentitud no debe marcar el upstream como caído para los proxys, que sí
    # tienen tiempo de sobra. Sólo el camino normal alimenta la caché negativa.
    remember_failure = timeout >= REQUEST_TIMEOUT
    try:
        raw = _fetch('/public/forms/%d/dashboard-data' % form_id, timeout=timeout)
        data = json.loads(raw.decode('utf-8'))
    except OfformError as error:
        if remember_failure:
            _cache_failure(key, str(error))
        raise
    except (ValueError, UnicodeDecodeError):
        log.warning('csunesco: ofform dashboard-data was not valid JSON '
                    '(form %s)', form_id)
        # Same rule as the OfformError branch above: a SHORT-timeout probe must
        # never blacklist the upstream for the proxies. These two branches
        # ignored it, so one garbled response during a review-panel probe could
        # poison the proxy cache for FAILURE_CACHE_TTL.
        if remember_failure:
            _cache_failure(key, 'invalid upstream response')
        raise OfformError('invalid upstream response')
    if not isinstance(data, dict):
        if remember_failure:
            _cache_failure(key, 'invalid upstream response')
        raise OfformError('invalid upstream response')
    _cache_set(key, data)
    return data


def fetch_csv(form_id):
    """The public CSV export for a form (TTL-cached text)."""
    form_id = _coerce_form_id(form_id)
    key = ('csv', form_id)
    cached = _cached_or_raise(key)
    if cached is not None:
        return cached
    try:
        raw = _fetch('/public/forms/%d/export.csv' % form_id)
    except OfformError as error:
        _cache_failure(key, str(error))
        raise
    text = raw.decode('utf-8', errors='replace')
    _cache_set(key, text)
    return text


# ---------------------------------------------------------------------------
# Review-time probe (admin panel context for approving data sources)
# ---------------------------------------------------------------------------


def public_form_url(form_id):
    """The app-frontend URL of a form's public dashboard, or ``None``.

    Requires ``ckanext.csunesco.ofform_app_url`` (the PWA base). Used only to
    render "open in the app" links for reviewers -- never fetched server-side.
    """
    base = (tk.config.get(APP_URL_OPTION) or '').strip().rstrip('/')
    if not base:
        return None
    try:
        return '%s/public/forms/%d' % (base, _coerce_form_id(form_id))
    except OfformError:
        return None


def summarize_dashboard(data):
    """Pure review summary of a dashboard-data payload (unit-testable).

    Returns ``{'ok': True, 'total', 'truncated', 'first_date', 'last_date',
    'with_coords'}`` -- the context a reviewer needs to approve with eyes open.
    """
    data = data or {}
    rows = data.get('rows') or []
    dates = sorted(
        str(row.get('date'))
        for row in rows
        if isinstance(row, dict) and row.get('date'))
    with_coords = sum(
        1 for row in rows
        if isinstance(row, dict) and _valid_coord(row.get('lat'),
                                                  row.get('lng')))
    return {
        'ok': True,
        'total': data.get('total') if data.get('total') is not None
        else len(rows),
        'truncated': bool(data.get('truncated')),
        'first_date': dates[0][:10] if dates else None,
        'last_date': dates[-1][:10] if dates else None,
        'with_coords': with_coords,
    }


def probe_form(form_id):
    """Live health/summary check of a form's public data for the review panel.

    ``{'ok': False}`` when the form is unreachable, not public or ofform is
    not configured -- the reviewer sees a clear warning instead of approving a
    source that would 502. Short timeout + its own TTL cache.

    That cache is the point: a probe FAILURE is deliberately not remembered by
    :func:`fetch_dashboard_data` (its short timeout must never blacklist the
    upstream for the proxies), so without a memo of its own the panel re-dialled
    every dark form on every single render -- eight dead forms x PROBE_TIMEOUT
    is a 48-second dashboard. The key is disjoint from the payload cache's, so
    the proxies' negative cache is still never poisoned by a probe.
    """
    try:
        key = ('probe', _coerce_form_id(form_id))
    except OfformError:
        return {'ok': False}
    cached = _cache_get(key)
    if cached is not None:
        return cached
    try:
        result = summarize_dashboard(
            fetch_dashboard_data(form_id, timeout=PROBE_TIMEOUT))
    except OfformError:
        result = {'ok': False}
    # Deliberately SHORTER than the payload TTL: the shared cache evicts by
    # nearest expiry, so a burst of probe entries from one dashboard render is
    # the first thing dropped rather than the ~1.6 MB parsed payloads
    # MAX_CACHE_ENTRIES exists to protect. Losing a probe costs one re-dial;
    # losing a payload costs a full upstream fetch on the next public page view.
    # ``cache_ttl()`` is operator-configurable, and that ordering argument only
    # holds while the probe TTL is the shorter of the two.
    _cache_set(key, result, ttl=min(PROBE_CACHE_TTL, cache_ttl()))
    return result


# ---------------------------------------------------------------------------
# GeoJSON conversion (pure -- no HTTP, unit-testable)
# ---------------------------------------------------------------------------


def _valid_coord(lat, lng):
    """True when both coordinates are finite numbers inside WGS84 bounds."""
    try:
        lat = float(lat)
        lng = float(lng)
    except (TypeError, ValueError):
        return False
    if not (math.isfinite(lat) and math.isfinite(lng)):
        return False
    return -90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0


def _flatten_value(value):
    """Flatten an answer value to a GeoJSON-property-friendly scalar."""
    if isinstance(value, float):
        return round(value, 6) if math.isfinite(value) else None
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return '|'.join(str(item) for item in value)
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return str(value)


def observation_site_keys(dashboard_data):
    """Distinct coordinate keys (rounded to 4 decimals ~ 11 m) of the rows.

    The working definition of a "site monitored". Returned as a set so
    multi-source refreshes can union across forms without double counting.
    """
    rows = (dashboard_data or {}).get('rows') or []
    return {
        (round(float(row['lat']), 4), round(float(row['lng']), 4))
        for row in rows
        if isinstance(row, dict) and _valid_coord(row.get('lat'),
                                                  row.get('lng'))
    }


def observation_stats(dashboard_data):
    """Pure counters from a dashboard-data payload (unit-testable).

    ``observations`` prefers the upstream ``total`` (accurate even when the
    row list is truncated); ``sites`` counts :func:`observation_site_keys`.
    """
    data = dashboard_data or {}
    total = data.get('total')
    return {
        'observations': int(total) if total is not None
        else len(data.get('rows') or []),
        'sites': len(observation_site_keys(data)),
    }


def rows_to_geojson(dashboard_data):
    """Convert ofform dashboard-data into a GeoJSON ``FeatureCollection``.

    One ``Point`` Feature per row with valid coordinates (rows without a
    usable lat/lng are skipped, not errored). Each feature carries ``id``,
    ``source`` and an ISO ``date`` property -- the per-feature time key Terria's
    time slider uses -- plus the row's answers flattened to scalars. The output
    is a plain FeatureCollection so terria_view, maplibre and Leaflet can all
    consume it directly.
    """
    rows = (dashboard_data or {}).get('rows') or []
    features = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        lat = row.get('lat')
        lng = row.get('lng')
        if not _valid_coord(lat, lng):
            continue
        properties = {
            'id': row.get('id'),
            'source': row.get('source'),
            'date': row.get('date'),
        }
        answers = row.get('answers')
        if isinstance(answers, dict):
            for key, value in answers.items():
                properties.setdefault(str(key), _flatten_value(value))
        features.append({
            'type': 'Feature',
            'geometry': {
                'type': 'Point',
                'coordinates': [float(lng), float(lat)],
            },
            'properties': properties,
        })
    return {'type': 'FeatureCollection', 'features': features}
