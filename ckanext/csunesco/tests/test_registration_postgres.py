"""Run with REGISTRATION_TEST_DATABASE_URL pointing to a disposable PostgreSQL DB."""
import os
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
import ckan.model as model
import ckan.plugins.toolkit as tk
from ckan.logic.action import create as core_create
from ckan.model.user import user_table
from ckan.model.dashboard import dashboard_table
from ckanext.csunesco import db
from ckanext.csunesco.logic import registration


@pytest.mark.skipif(not os.environ.get('REGISTRATION_TEST_DATABASE_URL'), reason='requires isolated PostgreSQL')
def test_real_core_deferred_registration_duplicate_email_rolls_back(monkeypatch):
    engine=sa.create_engine(os.environ['REGISTRATION_TEST_DATABASE_URL'])
    # This is an explicit disposable test database, never the configured CKAN DB.
    assert engine.url.database == 'registration_test'
    tables=list(db.metadata.sorted_tables)
    db.ensure_mappers()
    db.metadata.create_all(engine,tables=tables)
    with engine.begin() as connection:
        connection.execute(sa.text('CREATE UNIQUE INDEX IF NOT EXISTS idx_only_one_active_email ON public."user" (email,state) WHERE state=\'active\''))
    model.Session.remove(); model.Session.configure(bind=engine)
    monkeypatch.setattr(registration,'check_access',lambda *args: None)
    monkeypatch.setattr(core_create,'_check_access',lambda *args: None)
    monkeypatch.setattr(core_create.uploader,'get_uploader',lambda *args: SimpleNamespace(update_data_dict=lambda *a:None,upload=lambda *a:None))
    monkeypatch.setattr(tk,'get_action',lambda name: core_create.user_create if name=='user_create' else None)
    monkeypatch.setattr(tk,'_',lambda text:text)
    try:
        existing=model.User(name='existing-pg-test',email='existing@example.invalid')
        model.Session.add(existing); model.Session.commit()
        with pytest.raises(tk.ValidationError) as error:
            registration.create_citizen_scientist({'model':model,'session':model.Session,'user':None},
                {'username':'duplicate-pg-test','email':existing.email,'password':'disposable-password'},
                verification_token='disposable-token')
        assert error.value.error_dict['code']=='registration_account_conflict'
        assert error.value.error_dict['fields']=={'email':'registration_account_conflict'}
        assert model.Session.query(model.User).filter_by(name='duplicate-pg-test').count()==0
        assert model.Session.query(model.User).count()==1
        assert model.Session.query(db.CsCitizenScientist).count()==0
        assert model.Session.query(model.Dashboard).count()==0
        # New public v10 signup exercises CKAN core, the pending state and the
        # plugin profile together, with no project request or motivation.
        from ckanext.csunesco.logic.action import registration as action
        monkeypatch.setattr(tk, 'check_access', lambda *a, **kw: None)
        monkeypatch.setattr(registration, '_send_verification_email', lambda *a, **kw: True)
        result = action.csunesco_register_citizen_scientist({'model':model,'session':model.Session,'user':None}, {
            'registration_version':10, 'username':'canonical-pg-test','email':'canonical@example.invalid',
            'password':'disposable-password','fullname':'Canonical Scientist','date_of_birth':'1990-01-01',
            'nationality':'CL','gender':'female','language':'es','terms_accepted':True,
            'next_path':'/join-project?invitation=example', 'project_id':'ignored'})
        user=model.User.get(result['id']);profile=db.get_citizen_scientist(user.id)
        assert result['verification_pending'] and user.state=='pending' and not profile.email_verified
        assert profile.profile_type=='citizen' and not profile.motivation
        assert profile.registration_return_path=='/join-project?invitation=example'
        assert model.Session.query(db.CsJoinRequest).count()==0
        assert model.Session.query(db.CsProjectMember).count()==0
        with pytest.raises(tk.ValidationError):
            action.csunesco_register_citizen_scientist({'model':model,'session':model.Session,'user':None}, {
                'registration_version':10,'profile_type':'manager','username':'canonical-pg-test',
                'email':'canonical@example.invalid','password':'disposable-password'})
        assert db.get_citizen_scientist(user.id).profile_type=='citizen'


    finally:
        model.Session.remove()
        db.metadata.drop_all(engine,tables=list(reversed(tables)))
        engine.dispose()
