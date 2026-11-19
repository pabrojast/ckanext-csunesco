"""Versioned chart contract and calculator, vendored unchanged in CKAN.

Only configuration and chart series cross the boundary, never authorization.
Callers MUST supply rows already filtered by their own access policy.
"""
import datetime as dt
import json
import math
from collections import Counter, defaultdict

VERSION = 1
DEFAULTS = dict(version=VERSION, title='', caption='', mode='numeric', field='',
                chart='line', agg='mean', bucket='month', group_by='auto',
                range='all', start=None, end=None, sites=[], filters={}, axes={},
                include_history=False, source_id='', height=320)


def normalize(raw):
    if not isinstance(raw, dict) or set(raw) - set(DEFAULTS):
        raise ValueError('Unknown chart settings')
    if len(json.dumps(raw, allow_nan=False).encode()) > 32768:
        raise ValueError('Chart settings too large')
    c = dict(DEFAULTS, **raw)
    enums = dict(mode=('numeric', 'category', 'count'), chart=('line', 'bar', 'pie'),
                 agg=('mean', 'min', 'max', 'sum', 'count'),
                 bucket=('auto', 'raw', 'day', 'week', 'month'),
                 range=('all', '7d', '30d', '90d', '1y'))
    if c['version'] != VERSION:
        raise ValueError('Unsupported chart version')
    for key, allowed in enums.items():
        if c[key] not in allowed:
            raise ValueError('Invalid ' + key)
    for key, limit in [('title', 160), ('caption', 300), ('field', 200), ('group_by', 200), ('source_id', 200)]:
        if not isinstance(c[key], str) or len(c[key]) > limit:
            raise ValueError('Invalid ' + key)
    if c['mode'] != 'count' and not c['field']:
        raise ValueError('Choose a variable')
    if c['chart'] == 'pie' and c['mode'] != 'category':
        raise ValueError('Pie charts require categorical data')
    if c['mode'] == 'category' and c['chart'] == 'line':
        raise ValueError('Choose bars or pie for categories')
    for key in ['start', 'end']:
        if c[key] is not None:
            if not isinstance(c[key], str) or len(c[key]) != 10:
                raise ValueError('Invalid date')
            dt.date.fromisoformat(c[key])
    if c['start'] and c['end'] and c['start'] > c['end']:
        raise ValueError('Invalid date interval')
    if type(c['include_history']) is not bool or type(c['height']) is not int or not 200 <= c['height'] <= 600:
        raise ValueError('Invalid chart options')
    def strings(value):
        if not isinstance(value, list) or len(value) > 200 or any(not isinstance(v, str) or len(v) > 500 for v in value):
            raise ValueError('Invalid chart filter')
        return list(dict.fromkeys(value))
    c['sites'] = strings(c['sites'])
    f = c['filters']
    if not isinstance(f, dict) or set(f) - {'sites', 'facets', 'search'}:
        raise ValueError('Invalid chart filters')
    facets = f.get('facets', {})
    if not isinstance(facets, dict) or len(facets) > 50 or any(not isinstance(k, str) or len(k) > 200 for k in facets):
        raise ValueError('Invalid categorical filters')
    search = f.get('search', '')
    if not isinstance(search, str) or len(search) > 500:
        raise ValueError('Invalid search')
    c['filters'] = dict(sites=strings(f.get('sites', [])), facets={k: strings(v) for k, v in facets.items()}, search=search)
    if not isinstance(c['axes'], dict) or set(c['axes']) - {'xMin', 'xMax', 'yMin', 'yMax'}:
        raise ValueError('Invalid axes')
    for v in c['axes'].values():
        if type(v) not in (float, int) or not math.isfinite(v):
            raise ValueError('Invalid axis limit')
    for axis in ['x', 'y']:
        if c['axes'].get(axis + 'Min', -math.inf) >= c['axes'].get(axis + 'Max', math.inf):
            raise ValueError('Invalid axis interval')
    return c


def _category(value):
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return value if isinstance(value, str) and value.strip() else None


def _date(value):
    try:
        date = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return date.replace(tzinfo=dt.timezone.utc) if not date.tzinfo else date.astimezone(dt.timezone.utc)
    except (ValueError, TypeError):
        return None


def series(raw, payload, today=None):
    c = normalize(raw)
    rows = payload.get('rows') or []
    fields = (payload.get('schema') or {}).get('fields') or []
    field_map = {f['name']: f for f in fields if isinstance(f, dict) and 'name' in f}
    if c['mode'] != 'count' and c['field'] not in field_map:
        raise ValueError('Chart variable no longer exists')
    candidates = ['site', 'site_name', 'sitio', 'nombre_sitio', 'river', 'rio', 'station']
    site_field = next((name for name in candidates if name in field_map
        and field_map[name].get('type') in ('short_text', 'single_select', 'site')
        and 1 <= len({_category((r.get('answers') or {}).get(name)) for r in rows}
                     - {None}) <= 30), None)
    group = site_field if c['group_by'] == 'auto' else c['group_by'] or None
    if group and group not in field_map:
        raise ValueError('Chart grouping no longer exists')
    for field in c['filters']['facets']:
        if field not in field_map:
            raise ValueError('Chart filter no longer exists')
    start, end = c['start'], c['end']
    if not start and not end and c['range'] != 'all':
        today = today or dt.datetime.now(dt.timezone.utc).date()
        start = (today - dt.timedelta(days={'7d': 7, '30d': 30, '90d': 90, '1y': 365}[c['range']])).isoformat()
    filtered = []
    for row in rows:
        a = row.get('answers') or {}
        date = _date(row.get('date'))
        day = date.date().isoformat() if date else None
        if (start or end) and (not day or start and day < start or end and day > end):
            continue
        if c['filters']['sites'] and (not site_field or a.get(site_field) not in c['filters']['sites']):
            continue
        if any(values and _category(a.get(field)) not in values for field, values in c['filters']['facets'].items()):
            continue
        search = c['filters']['search'].strip().lower()
        def text(v):
            if isinstance(v, bool): return 'true' if v else 'false'
            if isinstance(v, list): return ', '.join(text(x) for x in v)
            return '' if v is None else str(v)
        if search and search not in (day or '') and not any(search in text(v).lower() for v in a.values()):
            continue
        # A selected site/group restricts every mode, including category and
        # total-count charts. Keep the count series label independent of it.
        if c['sites'] and _category(a.get(group)) not in c['sites']:
            continue
        filtered.append((row, a, date))
    out = dict(mode=c['mode'], chart=c['chart'], title=c['title'], axes=c['axes'],
               total_rows=payload.get('total', len(rows)), used_rows=len(filtered),
               truncated=bool(payload.get('truncated')), labels=[], series=[])
    if c['mode'] == 'category':
        counts = Counter(_category(a.get(c['field'])) for _, a, _ in filtered)
        counts.pop(None, None)
        ordered = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
        if len(ordered) > 12:
            ordered = ordered[:11] + [('Other', sum(n for _, n in ordered[11:]))]
        labels = {o['value']: o.get('label', o['value']) for o in field_map[c['field']].get('options') or [] if isinstance(o, dict) and 'value' in o}
        out['used_rows'] = sum(counts.values())
        out.update(labels=[labels.get(k, k) for k, _ in ordered], series=[dict(name=c['title'], points=[v for _, v in ordered])])
        return out
    groups = defaultdict(list)
    for row, answers, date in filtered:
        if date is None:
            continue
        value = 1 if c['mode'] == 'count' else answers.get(c['field'])
        if isinstance(value, str) and value.strip():
            try: value = float(value)
            except ValueError: continue
        if type(value) not in (int, float) or not math.isfinite(value):
            continue
        name = 'Observations' if c['mode'] == 'count' else (_category(answers.get(group)) or '')
        groups[name].append((date, value))
    names = sorted(groups, key=lambda name: -len(groups[name]))
    if not c['sites']:
        names = names[:8]
    out['used_rows'] = sum(len(groups[name]) for name in names)
    bucket = c['bucket']
    if bucket == 'raw' and c['mode'] == 'count':
        bucket = 'day'
    if bucket == 'auto':
        dates = [d for n in names for d, _ in groups[n]]
        span = (max(dates) - min(dates)).days if dates else 0
        bucket = 'day' if span <= 180 else 'week' if span <= 1095 else 'month'
    for name in names:
        values = sorted(groups[name])
        if bucket == 'raw' and c['mode'] != 'count':
            points = [{'x': date.isoformat(), 'y': v} for date, v in values]
        else:
            bins = defaultdict(list)
            for date, v in values:
                day = date.date()
                if bucket == 'week': day -= dt.timedelta(days=day.weekday())
                elif bucket == 'month': day = day.replace(day=1)
                bins[day.isoformat()].append(v)
            agg = 'count' if c['mode'] == 'count' else c['agg']
            def reduce(v):
                return {'mean': lambda: sum(v) / len(v), 'sum': lambda: sum(v), 'min': lambda: min(v), 'max': lambda: max(v), 'count': lambda: len(v)}[agg]()
            points = [{'x': day, 'y': reduce(v)} for day, v in sorted(bins.items())]
        out['series'].append(dict(name=name, xy=points))
    values = sorted(p['y'] for item in out['series'] for p in item['xy'])
    if len(values) >= 8:
        def quantile(fraction):
            index = (len(values) - 1) * fraction
            lo, hi = math.floor(index), math.ceil(index)
            return values[lo] + (values[hi] - values[lo]) * (index - lo)
        q1, q3 = quantile(.25), quantile(.75)
        spread = q3 - q1
        if spread > 0:
            lo, hi = q1 - 1.5 * spread, q3 + 1.5 * spread
            if hi < values[-1]: out['y_max'] = math.ceil(hi)
            if lo > values[0]: out['y_min'] = max(0, math.floor(lo)) if values[0] >= 0 else math.floor(lo)
    out['bucket'] = bucket
    return out
