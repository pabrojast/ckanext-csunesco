"""The history of a hidden managed package is as hidden as the package."""
from types import SimpleNamespace

import pytest
import ckan.plugins.toolkit as tk

from ckanext.csunesco.logic import auth, managed_data, snapshots


def _toolbox(monkeypatch, confirms):
    def request(path, payload=None):
        if not confirms:
            raise ValueError('HTTP Error 404: Not Found')
        return {'access_level': 'restricted'}
    monkeypatch.setattr(snapshots, 'app_request', request)


@pytest.fixture
def packages(monkeypatch):
    source = SimpleNamespace()
    monkeypatch.setattr(managed_data, 'source_for', lambda package_id: source if package_id == 'managed' else None)
    monkeypatch.setattr(managed_data.db, 'data_source_dictize', lambda obj: {
        'partition_id': 9, 'policy_revision': 8, 'access_level': 'restricted'})
    known = {'managed': SimpleNamespace(id='managed'), 'managed-name': SimpleNamespace(id='managed'),
             'plain': SimpleNamespace(id='plain')}
    asked = []
    context = {'user': '', 'model': SimpleNamespace(Package=SimpleNamespace(get=known.get))}
    return context, asked, lambda ctx, data: asked.append(data.get('id')) or {'success': True}


def test_auth_refuses_a_hidden_managed_package_as_not_found(monkeypatch, packages):
    context, asked, next_auth = packages
    _toolbox(monkeypatch, confirms=False)
    for key in ('managed', 'managed-name'):
        with pytest.raises(tk.ObjectNotFound):
            managed_data.package_show_auth(next_auth, context, {'id': key})
    assert asked == []
    # A package the extension does not manage, and one that does not exist,
    # are left to CKAN.
    assert managed_data.package_show_auth(next_auth, context, {'id': 'plain'}) == {'success': True}
    assert managed_data.package_show_auth(next_auth, context, {'id': 'missing'}) == {'success': True}
    assert asked == ['plain', 'missing']


def test_auth_lets_a_confirmed_managed_package_through(monkeypatch, packages):
    context, asked, next_auth = packages
    _toolbox(monkeypatch, confirms=True)
    assert managed_data.package_show_auth(next_auth, context, {'id': 'managed'}) == {'success': True}
    assert asked == ['managed']


def test_auth_uses_the_package_of_the_context_when_no_id_is_given(monkeypatch, packages):
    context, asked, next_auth = packages
    _toolbox(monkeypatch, confirms=False)
    with pytest.raises(tk.ObjectNotFound):
        managed_data.package_show_auth(next_auth, dict(context, package=SimpleNamespace(id='managed')), {})


def test_the_partition_sync_is_not_checked(monkeypatch, packages):
    context, asked, next_auth = packages
    _toolbox(monkeypatch, confirms=False)
    assert managed_data.package_show_auth(next_auth, dict(context, _cs_partition_sync=True), {'id': 'managed'}) == {'success': True}


def test_the_auth_function_is_chained_open_to_anonymous_visitors_and_registered():
    # Without the anonymous flag CKAN would refuse every anonymous dataset read.
    assert managed_data.package_show_auth.auth_allow_anonymous_access is True
    assert managed_data.package_show_auth.chained_auth_function is True
    assert auth.get_auth_functions()['package_show'] is managed_data.package_show_auth


def _activities():
    return [{'id': 1, 'object_id': 'hidden', 'activity_type': 'changed package'},
            {'id': 2, 'object_id': 'shown', 'activity_type': 'new package'},
            {'id': 3, 'object_id': 'group', 'activity_type': 'changed group'},
            {'id': 4, 'object_id': 'plain', 'activity_type': 'deleted package'},
            {'id': 5, 'object_id': 'hidden', 'activity_type': 'new package'}]


def test_activity_lists_drop_the_items_of_hidden_packages(monkeypatch):
    monkeypatch.setattr(managed_data, '_managed_among', lambda ids: ids & {'hidden', 'shown'})
    monkeypatch.setattr(managed_data, 'hidden_package_ids', lambda: ['hidden'])
    result = managed_data.activity_list(lambda ctx, data: _activities(), {'user': ''}, {'id': 'org'})
    assert [item['id'] for item in result] == [2, 3, 4]
    # Internal readers keep the whole list.
    assert len(managed_data.activity_list(lambda ctx, data: _activities(), {'ignore_auth': True}, {})) == 5


def test_a_page_without_managed_packages_does_not_ask_the_toolbox(monkeypatch):
    monkeypatch.setattr(managed_data, '_managed_among', lambda ids: set())
    def never():
        raise AssertionError('the Toolbox was asked')
    monkeypatch.setattr(managed_data, 'hidden_package_ids', never)
    assert len(managed_data.activity_list(lambda ctx, data: _activities(), {'user': ''}, {})) == 5
    assert managed_data.activity_list(lambda ctx, data: [], {'user': ''}, {}) == []


def test_list_actions_are_chained_only_where_the_activity_plugin_runs(monkeypatch):
    monkeypatch.setitem(tk.config, 'ckan.plugins', 'csunesco datastore')
    assert not set(managed_data.ACTIVITY_LISTS) & set(managed_data.get_actions())
    monkeypatch.setitem(tk.config, 'ckan.plugins', 'activity csunesco')
    actions = managed_data.get_actions()
    assert all(actions[name] is managed_data.activity_list for name in managed_data.ACTIVITY_LISTS)
    assert managed_data.activity_list.chained_action is True
    assert managed_data.activity_list.side_effect_free is True
