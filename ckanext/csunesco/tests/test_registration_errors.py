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


@pytest.mark.parametrize('language,translated', [
    ('fr', "Ce nom d'utilisateur n'est pas disponible."),
    ('es', 'Ese nombre de usuario no está disponible.'),
    ('ar', 'اسم المستخدم غير متاح.')])
def test_translated_core_username_conflicts_keep_their_meaning(monkeypatch, language, translated):
    from flask import Flask
    app = Flask(__name__)
    app.extensions['babel'] = object()
    monkeypatch.setattr(tk, '_', lambda text: translated)
    with app.test_request_context('/?lng=' + language):
        result = errors.from_validation(tk.ValidationError({'name': [translated]}))
    assert result['code'] == 'registration_username_taken'
    assert result['fields'] == {'username': 'registration_username_taken'}
    assert result['message'] == COPY[language]['username_taken']


def test_translated_core_email_conflict_does_not_echo_the_address(monkeypatch):
    from flask import Flask
    app = Flask(__name__)
    app.extensions['babel'] = object()
    template = "L'adresse '{email}' appartient à un utilisateur inscrit."
    monkeypatch.setattr(tk, '_', lambda text: template)
    with app.test_request_context('/?lng=fr'):
        result = errors.from_validation(tk.ValidationError({'email': [template.format(email='private@example.invalid')]}))
    assert result['code'] == 'registration_account_conflict'
    assert 'private@example.invalid' not in str(result)


def test_legacy_terms_field_uses_the_public_field_contract():
    assert errors.problem('terms_required', 'terms')['fields'] == {'terms_accepted': 'registration_terms_required'}


def test_resend_recovery_normalizes_toolbox_pm_destination():
    from flask import Flask
    from ckanext.csunesco.logic import registration
    with Flask(__name__).test_request_context('/verify/resend?next=%2Fregister-project-manager&lng=fr'):
        assert registration._registration_next() == '/citizen-science/register/project-manager'
