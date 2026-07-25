# encoding: utf-8
"""Pure aggregation over CS Toolbox observation rows (NO CKAN import).

This module turns the app's ``dashboard-data`` payload -- ``{schema, total,
truncated, rows: [{id, date, lat, lng, source, answers{}}]}`` -- into the small,
display-ready series the chart blocks consume. It exists because the raw payload
is big: form 3 on the dev portal is **1605 rows / 1.63 MB**, which must never be
shipped to a browser just to draw a line. Aggregating here takes it to ~3 KB.

Two design rules make the client trivial:

* **Dense, aligned arrays.** ``labels`` is a complete, gap-free list of period
  keys; every series has exactly ``len(labels)`` points with ``None`` where no
  observation exists. A gap has to be *visible* as a hole, not silently closed
  by collapsing the axis.
* **Sortable, locale-free bucket keys** (``2026-07-20`` / ``2026-W30`` /
  ``2026-07`` / ``2026``). Chart.js then needs no date adapter at all -- the x
  axis is a plain category axis.

The detection/aggregation heuristics are a Python port of the already
unit-tested ``aggregate.ts`` in the CS Toolbox frontend, so both surfaces group
observations the same way. Everything here is pure and deterministic: no CKAN,
no I/O, no clock (callers pass ``now``), so it is testable without a container.
"""
import datetime
import re

# --------------------------------------------------------------------------- #
# Tunables (mirrored from the CS Toolbox frontend so both agree on grouping)   #
# --------------------------------------------------------------------------- #

# Column names that most likely identify a monitoring site, in priority order.
SITE_CANDIDATES = (
    'site', 'site_name', 'sitio', 'nombre_sitio', 'river', 'rio', 'station',
)

# A categorical field is only useful as a FACET inside this cardinality band:
# 1 value says nothing, 30+ is a free-text column masquerading as a category
# (and would render as an unusable wall of chips).
MIN_FACET = 2
MAX_FACET = 30

# Site detection is deliberately more permissive than faceting. The frontend
# dashboard reuses its 30-value facet ceiling here, but that ceiling exists to
# keep a chip row readable -- for SERIES it is the wrong test, because the
# series cap (8, largest first) already bounds both the payload and the legend.
# Applying the facet ceiling meant a real form with 30+ monitoring sites folded
# every site into ONE flat line, which is precisely the chart nobody wants.
MAX_SITE_VALUES = 500

# Schema field types treated as categorical for grouping / counting.
CATEGORICAL_TYPES = frozenset(
    ('short_text', 'single_select', 'radio', 'checkbox'))

# Types whose values are text we can group by (used for site detection).
_TEXTISH_TYPES = frozenset(('short_text', 'single_select'))

# Granularities, coarsest last. 'year' is an OVERFLOW step only -- it is never
# offered as a user choice, it just keeps `labels` bounded for absurd spans.
GRANULARITIES = ('day', 'week', 'month', 'year')

# Hard ceiling on the x axis. Beyond this a line chart is unreadable anyway and
# the payload stops being small, which is the whole point of this module.
MAX_LABELS = 400

# Series are assigned palette slots by index and the palette never cycles, so
# the cap here must match the palette length on the JS side.
MAX_SERIES = 8

# Categories beyond this fold into a single "Other" bucket.
MAX_CATEGORIES = 12

AGGREGATIONS = ('mean', 'min', 'max', 'sum', 'count')


# --------------------------------------------------------------------------- #
# Value coercion                                                              #
# --------------------------------------------------------------------------- #

def to_number(value):
    """Return ``value`` as a float, or ``None`` when it is not numeric.

    Booleans are rejected on purpose: ``True`` is not a measurement, and Python
    would happily treat it as ``1.0``.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if _is_finite(value) else None
    if isinstance(value, str) and value.strip():
        try:
            number = float(value.strip())
        except ValueError:
            return None
        return number if _is_finite(number) else None
    return None


def _is_finite(number):
    return number == number and number not in (float('inf'), float('-inf'))


def category_value(value):
    """Categorical key of a raw answer, or ``None`` when it is not one.

    Shared by the facet detection, the counting and the site grouping so they
    can never disagree about what counts as a category.
    """
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, str) and value.strip():
        return value
    return None


# --------------------------------------------------------------------------- #
# Dates                                                                       #
# --------------------------------------------------------------------------- #

_DATE_PREFIX_RE = re.compile(r'^(\d{4})-(\d{2})-(\d{2})')


def parse_iso_day(value):
    """The calendar day of an ISO-8601 timestamp, or ``None``.

    Deliberately tolerant -- it only reads the ``YYYY-MM-DD`` prefix, so the
    time part, a ``Z`` suffix and any offset are all irrelevant. Observation
    timestamps come from several importers and we only ever bucket by day.
    """
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.date() if isinstance(value, datetime.datetime) else value
    if not isinstance(value, str):
        return None
    match = _DATE_PREFIX_RE.match(value.strip())
    if match is None:
        return None
    try:
        return datetime.date(int(match.group(1)), int(match.group(2)),
                             int(match.group(3)))
    except ValueError:
        return None


def row_day(row):
    """The calendar day of an observation row, or ``None``."""
    if not isinstance(row, dict):
        return None
    return parse_iso_day(row.get('date'))


def date_span(rows):
    """``(first_day, last_day)`` across ``rows``; ``(None, None)`` when empty."""
    first = last = None
    for row in rows or []:
        day = row_day(row)
        if day is None:
            continue
        if first is None or day < first:
            first = day
        if last is None or day > last:
            last = day
    return first, last


def filter_rows_by_date(rows, start=None, end=None):
    """Rows whose day falls in ``[start, end]`` (both inclusive, both optional).

    Rows with an unparseable date are dropped as soon as ANY bound is given --
    a row we cannot place in time must not silently survive a time filter.
    """
    if start is None and end is None:
        return list(rows or [])
    kept = []
    for row in rows or []:
        day = row_day(row)
        if day is None:
            continue
        if start is not None and day < start:
            continue
        if end is not None and day > end:
            continue
        kept.append(row)
    return kept


def preset_start(preset, now):
    """Start day for a range preset relative to ``now``; ``None`` = no filter.

    ``now`` is always passed in: a module that reads the clock cannot be tested
    deterministically.
    """
    days = {'7d': 7, '30d': 30, '90d': 90, '180d': 180, '1y': 365, '2y': 730}
    if preset in (None, '', 'all'):
        return None
    if preset not in days:
        return None
    if isinstance(now, datetime.datetime):
        now = now.date()
    return now - datetime.timedelta(days=days[preset])


# --------------------------------------------------------------------------- #
# Bucketing                                                                   #
# --------------------------------------------------------------------------- #

def bucket_key(day, granularity):
    """Sortable, locale-free period key for ``day``.

    ``2026-07-20`` (day) / ``2026-W30`` (ISO week) / ``2026-07`` (month) /
    ``2026`` (year). Lexicographic order equals chronological order for each
    granularity, which is why the labels never need a date parser downstream.
    """
    if day is None:
        return None
    if granularity == 'day':
        return day.isoformat()
    if granularity == 'week':
        iso_year, iso_week = day.isocalendar()[0], day.isocalendar()[1]
        return '%04d-W%02d' % (iso_year, iso_week)
    if granularity == 'month':
        return '%04d-%02d' % (day.year, day.month)
    if granularity == 'year':
        return '%04d' % day.year
    raise ValueError('unknown granularity %r' % (granularity,))


def _estimate_labels(first, last, granularity):
    """How many dense labels ``[first, last]`` would produce at ``granularity``."""
    if first is None or last is None:
        return 0
    span = (last - first).days
    if granularity == 'day':
        return span + 1
    if granularity == 'week':
        return span // 7 + 2
    if granularity == 'month':
        return (last.year - first.year) * 12 + (last.month - first.month) + 1
    if granularity == 'year':
        return last.year - first.year + 1
    raise ValueError('unknown granularity %r' % (granularity,))


def estimate_labels(first, last, granularity):
    """Public alias: how many labels a granularity would produce over a span.

    Callers use it to reject an author-chosen granularity that would blow past
    the axis ceiling before committing to it.
    """
    return _estimate_labels(first, last, granularity)


def choose_bucket(first, last, max_labels=MAX_LABELS):
    """Pick the finest granularity that keeps the axis readable and bounded.

    Day up to ~3 months, week up to ~2 years, month beyond that -- then coarsen
    further while the dense label count would still exceed ``max_labels``.
    """
    if first is None or last is None:
        return 'day'
    span = (last - first).days
    if span <= 90:
        granularity = 'day'
    elif span <= 730:
        granularity = 'week'
    else:
        granularity = 'month'
    index = GRANULARITIES.index(granularity)
    while (index < len(GRANULARITIES) - 1
           and _estimate_labels(first, last, GRANULARITIES[index]) > max_labels):
        index += 1
    return GRANULARITIES[index]


def _next_period(day, granularity):
    if granularity == 'day':
        return day + datetime.timedelta(days=1)
    if granularity == 'week':
        return day + datetime.timedelta(days=7)
    if granularity == 'month':
        if day.month == 12:
            return datetime.date(day.year + 1, 1, 1)
        return datetime.date(day.year, day.month + 1, 1)
    if granularity == 'year':
        return datetime.date(day.year + 1, 1, 1)
    raise ValueError('unknown granularity %r' % (granularity,))


def _period_start(day, granularity):
    if granularity == 'day':
        return day
    if granularity == 'week':
        return day - datetime.timedelta(days=day.isocalendar()[2] - 1)
    if granularity == 'month':
        return datetime.date(day.year, day.month, 1)
    if granularity == 'year':
        return datetime.date(day.year, 1, 1)
    raise ValueError('unknown granularity %r' % (granularity,))


def bucket_labels(first, last, granularity, max_labels=MAX_LABELS):
    """Every period key from ``first`` to ``last`` inclusive -- **dense**.

    Periods with no observation still get a label (and later a ``None`` point):
    a month where nobody sampled is information, and dropping it would draw a
    straight line across the gap as if the value had been stable.
    """
    if first is None or last is None:
        return []
    if last < first:
        first, last = last, first
    labels = []
    cursor = _period_start(first, granularity)
    end = _period_start(last, granularity)
    while cursor <= end:
        labels.append(bucket_key(cursor, granularity))
        cursor = _next_period(cursor, granularity)
        # Defensive: an absurd span at a fine granularity must not spin here.
        if len(labels) > max_labels:
            break
    return labels


# --------------------------------------------------------------------------- #
# Schema introspection                                                        #
# --------------------------------------------------------------------------- #

def schema_fields(schema):
    """The list of field definitions in a dashboard-data ``schema`` block."""
    if isinstance(schema, dict):
        fields = schema.get('fields')
        if isinstance(fields, list):
            return [f for f in fields if isinstance(f, dict) and f.get('name')]
    return []


def field_label(schema, name):
    """Human label of a schema field, falling back to its machine name."""
    for field in schema_fields(schema):
        if field.get('name') == name:
            return field.get('label') or name
    return name


def _distinct_values(rows, field, ceiling=MAX_FACET + 1):
    """Distinct categorical values of ``field``, stopping early past ``ceiling``."""
    values = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        value = category_value((row.get('answers') or {}).get(field))
        if value is not None:
            values.add(value)
        if len(values) > ceiling:
            break
    return values


def detect_site_field(schema, rows):
    """The field to split series by, or ``None`` for a single series.

    Bounded by :data:`MAX_SITE_VALUES`, not by the facet ceiling: the series cap
    already limits how many lines are drawn, so a form with 40 monitoring sites
    should chart its 8 busiest, not collapse into one line.
    """
    textish = {f['name'] for f in schema_fields(schema)
               if f.get('type') in _TEXTISH_TYPES}
    for candidate in SITE_CANDIDATES:
        if candidate in textish:
            count = len(_distinct_values(rows, candidate,
                                         ceiling=MAX_SITE_VALUES))
            if 1 <= count <= MAX_SITE_VALUES:
                return candidate
    return None


def numeric_fields_with_data(schema, rows):
    """Numeric schema fields that actually carry at least one numeric value."""
    out = []
    for field in schema_fields(schema):
        if field.get('type') != 'number':
            continue
        name = field['name']
        hits = sum(1 for row in rows or []
                   if isinstance(row, dict)
                   and to_number((row.get('answers') or {}).get(name)) is not None)
        if hits:
            out.append({'name': name,
                        'label': field.get('label') or name,
                        'unit': field.get('unit'),
                        'rows': hits})
    return out


def categorical_field_options(schema, rows, site_field=None):
    """Categorical fields usable for counting / colouring (2..30 distinct)."""
    out = []
    for field in schema_fields(schema):
        if field.get('type') not in CATEGORICAL_TYPES:
            continue
        name = field['name']
        if name == site_field:
            continue
        count = len(_distinct_values(rows, name))
        if MIN_FACET <= count <= MAX_FACET:
            out.append({'name': name,
                        'label': field.get('label') or name,
                        'options': len(field.get('options') or []),
                        'distinct': count})
    return out


def value_counts(rows, field):
    """``[(value, count)]`` for ``field``, most frequent first (ties by value)."""
    counts = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        value = category_value((row.get('answers') or {}).get(field))
        if value is not None:
            counts[value] = counts.get(value, 0) + 1
    return sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))


# --------------------------------------------------------------------------- #
# Robust axis range                                                           #
# --------------------------------------------------------------------------- #

def robust_range(values):
    """A Tukey-fence y range, or ``{}`` when the data needs no clamping.

    A handful of bad readings (a pH of 800 where the scale is 0-14) otherwise
    blow the auto-scale out and flatten the real signal into a flat line at the
    bottom. Only returns a bound when the data actually crosses the fence, so
    clean data is always left to auto-scale. Non-negative data never clamps
    below zero.
    """
    numbers = sorted(v for v in (to_number(v) for v in values or [])
                     if v is not None)
    if len(numbers) < 8:
        return {}

    def quantile(p):
        index = (len(numbers) - 1) * p
        low = int(index)
        high = low if index == low else low + 1
        return numbers[low] + (numbers[high] - numbers[low]) * (index - low)

    q1 = quantile(0.25)
    q3 = quantile(0.75)
    iqr = q3 - q1
    if iqr <= 0:
        return {}
    fence_high = q3 + 1.5 * iqr
    fence_low = q1 - 1.5 * iqr
    data_min, data_max = numbers[0], numbers[-1]
    out = {}
    if fence_high < data_max:
        out['max'] = _ceil(fence_high)
    if fence_low > data_min:
        out['min'] = max(0.0, _floor(fence_low)) if data_min >= 0 \
            else _floor(fence_low)
    return out


def _ceil(number):
    whole = int(number)
    return float(whole if whole == number or number < 0 else whole + 1)


def _floor(number):
    whole = int(number)
    return float(whole if whole == number or number > 0 else whole - 1)


# --------------------------------------------------------------------------- #
# Aggregations                                                                #
# --------------------------------------------------------------------------- #

def _reduce(bucket, agg):
    """Collapse one accumulator to a single value for ``agg``."""
    if not bucket['count']:
        return None
    if agg == 'count':
        return float(bucket['count'])
    if agg == 'sum':
        return bucket['sum']
    if agg == 'min':
        return bucket['min']
    if agg == 'max':
        return bucket['max']
    return bucket['sum'] / bucket['count']


def aggregate_numeric(rows, field, granularity, labels, site_field=None,
                      agg='mean', max_series=MAX_SERIES):
    """One dense series per site for a numeric ``field``.

    Series are ordered by sample count descending and capped at ``max_series``
    so palette slots are handed out to the biggest entities first and never
    cycle. Returns ``{'series': [...], 'used_rows': int, 'values': [...]}``
    where ``values`` are the raw numbers (for :func:`robust_range`).
    """
    if agg not in AGGREGATIONS:
        agg = 'mean'
    label_index = {label: i for i, label in enumerate(labels)}
    per_site = {}
    raw_values = []
    used = 0

    for row in rows or []:
        if not isinstance(row, dict):
            continue
        answers = row.get('answers') or {}
        value = to_number(answers.get(field))
        if value is None:
            continue
        key = bucket_key(row_day(row), granularity)
        if key is None or key not in label_index:
            continue
        name = ''
        if site_field:
            name = category_value(answers.get(site_field)) or ''
        buckets = per_site.setdefault(name, {})
        bucket = buckets.get(key)
        if bucket is None:
            bucket = {'sum': 0.0, 'count': 0, 'min': value, 'max': value}
            buckets[key] = bucket
        bucket['sum'] += value
        bucket['count'] += 1
        bucket['min'] = min(bucket['min'], value)
        bucket['max'] = max(bucket['max'], value)
        raw_values.append(value)
        used += 1

    ordered = sorted(
        per_site.items(),
        key=lambda item: (-sum(b['count'] for b in item[1].values()), item[0]))

    series = []
    for name, buckets in ordered[:max_series]:
        points = [None] * len(labels)
        counts = [0] * len(labels)
        for key, bucket in buckets.items():
            index = label_index[key]
            points[index] = _reduce(bucket, agg)
            counts[index] = bucket['count']
        series.append({'name': name, 'points': points, 'counts': counts})

    return {'series': series, 'used_rows': used, 'values': raw_values}


def aggregate_counts(rows, granularity, labels, series_name='observations'):
    """A single dense series counting observations per period."""
    label_index = {label: i for i, label in enumerate(labels)}
    counts = [0] * len(labels)
    used = 0
    for row in rows or []:
        key = bucket_key(row_day(row), granularity)
        if key is None or key not in label_index:
            continue
        counts[label_index[key]] += 1
        used += 1
    points = [float(c) for c in counts]
    return {'series': [{'name': series_name, 'points': points,
                        'counts': list(counts)}],
            'used_rows': used}


def aggregate_categories(rows, field, top_n=MAX_CATEGORIES, other_label='Other'):
    """Counts per category value, long tail folded into one ``other_label``.

    Returns ``{'labels': [...], 'series': [one]}`` -- the labels ARE the
    categories here, so the caller must not also build date labels.
    """
    counts = value_counts(rows, field)
    used = sum(count for _value, count in counts)
    if len(counts) > top_n:
        head = counts[:top_n - 1]
        tail = sum(count for _value, count in counts[top_n - 1:])
        counts = head + [(other_label, tail)]
    labels = [value for value, _count in counts]
    points = [float(count) for _value, count in counts]
    return {'labels': labels,
            'series': [{'name': field, 'points': points,
                        'counts': [int(p) for p in points]}],
            'used_rows': used}


def aggregate_scalar(rows, field, agg='mean', group_by=None,
                     max_groups=MAX_SERIES):
    """One number for ``field`` over ``rows`` -- no time axis.

    :func:`aggregate_numeric` answers "how did pH move over time"; this answers
    "what IS the pH", which is the shape most plain-language questions actually
    have ("what is the average conductivity at each site?"). Optionally splits
    by ``group_by``, ordered by sample count and capped like the series cap, so
    a form with 40 sites reports its 8 busiest plus how many it left out --
    ``omitted_groups`` exists so the caller can say so instead of quietly
    truncating.

    Returns ``{'overall', 'groups', 'used_rows', 'omitted_groups', 'values'}``;
    ``values`` are the raw numbers, for :func:`robust_range`.
    """
    if agg not in AGGREGATIONS:
        agg = 'mean'
    overall = {'sum': 0.0, 'count': 0, 'min': None, 'max': None}
    per_group = {}
    raw_values = []

    for row in rows or []:
        if not isinstance(row, dict):
            continue
        answers = row.get('answers') or {}
        value = to_number(answers.get(field))
        if value is None:
            continue
        for bucket in (overall, _group_bucket(per_group, answers, group_by)):
            if bucket is None:
                continue
            bucket['sum'] += value
            bucket['count'] += 1
            bucket['min'] = value if bucket['min'] is None \
                else min(bucket['min'], value)
            bucket['max'] = value if bucket['max'] is None \
                else max(bucket['max'], value)
        raw_values.append(value)

    ordered = sorted(per_group.items(),
                     key=lambda item: (-item[1]['count'], item[0]))
    groups = [{'name': name, 'value': _reduce(bucket, agg),
               'count': bucket['count']}
              for name, bucket in ordered[:max_groups]]

    return {'overall': _reduce(overall, agg),
            'groups': groups,
            'omitted_groups': max(0, len(ordered) - len(groups)),
            'used_rows': overall['count'],
            'values': raw_values}


def _group_bucket(per_group, answers, group_by):
    """The accumulator for this row's group, or ``None`` when not grouping.

    A row whose group value is missing is counted in ``overall`` but belongs to
    no group -- lumping it under an empty-string name would invent a site.
    """
    if not group_by:
        return None
    name = category_value(answers.get(group_by))
    if name is None:
        return None
    return per_group.setdefault(
        name, {'sum': 0.0, 'count': 0, 'min': None, 'max': None})


def round_series(series, digits=4):
    """Round every point in place-ish (returns a new list) to shrink the payload.

    A mean of 7.213333333333333 costs 18 bytes and says nothing more than 7.2133
    to a chart drawn a few hundred pixels wide.
    """
    out = []
    for entry in series or []:
        points = [None if p is None else round(float(p), digits)
                  for p in entry.get('points') or []]
        rounded = dict(entry)
        rounded['points'] = points
        out.append(rounded)
    return out
