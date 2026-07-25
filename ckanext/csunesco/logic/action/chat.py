# encoding: utf-8
"""``csunesco_data_chat`` -- plain-language questions over a project's data.

The loop, and why it is shaped this way:

1. Build the column profile from ``csunesco_data_source_fields`` -- the same
   introspection the chart editor's field picker uses.
2. Ask the model for **one tool call**, validated against that profile
   (:func:`ckanext.csunesco.logic.chat.validate_tool_call`). A rejected call is
   fed back once with the reason; a second failure becomes a refusal.
3. **Run the call ourselves**, through the aggregator that draws the chart
   blocks. The model has no arithmetic to do and no data to hallucinate from.
4. Only then let the model write prose, with the computed result in front of it
   and no tools available, so it cannot wander off and compute something else.

Steps 3 and 4 are the whole design. Every figure a reader sees came out of
``logic/aggregate.py``, which means it necessarily matches the charts on the
same page -- and it is why a refusal or an empty result short-circuits before
step 4: there is nothing to narrate, and paying a provider to dress up "no
data" is how these interfaces end up sounding confident about nothing.

This action is NOT ``side_effect_free``: it spends a per-user quota and money.
"""
import datetime
import json
import logging

import ckan.plugins.toolkit as tk
import ckan.model as model

from ckanext.csunesco import db
from ckanext.csunesco.logic import chat as cs_chat
from ckanext.csunesco.logic import llm
from ckanext.csunesco.logic.action import current_user_id
# Imported rather than re-implemented ON PURPOSE: it encodes the rule that a
# source must be APPROVED (and optionally pinned to a project) before anyone
# may read it. A second copy of that check is a second thing to get wrong.
from ckanext.csunesco.logic.action.data import _approved_source_or_404

log = logging.getLogger(__name__)

QUOTA_OPTION = 'ckanext.csunesco.llm_daily_quota'
DEFAULT_DAILY_QUOTA = 40

# Stable status codes; the wording lives in the templates so it is translatable
# (the ``blocks.DropReport`` rule).
#   ok            -- there is an answer and a card
#   unconfigured  -- no provider key on this portal; the feature is off
#   no_data       -- the source has no columns worth asking about yet
#   empty         -- the question was valid but matched no observations
#   refused       -- the model correctly declined; see card.reason
#   quota_reached -- this user has spent their allowance for today
#   unavailable   -- the provider or the upstream data could not be reached
STATUSES = ('ok', 'unconfigured', 'no_data', 'empty', 'refused',
            'quota_reached', 'unavailable')

MAX_REPLY_CHARS = 1200


def _daily_quota():
    try:
        return max(0, int(tk.config.get(QUOTA_OPTION, DEFAULT_DAILY_QUOTA)))
    except (TypeError, ValueError):
        return DEFAULT_DAILY_QUOTA


def _today():
    return datetime.datetime.utcnow().strftime('%Y-%m-%d')


def _language(data_dict):
    """The reader's language, as a short code the prompt can carry."""
    raw = (data_dict or {}).get('language')
    if isinstance(raw, str):
        code = ''.join(ch for ch in raw.strip()[:5] if ch.isalnum() or ch == '-')
        if code:
            return code
    try:
        return str(tk.h.lang() or 'en')
    except Exception:
        return 'en'


def csunesco_data_chat(context, data_dict):
    """Answer one question about an approved data source's observations."""
    if not context.get('user'):
        raise tk.NotAuthorized(
            tk._('You must be logged in to ask about the data'))
    tk.check_access('csunesco_data_chat', context, data_dict)
    data_dict = data_dict or {}

    # Resolve first: a bad id must 404 before we spend anything.
    data_source = _approved_source_or_404(data_dict)

    if not llm.is_configured():
        return _envelope('unconfigured')

    user_id = current_user_id(context)
    quota = _daily_quota()
    used = db.chat_usage_count(user_id, _today())
    if quota and used >= quota:
        return _envelope('quota_reached', quota_left=0)

    question = cs_chat.clamp_question(data_dict.get('question'))
    if not question:
        raise tk.ValidationError({'question': [tk._('Missing value')]})

    try:
        profile = cs_chat.build_profile(
            tk.get_action('csunesco_data_source_fields')(
                context, {'id': data_source.id}))
    except tk.ObjectNotFound:
        raise
    except Exception:
        log.warning('csunesco: chat could not profile the data source')
        return _envelope('unavailable')

    suggestions = cs_chat.suggestions_from_profile(profile)
    if not cs_chat.has_data(profile):
        return _envelope('no_data', suggestions=suggestions)

    history = cs_chat.clamp_history(data_dict.get('history'))
    history.append({'role': 'user', 'content': question})
    language = _language(data_dict)

    # Every path below this point has spent a provider call, so the quota is
    # charged here rather than on success only -- a failed attempt costs the
    # portal exactly as much as a good one.
    remaining = _charge(user_id, quota)

    try:
        call = _choose_tool(profile, history, language)
    except llm.LlmDisabled:
        return _envelope('unconfigured')
    except llm.LlmError:
        return _envelope('unavailable', quota_left=remaining)

    if call['name'] == 'cannot_answer':
        # A refusal is already the complete answer: the reason code and the
        # suggested alternative are what the reader needs, and asking the model
        # to elaborate only invites it to soften the refusal into a guess.
        return _envelope('refused', card=cs_chat.answer_card(call, {}, profile),
                         suggestions=suggestions, quota_left=remaining)

    try:
        result = _run_tool(context, data_source, call)
    except tk.ValidationError:
        # The aggregator rejected what validation let through -- a real bug on
        # our side, not something to narrate at the reader.
        log.warning('csunesco: chat tool %r was rejected downstream',
                    call.get('name'))
        return _envelope('unavailable', quota_left=remaining)
    except Exception:
        log.warning('csunesco: chat tool %r failed', call.get('name'))
        return _envelope('unavailable', quota_left=remaining)

    card = cs_chat.answer_card(call, result, profile)
    if cs_chat.result_is_empty(card):
        return _envelope('empty', card=card, suggestions=suggestions,
                         quota_left=remaining)

    try:
        reply = _write_answer(profile, history, language, call, result)
    except llm.LlmError:
        # The figures are already computed and correct; losing the prose is a
        # degraded answer, not a failed one.
        reply = ''
    return _envelope('ok', reply=reply, card=card, suggestions=suggestions,
                     quota_left=remaining)


def _envelope(status, reply='', card=None, suggestions=None, quota_left=None):
    return {'status': status if status in STATUSES else 'unavailable',
            'reply': reply,
            'card': card,
            'suggestions': suggestions or [],
            'quota_left': quota_left}


def _charge(user_id, quota):
    """Count this call against the user's day; return what is left after it."""
    try:
        used = db.bump_chat_usage(user_id, _today())
        model.Session.commit()
    except Exception:
        model.Session.rollback()
        log.warning('csunesco: chat usage could not be recorded')
        return None
    return max(0, quota - used) if quota else None


# ---------------------------------------------------------------------------
# Step 2: choose one validated tool call
# ---------------------------------------------------------------------------

def _choose_tool(profile, history, language):
    """The model's tool call, validated -- with one repair attempt.

    Always returns a call: an attempt that cannot be repaired becomes an
    explicit refusal rather than a guess.
    """
    feedback = None
    for _attempt in range(cs_chat.MAX_TOOL_ATTEMPTS):
        answer = llm.complete(
            cs_chat.build_messages(profile, history, language, feedback),
            tools=cs_chat.TOOLS)
        raw = answer.get('tool_call')
        if not raw:
            feedback = ('You replied with prose. Answer with exactly one tool '
                        'call instead, or call cannot_answer.')
            continue
        call, error = cs_chat.validate_tool_call(raw['name'], raw['args'],
                                                 profile)
        if call is not None:
            return call
        feedback = error
    return {'name': 'cannot_answer',
            'args': {'reason': 'unclear', 'suggestion': ''}}


# ---------------------------------------------------------------------------
# Step 3: run it through the aggregator
# ---------------------------------------------------------------------------

def _run_tool(context, data_source, call):
    """Execute a validated call. Never sees model output that was not validated."""
    args = call['args']
    name = call['name']

    if name == 'series':
        return tk.get_action('csunesco_data_source_series')(
            context, dict(args, id=data_source.id))

    if name == 'top_categories':
        # A category breakdown IS the series action's 'category' mode; routing
        # it there keeps one implementation of the top-N + "Other" fold.
        return tk.get_action('csunesco_data_source_series')(
            context, {'id': data_source.id, 'mode': 'category',
                      'field': args['field'],
                      'max_categories': args['limit']})

    return _run_stat(data_source, args)


def _run_stat(data_source, args):
    """One figure (optionally per group) -- the shape with no time axis.

    Not an action of its own: it exists only to answer chat questions, and
    adding a public route for it would put a third caller on the upstream fetch
    for no one's benefit. It reuses the same TTL-cached payload as the charts.
    """
    from ckanext.csunesco.logic import aggregate, ofform
    payload = ofform.fetch_dashboard_data(data_source.form_id)
    rows = payload.get('rows') or []
    schema = payload.get('schema') or {}
    total_rows = payload.get('total', len(rows))

    first, last = aggregate.date_span(rows)
    start = aggregate.preset_start(args.get('range'),
                                   datetime.datetime.utcnow().date())
    rows = aggregate.filter_rows_by_date(rows, start, None)

    group_by = args.get('group_by')
    if group_by == 'auto':
        group_by = aggregate.detect_site_field(schema, rows)

    out = aggregate.aggregate_scalar(rows, args['field'], agg=args['agg'],
                                     group_by=group_by)
    return {
        'field': args['field'],
        'field_label': aggregate.field_label(schema, args['field']),
        'agg': args['agg'],
        'group_by': group_by or '',
        'overall': None if out['overall'] is None else round(out['overall'], 4),
        'groups': [{'name': g['name'], 'count': g['count'],
                    'value': None if g['value'] is None else round(g['value'], 4)}
                   for g in out['groups']],
        'omitted_groups': out['omitted_groups'],
        'used_rows': out['used_rows'],
        'total_rows': total_rows,
        'truncated': bool(payload.get('truncated')),
        'first_date': first.isoformat() if first else None,
        'last_date': last.isoformat() if last else None,
    }


# ---------------------------------------------------------------------------
# Step 4: prose about an already-computed result
# ---------------------------------------------------------------------------

def _write_answer(profile, history, language, call, result):
    """Ask for the wording only -- no tools, so nothing can be recomputed.

    The result rides in as a plain user message rather than through the
    ``role: "tool"`` protocol: that protocol needs the provider's own call id
    echoed back in exactly the right place, and this is one string either way.
    Portability is worth more than protocol purity for a single hand-off.
    """
    messages = cs_chat.build_messages(profile, history, language)
    messages.append({
        'role': 'user',
        'content': json.dumps(
            {'tool_that_ran': call['name'],
             'arguments': call['args'],
             'result': _trim_result(result),
             'instruction': (
                 'These figures were computed from the real observations. '
                 'Write the answer for the reader in the answer_language. '
                 'Use only these figures, and do not repeat all of them -- '
                 'the chart is shown next to your text.')},
            ensure_ascii=False, sort_keys=True),
    })
    answer = llm.complete(messages, max_tokens=400)
    return (answer.get('text') or '')[:MAX_REPLY_CHARS]


def _trim_result(result):
    """Keep the prompt small: the model needs the shape, not every point.

    A 400-label series is ~10 KB of numbers the model cannot say anything
    useful about; the chart already shows them. What it needs is the extremes
    and the size of the thing.
    """
    if not isinstance(result, dict):
        return {}
    out = {key: value for key, value in result.items()
           if key not in ('series', 'labels')}
    labels = result.get('labels') or []
    if labels:
        out['label_count'] = len(labels)
        out['first_label'] = labels[0]
        out['last_label'] = labels[-1]
    trimmed = []
    for entry in (result.get('series') or [])[:cs_chat.MAX_SUGGESTIONS + 4]:
        points = [p for p in (entry.get('points') or []) if p is not None]
        if not points:
            continue
        trimmed.append({'name': entry.get('name') or '',
                        'points_with_data': len(points),
                        'min': min(points), 'max': max(points),
                        'first': points[0], 'last': points[-1]})
    if trimmed:
        out['series_summary'] = trimmed
    return out


def get_actions():
    return {'csunesco_data_chat': csunesco_data_chat}
