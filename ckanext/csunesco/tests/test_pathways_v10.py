"""Registration v10: independent role requests, scoped approval and invitation privacy."""
import datetime
from types import SimpleNamespace as NS
import pytest
import ckan.model as model
import ckan.plugins.toolkit as tk
from ckanext.csunesco import db
from ckanext.csunesco.logic import pathways as p, auth, notify
from ckanext.csunesco.tests.test_manager_approval import session

@pytest.fixture
def setup(session, monkeypatch):
    users={name:NS(id=name,name=name,fullname=name,email=name+'@example.invalid',state='active',sysadmin=name=='admin') for name in ('citizen','pm','admin','outsider')}
    monkeypatch.setattr(model.User,'get',lambda key: users.get(key))
    monkeypatch.setattr(auth,'_user_obj',lambda ctx: users.get(ctx.get('user')))
    monkeypatch.setattr(auth,'_is_sysadmin',lambda ctx:ctx.get('user')=='admin')
    monkeypatch.setattr(auth,'_is_project_admin',lambda ctx,pid:ctx.get('user')=='pm')
    monkeypatch.setattr(auth,'_is_project_initiative_admin',lambda *a:False)
    monkeypatch.setattr(notify,'notify_join_decision',lambda *a,**kw:None)
    monkeypatch.setattr(p,'notify_request',lambda *a:None)
    project=db.CsProject(id='project',slug='river',title='River',short_description='Observe the river',status='approved',created_by='pm',discovery_visibility='public')
    session.add(project)
    for name in users:
        session.add(db.CsCitizenScientist(user_id=name,profile_type='citizen',email_verified=True, date_of_birth=datetime.date(1990,1,1),nationality='CL',gender='female',language='es',terms_accepted_at=datetime.datetime.utcnow()))
    session.commit()
    return project,users

def request(role='citizen',**kw):
    return p.csunesco_pathway_request({'user':'citizen'},dict(project_slug='river',requested_role=role,note='I want to observe the river in my community.',**kw))

def test_cs_approval_does_not_grant_organization(session,setup):
    row=request()
    p.csunesco_pathway_decide({'user':'pm'},{'id':row['id'],'decision':'approve'})
    assert db.project_member('project','citizen').role=='scientist'
    assert session.query(model.Member).count()==0
    assert p.csunesco_pathway_decide({'user':'pm'},{'id':row['id'],'decision':'approve'})['status']=='approved'

def test_pm_request_preserves_cs_and_requires_global_approval(session,setup):
    profile=db.get_citizen_scientist('citizen');profile.profile_type='manager';session.add(db.CsProjectMember(project_id='project',user_id='citizen',role='scientist',status='active'));session.commit()
    row=request('project_manager')
    assert row['review_state']=='waiting_for_account_approval'
    assert db.project_member('project','citizen').role=='scientist'
    with pytest.raises(tk.NotAuthorized):p.csunesco_pathway_decide({'user':'pm'},{'id':row['id'],'decision':'approve'})
    with pytest.raises(tk.ValidationError):p.csunesco_pathway_decide({'user':'admin'},{'id':row['id'],'decision':'approve'})
    profile.manager_decision='approved';profile.org_id='institution';session.commit()
    p.csunesco_pathway_decide({'user':'admin'},{'id':row['id'],'decision':'approve'})
    assert db.project_member('project','citizen').role=='admin'

def test_pending_cs_cannot_downgrade_new_pm(session,setup):
    row=request();member=db.project_member('project','citizen');member.role='admin';member.status='active';session.commit()
    p.csunesco_pathway_decide({'user':'pm'},{'id':row['id'],'decision':'approve'})
    assert db.project_member('project','citizen').role=='admin'

def test_rejection_cancels_only_pm_work_keeps_cs_and_drafts(session,setup):
    profile=db.get_citizen_scientist('citizen');profile.profile_type='manager';session.add(db.CsProjectMember(project_id='project',user_id='citizen',role='scientist',status='active'));session.commit()
    row=request('project_manager')
    draft=db.CsProject(slug='draft',title='My draft',status='pending',created_by='citizen',extras='{"manager_registration_user_id":"citizen"}')
    session.add(draft);session.commit();p.cancel_manager_work('citizen','Please correct the organization');session.commit()
    assert session.query(db.CsJoinRequest).get(row['id']).status=='cancelled'
    assert db.project_member('project','citizen').status=='active'
    assert draft.status=='draft' and draft.title=='My draft'

def test_unlisted_invitation_is_summary_only_revocable_and_private(session,setup):
    token=p.csunesco_pathway_invitation({'user':'pm'},{'project_slug':'river','visibility':'unlisted','join_policy':'invitation'})['invitation']
    assert not p.csunesco_pathway_projects({'user':'citizen'},{})
    assert session.query(db.CsProject).filter(db.public_project_clause()).count()==0
    with pytest.raises(tk.NotAuthorized):request()
    rows=p.csunesco_pathway_projects({'user':'citizen'},{'invitation':token})
    assert rows[0]['invited'] and not rows[0]['role']
    assert not any(k in rows[0] for k in ('members','organization_id','invitation'))
    assert db.project_member('project','citizen') is None
    assert token==p.csunesco_pathway_invitation({'user':'pm'},{'project_slug':'river'})['invitation']
    p.csunesco_pathway_invitation({'user':'pm'},{'project_slug':'river','rotate':True})
    with pytest.raises(tk.ObjectNotFound):p.csunesco_pathway_projects({'user':'citizen'},{'invitation':token})
    with pytest.raises(tk.NotAuthorized):request(invitation=token)

@pytest.mark.parametrize('note',['',' '*40,'short','x'*501])
def test_motivation_validated_server_side(session,setup,note):
    with pytest.raises(tk.ValidationError):p.csunesco_pathway_request({'user':'citizen'},{'project_slug':'river','note':note})
    assert session.query(db.CsJoinRequest).count()==0

def test_repeat_cancel_retry_keeps_history_and_scope(session,setup):
    first=request(); assert request()['id']==first['id']
    with pytest.raises(tk.NotAuthorized):p.csunesco_pathway_decide({'user':'outsider'},{'id':first['id'],'decision':'approve'})
    p.csunesco_pathway_decide({'user':'citizen'},{'id':first['id'],'decision':'cancel'})
    second=request();assert second['id']==first['id']
    assert [event['event'] for event in second['history']]==['submitted','cancelled','submitted']
    assert not p.csunesco_pathway_requests({'user':'outsider'},{})

def test_legacy_adoption_is_idempotent(session,setup):
    session.add(db.CsProjectMember(project_id='project',user_id='citizen',role='scientist',status='pending',note='Original legacy note'));session.commit()
    p.adopt_legacy();session.commit();p.adopt_legacy();session.commit()
    assert session.query(db.CsJoinRequest).count()==1
    assert session.query(db.CsJoinRequest).one().note=='Original legacy note'
