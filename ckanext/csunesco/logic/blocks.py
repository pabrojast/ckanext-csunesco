# encoding: utf-8
"""THE block registry for project pages (NO CKAN import).

A project landing page is an ordered list of blocks. This module is the single
place that knows what a block type is: its label, its editor snippet, its
render snippet, how many may exist on one page, whether it embeds something
external, and -- above all -- how to normalize an untrusted block dict into a
stored one.

Why a registry and not a switch in five files: the Data Stories editor in
``ckanext-pages`` duplicates its block-type list across the edit template, the
clone ``<template>``, two JS serializers and one Python renderer. They drift,
and a stale editor there silently drops block types on save. Here every layer
(editor form, validator, renderer, admin preview) reads ``BLOCK_TYPES``.

**Trust model.** Nothing that arrives here is trusted -- not the POST body, not
the JSON already in the database. :func:`normalize_blocks` is total: it never
raises, it drops what it cannot understand (unknown types, unknown keys, bad
URLs) and clamps everything else into range. A forged or legacy payload must
degrade to a duller page, never to a 500 on a public URL.

**Layering.** This module validates *shape* only, using nothing but the stdlib
and :mod:`ckanext.csunesco.logic.sanitize`, so it is unit-testable without CKAN
or a container. *Policy* that needs config -- chiefly "is this Terria URL under
an allowlisted base" -- is enforced by the CKAN layer on write and again by
``helpers.csunesco_terria_embed_url`` at render time.
"""
import json
import re
import uuid
from urllib.parse import urlparse

from ckanext.csunesco.logic.sanitize import sanitize_page_html

# --------------------------------------------------------------------------- #
# Caps                                                                        #
# --------------------------------------------------------------------------- #

# Whole-page limits. MAX_JSON_BYTES is checked BEFORE json.loads -- the same
# cheap DoS guard the GeoJSON validator uses, for the same reason: never hand an
# unbounded string to the parser.
MAX_BLOCKS = 60
MAX_JSON_BYTES = 512_000

MAX_TITLE = 160
MAX_CAPTION = 300
MAX_RICH_TEXT = 40_000
MAX_CALLOUT_HTML = 4_000
MAX_URL = 500

WIDTHS = ('full', 'narrow')

_ID_RE = re.compile(r'^[0-9a-f]{8}$')
_FIELD_NAME_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
_REF_RE = re.compile(r'^[A-Za-z0-9._-]{1,100}$')
_TAG_RE = re.compile(r'<[^>]*>')
_WS_RE = re.compile(r'\s+')
# An internal link: a site-relative path only. No scheme, no host, no
# protocol-relative "//evil.com" (which a naive "starts with /" check lets in).
_INTERNAL_PATH_RE = re.compile(r'^/(?!/)[A-Za-z0-9/_%.~+&=?#-]*$')

# Video hosts we will build an embed for. Anything else is not a video.
_YOUTUBE_HOSTS = frozenset((
    'youtube.com', 'www.youtube.com', 'm.youtube.com',
    'youtu.be', 'www.youtu.be',
    'youtube-nocookie.com', 'www.youtube-nocookie.com',
))
_VIMEO_HOSTS = frozenset(('vimeo.com', 'www.vimeo.com', 'player.vimeo.com'))
_YOUTUBE_ID_RE = re.compile(r'^[A-Za-z0-9_-]{6,20}$')
_VIMEO_ID_RE = re.compile(r'^[0-9]{6,12}$')
_VIDEO_FILE_RE = re.compile(r'\.(mp4|webm|ogg)$', re.IGNORECASE)


# A drop report rides back to the editor through the signed session cookie, so
# it has to stay small.
MAX_DROPS = 8


class DropReport(object):
    """Collects author input that normalization threw away. Never raises.

    Opt-in: ``normalize_blocks(raw, report=None)`` by default, so the READ path
    -- which runs on every render of stored JSON -- pays nothing and behaves
    identically. This NEVER changes what is stored; it is a parallel record of
    what was discarded, so the editor can say so instead of flashing
    "Draft saved" over the top of it.

    Records a stable ``reason`` code, not a sentence: the wording belongs in the
    templates, where it can be translated, and this module stays CKAN-free.
    """

    def __init__(self):
        self.drops = []
        self._block = None

    def enter(self, block_id):
        """Called once a block's final id is known; drops attach to it."""
        self._block = block_id

    def note(self, field, reason, value=u'', item=None):
        if len(self.drops) >= MAX_DROPS or self._block is None:
            return
        self.drops.append({'block': self._block, 'field': field,
                           'item': item, 'reason': reason,
                           'value': _plain(value, 60)})


def _kept(report, field, cleaner, value, reason, item=None):
    """Run ``cleaner``; note it when a non-empty author value came back empty."""
    out = cleaner(value)
    if report is not None and not out and _plain(value, MAX_URL):
        report.note(field, reason, value, item)
    return out


# --------------------------------------------------------------------------- #
# Field primitives                                                            #
# --------------------------------------------------------------------------- #

def _plain(value, limit):
    """Untrusted value -> single-line plain text, tags stripped, truncated."""
    if value is None:
        return u''
    if not isinstance(value, str):
        value = str(value)
    value = _WS_RE.sub(u' ', _TAG_RE.sub(u'', value)).strip()
    return value[:limit]


def _enum(value, choices, default):
    return value if value in choices else default


def _bool(value):
    """Truthy in the HTML-form sense: a checkbox posts 'on', JSON posts true."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'on', 'yes')
    return bool(value)


def _int(value, default, minimum, maximum):
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def _https_url(value):
    """An absolute https URL with a host, or ``''``.

    https only: these end up as ``src``/``href`` on a page served over TLS, and
    an http asset would either be blocked as mixed content or leak the visit.
    """
    text = _plain(value, MAX_URL)
    if not text:
        return u''
    try:
        parsed = urlparse(text)
    except ValueError:
        return u''
    if parsed.scheme != 'https' or not parsed.netloc:
        return u''
    return text


def _link_url(value):
    """An http(s) URL **or** a site-relative path, or ``''``.

    Links (unlike embedded assets) may legitimately point at another page of
    this portal, so a relative path is allowed -- but only a real one.
    """
    text = _plain(value, MAX_URL)
    if not text:
        return u''
    if _INTERNAL_PATH_RE.match(text):
        return text
    try:
        parsed = urlparse(text)
    except ValueError:
        return u''
    if parsed.scheme in ('http', 'https') and parsed.netloc:
        return text
    return u''


def _ref(value):
    """A CKAN name / row id reference, or ``''``."""
    text = _plain(value, 100)
    return text if _REF_RE.match(text) else u''


def _field_name(value):
    text = _plain(value, 64)
    return text if _FIELD_NAME_RE.match(text) else u''


def _rich(value, limit):
    """Sanitize to the page allowlist, then truncate the CLEAN result."""
    if value is None:
        return u''
    if not isinstance(value, str):
        value = str(value)
    return (sanitize_page_html(value) or u'')[:limit]


def _items(raw, limit):
    """Normalize a repeatable sub-field into a list of dicts, capped."""
    if isinstance(raw, dict):
        # The form parser hands back {index: {...}}; order by index.
        raw = [raw[key] for key in sorted(raw, key=_as_int)]
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)][:limit]


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def parse_video(url):
    """``(provider, video_id)`` for a video URL, or ``('', '')``.

    The id is extracted here so the renderer can build the embed URL itself.
    An author-supplied URL must NEVER reach an iframe ``src`` verbatim -- that
    is how a "video block" turns into an arbitrary-embed block.
    """
    text = _https_url(url)
    if not text:
        return u'', u''
    try:
        parsed = urlparse(text)
    except ValueError:
        return u'', u''
    host = (parsed.netloc or '').lower().split(':')[0]
    path = parsed.path or ''

    if host in _YOUTUBE_HOSTS:
        candidate = u''
        if host in ('youtu.be', 'www.youtu.be'):
            candidate = path.lstrip('/').split('/')[0]
        elif path.startswith('/embed/') or path.startswith('/shorts/'):
            candidate = path.split('/')[2] if len(path.split('/')) > 2 else u''
        else:
            for pair in (parsed.query or '').split('&'):
                if pair.startswith('v='):
                    candidate = pair[2:]
                    break
        if _YOUTUBE_ID_RE.match(candidate or ''):
            return u'youtube', candidate

    if host in _VIMEO_HOSTS:
        for part in path.split('/'):
            if _VIMEO_ID_RE.match(part):
                return u'vimeo', part

    if _VIDEO_FILE_RE.search(path):
        return u'file', u''

    return u'', u''


# --------------------------------------------------------------------------- #
# Per-type normalizers                                                        #
# --------------------------------------------------------------------------- #
# Each returns ONLY the type-specific keys; the envelope is added by
# normalize_block. None of them may raise.

def _n_rich_text(raw, report=None):
    return {'html': _rich(raw.get('html'), MAX_RICH_TEXT)}


def _n_chart(raw, report=None):
    return {
        # Re-checked against THIS project's approved sources at render time --
        # an id in stored JSON proves nothing (the source may since have been
        # rejected, or the JSON copied from another project).
        'data_source_id': _kept(report, 'data_source_id', _ref,
                                raw.get('data_source_id'), 'bad_ref'),
        'chart': _enum(raw.get('chart'), ('line', 'bar', 'pie'), 'line'),
        'mode': _enum(raw.get('mode'), ('numeric', 'category', 'count'),
                      'count'),
        'field': _kept(report, 'field', _field_name, raw.get('field'),
                       'bad_field_name'),
        'agg': _enum(raw.get('agg'),
                     ('mean', 'min', 'max', 'sum', 'count'), 'mean'),
        'group_by': (u'auto' if raw.get('group_by') in (None, '', 'auto')
                     else _kept(report, 'group_by', _field_name,
                                raw.get('group_by'), 'bad_field_name')),
        'bucket': _enum(raw.get('bucket'),
                        ('auto', 'day', 'week', 'month'), 'auto'),
        'range': _enum(raw.get('range'), ('all', '1y', '90d', '30d'), 'all'),
        'caption': _plain(raw.get('caption'), MAX_CAPTION),
        'height': _int(raw.get('height'), 320, 200, 600),
    }


STAT_SOURCES = ('manual', 'observations', 'sites_monitored',
                'citizen_scientists', 'member_states')


def _n_stats(raw, report=None):
    items = []
    for item in _items(raw.get('items'), 4):
        source = _enum(item.get('source'), STAT_SOURCES, 'manual')
        value = _plain(item.get('value'), 24) if source == 'manual' else u''
        label = _plain(item.get('label'), 60)
        # The JS-off editor always renders four rows so a counter can be added
        # without a round trip; the unfilled ones must not become empty tiles.
        # A manual row needs a NUMBER to be a counter; a label on its own
        # rendered a tile with an empty figure.
        if source == 'manual' and not value:
            continue
        items.append({
            # A live source overrides the stored number at render time, so a
            # counter can never go stale inside the JSON.
            'source': source,
            'value': value,
            'label': label,
        })
    return {'items': items}


def _n_image(raw, report=None):
    items = []
    for index, item in enumerate(_items(raw.get('items'), 12)):
        url = _kept(report, 'url', _https_url, item.get('url'),
                    'not_https', item=index)
        if not url:
            continue
        items.append({
            'url': url,
            'alt': _plain(item.get('alt'), 160),
            'caption': _plain(item.get('caption'), 200),
            'link': _kept(report, 'link', _link_url, item.get('link'),
                          'bad_link', item=index),
        })
    return {
        'layout': _enum(raw.get('layout'), ('grid', 'single', 'wide'), 'grid'),
        'items': items,
        'lightbox': _bool(raw.get('lightbox')),
    }


def _n_video(raw, report=None):
    url = _kept(report, 'url', _https_url, raw.get('url'), 'not_https')
    provider, video_id = parse_video(url)
    return {
        'url': url,
        'provider': provider,
        'video_id': video_id,
        'caption': _plain(raw.get('caption'), MAX_CAPTION),
        'ratio': _enum(raw.get('ratio'), ('16:9', '4:3'), '16:9'),
    }


def _n_observation_map(raw, report=None):
    return {
        'data_source_id': _kept(report, 'data_source_id', _ref,
                                raw.get('data_source_id'), 'bad_ref'),
        'height': _int(raw.get('height'), 360, 240, 700),
        'caption': _plain(raw.get('caption'), MAX_CAPTION),
    }


def _n_terria_map(raw, report=None):
    # Shape only. Whether the URL sits under an allowlisted Terria base is
    # config-dependent policy, enforced on write by the action layer and AGAIN
    # at render by helpers.csunesco_terria_embed_url.
    return {
        'url': _kept(report, 'url', _https_url, raw.get('url'), 'not_https'),
        'height': _int(raw.get('height'), 520, 300, 800),
        'caption': _plain(raw.get('caption'), MAX_CAPTION),
    }


CONTENT_LIST_TYPES = ('cs-news', 'cs-event', 'cs-publication', 'cs-map', 'all')


def _n_content_list(raw, report=None):
    return {
        'content_type': _enum(raw.get('content_type'), CONTENT_LIST_TYPES,
                              'all'),
        'scope': _enum(raw.get('scope'), ('project', 'initiative'), 'project'),
        'limit': _int(raw.get('limit'), 6, 1, 12),
        'featured_only': _bool(raw.get('featured_only')),
        'layout': _enum(raw.get('layout'), ('grid', 'list'), 'grid'),
        'empty_text': _plain(raw.get('empty_text'), 200),
    }


def _n_datasets_list(raw, report=None):
    ids = []
    for index, value in enumerate(_list(raw.get('ids'))):
        ref = _kept(report, 'ids', dataset_ref, value, 'bad_ref',
                    item=index)
        if ref:
            ids.append(ref)
    return {
        'source': _enum(raw.get('source'), ('project', 'organization', 'ids'),
                        'project'),
        'org': _kept(report, 'org', _ref, raw.get('org'), 'bad_ref'),
        'ids': ids[:10],
        'limit': _int(raw.get('limit'), 6, 1, 12),
        'show_description': _bool(raw.get('show_description')),
    }


def _list(value):
    if value is None:
        return []
    if isinstance(value, str):
        # The JS-off form posts a textarea of one reference per line. Split on
        # line breaks and commas ONLY -- splitting on any whitespace would chop
        # an invalid entry like "bad name" into a "bad" that passes _ref, i.e.
        # turn a typo into a silently wrong dataset reference.
        return [part.strip() for part in re.split(r'[\r\n,]+', value)
                if part.strip()]
    if isinstance(value, dict):
        return [value[key] for key in sorted(value, key=_as_int)]
    if isinstance(value, list):
        return value
    return []


# What someone actually copies out of the address bar: .../dataset/<name>.
_DATASET_URL_RE = re.compile(
    r'^https?://[^/]+/(?:[a-z]{2}(?:_[A-Za-z]{2})?/)?dataset/([A-Za-z0-9._-]+)')


def dataset_ref(value):
    """A dataset name, accepting a full dataset URL and taking its slug.

    Pasting the link is the obvious thing to do and used to be discarded in
    silence, leaving the block showing "No datasets to show yet".
    """
    text = _plain(value, MAX_URL)
    match = _DATASET_URL_RE.match(text)
    if match:
        return _ref(match.group(1))
    return _ref(text)


def _n_callout(raw, report=None):
    return {
        'tone': _enum(raw.get('tone'),
                      ('info', 'success', 'warning', 'action'), 'info'),
        'html': _rich(raw.get('html'), MAX_CALLOUT_HTML),
        'cta_label': _plain(raw.get('cta_label'), 60),
        'cta_url': _kept(report, 'cta_url', _link_url, raw.get('cta_url'),
                         'bad_link'),
    }


def _n_builtin(raw, report=None):
    """Built-in wrappers carry no payload -- only the envelope matters."""
    return {}


# --------------------------------------------------------------------------- #
# The registry                                                                #
# --------------------------------------------------------------------------- #

class BlockType(object):
    """One entry in the registry. ``label``/``description`` are English source
    strings; templates translate them."""

    def __init__(self, key, label, icon, normalize, description=u'',
                 max_instances=1, requires_review=False, builtin=False,
                 addable=True, deprecated=False):
        self.key = key
        self.label = label
        self.icon = icon
        self.description = description
        self.normalize = normalize
        self.max_instances = max_instances
        # True when the block renders content fetched from an origin we do not
        # control. Those defeat a trusted project's auto-publish -- see
        # page_initial_status.
        self.requires_review = requires_review
        self.builtin = builtin
        self.addable = addable
        self.deprecated = deprecated

    @property
    def snippet(self):
        return 'csunesco/blocks/%s.html' % self.key

    @property
    def editor_snippet(self):
        return 'csunesco/blocks/edit/%s.html' % self.key


_TYPES = [
    # --- author-added -----------------------------------------------------
    BlockType('rich_text', u'Text', u'\U0001F4DD', _n_rich_text,
              u'A paragraph of formatted text.', max_instances=20),
    BlockType('chart', u'Chart', u'\U0001F4CA', _n_chart,
              u'A chart of the observations your volunteers collected in the '
              u'app. Nothing shows until you pick which data to chart.',
              max_instances=6),
    BlockType('stats', u'Your own counters', u'\U0001F522', _n_stats,
              u'Up to four big numbers you choose - typed in, or kept up to '
              u'date automatically from your data.', max_instances=3),
    BlockType('image', u'Images', u'\U0001F5BC', _n_image,
              u'One image or a gallery.', max_instances=8,
              requires_review=True),
    BlockType('video', u'Video', u'\U0001F3AC', _n_video,
              u'A YouTube or Vimeo video.', max_instances=4,
              requires_review=True),
    BlockType('observation_map', u'Observation map', u'\U0001F4CD',
              _n_observation_map,
              u'A map with a pin for every observation in one set of app data.',
              max_instances=4),
    BlockType('terria_map', u'Interactive map', u'\U0001F5FA', _n_terria_map,
              u'A map you built in the IHP-WINS map viewer and shared with its '
              u'Share button.', max_instances=2, requires_review=True),
    BlockType('content_list', u'A list of news or events', u'\U0001F4F0',
              _n_content_list,
              u'Pick exactly which news, events or publications to list, and '
              u'how many. (The standard "News, Events & More" section already '
              u'shows the most recent ones.)', max_instances=4),
    BlockType('datasets_list', u'Datasets', u'\U0001F5C3', _n_datasets_list,
              u'A list of datasets published on IHP-WINS.', max_instances=2),
    BlockType('callout', u'Highlight', u'\U0001F4A1', _n_callout,
              u'A highlighted note with an optional button.', max_instances=6),

    # --- built-in wrappers for the standard sections -----------------------
    # Addable=False: they exist on every page from the start and are toggled
    # with `hidden`, never added twice.
    BlockType('builtin_at_a_glance', u'At a Glance', u'\U0001F4C8', _n_builtin,
              u'The four standard project counters, kept up to date for you.',
              builtin=True, addable=False),
    BlockType('builtin_region_map', u'Project region', u'\U0001F30D',
              _n_builtin, u'The region map (hidden when no region is set).',
              builtin=True, addable=False),
    BlockType('builtin_about', u'About this project', u'\U0001F4C4',
              _n_builtin,
              u'Description text from before this editor existed. It cannot be '
              u'edited here - move it into a text block to change it.',
              builtin=True, addable=False, deprecated=True),
    BlockType('builtin_data', u'Data', u'\U0001F4BE', _n_builtin,
              u'Every set of app data on this project, with its map and '
              u'download links.', builtin=True, addable=False),
    BlockType('builtin_news_events', u'News, Events & More', u'\U0001F4F0',
              _n_builtin,
              u'The six most recent items published for this project.',
              builtin=True, addable=False),
    BlockType('builtin_join', u'Join this project', u'\U0001F91D', _n_builtin,
              u'The join button, QR code and share link.',
              builtin=True, addable=False),
]

BLOCK_TYPES = dict((block_type.key, block_type) for block_type in _TYPES)

# Order matters: it drives the "Add a block" palette in the editor.
ADDABLE_TYPES = [t.key for t in _TYPES if t.addable]
BUILTIN_TYPES = [t.key for t in _TYPES if t.builtin]

# The page every project has until its manager changes it -- exactly the
# section order the landing had before it became block-driven.
DEFAULT_BLOCK_TYPES = (
    'builtin_at_a_glance',
    'builtin_region_map',
    'builtin_about',
    'builtin_data',
    'builtin_news_events',
    'builtin_join',
)


def default_blocks():
    """A fresh copy of the default page (never share mutable module state)."""
    return normalize_blocks([{'type': key} for key in DEFAULT_BLOCK_TYPES])


# --------------------------------------------------------------------------- #
# Normalization                                                               #
# --------------------------------------------------------------------------- #

def new_block_id():
    return uuid.uuid4().hex[:8]


def normalize_block(raw, report=None):
    """One untrusted dict -> one stored block, or ``None`` to drop it."""
    if not isinstance(raw, dict):
        return None
    block_type = BLOCK_TYPES.get(raw.get('type'))
    if block_type is None:
        # Unknown or removed type: drop it silently. Raising here would let one
        # stale row take down a public page.
        return None

    block_id = raw.get('id')
    if not (isinstance(block_id, str) and _ID_RE.match(block_id)):
        block_id = new_block_id()

    block = {
        'id': block_id,
        'type': block_type.key,
        'title': _plain(raw.get('title'), MAX_TITLE),
        'hidden': _bool(raw.get('hidden')),
        'width': _enum(raw.get('width'), WIDTHS, 'full'),
    }
    if report is not None:
        # Attach any drop to the id the template will actually render, so the
        # editor can open and anchor the right block.
        report.enter(block_id)
    try:
        payload = block_type.normalize(raw, report)
    except Exception:
        # A normalizer must not raise; if one ever does, degrade to an empty
        # payload rather than losing the whole page.
        payload = {}
    block.update(payload or {})
    return block


def normalize_blocks(raw_blocks, max_blocks=MAX_BLOCKS, report=None):
    """An untrusted list -> the stored block list. Total: never raises.

    Enforces the per-type ``max_instances`` by dropping the extras beyond the
    cap (in document order) rather than erroring: a page that somehow acquired
    30 chart blocks should render its first six, not refuse to save.
    """
    if not isinstance(raw_blocks, list):
        return []
    out = []
    seen = {}
    used_ids = set()
    for raw in raw_blocks:
        block = normalize_block(raw, report)
        if block is None:
            continue
        key = block['type']
        used = seen.get(key, 0)
        if used >= BLOCK_TYPES[key].max_instances:
            if report is not None:
                report.note(u'', 'too_many', key)
            continue
        seen[key] = used + 1
        # Ids round-trip through a visible hidden field, so a hand-edited POST
        # can repeat one. Duplicates would put two id="block-X" on the public
        # page and collapse every aria-labelledby onto the first of them.
        if block['id'] in used_ids:
            block['id'] = new_block_id()
        used_ids.add(block['id'])
        out.append(block)
        if len(out) >= max_blocks:
            if report is not None:
                report.note(u'', 'too_many', u'')
            break
    return out


def blocks_to_json(blocks):
    return json.dumps(blocks or [], ensure_ascii=False, separators=(',', ':'))


def blocks_from_json(raw, default=None):
    """Parse stored JSON into a normalized block list. Fail-soft.

    ``default`` is returned for anything unusable -- including a payload over
    ``MAX_JSON_BYTES``, which is checked BEFORE parsing so an oversized blob is
    never handed to ``json.loads``.
    """
    if default is None:
        default = []
    if not raw:
        return list(default)
    if isinstance(raw, (bytes, bytearray)):
        if len(raw) > MAX_JSON_BYTES:
            return list(default)
        try:
            raw = raw.decode('utf-8')
        except UnicodeDecodeError:
            return list(default)
    if isinstance(raw, str):
        if len(raw.encode('utf-8')) > MAX_JSON_BYTES:
            return list(default)
        try:
            raw = json.loads(raw)
        except ValueError:
            return list(default)
    if not isinstance(raw, list):
        return list(default)
    return normalize_blocks(raw)


def oversized(blocks):
    """True when the serialized page would exceed the storage cap."""
    return len(blocks_to_json(blocks).encode('utf-8')) > MAX_JSON_BYTES


# --------------------------------------------------------------------------- #
# HTML form <-> blocks                                                        #
# --------------------------------------------------------------------------- #

# blocks[3][title] and blocks[3][items][0][url] -- the repeatable-fieldset
# convention. Indices arrive with GAPS after a client-side delete, so they are
# only ever used for ORDERING, never as a count.
_FORM_KEY_RE = re.compile(
    r'^blocks\[(\d+)\]\[([A-Za-z0-9_]+)\](?:\[(\d+)\]\[([A-Za-z0-9_]+)\])?$')


def blocks_from_form(pairs, report=None):
    """Parse ``blocks[N][field]`` form pairs into a normalized block list.

    ``pairs`` is an iterable of ``(key, value)`` -- e.g.
    ``request.form.items(multi=True)``, so repeated keys survive. Keys that do
    not match the grammar are ignored.
    """
    by_index = {}
    for key, value in pairs or ():
        match = _FORM_KEY_RE.match(key or '')
        if match is None:
            continue
        index, field, sub_index, sub_field = match.groups()
        block = by_index.setdefault(int(index), {})
        if sub_index is None:
            block[field] = value
        else:
            items = block.setdefault(field, {})
            if not isinstance(items, dict):
                items = {}
                block[field] = items
            items.setdefault(int(sub_index), {})[sub_field] = value
    ordered = [by_index[index] for index in sorted(by_index)]
    return normalize_blocks(ordered, report=report)


# --------------------------------------------------------------------------- #
# Editor operations                                                           #
# --------------------------------------------------------------------------- #

_OP_RE = re.compile(
    r'^(save|submit|preview|convert|move_up|move_down|delete|add)(?::(.+))?$')


def choose_op(pressed, js_fallback):
    """The operation this POST asked for. The PRESSED button always wins.

    Two different form names on purpose. Carrying both on one name makes the
    answer depend on their order in the DOM -- a rule that lives in a template
    and is read in a view, i.e. one nobody can see being broken. It WAS broken:
    a hidden ``op`` rendered before the buttons made ``MultiDict.get`` return
    ``"save"`` every time, so with JavaScript disabled every button in the
    editor silently became "Draft saved" -- including "Save and publish", which
    therefore never reached a reviewer.

    ``js_fallback`` exists only because a ``disabled`` button contributes
    neither its name nor its value, so a future disable-on-submit needs
    somewhere else to put the operation.
    """
    return (pressed or js_fallback or u'').strip()


def parse_op(raw):
    """``(name, argument)`` for an editor op string; defaults to ``('save', '')``."""
    match = _OP_RE.match((raw or '').strip())
    if match is None:
        return 'save', u''
    return match.group(1), (match.group(2) or u'')[:64]


def apply_op(blocks, raw_op, convert_html=None):
    """Apply one editor operation. Returns ``(blocks, anchor_block_id)``.

    Out-of-range indices and no-op moves (up from the top, down from the
    bottom) are silently ignored, so a stale form or a double submit cannot
    corrupt the order.

    ``convert_html`` is the legacy description a ``convert`` op moves into a new
    text block; the caller supplies it because this module never touches the
    database.
    """
    blocks = list(blocks or [])
    name, argument = parse_op(raw_op)

    if name in ('save', 'submit', 'preview'):
        return blocks, None

    if name == 'add':
        block_type = BLOCK_TYPES.get(argument)
        if block_type is None or not block_type.addable:
            return blocks, None
        if sum(1 for b in blocks if b['type'] == argument) \
                >= block_type.max_instances:
            return blocks, None
        if len(blocks) >= MAX_BLOCKS:
            return blocks, None
        block = normalize_block({'type': argument})
        # Insert BEFORE the first standard section rather than appending: the
        # six built-ins already sit in the list, so appending would drop every
        # new section below "Join this project" and cost the author one full
        # page reload per position to move it up.
        at = _first_builtin_index(blocks)
        blocks.insert(at, block)
        return blocks, block['id']

    # Strict: a non-numeric argument is NOT index 0. Coercing garbage to 0
    # would make "delete:" quietly delete the first block.
    if not argument.isdigit():
        return blocks, None
    index = int(argument)
    if index >= len(blocks):
        return blocks, None

    if name == 'convert':
        # Turn the deprecated "About" wrapper into a real text block holding a
        # copy of the legacy description, and hide the original so the text is
        # not published twice. The promise of a one-click conversion was in the
        # UI long before the action existed.
        block = blocks[index]
        if block['type'] != 'builtin_about':
            return blocks, block['id']
        replacement = normalize_block({'type': 'rich_text',
                                       'html': convert_html or u''})
        if replacement is None:
            return blocks, block['id']
        block['hidden'] = True
        blocks.insert(index, replacement)
        return blocks, replacement['id']

    if name == 'delete':
        block = blocks[index]
        if BLOCK_TYPES[block['type']].builtin:
            # Built-ins are hidden, never deleted: deleting one would make the
            # standard section unrecoverable from the editor.
            return blocks, block['id']
        blocks.pop(index)
        neighbour = blocks[min(index, len(blocks) - 1)] if blocks else None
        return blocks, neighbour['id'] if neighbour else None

    # Anchor on the MOVED block (captured before the swap) so the redirect
    # scrolls back to what the user just acted on, not to its neighbour.
    moved = blocks[index]
    if name == 'move_up' and index > 0:
        blocks[index - 1], blocks[index] = blocks[index], blocks[index - 1]
    elif name == 'move_down' and index < len(blocks) - 1:
        blocks[index + 1], blocks[index] = blocks[index], blocks[index + 1]
    return blocks, moved['id']


def _first_builtin_index(blocks):
    """Where the standard sections start (end of the list when there are none)."""
    for index, block in enumerate(blocks or []):
        if BLOCK_TYPES.get(block.get('type')) is not None \
                and BLOCK_TYPES[block['type']].builtin:
            return index
    return len(blocks or [])


def ensure_builtins(blocks):
    """Append any built-in block missing from ``blocks`` (hidden by default).

    A page saved before a new built-in existed must still be able to show it,
    and a PM who somehow lost one should be able to switch it back on.
    """
    blocks = list(blocks or [])
    present = {block['type'] for block in blocks}
    for key in DEFAULT_BLOCK_TYPES:
        if key not in present:
            block = normalize_block({'type': key, 'hidden': True})
            if block is not None:
                blocks.append(block)
    return blocks


# --------------------------------------------------------------------------- #
# Review policy                                                               #
# --------------------------------------------------------------------------- #

def blocks_requiring_review(blocks):
    """The distinct block types on this page that embed external content."""
    return sorted({block['type'] for block in blocks or []
                   if not block.get('hidden')
                   and block.get('type') in BLOCK_TYPES
                   and BLOCK_TYPES[block['type']].requires_review})


def page_initial_status(is_sysadmin, trusted, blocks):
    """``'approved'`` (publish now) or ``'pending'`` (queue for review).

    Mirrors ``content.content_initial_status`` and keeps its most important
    nuance: trust is NOT a blanket bypass. That function deliberately excludes
    publications and maps from a trusted project's auto-publish because they
    carry external links; a page can hold images, video and Terria embeds --
    exactly the same surface -- so a trusted page auto-publishes only while it
    embeds nothing from an origin we do not control.
    """
    if is_sysadmin:
        return 'approved'
    if trusted and not blocks_requiring_review(blocks):
        return 'approved'
    return 'pending'
