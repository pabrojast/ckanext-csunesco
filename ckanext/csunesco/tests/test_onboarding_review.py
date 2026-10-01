"""Private review, narrow PM permissions and organization approval regression tests."""
from types import SimpleNamespace as NS
import pytest
import ckan.model as model
import ckan.plugins.toolkit as tk
from ckanext.csunesco import db
from ckanext.csunesco.logic import auth, onboarding
from ckanext.csunesco.logic.action import registration
from ckanext.csunesco.tests.test_manager_approval import session, harness, _manager_profile, _ctx


def test_private_projection_excludes_tokens_and_passwords(harness, session):
    user, _ = harness
    profile = _manager_profile(session)
    profile.motivation = 'Help monitor rivers in my community.'
    profile.verification_token = 'never-public'
    profile.language = 'es'
    result = onboarding.profile_dict(user, profile)
    assert result['motivation'] == profile.motivation
    assert result['language'] == 'es'
    assert not {'verification_token','password','reset_key','token_created'}.intersection(result)


def test_backend_actor_cannot_borrow_service_sysadmin(monkeypatch):
    user = NS(id='real-id', name='reviewer', state='active', sysadmin=False)
    monkeypatch.setattr(model.User, 'get', lambda key: user)
    monkeypatch.setattr(auth, '_is_sysadmin', lambda ctx: ctx.get('user') == 'service')
    context = onboarding.review_context({'user':'service', 'ignore_auth':True}, {'actor_username':'reviewer','actor_id':'real-id'})
    assert context['user'] == 'reviewer' and 'ignore_auth' not in context
    assert not auth._is_sysadmin(context)
    with pytest.raises(tk.NotAuthorized):
        onboarding.review_context({'user':'service'}, {'actor_username':'reviewer','actor_id':'wrong-id'})
    with pytest.raises(tk.NotAuthorized):
        onboarding.review_context({'user':'outsider'}, {'actor_username':'reviewer','actor_id':'real-id'})


def test_dossier_requires_project_membership_and_scoped_reviewer(monkeypatch):
    user = NS(id='applicant', name='applicant')
    monkeypatch.setattr(model.User, 'get', lambda key: user)
    monkeypatch.setattr(auth, '_is_sysadmin', lambda ctx: False)
    monkeypatch.setattr(auth, '_user_obj', lambda ctx: NS(id='reviewer'))
    monkeypatch.setattr(db, 'get_project', lambda key: NS(id='project'))
    monkeypatch.setattr(db, 'project_member', lambda *a: None)
    monkeypatch.setattr(auth, '_is_project_admin', lambda *a: True)
    with pytest.raises(tk.NotAuthorized):
        onboarding.csunesco_registration_review_show({'user':'reviewer'}, {'id':'applicant','project_id':'project'})
    monkeypatch.setattr(db, 'project_member', lambda *a: NS(status='pending'))
    monkeypatch.setattr(onboarding, 'profile_dict', lambda u: {'username': u.name})
    assert onboarding.csunesco_registration_review_show({'user':'reviewer'}, {'id':'applicant','project_id':'project'}) == {'username':'applicant'}


@pytest.mark.parametrize('decision,verified,role,allowed', [('approved',True,'member',True),('approved',True,None,False),('rejected',True,'member',False),(None,True,'member',False),('approved',False,'member',False)])
def test_member_can_only_propose_after_pm_approval(monkeypatch, decision, verified, role, allowed):
    import ckan.authz
    user = NS(id='user', name='member', state='active')
    org = NS(id='org', state='active')
    profile = NS(profile_type='manager', manager_decision=decision, email_verified=verified, org_id='org')
    monkeypatch.setattr(auth, '_is_org_editor', lambda *a: False)
    monkeypatch.setattr(auth, '_is_sysadmin', lambda *a: False)
    monkeypatch.setattr(auth, '_user_obj', lambda ctx: user)
    monkeypatch.setattr(db, 'get_citizen_scientist', lambda key: profile)
    monkeypatch.setattr(model.Group, 'get', lambda key: org if key == 'org' else NS(id='other', state='active'))
    monkeypatch.setattr(ckan.authz, 'users_role_for_group_or_org', lambda *a: role)
    assert auth.can_propose_for_org({'user':'member'}, 'org') is allowed
    assert not auth.can_propose_for_org({'user':'member'}, 'other')
    assert not auth.can_manage_content_scope({'user':'member'}, None, 'org')


@pytest.mark.parametrize('role', ['member','admin'])
def test_existing_org_approval_preserves_selected_role(harness, session, role):
    _, calls = harness
    profile = _manager_profile(session, org_id='existing-org')
    profile.org_role = role
    result = registration.csunesco_manager_approve(_ctx(), {'username':'paula'})
    assert result['org_role'] == role
    assert dict(calls)['organization_member_create']['role'] == role


def test_new_org_resolution_never_implicitly_grants_admin(harness, session, monkeypatch):
    _, calls = harness
    _manager_profile(session, org_name_requested='Existing Lab')
    org = NS(id='org-existing', name='existing-lab', title='Existing Lab', state='active', is_organization=True)
    monkeypatch.setattr(model.Group, 'get', lambda key: org)
    with pytest.raises(tk.ValidationError):
        registration.csunesco_manager_approve(_ctx(), {'username':'paula','organization_id':'existing-lab'})
    result = registration.csunesco_manager_approve(_ctx(), {'username':'paula','organization_id':'existing-lab','organization_role':'member'})
    assert result['org_id'] == 'org-existing' and result['org_role'] == 'member'
    assert 'organization_create' not in dict(calls)


def test_duplicate_organization_blocks_approval(harness, session, monkeypatch):
    user, calls = harness
    _manager_profile(session, org_name_requested='Hydrology Lab')
    monkeypatch.setattr(onboarding, 'exact_org_match', lambda title: True)
    with pytest.raises(tk.ValidationError):
        registration.csunesco_manager_approve(_ctx(), {'username':'paula'})
    assert not user.activated and not calls


def test_reject_approved_does_not_revoke_account(harness, session):
    _, calls = harness
    profile = _manager_profile(session, org_id='existing')
    profile.manager_decision = 'approved'; session.commit()
    with pytest.raises(tk.ValidationError):
        registration.csunesco_manager_reject(_ctx(), {'username':'paula'})
    assert profile.manager_decision == 'approved' and not calls


def test_logo_upload_uses_existing_storage_and_rolls_back(monkeypatch, tmp_path):
    from ckanext.csunesco.tests.test_uploads import _factory, FakeUploader, DummyFile
    upload = FakeUploader('qa-logo.png', str(tmp_path))
    _factory(monkeypatch, [upload])
    monkeypatch.setitem(tk.config, 'ckan.storage_path', str(tmp_path))
    url, batch = onboarding.store_org_logo(DummyFile('logo.png'))
    from pathlib import Path
    assert url == '/uploads/csunesco/qa-logo.png' and Path(upload.filepath).exists()
    batch.rollback()
    assert not Path(upload.filepath).exists()


def test_fake_logo_rejected_before_storage(monkeypatch):
    from ckanext.csunesco.tests.test_uploads import DummyFile
    from ckanext.csunesco.logic import uploads
    monkeypatch.setattr(tk, '_', lambda text: text)
    monkeypatch.setattr(uploads, 'uploads_enabled', lambda: True)
    with pytest.raises(tk.ValidationError):
        onboarding.store_org_logo(DummyFile('fake.png', b'<script>bad</script>'))


def test_approval_note_persists_in_private_history(harness, session):
    _manager_profile(session, org_id='existing')
    result = registration.csunesco_manager_approve(_ctx(), {'username':'paula', 'reason':'Affiliation confirmed.'})
    assert result['manager_review_reason'] == 'Affiliation confirmed.'


def test_rejection_retry_does_not_notify_twice(harness, session):
    _, calls = harness
    _manager_profile(session, org_id='existing')
    for _ in range(2):
        registration.csunesco_manager_reject(_ctx(), {'username':'paula', 'reason':'Please confirm affiliation.'})
    assert len([call for call in calls if call[0] == 'email']) == 1
