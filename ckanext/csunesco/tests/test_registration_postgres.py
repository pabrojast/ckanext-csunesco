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
    tables=[user_table, dashboard_table]+list(db._ALL_TABLES)
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
    finally:
        model.Session.remove()
        db.metadata.drop_all(engine,tables=list(reversed(tables)))
        engine.dispose()
