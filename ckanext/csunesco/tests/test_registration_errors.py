from types import SimpleNamespace
import pytest
from sqlalchemy.exc import IntegrityError
import ckan.plugins.toolkit as tk
from ckanext.csunesco.logic import registration_errors as errors
from ckanext.csunesco.logic.registration_error_copy import COPY


@pytest.mark.parametrize('constraint,expected', [('idx_only_one_active_email','registration_account_conflict'),('user_name_key','registration_username_taken'),('other',None)])
def test_only_known_unique_constraints_are_conflicts(constraint,expected):
    error=IntegrityError('secret SQL',{'password':'secret'},SimpleNamespace(pgcode='23505',diag=SimpleNamespace(constraint_name=constraint)))
    result=errors.integrity_problem(error)
    assert (result['code'] if result else None)==expected
    assert 'secret' not in str(result)


def test_unknown_errors_and_structured_messages_are_sanitized():
    result=errors.from_validation(tk.ValidationError({'code':'registration_account_conflict','message':'private SQL','fields':{'email':'private email','password_hash':'secret'}}))
    assert result['fields']=={'email':'registration_account_conflict'}
    assert 'private' not in str(result) and 'secret' not in str(result)
    result=errors.from_validation(tk.ValidationError({'email':['The email address belongs to a registered user.']}))
    assert result['code']=='registration_account_conflict'


@pytest.mark.parametrize('lang',list(COPY))
def test_selected_language_and_no_invented_retry_time(lang):
    from flask import Flask
    with Flask(__name__).test_request_context('/?lng='+lang):
        assert errors.problem('account_conflict')['message']==COPY[lang]['account_conflict']
        assert errors.problem('too_many_attempts')['message']==COPY[lang]['rate_limit_unknown']
        message=errors.problem('too_many_attempts',retry_seconds=61)['message']
        assert '2' in message and '{{' not in message
    assert set(COPY[lang])==set(COPY['en'])


def test_verification_lookup_failure_allows_retry(monkeypatch):
    from flask import Flask
    from ckanext.csunesco import db
    from ckanext.csunesco.logic import registration
    def unavailable(token):
        raise RuntimeError('private database details')
    monkeypatch.setattr(db, 'get_citizen_scientist_by_token', unavailable)
    monkeypatch.setattr(registration, '_render_verify', lambda state: state)
    with Flask(__name__).test_request_context('/verify/valid-token'):
        assert registration.verify_citizen('valid-token') == 'error'
