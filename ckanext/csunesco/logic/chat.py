# encoding: utf-8
"""Plain-language questions over a project's observations (NO CKAN import).

This module is the whole "chat with your data" brain **except** the network
call and the arithmetic. It owns:

* :data:`TOOLS` -- the closed vocabulary the model may answer in,
* :func:`build_profile` -- the column facts the model is allowed to rely on,
* :func:`validate_tool_call` -- the gate every model answer passes through,
* the prompt, the starter questions and the shape of the answer card.

**The model never produces a number.** It picks one tool call; the server runs
it through :mod:`ckanext.csunesco.logic.aggregate` -- the same code that draws
the chart blocks -- and only then does the model get to write prose *about* an
already-computed result. That is what makes an answer checkable: every figure
on screen came out of the aggregator, so it necessarily matches the charts on
the same page. It is also why the tool vocabulary is closed rather than "write
me a query": the documented failure mode of these systems is a fluent answer
with an invented figure in it, and no amount of prompt wording prevents that if
the model is the thing doing the counting.

**Trust model, borrowed from** :mod:`ckanext.csunesco.logic.blocks`: nothing
here is trusted -- not the model's output, not the browser's history.
:func:`validate_tool_call` is total; it never raises, it returns a precise,
model-readable complaint instead, which the caller feeds back for exactly one
repair attempt.

**Layering.** Pure stdlib, so this is unit-testable without CKAN or a
container. It is also translation-free: like ``blocks.BlockType.label``, every
string it returns is either a stable code or an English source string, and the
sentences are composed in the templates where ``_()`` can reach them.
"""
import json
import re

# --------------------------------------------------------------------------- #
# Caps                                                                        #
# --------------------------------------------------------------------------- #

# The browser owns the conversation and posts it back every turn, so none of it
# is trusted -- for cost or for prompt injection. Both caps are enforced
# server-side on the way in, not merely in the JS that sends them.
MAX_TURNS = 8
MAX_MESSAGE_CHARS = 600

# One question is one tool call. The second attempt exists only to repair a
# call that failed validation (an invented column name, usually); after that we
# refuse rather than let the model wander.
MAX_TOOL_ATTEMPTS = 2

# Starter chips. More than four is a wall, fewer reads as an accident.
MAX_SUGGESTIONS = 4

# Mirrors the enums the chart block already accepts, so a question and a chart
# block can never ask the aggregator for something different.
MODES = ('numeric', 'category', 'count')
AGGREGATIONS = ('mean', 'min', 'max', 'sum', 'count')
BUCKETS = ('auto', 'day', 'week', 'month')
RANGES = ('all', '30d', '90d', '180d', '1y', '2y')

TOOL_NAMES = ('series', 'stat', 'top_categories', 'cannot_answer')

# Stable refusal codes. The wording lives in the templates -- same rule as
# ``blocks.DropReport``, and for the same reason: this module stays
# translation-free.
REFUSAL_CODES = ('not_in_data', 'not_about_data', 'needs_a_field', 'unclear')


# --------------------------------------------------------------------------- #
# Column semantics                                                            #
# --------------------------------------------------------------------------- #

# A trailing parenthesised token at the end of a label. The CS Toolbox has no
# `unit` attribute on its FieldDef -- units live inside the label text
# ("Electrical conductivity (µS/cm)") -- so this is the only place a unit can
# come from. (It is also why ``aggregate.numeric_fields_with_data`` reads a
# ``unit`` key that upstream never sets; we do not change that contract here.)
_UNIT_RE = re.compile(r'\(([^()]{1,12})\)\s*$')

# A unit either carries a symbol (µS/cm, °C, %, mg/L) or is a short alphabetic
# abbreviation (ppm, NTU, cm). Anything else is a parenthesised English word --
# "Other weather (detail)" -- and calling it a unit would put nonsense next to
# every figure.
_UNIT_SYMBOL_RE = re.compile(r'[^0-9A-Za-z]')


def parse_unit(label):
    """The unit inside a field label, or ``None``.

    ``"Electrical conductivity (µS/cm)"`` -> ``"µS/cm"``;
    ``"Other weather (detail)"`` -> ``None``.
    """
    if not isinstance(label, str):
        return None
    match = _UNIT_RE.search(label)
    if not match:
        return None
    unit = match.group(1).strip()
    if not unit:
        return None
    if _UNIT_SYMBOL_RE.search(unit):
        return unit
    return unit if len(unit) <= 4 else None


def build_profile(fields_payload):
    """The column facts the model may rely on, from ``csunesco_data_source_fields``.

    Deliberately small: names, labels, units, how many rows each field actually
    carries, the date span and the site column. That is everything a question
    about this data can legitimately refer to, and nothing else is offered --
    a model that cannot see a column cannot invent a question about it.
    """
    payload = fields_payload if isinstance(fields_payload, dict) else {}
    numeric = []
    for field in payload.get('numeric') or []:
        if not isinstance(field, dict) or not field.get('name'):
            continue
        label = field.get('label') or field['name']
        numeric.append({
            'name': field['name'],
            'label': label,
            'unit': field.get('unit') or parse_unit(label),
            'rows': int(field.get('rows') or 0),
        })

    categorical = []
    for field in payload.get('categorical') or []:
        if not isinstance(field, dict) or not field.get('name'):
            continue
        categorical.append({
            'name': field['name'],
            'label': field.get('label') or field['name'],
            'distinct': int(field.get('distinct') or 0),
        })

    site_field = payload.get('site_field') or None
    return {
        'title': payload.get('title') or '',
        'total': int(payload.get('total') or 0),
        'truncated': bool(payload.get('truncated')),
        'first_date': payload.get('first_date'),
        'last_date': payload.get('last_date'),
        'site_field': site_field,
        'site_label': (payload.get('site_label')
                       or _label_of(site_field, numeric, categorical)),
        'numeric': numeric,
        'categorical': categorical,
    }


def _label_of(name, numeric, categorical):
    if not name:
        return None
    for field in list(numeric) + list(categorical):
        if field['name'] == name:
            return field['label']
    return name


def numeric_names(profile):
    return [f['name'] for f in (profile or {}).get('numeric') or []]


def groupable_names(profile):
    """Columns a question may split by: the categoricals plus the site column.

    ``detect_site_field`` deliberately allows far more distinct values than the
    facet ceiling, so the site column is usually absent from ``categorical``
    even though grouping by it is the single most useful thing to do.
    """
    names = [f['name'] for f in (profile or {}).get('categorical') or []]
    site = (profile or {}).get('site_field')
    if site and site not in names:
        names.append(site)
    return names


def has_data(profile):
    """Whether there is anything at all to ask about."""
    profile = profile or {}
    return bool(profile.get('total')) and bool(
        profile.get('numeric') or profile.get('categorical'))


# --------------------------------------------------------------------------- #
# The closed tool vocabulary                                                  #
# --------------------------------------------------------------------------- #

TOOLS = [
    {
        'type': 'function',
        'function': {
            'name': 'series',
            'description': (
                'How something changed over time. Use for questions about '
                'trends, evolution, seasons, or "when". Returns a chart.'),
            'parameters': {
                'type': 'object',
                'properties': {
                    'mode': {
                        'type': 'string', 'enum': list(MODES),
                        'description': (
                            'numeric = chart a measured field over time; '
                            'count = chart how many observations were made '
                            'over time (no field needed); category = a '
                            'breakdown by category, not a time series.'),
                    },
                    'field': {
                        'type': 'string',
                        'description': (
                            'Exact column name from the profile. Required for '
                            'mode=numeric and mode=category.'),
                    },
                    'agg': {'type': 'string', 'enum': list(AGGREGATIONS)},
                    'group_by': {
                        'type': 'string',
                        'description': (
                            'Exact column name to draw one line per value, or '
                            '"auto" to split by monitoring site.'),
                    },
                    'bucket': {'type': 'string', 'enum': list(BUCKETS)},
                    'range': {'type': 'string', 'enum': list(RANGES)},
                },
                'required': ['mode'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'stat',
            'description': (
                'A single figure, optionally one per group. Use for "what is '
                'the average/highest/lowest", "how many", "which site has the '
                'most". This is the right tool when the question has no time '
                'dimension.'),
            'parameters': {
                'type': 'object',
                'properties': {
                    'field': {
                        'type': 'string',
                        'description': 'Exact numeric column name.',
                    },
                    'agg': {'type': 'string', 'enum': list(AGGREGATIONS)},
                    'group_by': {
                        'type': 'string',
                        'description': (
                            'Exact column name for one figure per value, or '
                            '"auto" for per monitoring site. Omit for one '
                            'overall figure.'),
                    },
                    'range': {'type': 'string', 'enum': list(RANGES)},
                },
                'required': ['field', 'agg'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'top_categories',
            'description': (
                'How many observations fall in each value of a categorical '
                'column. Use for "which is most common", "how many of each".'),
            'parameters': {
                'type': 'object',
                'properties': {
                    'field': {
                        'type': 'string',
                        'description': 'Exact categorical column name.',
                    },
                    'limit': {'type': 'integer', 'minimum': 2, 'maximum': 24},
                },
                'required': ['field'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'cannot_answer',
            'description': (
                'Use this whenever the question cannot be answered from these '
                'columns -- including general knowledge, questions about other '
                'datasets, and questions needing a column that is not in the '
                'profile. Refusing is always better than guessing.'),
            'parameters': {
                'type': 'object',
                'properties': {
                    'reason': {'type': 'string', 'enum': list(REFUSAL_CODES)},
                    'suggestion': {
                        'type': 'string',
                        'description': (
                            'One question about THIS data the user could ask '
                            'instead, in their language. Max 120 characters.'),
                    },
                },
                'required': ['reason'],
            },
        },
    },
]


# --------------------------------------------------------------------------- #
# Validation                                                                  #
# --------------------------------------------------------------------------- #

def validate_tool_call(name, args, profile):
    """``(call, error)`` -- never raises.

    ``call`` is a normalized ``{'name', 'args'}`` ready to hand to the
    aggregator; ``error`` is an English sentence written **for the model**,
    naming the columns that do exist, so the single repair attempt has
    something to work with. Exactly one of the two is ``None``.
    """
    if name not in TOOL_NAMES:
        return None, 'Unknown tool %r. Use one of: %s.' % (
            name, ', '.join(TOOL_NAMES))
    if not isinstance(args, dict):
        args = {}
    profile = profile or {}

    if name == 'cannot_answer':
        reason = args.get('reason')
        if reason not in REFUSAL_CODES:
            reason = 'unclear'
        return {'name': 'cannot_answer',
                'args': {'reason': reason,
                         'suggestion': _text(args.get('suggestion'), 120)}}, None

    if name == 'top_categories':
        field, error = _require_field(args.get('field'), groupable_names(profile),
                                      profile, 'categorical')
        if error:
            return None, error
        return {'name': 'top_categories',
                'args': {'field': field,
                         'limit': _bounded(args.get('limit'), 12, 2, 24)}}, None

    if name == 'stat':
        field, error = _require_field(args.get('field'), numeric_names(profile),
                                      profile, 'numeric')
        if error:
            return None, error
        group_by, error = _optional_group(args.get('group_by'), profile)
        if error:
            return None, error
        return {'name': 'stat',
                'args': {'field': field,
                         'agg': _enum(args.get('agg'), AGGREGATIONS, 'mean'),
                         'group_by': group_by,
                         'range': _enum(args.get('range'), RANGES, 'all')}}, None

    # series
    mode = _enum(args.get('mode'), MODES, 'count')
    out = {'mode': mode,
           'bucket': _enum(args.get('bucket'), BUCKETS, 'auto'),
           'range': _enum(args.get('range'), RANGES, 'all')}
    if mode == 'numeric':
        field, error = _require_field(args.get('field'), numeric_names(profile),
                                      profile, 'numeric')
        if error:
            return None, error
        group_by, error = _optional_group(args.get('group_by'), profile)
        if error:
            return None, error
        out.update({'field': field,
                    'agg': _enum(args.get('agg'), AGGREGATIONS, 'mean'),
                    'group_by': group_by or 'auto'})
    elif mode == 'category':
        field, error = _require_field(args.get('field'), groupable_names(profile),
                                      profile, 'categorical')
        if error:
            return None, error
        out['field'] = field
    else:
        out['field'] = ''
    return {'name': 'series', 'args': out}, None


def _require_field(value, allowed, profile, kind):
    name = _text(value, 64)
    if not name:
        return None, ('This tool needs a %s column name. Available: %s.'
                      % (kind, _catalogue(profile, kind)))
    if name not in allowed:
        return None, ('There is no %s column called %r in this data. '
                      'Use one of these exact names: %s.'
                      % (kind, name, _catalogue(profile, kind)))
    return name, None


def _optional_group(value, profile):
    """``group_by`` is optional; ``'auto'`` means "split by monitoring site"."""
    name = _text(value, 64)
    if not name:
        return None, None
    if name == 'auto':
        return 'auto', None
    if name not in groupable_names(profile):
        return None, ('There is no column called %r to group by. Use "auto" '
                      'for per-site, or one of: %s.'
                      % (name, _catalogue(profile, 'categorical')))
    return name, None


def _catalogue(profile, kind):
    """``name (Label)`` list for the error message the model has to act on."""
    profile = profile or {}
    if kind == 'numeric':
        fields = profile.get('numeric') or []
    else:
        fields = list(profile.get('categorical') or [])
        site = profile.get('site_field')
        if site and site not in [f['name'] for f in fields]:
            fields = [{'name': site,
                       'label': profile.get('site_label') or site}] + fields
    if not fields:
        return '(none in this data)'
    return ', '.join('%s (%s)' % (f['name'], f['label']) for f in fields)


def _text(value, limit):
    if not isinstance(value, str):
        return ''
    return ' '.join(value.split())[:limit].strip()


def _enum(value, allowed, default):
    return value if value in allowed else default


def _bounded(value, default, minimum, maximum):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


# --------------------------------------------------------------------------- #
# Prompt                                                                      #
# --------------------------------------------------------------------------- #

SYSTEM_PROMPT = u"""\
You help citizen scientists understand observations collected in the field, on \
a public UNESCO water-data portal. Most readers are volunteers, not analysts.

You do NOT have the data and you cannot count, average or compare anything \
yourself. You have exactly one job per question: choose the single tool call \
that would answer it. A server then runs that call over the real observations \
and gives you the result.

Rules, in order of importance:
1. The data profile in the next message is the ONLY source of truth for column \
names. Never use a column that is not listed there, and never guess a name -- \
copy it exactly.
2. If the question cannot be answered from those columns, call cannot_answer. \
That includes general knowledge, other datasets, causes and predictions. \
Refusing is a good answer; a plausible invented one is the worst outcome.
3. Never state a figure that is not in a tool result you were given. Do not \
estimate, extrapolate or round toward a nicer number.
4. When you write the final answer: lead with the figure and what it means, in \
two or three short sentences. No preamble, no bullet lists, no markdown, no \
restating the question. The chart and the figures are shown next to your text, \
so do not repeat every number -- explain what the reader is looking at.
5. If the result is empty or thin, say so plainly rather than dressing it up.
"""


def build_messages(profile, history, language='en', feedback=None):
    """``[system, profile, ...history]`` for the provider.

    The profile rides in its own user message rather than in the system prompt
    (the shape ``ckanext-terriassistant`` uses) so the system prompt stays a
    constant: identical bytes on every request, for every project.
    """
    context = {
        'data_profile': profile or {},
        'answer_language': language or 'en',
        'instruction': ('Use the data profile above as the only source of '
                        'truth for column names.'),
    }
    if feedback:
        context['previous_attempt_was_rejected'] = feedback
    messages = [
        {'role': 'system', 'content': SYSTEM_PROMPT},
        {'role': 'user',
         'content': json.dumps(context, ensure_ascii=False, sort_keys=True)},
    ]
    messages.extend(clamp_history(history))
    return messages


def clamp_question(value):
    """One question, whitespace-normalized and capped. ``''`` when unusable."""
    return _text(value, MAX_MESSAGE_CHARS)


def clamp_history(history):
    """The last :data:`MAX_TURNS` well-formed turns, each truncated.

    The browser sends the whole conversation every turn, so this runs on
    untrusted input: anything that is not a plain user/assistant string turn is
    dropped rather than forwarded.
    """
    out = []
    for item in list(history or [])[-MAX_TURNS:]:
        if not isinstance(item, dict):
            continue
        role = item.get('role')
        content = item.get('content')
        if role not in ('user', 'assistant') or not isinstance(content, str):
            continue
        content = content.strip()[:MAX_MESSAGE_CHARS]
        if content:
            out.append({'role': role, 'content': content})
    return out


# --------------------------------------------------------------------------- #
# Starter questions                                                           #
# --------------------------------------------------------------------------- #

def suggestions_from_profile(profile):
    """Starter chips built from columns that actually hold data.

    Generic examples are the reason these interfaces feel like a dead end: a
    reader who types the suggested question and is told the column does not
    exist stops trusting the box. Every chip here names a real field, and the
    structured form lets the template phrase it in the reader's language.
    """
    profile = profile or {}
    numeric = profile.get('numeric') or []
    categorical = profile.get('categorical') or []
    site = profile.get('site_field')
    out = []

    if numeric:
        busiest = max(numeric, key=lambda f: f.get('rows') or 0)
        out.append({'kind': 'average', 'field': busiest['name'],
                    'label': busiest['label'], 'unit': busiest.get('unit')})
        if site:
            out.append({'kind': 'by_site', 'field': busiest['name'],
                        'label': busiest['label'],
                        'unit': busiest.get('unit'),
                        'site_label': profile.get('site_label') or site})
        if len(numeric) > 1:
            other = next(f for f in numeric if f['name'] != busiest['name'])
            out.append({'kind': 'trend', 'field': other['name'],
                        'label': other['label'], 'unit': other.get('unit')})
    out.append({'kind': 'count_over_time'})
    if categorical:
        first = categorical[0]
        out.append({'kind': 'breakdown', 'field': first['name'],
                    'label': first['label']})
    return out[:MAX_SUGGESTIONS]


# --------------------------------------------------------------------------- #
# The answer card                                                             #
# --------------------------------------------------------------------------- #

def answer_card(call, result, profile):
    """What the browser draws next to the prose: the computed result itself.

    Structured, never a sentence -- the template composes the wording so it can
    be translated, exactly as ``blocks._drop.html`` does with drop codes. The
    ``basis`` block is the part that makes an answer checkable: how many
    observations it used, over which span, and what was left out.
    """
    call = call or {}
    args = call.get('args') or {}
    result = result if isinstance(result, dict) else {}
    profile = profile or {}
    name = call.get('name')

    if name == 'cannot_answer':
        return {'kind': 'refusal',
                'reason': args.get('reason') or 'unclear',
                'suggestion': args.get('suggestion') or ''}

    field = args.get('field') or ''
    card = {
        'kind': {'series': 'series', 'stat': 'stat',
                 'top_categories': 'categories'}.get(name, 'none'),
        'query': {
            'tool': name,
            'field': field,
            'field_label': result.get('field_label') or _field_label(field, profile),
            'unit': _field_unit(field, profile),
            'mode': args.get('mode'),
            'agg': args.get('agg'),
            'group_by': args.get('group_by') or '',
            'group_by_label': _field_label(args.get('group_by'), profile),
            'bucket': result.get('bucket') or args.get('bucket'),
            'range': args.get('range') or 'all',
        },
        'basis': {
            'used_rows': int(result.get('used_rows') or 0),
            'total_rows': int(result.get('total_rows')
                              or profile.get('total') or 0),
            'first_date': result.get('first_date') or profile.get('first_date'),
            'last_date': result.get('last_date') or profile.get('last_date'),
            'truncated': bool(result.get('truncated')
                              or profile.get('truncated')),
            'omitted_groups': int(result.get('omitted_groups') or 0),
        },
    }

    if card['kind'] == 'stat':
        card['overall'] = result.get('overall')
        card['groups'] = result.get('groups') or []
    else:
        card['labels'] = result.get('labels') or []
        card['series'] = result.get('series') or []
        for key in ('y_min', 'y_max'):
            if key in result:
                card[key] = result[key]
    return card


def _field_label(name, profile):
    if not name or name == 'auto':
        return ''
    for field in list((profile or {}).get('numeric') or []) \
            + list((profile or {}).get('categorical') or []):
        if field['name'] == name:
            return field['label']
    if name == (profile or {}).get('site_field'):
        return (profile or {}).get('site_label') or name
    return name


def _field_unit(name, profile):
    for field in (profile or {}).get('numeric') or []:
        if field['name'] == name:
            return field.get('unit')
    return None


def result_is_empty(card):
    """Whether the computation found nothing -- the honest-answer signal.

    Worth having in one place: "no rows matched" must read as "there is no data
    for that", not as a chart of zeroes with confident prose above it.
    """
    if not isinstance(card, dict):
        return True
    if card.get('kind') == 'refusal':
        return False
    if not (card.get('basis') or {}).get('used_rows'):
        return True
    if card.get('kind') == 'stat':
        return card.get('overall') is None and not card.get('groups')
    return not card.get('labels')
