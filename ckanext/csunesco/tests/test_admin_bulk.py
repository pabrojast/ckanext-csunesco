# encoding: utf-8
"""Bulk-approval error reporting -- CKAN imports, no site, no database.

What these pin down is the contract a reviewer depends on: a batch never dies
on one bad row, the reasons that come back are actionable and deduplicated, and
an unexpected failure is logged with its row id **without** its message
reaching the screen.
"""
import logging

import pytest

try:
    import ckan.plugins.toolkit as tk
    from ckanext.csunesco.logic import views_admin
    HAVE_CKAN = True
except Exception:
    HAVE_CKAN = False

pytestmark = pytest.mark.skipif(
    not HAVE_CKAN, reason='requires CKAN (ckan.plugins.toolkit)')


@pytest.fixture
def no_request(monkeypatch):
    """``_bulk_approve`` builds a context from ``tk.g``; there is no request."""
    monkeypatch.setattr(views_admin, '_context', lambda: {'user': 'reviewer'})


def _actions(outcomes):
    """A ``get_action`` stand-in driven by ``{row_id: exception or None}``."""
    calls = []

    def get_action(name):
        def run(context, data_dict):
            row_id = data_dict['id']
            calls.append((name, row_id))
            outcome = outcomes.get(row_id)
            if outcome is not None:
                raise outcome
            return {'id': row_id}
        return run
    get_action.calls = calls
    return get_action


# --------------------------------------------------------------------------- #
# The happy path                                                              #
# --------------------------------------------------------------------------- #

def test_every_row_approved_reports_no_reasons(no_request, monkeypatch):
    monkeypatch.setattr(tk, 'get_action', _actions({}))
    approved, reasons = views_admin._bulk_approve('csunesco_content_approve',
                                                  ['a', 'b', 'c'])
    assert approved == 3
    assert reasons == []


# --------------------------------------------------------------------------- #
# One bad row must not cost the batch                                         #
# --------------------------------------------------------------------------- #

def test_a_failing_row_does_not_abort_the_others(no_request, monkeypatch):
    get_action = _actions({'b': tk.ObjectNotFound('gone')})
    monkeypatch.setattr(tk, 'get_action', get_action)
    approved, reasons = views_admin._bulk_approve('csunesco_content_approve',
                                                  ['a', 'b', 'c'])
    assert approved == 2
    assert len(reasons) == 1
    # Every row was still attempted -- the loop did not stop at 'b'.
    assert [row for _name, row in get_action.calls] == ['a', 'b', 'c']


def test_an_unauthorized_row_does_not_cost_the_rest(no_request, monkeypatch):
    monkeypatch.setattr(tk, 'get_action',
                        _actions({'a': tk.NotAuthorized('nope')}))
    approved, reasons = views_admin._bulk_approve('csunesco_data_source_approve',
                                                  ['a', 'b'])
    assert approved == 1
    assert 'authorized' in reasons[0]


# --------------------------------------------------------------------------- #
# The reasons a reviewer can act on                                           #
# --------------------------------------------------------------------------- #

def test_validation_messages_are_surfaced_verbatim(no_request, monkeypatch):
    """The actions phrase these for the reviewer; collapsing them into
    "something went wrong" just makes them press the button again."""
    error = tk.ValidationError({'owner_org': ['No organization was resolved.']})
    monkeypatch.setattr(tk, 'get_action', _actions({'a': error}))
    _approved, reasons = views_admin._bulk_approve(
        'csunesco_data_source_approve', ['a'])
    assert reasons == ['No organization was resolved.']


def test_identical_causes_are_reported_once(no_request, monkeypatch):
    """Twenty rows failing for one missing config setting is ONE thing to fix."""
    error = tk.ValidationError({'owner_org': ['No organization was resolved.']})
    monkeypatch.setattr(tk, 'get_action',
                        _actions({row: error for row in 'abcde'}))
    approved, reasons = views_admin._bulk_approve(
        'csunesco_data_source_approve', list('abcde'))
    assert approved == 0
    assert reasons == ['No organization was resolved.']


def test_distinct_causes_are_all_collected(no_request, monkeypatch):
    monkeypatch.setattr(tk, 'get_action', _actions({
        'a': tk.ValidationError({'owner_org': ['No organization.']}),
        'b': tk.ValidationError({'title': ['Title is required.']}),
    }))
    _approved, reasons = views_admin._bulk_approve(
        'csunesco_data_source_approve', ['a', 'b'])
    assert reasons == ['No organization.', 'Title is required.']


# --------------------------------------------------------------------------- #
# An unexpected failure: findable in the log, opaque on screen                #
# --------------------------------------------------------------------------- #

def test_unexpected_errors_never_leak_their_message(no_request, monkeypatch):
    boom = RuntimeError('connection to secret-host:5432 refused')
    monkeypatch.setattr(tk, 'get_action', _actions({'a': boom}))
    _approved, reasons = views_admin._bulk_approve(
        'csunesco_content_approve', ['a'])
    assert reasons == [tk._(views_admin.GENERIC_ERROR)]
    assert 'secret-host' not in ' '.join(reasons)


def test_every_failure_is_logged_with_its_row_id(no_request, monkeypatch,
                                                 caplog):
    """The reviewer reports a count; the sysadmin needs the row."""
    monkeypatch.setattr(tk, 'get_action', _actions({
        'row-42': RuntimeError('boom'),
        'row-7': tk.ValidationError({'owner_org': ['No organization.']}),
    }))
    with caplog.at_level(logging.WARNING):
        views_admin._bulk_approve('csunesco_data_source_approve',
                                  ['row-42', 'row-7', 'ok'])
    logged = caplog.text
    assert 'row-42' in logged and 'RuntimeError' in logged
    assert 'row-7' in logged and 'No organization.' in logged
    # The successful row is not noise in the log.
    assert "'ok'" not in logged


# --------------------------------------------------------------------------- #
# The flash the reviewer actually reads                                       #
# --------------------------------------------------------------------------- #

def test_bulk_error_message_appends_the_causes():
    out = views_admin._bulk_error_message('2 failed.', ['No organization.'])
    assert out == '2 failed. No organization.'


def test_bulk_error_message_without_causes_is_just_the_summary():
    assert views_admin._bulk_error_message('2 failed.', []) == '2 failed.'


def test_bulk_error_message_caps_the_causes_it_prints():
    reasons = ['Cause %d.' % i for i in range(views_admin.MAX_BULK_REASONS + 2)]
    out = views_admin._bulk_error_message('9 failed.', reasons)
    assert 'Cause 0.' in out
    assert reasons[-1] not in out
    # It says so rather than silently truncating.
    assert 'other problems' in out


def test_validation_messages_flattens_strings_and_lists():
    error = tk.ValidationError({'a': 'one', 'b': ['two', 'three']})
    assert sorted(views_admin._validation_messages(error)) == \
        ['one', 'three', 'two']


def test_validation_messages_survives_an_empty_error():
    assert views_admin._validation_messages(tk.ValidationError({})) == []
    assert views_admin._validation_messages(RuntimeError('x')) == []
