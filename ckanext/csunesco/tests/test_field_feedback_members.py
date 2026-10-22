"""Project approvals confirm organization membership in the same transaction."""
from types import SimpleNamespace

import pytest
import ckan.model as model
import ckan.plugins.toolkit as tk
from ckanext.csunesco import db
from ckanext.csunesco.logic import auth, notify
from ckanext.csunesco.logic.action import members
from ckanext.csunesco.tests.test_join_requests import session, project


@pytest.fixture
def review(session, project, monkeypatch):
    user = SimpleNamespace(id='applicant', name='applicant', fullname='Applicant', state='active')
    org = model.Group(name='project-organization', title='Project Organization', type='organization', is_organization=True)
    org.state = 'active'
    session.add(org); session.flush()
    project.organization_id = org.id
    profile = db.CsCitizenScientist(); profile.user_id = user.id; profile.email_verified = True
    request = db.CsProjectMember(); request.project_id = project.id; request.user_id = user.id; request.status = 'pending'; request.role = 'member'
    session.add_all([project, profile, request]); session.commit()
    monkeypatch.setattr(model.Member, 'related_packages', lambda self: [])
    calls = []; emails = []
    monkeypatch.setattr(model.User, 'get', staticmethod(lambda key: user if key in (user.id, user.name) else None))
    monkeypatch.setattr(tk, 'check_access', lambda *a, **k: True)
    monkeypatch.setattr(auth, '_is_sysadmin', lambda context: False)
    monkeypatch.setattr(auth, 'decider_role', lambda *args: 'project_manager')
    monkeypatch.setattr(notify, 'notify_join_decision', lambda *a, **k: emails.append((a, k)))
    def core_action(name):
        assert name == 'organization_member_create'
        def create(context, data):
            assert context['defer_commit'] is True
            assert data == {'id': org.id, 'username': user.name, 'role': 'member'}
            calls.append(data)
            session.add(model.Member(group=org, table_name='user', table_id=user.id, state='active', capacity='member'))
        return create
    monkeypatch.setattr(tk, 'get_action', core_action)
    context = {'user': 'reviewer', 'auth_user_obj': SimpleNamespace(id='reviewer', is_anonymous=False)}
    return user, org, request, profile, calls, emails, context


def test_join_grants_organization_membership_once_and_retries_without_duplicate_history(session, project, review):
    user, org, request, _, calls, emails, context = review
    payload = {'project_id': project.id, 'user_id': user.id}
    first = members.csunesco_join_approve(context, payload)
    again = members.csunesco_join_approve(context, payload)
    assert first['membership']['status'] == again['membership']['status'] == 'active'
    assert first['membership']['history'] == again['membership']['history']
    assert again['citizen_scientists'] == 1
    assert len(calls) == len(emails) == 1
    assert session.query(model.Member).filter_by(group_id=org.id, table_id=user.id).one().capacity == 'member'


def test_join_preserves_existing_organization_admin(session, project, review):
    user, org, _, _, calls, _, context = review
    session.add(model.Member(group=org, table_name='user', table_id=user.id, state='active', capacity='admin')); session.commit()
    members.csunesco_join_approve(context, {'project_id': project.id, 'user_id': user.id})
    assert not calls
    assert session.query(model.Member).filter_by(group_id=org.id, table_id=user.id).one().capacity == 'admin'


def test_failed_organization_grant_leaves_join_pending(session, project, review, monkeypatch):
    user, org, request, _, _, emails, context = review
    monkeypatch.setattr(tk, 'get_action', lambda name: lambda *a: None)  # malformed success
    with pytest.raises(tk.ValidationError, match='not confirmed'):
        members.csunesco_join_approve(context, {'project_id': project.id, 'user_id': user.id})
    session.rollback()
    assert db.project_member(project.id, user.id).status == 'pending'
    assert not session.query(model.Member).filter_by(group_id=org.id, table_id=user.id).first()
    assert not emails


def test_verified_citizen_can_be_assigned_pm_without_global_pm_approval(session, project, review):
    user, _, _, profile, _, _, context = review
    assert profile.manager_decision is None
    existing = db.CsProjectMember(); existing.project_id = project.id; existing.user_id = 'original-manager'; existing.role = 'admin'; existing.status = 'active'
    session.add(existing); session.commit()
    payload = {'project_slug': project.slug, 'username': user.name, 'replace': False}
    candidate = members.csunesco_project_manager_lookup(context, payload)
    assert candidate['project_id'] == project.id and candidate['email_verified'] is True
    first = members.csunesco_project_manager_set(context, payload)
    again = members.csunesco_project_manager_set(context, payload)
    assert first['history'] == again['history']
    assert set(db.project_admin_user_ids(project.id)) == {user.id, 'original-manager'}


def test_unverified_account_cannot_be_assigned_pm(session, project, review):
    user, org, _, profile, calls, _, context = review
    profile.email_verified = False; session.add(profile); session.commit()
    with pytest.raises(tk.ValidationError, match='Verify'):
        members.csunesco_project_manager_set(context, {'project_id': project.id, 'username': user.name})
    assert not calls and db.project_member(project.id, user.id).status == 'pending'
