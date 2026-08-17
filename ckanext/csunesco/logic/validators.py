# encoding: utf-8
"""Validators for ckanext-csunesco.

Increment 3: field-level validators shared by the CS project-request schema
(``logic/schema.py``) and future content schemas. Registered with CKAN through
the plugin's ``IValidators`` (``get_validators``).

``ckan`` is imported at module top -- that is safe for byte-compilation, which
never executes the module -- and each validator stays small and side-effect
light. Validators raise ``tk.Invalid`` on bad input (the navl contract) and
otherwise return the (possibly normalized) value.
"""
import datetime
import json
import re
from urllib.parse import urlparse

import ckan.plugins.toolkit as tk

from ckanext.csunesco import constants

# Content types accepted for cs_content rows (news, events, publications and
# Terria maps; media is a future increment and intentionally not accepted yet).
CONTENT_TYPES = {'cs-news', 'cs-event', 'cs-publication', 'cs-map'}

# Config option holding the space-separated allowlist of Terria base URLs a
# cs-map may embed. Unset means maps are disabled (fail closed).
TERRIA_BASE_URL_OPTION = 'ckanext.csunesco.terria_base_url'

# slug: lowercase alphanumerics joined by single hyphens (no leading/trailing/
# doubled hyphens).
_SLUG_RE = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')

# Hard cap on the raw GeoJSON payload (bytes). Checked BEFORE parsing as a cheap
# DoS guard so we never hand an unbounded string to ``json.loads``.
MAX_GEOJSON_BYTES = 1_000_000

# GeoJSON object types accepted for a project's region.
_ALLOWED_GEOJSON_TYPES = {
    'FeatureCollection', 'Feature', 'Polygon', 'MultiPolygon',
}

# Parent group whose active children define the set of valid member states.
MEMBER_STATES_GROUP = constants.MEMBER_STATES_GROUP


def _initiative_names():
    return {initiative['name'] for initiative in constants.CS_INITIATIVES}


def csunesco_valid_initiative(value):
    """Accept a known Citizen Science initiative name (hyphen-tolerant).

    The CS Toolbox app historically used hyphenated slugs (``river-watch``,
    ``island-watch``) for groups whose canonical CKAN names have none
    (``riverwatch``, ``islandwatch``); the outbox retries those payloads
    verbatim, so aliases must NORMALIZE to the canonical name here rather
    than bounce forever. The returned (stored) value is always canonical.
    """
    if value in (None, ''):
        return value
    names = _initiative_names()
    candidate = str(value).strip().lower()
    if candidate in names:
        return candidate
    dehyphenated = {name.replace('-', ''): name for name in names}
    alias = dehyphenated.get(candidate.replace('-', ''))
    if alias is not None:
        return alias
    raise tk.Invalid(
        tk._('Unknown Citizen Science initiative: %s') % value)


def csunesco_valid_slug(value):
    """Accept only URL-safe slugs (``^[a-z0-9]+(?:-[a-z0-9]+)*$``)."""
    if value in (None, ''):
        return value
    if not _SLUG_RE.match(value):
        raise tk.Invalid(tk._(
            'Invalid slug: use lowercase letters, numbers and single hyphens'))
    return value


def csunesco_valid_geojson(value):
    """Validate a region GeoJSON string (size guard first, then structure)."""
    if value in (None, ''):
        return value
    raw = value if isinstance(value, str) else str(value)
    # DoS guard FIRST: reject oversized payloads before attempting to parse.
    if len(raw.encode('utf-8')) > MAX_GEOJSON_BYTES:
        raise tk.Invalid(tk._('GeoJSON region payload is too large'))
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        raise tk.Invalid(tk._('Region is not valid JSON'))
    if (not isinstance(parsed, dict)
            or parsed.get('type') not in _ALLOWED_GEOJSON_TYPES):
        raise tk.Invalid(tk._(
            'Region must be a GeoJSON FeatureCollection, Feature, Polygon or '
            'MultiPolygon'))
    return raw


def _coerce_country_list(value):
    """Coerce a JSON-array string OR a Python list into a list of names."""
    if value in (None, ''):
        return []
    if isinstance(value, (list, tuple)):
        return [str(country).strip() for country in value if str(country).strip()]
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        raise tk.Invalid(tk._('Countries must be a JSON list'))
    if not isinstance(parsed, list):
        raise tk.Invalid(tk._('Countries must be a JSON list'))
    return [str(country).strip() for country in parsed if str(country).strip()]


def _member_state_names(model):
    """Return the set of valid member-state group names in ONE query.

    Member states are the active child groups of the ``member-states`` parent
    group (water-family pattern). Returns an empty set when the parent group is
    missing so an un-seeded deployment fails closed rather than accepting any
    value.
    """
    parent = model.Group.get(MEMBER_STATES_GROUP)
    if parent is None:
        return set()
    rows = (
        model.Session.query(model.Group.name)
        .join(model.Member, model.Member.table_id == model.Group.id)
        .filter(model.Member.group_id == parent.id)
        .filter(model.Member.table_name == 'group')
        .filter(model.Member.state == 'active')
        .filter(model.Group.state == 'active')
        .all()
    )
    return {name for (name,) in rows}


def csunesco_valid_country_list(value, context):
    """Validate that every country is a known member state; normalize to JSON.

    Accepts either a JSON-array string or a list. Fetches the valid member-state
    names in a single query and checks membership in memory. Returns a JSON
    string ready to store in the ``cs_project.countries`` text column.

    A value the project ALREADY holds is always accepted, via
    ``context['csunesco_existing_countries']`` (set by
    ``csunesco_project_update``). Keeping a country you declared earlier is not
    the same act as adding a new one, and treating it as one made the edit form
    unsavable whenever the member-state list was unreachable or a state had
    since been de-published: the picker re-offered the stored value, the
    validator then rejected it, and you could not even fix a typo in the title
    until someone repaired the group. New values are still checked.
    """
    countries = _coerce_country_list(value)
    if not countries:
        return json.dumps([])
    model = context.get('model')
    valid = _member_state_names(model) if model is not None else set()
    already = set(context.get('csunesco_existing_countries') or ())
    for country in countries:
        if country not in valid and country not in already:
            raise tk.Invalid(tk._('Unknown member state: %s') % country)
    return json.dumps(countries)


# Bounds for user-supplied option lists (multi-selects with an "add your own"
# escape hatch). Generous for real use, tight enough to stop payload abuse.
MAX_LIST_ITEMS = 50
MAX_LIST_ITEM_LENGTH = 200

_TAG_RE = re.compile(r'<[^>]*>')


def csunesco_valid_string_list(value):
    """Coerce a list / JSON-array string / comma-separated string into a
    clean list of plain-text items (tags stripped, trimmed, bounded).

    The shared shape of every phase-1 multi-value field. Returns a Python
    LIST -- these fields live inside the ``extras`` JSON blob, which
    ``json.dumps`` serializes as a whole, unlike ``countries`` (a standalone
    text column that stores its own JSON string).
    """
    if value in (None, ''):
        return []
    if isinstance(value, (list, tuple)):
        items = list(value)
    else:
        text = str(value)
        try:
            parsed = json.loads(text)
            items = parsed if isinstance(parsed, list) else [text]
        except (ValueError, TypeError):
            items = text.split(',')
    cleaned = []
    for item in items:
        text = _TAG_RE.sub('', str(item)).strip()
        if not text:
            continue
        if len(text) > MAX_LIST_ITEM_LENGTH:
            raise tk.Invalid(tk._('List entries must be %d characters or '
                                  'fewer') % MAX_LIST_ITEM_LENGTH)
        if text not in cleaned:
            cleaned.append(text)
    if len(cleaned) > MAX_LIST_ITEMS:
        raise tk.Invalid(tk._('Too many entries (maximum %d)')
                         % MAX_LIST_ITEMS)
    return cleaned


def csunesco_choice(options, allow_other=False):
    """Factory: a single value drawn from ``options`` (blank passes through).

    ``allow_other=True`` accepts any bounded free string -- the spec's "Other
    (option to add additional list item)" escape hatch.
    """
    allowed = set(options)

    def validator(value):
        if value in (None, ''):
            return value
        text = _TAG_RE.sub('', str(value)).strip()
        if text in allowed:
            return text
        if allow_other and 0 < len(text) <= MAX_LIST_ITEM_LENGTH:
            return text
        raise tk.Invalid(tk._('Unknown option: %s') % text)
    return validator


def csunesco_choice_list(options, allow_other=False):
    """Factory: run AFTER ``csunesco_valid_string_list`` -- each entry must be
    a known option (or, with ``allow_other``, any bounded free string)."""
    allowed = set(options)

    def validator(value):
        items = value if isinstance(value, list) else \
            csunesco_valid_string_list(value)
        for item in items:
            if item in allowed:
                continue
            if allow_other:
                continue
            raise tk.Invalid(tk._('Unknown option: %s') % item)
        return items
    return validator


def csunesco_require_list(min_items=1, max_items=None):
    """Factory: STRICT-form rule -- the list must carry at least ``min_items``
    (and at most ``max_items``) entries. Not used by the lenient action
    schema, which must keep accepting the app's fixed payload."""
    def validator(value):
        items = value if isinstance(value, list) else \
            csunesco_valid_string_list(value)
        if len(items) < min_items:
            raise tk.Invalid(
                tk._('Select at least %d') % min_items if min_items > 1
                else tk._('Missing value'))
        if max_items is not None and len(items) > max_items:
            raise tk.Invalid(tk._('Select at most %d') % max_items)
        return items
    return validator


def _valid_float(value, low, high, label):
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        raise tk.Invalid(tk._('%s must be a number') % label)
    if not (low <= number <= high):
        raise tk.Invalid(tk._('%s must be between %s and %s')
                         % (label, low, high))
    return number


def csunesco_valid_latitude(value):
    if value in (None, ''):
        return None
    return _valid_float(value, -90, 90, tk._('Latitude'))


def csunesco_valid_longitude(value):
    if value in (None, ''):
        return None
    return _valid_float(value, -180, 180, tk._('Longitude'))


def csunesco_valid_radius_km(value):
    if value in (None, ''):
        return None
    return _valid_float(value, 0.01, 5000, tk._('Radius'))


def csunesco_valid_document_url(value):
    """Allow only ``http``/``https`` document URLs (no javascript:, data:, ...)."""
    if value in (None, ''):
        return value
    parsed = urlparse(value)
    if parsed.scheme not in ('http', 'https'):
        raise tk.Invalid(tk._('Document URL must use http or https'))
    return value


# Charset allowed in an image URL/path. Deliberately excludes quotes,
# parentheses, backslashes and whitespace so a stored value can never break
# out of a CSS ``url('...')`` or an HTML attribute, wherever templates
# interpolate it (Jinja's HTML-escaping does not protect the CSS context).
_IMAGE_URL_SAFE_RE = re.compile(r'^[A-Za-z0-9/_:.,~%&=?#+-]*$')


def csunesco_valid_image_url(value):
    """Accept an https image URL or an internal ``/path`` (charset-restricted).

    Plain http is rejected -- it would be mixed content on the https portal.
    Internal paths (bundled assets like ``/csunesco/images/...``) must start
    with a single ``/`` (``//host`` is scheme-relative and refused).
    """
    if value in (None, ''):
        return value
    url = str(value).strip()
    if not _IMAGE_URL_SAFE_RE.match(url):
        raise tk.Invalid(tk._('Image URL contains unsupported characters'))
    if url.startswith('/'):
        if url.startswith('//'):
            raise tk.Invalid(tk._(
                'Image URL must be https or an internal path'))
        return url
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.netloc:
        raise tk.Invalid(tk._(
            'Image URL must be https or an internal path'))
    return url


# ---------------------------------------------------------------------------
# Content validators (Increment 5 -- news / events)
# ---------------------------------------------------------------------------


def csunesco_valid_content_type(value):
    """Accept only the known content types (news / event / publication / map)."""
    if value not in CONTENT_TYPES:
        raise tk.Invalid(tk._('Unknown content type: %s') % value)
    return value


# 'logged-in' is the spec's middle tier (section 5): visible to any
# authenticated portal user, hidden from anonymous visitors.
CONTENT_VISIBILITIES = ('public', 'logged-in', 'private')


def csunesco_valid_visibility(value):
    """Accept 'public' / 'logged-in' / 'private'; missing defaults to public."""
    if not value:
        return 'public'
    value = str(value).strip().lower()
    if value not in CONTENT_VISIBILITIES:
        raise tk.Invalid(tk._(
            'Visibility must be public, logged-in or private'))
    return value


def terria_allowed_bases():
    """The configured Terria base URLs (normalized, no trailing slash)."""
    raw = tk.config.get(TERRIA_BASE_URL_OPTION) or ''
    return [base.rstrip('/') for base in raw.split() if base.strip()]


def csunesco_valid_terria_url(value):
    """Accept only share/scene URLs under a configured Terria base.

    The prefix check requires the character after the base to be ``/``, ``#`` or
    ``?`` (or an exact match) so ``https://terria.example.evil.com`` can never
    satisfy a ``https://terria.example`` base. Unset config fails closed.
    """
    if value in (None, ''):
        return value
    url = str(value).strip()
    bases = terria_allowed_bases()
    if not bases:
        raise tk.Invalid(tk._('Terria maps are not enabled on this portal'))
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        raise tk.Invalid(tk._('Map URL must use http or https'))
    for base in bases:
        if url == base or url.startswith((base + '/', base + '#', base + '?')):
            return url
    raise tk.Invalid(tk._(
        'Map URL must start with one of the allowed Terria addresses: %s'
    ) % ', '.join(bases))


def _parse_iso_datetime(value):
    """Parse an ISO ``YYYY-MM-DD`` date or ``YYYY-MM-DDTHH:MM[:SS]`` datetime.

    Returns a naive ``datetime``. Raises ``ValueError`` on anything else -- the
    ``Z`` / timezone-offset forms are intentionally NOT accepted (kept simple and
    consistent with the DateTime columns, which store naive UTC).
    """
    text = str(value).strip()
    try:
        return datetime.datetime.fromisoformat(text)
    except ValueError:
        pass
    parsed_date = datetime.date.fromisoformat(text)  # raises ValueError if bad
    return datetime.datetime(parsed_date.year, parsed_date.month,
                             parsed_date.day)


def csunesco_valid_iso_date(value):
    """Validate an ISO date/datetime, returning a naive ``datetime`` (or None).

    Empty input normalizes to ``None`` so an unset optional date stores NULL
    rather than an empty string (which the DateTime column would reject).
    """
    if value in (None, ''):
        return None
    if isinstance(value, datetime.datetime):
        return value
    try:
        return _parse_iso_datetime(value)
    except (ValueError, TypeError):
        raise tk.Invalid(tk._(
            'Date must be in ISO format (e.g. 2026-07-16 or '
            '2026-07-16T09:30)'))


def _end_after(key, data, errors, start_field, allow_equal):
    """Shared body: flag ``end`` that does not follow ``start_field``.

    No-op when either side is missing -- a required ``end_date`` is
    ``not_empty``'s business, not this validator's.

    BOTH sides are coerced here rather than trusting that the per-key date
    validators already ran. navl walks the flattened keys in sorted order, so
    ``end_date`` is always validated BEFORE ``publish_date`` or ``start_date``:
    by the time this runs ``end`` is a ``datetime`` while the start field is
    still the raw string off the form, and comparing those two raises
    ``TypeError: '<' not supported between instances of 'datetime.datetime'
    and 'str'`` -- a 500, not a field error.
    """
    end = data.get(key)
    start = data.get((start_field,))
    if not end or not start:
        return
    try:
        end = csunesco_valid_iso_date(end)
        start = csunesco_valid_iso_date(start)
    except tk.Invalid:
        # An unparseable start date is the start field's own error to report;
        # adding a second, confusing message here would help nobody.
        return
    if not end or not start:
        return
    if (end < start) if allow_equal else (end <= start):
        errors[key].append(tk._('End date must be after the start date'))


def csunesco_end_after_start(key, data, errors, context):
    """An event's ``end_date`` must be strictly later than ``publish_date``."""
    _end_after(key, data, errors, 'publish_date', allow_equal=False)


def csunesco_end_after(start_field, allow_equal=False):
    """Build an "end follows start" validator for an arbitrary start field.

    A content event pairs ``publish_date``/``end_date``; a project pairs
    ``start_date``/``end_date``. Same rule, different neighbour -- so the field
    name is a parameter instead of a second copy of the comparison.

    ``allow_equal`` exists because the two cases differ genuinely: an event
    that ends the instant it starts is a data-entry mistake, but a project that
    runs for a single day legitimately has ``start_date == end_date``.
    """
    def validator(key, data, errors, context):
        _end_after(key, data, errors, start_field, allow_equal)
    return validator


def csunesco_valid_media_list(value):
    """Validate a media value: a JSON list (or Python list) of http/https URLs.

    Accepts a JSON-array string OR a list; every non-empty item must be an
    ``http``/``https`` URL. Returns a JSON string ready to store in the
    ``cs_content.media`` text column. NO file uploads are handled here -- media is
    URLs only in this increment (file upload is a deliberate future increment so
    we do not open the upload security surface now).
    """
    if value in (None, ''):
        return json.dumps([])
    if isinstance(value, (list, tuple)):
        items = list(value)
    else:
        try:
            items = json.loads(value)
        except (ValueError, TypeError):
            raise tk.Invalid(tk._('Media must be a JSON list of URLs'))
    if not isinstance(items, list):
        raise tk.Invalid(tk._('Media must be a JSON list of URLs'))
    urls = []
    for item in items:
        url = str(item).strip()
        if not url:
            continue
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https'):
            raise tk.Invalid(tk._('Media URLs must use http or https'))
        urls.append(url)
    return json.dumps(urls)


def csunesco_nonempty_media_list(value):
    """Run AFTER ``csunesco_valid_media_list``: require at least one URL.

    Needed because a ``'[]'`` JSON string is truthy and slips past ``not_empty``;
    publications must carry at least one document link.
    """
    try:
        items = json.loads(value) if isinstance(value, str) else (value or [])
    except (ValueError, TypeError):
        items = []
    if not items:
        raise tk.Invalid(tk._('At least one document link is required'))
    return value


def get_validators():
    return {
        'csunesco_valid_initiative': csunesco_valid_initiative,
        'csunesco_valid_string_list': csunesco_valid_string_list,
        'csunesco_valid_latitude': csunesco_valid_latitude,
        'csunesco_valid_longitude': csunesco_valid_longitude,
        'csunesco_valid_radius_km': csunesco_valid_radius_km,
        'csunesco_valid_slug': csunesco_valid_slug,
        'csunesco_valid_geojson': csunesco_valid_geojson,
        'csunesco_valid_country_list': csunesco_valid_country_list,
        'csunesco_valid_document_url': csunesco_valid_document_url,
        'csunesco_valid_image_url': csunesco_valid_image_url,
        'csunesco_valid_content_type': csunesco_valid_content_type,
        'csunesco_valid_visibility': csunesco_valid_visibility,
        'csunesco_valid_terria_url': csunesco_valid_terria_url,
        'csunesco_valid_iso_date': csunesco_valid_iso_date,
        'csunesco_end_after_start': csunesco_end_after_start,
        'csunesco_valid_media_list': csunesco_valid_media_list,
        'csunesco_nonempty_media_list': csunesco_nonempty_media_list,
    }
