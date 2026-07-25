# encoding: utf-8
"""Behavioural tests for the data-chat loop -- CKAN imports, NO network.

The provider is always a stub: these assert what the *loop* does with an
answer, never what a model would say. Two properties matter most and both are
covered here:

* a tool call that names a column the data does not have is rejected, retried
  once with the reason, and then refused -- it never reaches the aggregator;
* the figures come out of ``logic/aggregate.py``, so an answer and a chart
  block asking the same thing agree.

Requires CKAN only because the modules under test import ``plugins.toolkit``;
nothing here needs a configured site or a database.
"""
import json
import os

import pytest

try:
    from ckanext.csunesco.logic import chat as cs_chat
    from ckanext.csunesco.logic.action import chat as action_chat
    from ckanext.csunesco.logic import llm
    HAVE_CKAN = True
except Exception:
    HAVE_CKAN = False

pytestmark = pytest.mark.skipif(
    not HAVE_CKAN, reason='requires CKAN (ckan.plugins.toolkit)')

FIXTURE = os.path.join(os.path.dirname(__file__), 'fixtures',
                       'ofform_form3.json')


def _payload():
    with open(FIXTURE, 'r') as fh:
        return json.load(fh)


def _profile():
    from ckanext.csunesco.logic import aggregate as ag
    payload = _payload()
    rows, schema = payload['rows'], payload['schema']
    site_field = ag.detect_site_field(schema, rows)
    return cs_chat.build_profile({
        'title': payload.get('title'),
        'total': payload.get('total'),
        'site_field': site_field,
        'site_label': ag.field_label(schema, site_field),
        'numeric': ag.numeric_fields_with_data(schema, rows),
        'categorical': ag.categorical_field_options(schema, rows, site_field),
    })


class FakeProvider(object):
    """Replays canned provider answers and records what it was asked."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, messages, tools=None, max_tokens=700):
        self.calls.append({'messages': messages, 'tools': tools})
        if not self.answers:
            raise AssertionError('the loop asked for more answers than staged')
        return self.answers.pop(0)


def _tool(name, **args):
    return {'text': '', 'tool_call': {'name': name, 'args': args}}


def _prose(text):
    return {'text': text, 'tool_call': None}


# --------------------------------------------------------------------------- #
# The tool loop                                                               #
# --------------------------------------------------------------------------- #

def test_a_valid_call_is_taken_on_the_first_attempt(monkeypatch):
    provider = FakeProvider(_tool('stat', field='ph', agg='mean'))
    monkeypatch.setattr(llm, 'complete', provider)
    call = action_chat._choose_tool(_profile(), [], 'en')
    assert call == {'name': 'stat',
                    'args': {'field': 'ph', 'agg': 'mean',
                             'group_by': None, 'range': 'all'}}
    assert len(provider.calls) == 1


def test_an_invented_column_is_retried_with_the_reason(monkeypatch):
    provider = FakeProvider(
        _tool('stat', field='ph_level', agg='mean'),
        _tool('stat', field='ph', agg='mean'))
    monkeypatch.setattr(llm, 'complete', provider)
    call = action_chat._choose_tool(_profile(), [], 'en')

    assert call['args']['field'] == 'ph'
    assert len(provider.calls) == 2
    # The retry must carry the complaint, otherwise the model repeats itself.
    context = provider.calls[1]['messages'][1]['content']
    assert 'previous_attempt_was_rejected' in context
    assert 'ph_level' in context


def test_two_bad_attempts_become_a_refusal_not_a_guess(monkeypatch):
    provider = FakeProvider(
        _tool('stat', field='nope', agg='mean'),
        _tool('stat', field='still_nope', agg='mean'))
    monkeypatch.setattr(llm, 'complete', provider)
    call = action_chat._choose_tool(_profile(), [], 'en')
    assert call['name'] == 'cannot_answer'
    assert len(provider.calls) == cs_chat.MAX_TOOL_ATTEMPTS


def test_prose_instead_of_a_tool_call_is_retried_then_refused(monkeypatch):
    provider = FakeProvider(_prose('The average pH is about 7.4.'),
                            _prose('Roughly 7.4.'))
    monkeypatch.setattr(llm, 'complete', provider)
    call = action_chat._choose_tool(_profile(), [], 'en')
    # A confident sentence with a number in it is exactly what must NOT get
    # through: no tool ran, so no figure was computed.
    assert call['name'] == 'cannot_answer'


def test_the_model_is_offered_the_closed_tool_vocabulary(monkeypatch):
    provider = FakeProvider(_tool('series', mode='count'))
    monkeypatch.setattr(llm, 'complete', provider)
    action_chat._choose_tool(_profile(), [], 'en')
    offered = {t['function']['name'] for t in provider.calls[0]['tools']}
    assert offered == set(cs_chat.TOOL_NAMES)


# --------------------------------------------------------------------------- #
# The computation                                                             #
# --------------------------------------------------------------------------- #

class FakeSource(object):
    id = 'src-1'
    form_id = 3


def _stub_ofform(monkeypatch):
    from ckanext.csunesco.logic import ofform
    monkeypatch.setattr(ofform, 'fetch_dashboard_data',
                        lambda form_id, **kwargs: _payload())


def test_stat_matches_a_hand_computed_mean(monkeypatch):
    from ckanext.csunesco.logic import aggregate as ag
    _stub_ofform(monkeypatch)
    result = action_chat._run_stat(
        FakeSource(), {'field': 'ph', 'agg': 'mean', 'group_by': None,
                       'range': 'all'})

    values = [ag.to_number((r.get('answers') or {}).get('ph'))
              for r in _payload()['rows']]
    values = [v for v in values if v is not None]
    assert result['used_rows'] == len(values)
    assert abs(result['overall'] - round(sum(values) / len(values), 4)) < 1e-4
    assert result['field_label'] == 'pH'


def test_stat_group_by_auto_resolves_to_the_site_column(monkeypatch):
    _stub_ofform(monkeypatch)
    result = action_chat._run_stat(
        FakeSource(), {'field': 'ph', 'agg': 'mean', 'group_by': 'auto',
                       'range': 'all'})
    assert result['group_by'] == 'site'
    assert result['groups']
    assert all(set(g) == {'name', 'value', 'count'} for g in result['groups'])


def test_stat_card_reports_the_basis_of_the_figure(monkeypatch):
    _stub_ofform(monkeypatch)
    profile = _profile()
    call, error = cs_chat.validate_tool_call(
        'stat', {'field': 'ph', 'agg': 'max'}, profile)
    assert error is None
    card = cs_chat.answer_card(call, action_chat._run_stat(FakeSource(),
                                                           call['args']),
                               profile)
    assert card['basis']['used_rows'] > 0
    assert card['basis']['total_rows'] == 200
    assert card['basis']['first_date'] and card['basis']['last_date']
    assert not cs_chat.result_is_empty(card)


# --------------------------------------------------------------------------- #
# The prose step                                                              #
# --------------------------------------------------------------------------- #

def test_the_writer_gets_no_tools_and_only_the_computed_result(monkeypatch):
    provider = FakeProvider(_prose('pH averages 7.4 across the sites.'))
    monkeypatch.setattr(llm, 'complete', provider)
    call = {'name': 'stat', 'args': {'field': 'ph', 'agg': 'mean'}}
    reply = action_chat._write_answer(
        _profile(), [{'role': 'user', 'content': 'average ph?'}], 'en',
        call, {'overall': 7.4, 'used_rows': 120})

    assert reply == 'pH averages 7.4 across the sites.'
    # No tools on the writing call: it must not be able to compute anything.
    assert provider.calls[0]['tools'] is None
    handoff = json.loads(provider.calls[0]['messages'][-1]['content'])
    assert handoff['result']['overall'] == 7.4
    assert handoff['tool_that_ran'] == 'stat'


def test_trim_result_summarises_series_instead_of_shipping_every_point():
    big = {'labels': ['2026-%02d' % m for m in range(1, 13)],
           'series': [{'name': 'A', 'points': [1.0, None, 3.0] + [None] * 9}],
           'used_rows': 2}
    out = action_chat._trim_result(big)
    assert 'series' not in out and 'labels' not in out
    assert out['label_count'] == 12
    assert out['first_label'] == '2026-01' and out['last_label'] == '2026-12'
    assert out['series_summary'] == [
        {'name': 'A', 'points_with_data': 2, 'min': 1.0, 'max': 3.0,
         'first': 1.0, 'last': 3.0}]


def test_trim_result_survives_junk():
    assert action_chat._trim_result(None) == {}
    assert action_chat._trim_result('boom') == {}


# --------------------------------------------------------------------------- #
# Provider envelope parsing                                                   #
# --------------------------------------------------------------------------- #

def test_tool_arguments_arrive_as_a_json_string():
    message = {'content': None, 'tool_calls': [
        {'function': {'name': 'stat',
                      'arguments': '{"field": "ph", "agg": "mean"}'}}]}
    assert llm._tool_call_of(message) == {
        'name': 'stat', 'args': {'field': 'ph', 'agg': 'mean'}}


def test_mangled_tool_arguments_degrade_to_empty_not_an_exception():
    """Validation then rejects it with something the repair attempt can use."""
    message = {'tool_calls': [
        {'function': {'name': 'stat', 'arguments': '{not json'}}]}
    assert llm._tool_call_of(message) == {'name': 'stat', 'args': {}}


def test_no_tool_call_reads_as_none():
    assert llm._tool_call_of({'content': 'hello'}) is None
    assert llm._tool_call_of({'tool_calls': []}) is None
    assert llm._tool_call_of({'tool_calls': [{'function': {}}]}) is None


def test_complete_refuses_without_a_key(monkeypatch):
    monkeypatch.setattr(llm, 'api_key', lambda: '')
    assert llm.is_configured() is False
    with pytest.raises(llm.LlmDisabled):
        llm.complete([{'role': 'user', 'content': 'hi'}])
