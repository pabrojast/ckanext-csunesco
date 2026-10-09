"""Completion of an existing CKAN identity and independent PM/project gates."""
import datetime
from types import SimpleNamespace
import pytest
import ckan.model as model
import ckan.plugins.toolkit as tk
from ckanext.csunesco import db
from ckanext.csunesco.logic import registration_profile as profiles, registration, auth
from ckanext.csunesco.logic.action import projects, members
from ckanext.csunesco.tests.test_manager_approval import session, harness

BODY = dict(fullname='Synthetic Participant', date_of_birth='1990-01-01', nationality='CL',
            gender='prefer_not_to_say', language='fr', motivation='I want to monitor our local river.', terms_accepted=True)

@pytest.fixture
def identity(harness, monkeypatch):
    user, calls = harness
    user.state='active'; user.sysadmin=False; user.password='unchanged'; user.is_anonymous=False
    monkeypatch.setattr(registration,'_lock_manager_account',lambda uid:user)
    monkeypatch.setattr(tk,'_',lambda text:text)
    return user, {'user':user.name,'auth_user_obj':user}


def test_read_is_non_creating_and_update_requires_consent(session,identity):
    user,ctx=identity
    assert not profiles.csunesco_registration_profile_show(ctx,{})['profile_exists']
    assert session.query(db.CsCitizenScientist).count()==0
    with pytest.raises(tk.ValidationError): profiles.require_complete(user)
    with pytest.raises(tk.ValidationError): profiles.csunesco_registration_profile_update(ctx,{**BODY,'terms_accepted':False})
    assert session.query(db.CsCitizenScientist).count()==0
    out=profiles.csunesco_registration_profile_update(ctx,BODY)
    assert out['profile_complete'] and out['email_verified']
    profile=db.get_citizen_scientist(user.id); stamp=profile.terms_accepted_at
    profiles.csunesco_registration_profile_update(ctx,{**BODY,'terms_accepted':False})
    assert session.query(db.CsCitizenScientist).count()==1
    assert profile.terms_accepted_at==stamp
    assert user.password=='unchanged' and user.state=='active' and not user.sysadmin


@pytest.mark.parametrize('extra',[{'id':'other'},{'email':'other@test'},{'manager_decision':'approved'},{'profile_type':'manager'}])
def test_identity_and_privileged_fields_cannot_change(identity,extra):
    _,ctx=identity
    with pytest.raises((tk.ValidationError,tk.NotAuthorized)):
        profiles.csunesco_registration_profile_update(ctx,{**BODY,**extra})


@pytest.mark.parametrize('new_org',[False,True])
def test_pending_verified_manager_can_propose_but_not_publish(session,identity,monkeypatch,new_org):
    user,ctx=identity
    profiles.csunesco_registration_profile_update(ctx,BODY)
    profile=db.get_citizen_scientist(user.id)
    profile.profile_type='manager';profile.org_id=None if new_org else 'org';profile.org_name_requested='New organization' if new_org else None
    session.commit()
    org=SimpleNamespace(id='org',name='org',state='active',is_organization=True)
    monkeypatch.setattr(model.Group,'get',lambda key:org if key=='org' else None)
    monkeypatch.setattr(auth,'_is_org_editor',lambda *a:False)
    from ckanext.csunesco.logic import approval_events
    monkeypatch.setattr(approval_events,'record',lambda *a:None)
    assert auth.can_propose_project(ctx)
    assert auth.csunesco_project_request_create(ctx,{'organization_id':profile.org_id})['success']
    payload={'title':'Synthetic pending proposal'}
    if not new_org:payload['organization_id']='org'
    out=projects.csunesco_project_request_create(ctx,payload)
    saved=db.get_project(out['id'])
    assert saved.status=='pending' and saved.organization_id==profile.org_id
    with pytest.raises(tk.ValidationError) as exc:projects.csunesco_project_approve(ctx,{'id':saved.id})
    assert 'manager_approval_required' in exc.value.error_dict
    assert saved.status=='pending'
    profile.manager_decision='approved';profile.org_id='org';session.commit()
    monkeypatch.setattr(projects,'_send_project_decision_email',lambda *a,**kw:None,raising=False)
    # Approval binds the approved organization, not the unreviewed free-text name.
    projects.csunesco_project_approve(ctx,{'id':saved.id})
    assert saved.status=='approved' and saved.organization_id=='org'


def test_rejected_manager_cannot_submit_via_service(session,identity,monkeypatch):
    user,ctx=identity
    profiles.csunesco_registration_profile_update(ctx,BODY)
    profile=db.get_citizen_scientist(user.id);profile.profile_type='manager';profile.manager_decision='rejected';session.commit()
    monkeypatch.setattr(auth,'_is_sysadmin',lambda *a:True)
    monkeypatch.setattr(auth,'_is_org_editor',lambda *a:False)
    with pytest.raises(tk.ValidationError):projects.csunesco_project_request_create(ctx,{'title':'Rejected proposal','requested_by':user.name})
    assert session.query(db.CsProject).count()==0


def test_existing_pending_verified_account_requires_new_link(identity):
    user,_=identity;user.state='pending'
    profile=SimpleNamespace(profile_type='manager',email_verified=True,manager_decision=None)
    assert profiles.needs_verification_link(user,profile)
    profile.manager_decision='rejected'
    assert not profiles.needs_verification_link(user,profile)
    user.state='deleted';profile.manager_decision=None
    assert not profiles.needs_verification_link(user,profile)


def test_revocation_is_receipted_and_protects_last_pm(session,identity,monkeypatch):
    from ckanext.csunesco.logic import portal
    from ckanext.csunesco import constants as C
    user,ctx=identity
    monkeypatch.setattr(portal,'require_service',lambda context:None)
    project=db.CsProject(slug='synthetic-removal',title='Synthetic removal',status='approved',created_by=user.id)
    session.add(project);session.flush()
    member=db.CsProjectMember(project_id=project.id,user_id=user.id,role=C.MEMBER_ROLE_PM,status=C.MEMBER_STATUS_ACTIVE)
    session.add(member);session.commit()
    payload={'project_slug':project.slug,'id':user.id,'username':user.name,'actor_id':'admin-id','actor_username':'synthetic-admin',
             'actor_role':'platform_admin','toolbox_actor_id':99,'delivery_id':'e'*64}
    with pytest.raises(tk.ValidationError) as exc:members.csunesco_project_member_remove(ctx,payload)
    assert 'last_owner' in exc.value.error_dict
    session.rollback()
    session.add(db.CsProjectMember(project_id=project.id,user_id='another-pm',role=C.MEMBER_ROLE_PM,status=C.MEMBER_STATUS_ACTIVE));session.commit()
    first=members.csunesco_project_member_remove(ctx,payload)
    assert first['status']=='removed' and member.status==C.MEMBER_STATUS_REJECTED
    assert members.csunesco_project_member_remove(ctx,payload)==first
    assert user.state=='active'
    assert session.query(db.CsProjectMember).count()==2


def test_new_join_without_profile_is_blocked(session,identity):
    user,ctx=identity
    project=db.CsProject(slug='join-test',title='Synthetic join',status='approved')
    session.add(project);session.commit()
    with pytest.raises(tk.ValidationError) as exc:
        members.csunesco_join_request_create(ctx,{'project_id':project.id})
    assert 'registration_profile_required' in exc.value.error_dict
    assert session.query(db.CsProjectMember).count()==0


@pytest.mark.parametrize('path',['/citizen-science/project/new','/fr/citizen-science/project/river','/quh/citizen-science/project/river?tab=data'])
def test_completion_returns_to_same_local_context(monkeypatch,path):
    monkeypatch.setattr(tk,'url_for',lambda *a,**kw:'/citizen-science/')
    assert profiles.safe_next(path)==path


@pytest.mark.parametrize('path',['https://outside.test','//outside.test/path','/user/reset','/fr/citizen-science/profile/complete','/citizen-science/\\evil'])
def test_completion_rejects_external_or_recursive_return(monkeypatch,path):
    monkeypatch.setattr(tk,'url_for',lambda *a,**kw:'/citizen-science/')
    assert profiles.safe_next(path)=='/citizen-science/'


@pytest.fixture
def colab_source(monkeypatch):
    from ckanext.csunesco.logic import colab_profile
    from ckanext.colab.lib import registration_details
    monkeypatch.setattr(colab_profile.plugins, 'plugin_loaded', lambda name: name == 'colab')
    source = dict(fullname='Colab Name', date_of_birth=datetime.date(1990, 5, 18),
                  nationality='Chile', gender='preferNotToSay', user_role='admin',
                  terms_accepted_at='2026-01-01', language='fr')
    monkeypatch.setattr(registration_details, 'registration_details', lambda user: source)
    return source


def test_colab_prefills_self_service_without_creating_profile_or_consent(session, identity, colab_source):
    user, ctx = identity
    out = profiles.csunesco_registration_profile_show(ctx, {})
    assert out['fullname'] == user.fullname
    assert out['date_of_birth'] == '1990-05-18'
    assert out['nationality'] == 'CL' and out['gender'] == 'prefer_not_to_say'
    assert out['colab_prefilled_fields'] == ['date_of_birth', 'nationality', 'gender']
    assert not out['profile_complete'] and not out['can_propose_project']
    assert out['terms_accepted_at'] is None and out['language'] is None
    assert session.query(db.CsCitizenScientist).count() == 0
    with pytest.raises(tk.ValidationError):
        profiles.require_complete(user)
    saved = profiles.csunesco_registration_profile_update(ctx, {**BODY, 'date_of_birth':out['date_of_birth'], 'nationality':out['nationality'], 'gender':out['gender']})
    assert saved['profile_complete'] and saved['terms_accepted_at']
    assert session.query(db.CsCitizenScientist).count() == 1
    assert not user.sysadmin


def test_colab_does_not_replace_saved_cs_details(session, identity, colab_source):
    user, ctx = identity
    profiles.csunesco_registration_profile_update(ctx, BODY)
    out = profiles.csunesco_registration_profile_show(ctx, {})
    assert out['fullname'] == BODY['fullname']
    assert out['date_of_birth'] == BODY['date_of_birth']
    assert out['colab_prefilled_fields'] == []


@pytest.mark.parametrize('nationality,expected', [('cl','CL'), ('Francia','FR'), ('France','FR'), ('Canada','CA'), ('PREFER_NOT_TO_SAY','PREFER_NOT_TO_SAY'), ('French',None), ('Atlantis',None)])
def test_colab_nationality_preserves_free_text_when_not_a_country_code(identity,colab_source,nationality,expected):
    _, ctx = identity
    colab_source['nationality'] = nationality
    out = profiles.csunesco_registration_profile_show(ctx, {})
    if expected:
        assert out['nationality'] == expected
    else:
        assert out['nationality'] is None
        assert out['colab_unmapped_fields']['nationality'] == nationality


@pytest.mark.parametrize('gender,expected', [('woman','female'), ('man','male'), ('non-binary','non_binary'), ('preferNotToSay','prefer_not_to_say'), ('Custom identity',None)])
def test_colab_gender_normalization_and_unmapped_text(identity,colab_source,gender,expected):
    _, ctx = identity
    colab_source['gender'] = gender
    out = profiles.csunesco_registration_profile_show(ctx, {})
    assert out['gender'] == expected
    if expected is None:
        assert out['colab_unmapped_fields']['gender'] == gender


def test_colab_only_fills_valid_missing_values(identity, colab_source):
    user, ctx = identity
    user.fullname = ''
    colab_source['date_of_birth'] = 'not a date'
    out = profiles.csunesco_registration_profile_show(ctx, {})
    assert out['fullname'] == 'Colab Name'
    assert out['date_of_birth'] is None
    colab_source['date_of_birth'] = '2999-01-01'
    assert profiles.csunesco_registration_profile_show(ctx, {})['date_of_birth'] is None


def test_colab_prefill_still_rejects_another_identity(identity,colab_source):
    _, ctx = identity
    with pytest.raises(tk.NotAuthorized):
        profiles.csunesco_registration_profile_show(ctx, {'id':'another-user'})
