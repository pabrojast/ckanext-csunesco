"""Capture actual notification calls without contacting an email provider."""
from types import SimpleNamespace
from email import message_from_string, policy
from html import unescape
from string import Formatter

import pytest
from flask import Flask
import ckan.model as model
import ckan.plugins.toolkit as tk
from ckan.lib import mailer

from ckanext.csunesco import constants, db
from ckanext.csunesco.logic import email_templates, notify, registration
from ckanext.csunesco.logic.decision_copy import COPY as DECISION_COPY

_REAL_MAIL_RECIPIENT = mailer.mail_recipient
DECISIONS = [kind + '_' + outcome for kind in ('project', 'join', 'manager')
             for outcome in ('approved', 'rejected')]


def send_kind(kind, *, reason='Try again', project='River'):
    if kind == 'verification':
        return registration._send_verification_email('Pablo', 'pablo@example.org', 'token')
    approved = kind.endswith('_approved')
    if kind.startswith('project_'):
        return notify.notify_project_decision('u1', project, approved,
                                              reason=reason, project_slug='river')
    if kind.startswith('join_'):
        return notify.notify_join_decision('u1', project, approved, reason=reason)
    from ckanext.csunesco.logic.action import registration as action
    return action._send_decision_email(model.User.get('u1'), approved, reason=reason)


@pytest.mark.parametrize('language', ['en', 'fr', 'es', 'pt', 'uk', 'ar', 'quh'])
def test_verification_language_overrides_request_for_all_parts(mail, language):
    from ckanext.csunesco.logic.verification_copy import COPY
    assert registration._send_verification_email('Person', 'person@example.org', 'localized', language=language)
    args, kwargs = mail[0]
    copy = COPY[language]
    assert args[2] == copy['subject']
    assert copy['body'].split('\n\n')[0] in args[3]
    document = kwargs['body_html']
    assert '<html lang="%s"' % language in document
    assert copy['cta'] in document
    assert copy['footer'] in document
    assert copy['support'] in document
    assert 'dir="rtl"' in document if language == 'ar' else 'dir="ltr"' in document
    assert 'localized' in args[3] and 'localized' in document


@pytest.mark.parametrize('language,expected', [(None, 'en'), ('other', 'en'), ('fr-FR', 'fr'), ('FR_fr', 'fr')])
def test_verification_normalizes_recipient_language(mail, language, expected):
    from ckanext.csunesco.logic.verification_copy import COPY
    assert registration._send_verification_email('Person', 'person@example.org', 'token', language=language)
    assert mail[0][0][2] == COPY[expected]['subject']


def test_trusted_resend_uses_stored_language(mail, monkeypatch):
    from ckanext.csunesco.logic import onboarding
    monkeypatch.setattr(onboarding.auth, '_is_sysadmin', lambda context: True)
    user = SimpleNamespace(id='u-fr', fullname='Person', name='person', email='person@example.org')
    monkeypatch.setattr(model.User, 'get', staticmethod(lambda key: user))
    monkeypatch.setattr(db, 'get_citizen_scientist', lambda user_id: SimpleNamespace(
        email_verified=False, token_created=None, language='fr'))
    monkeypatch.setattr(db, 'set_verification_token', lambda *args: None)
    assert onboarding.csunesco_registration_resend({}, {'username': 'person'}) == {'sent': True}
    assert mail[0][0][2] == 'Vérifiez votre compte de science citoyenne de l’UNESCO'


def test_toolbox_registration_uses_stored_language(mail, monkeypatch):
    from ckanext.csunesco.logic.action import registration as action
    monkeypatch.setattr(tk, 'check_access', lambda *args: None)
    monkeypatch.setattr(model.User, 'get', staticmethod(lambda key: None))
    monkeypatch.setattr(action, 'create_citizen_scientist', lambda *args, **kwargs: {'id': 'u-fr', 'name': 'person'})
    monkeypatch.setattr(db, 'get_citizen_scientist', lambda user_id: SimpleNamespace(language='fr'))
    result = action.csunesco_register_citizen_scientist({}, {
        'username': 'person', 'email': 'person@example.org', 'fullname': 'Person',
        'password': 'test-password', 'terms_accepted': True, 'language': 'fr',
        'require_email_verification': True, 'motivation': 'Help monitor water with my community.',
    })
    assert result['verification_pending'] is True
    assert mail[0][0][2] == 'Vérifiez votre compte de science citoyenne de l’UNESCO'


@pytest.fixture
def mail(monkeypatch):
    calls = []
    monkeypatch.setattr(tk, '_', lambda text: text)
    monkeypatch.setattr(tk, 'h', SimpleNamespace(
        lang=lambda: 'en',
        url_for_static=lambda path, qualified: 'https://portal.test/catalog' + path,
    ))

    def url_for(endpoint, **values):
        assert values['_external'] is True
        if endpoint == 'csunesco.verify_citizen':
            return 'https://portal.test/catalog/citizen-science/verify/' + values['token']
        assert endpoint == 'csunesco.project_landing'
        return 'https://portal.test/catalog/citizen-science/project/' + values['slug']

    monkeypatch.setattr(tk, 'url_for', url_for)
    monkeypatch.setattr(mailer, 'mail_recipient',
                        lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(model.User, 'get', staticmethod(lambda key: SimpleNamespace(
        id='u1', fullname='Pablo <Example>', name='pablo', email='pablo@example.org',
        language='en')))
    monkeypatch.setattr(db, 'get_citizen_scientist', lambda user_id: SimpleNamespace(language='en'))
    return calls


def test_verification_has_html_and_preserves_token_and_expiry(mail):
    assert registration._send_verification_email('Pablo', 'pablo@example.org', 'token-123')
    args, kwargs = mail[0]
    assert args[:3] == ('Pablo', 'pablo@example.org',
                        'Verify your UNESCO Citizen Science account')
    url = 'https://portal.test/catalog/citizen-science/verify/token-123'
    document = kwargs['body_html']
    assert document.startswith('<!DOCTYPE html>')
    assert url in args[3] and 'href="' + url + '"' in document
    assert '%s hours' % constants.VERIFICATION_TOKEN_TTL_HOURS in args[3]
    assert '%s hours' % constants.VERIFICATION_TOKEN_TTL_HOURS in document
    assert 'Verify my account' in document
    assert 'https://portal.test/catalog' + email_templates.LOGO_PATH in document
    assert 'mailto:ihp-wins@unesco.org' in document
    # Replies go to the UNESCO mailbox even though the From is the SMTP sender.
    assert kwargs['headers'] == {'Reply-To': 'ihp-wins@unesco.org'}


def test_project_approval_has_html_escaped_title_and_public_link(mail):
    title = 'TEST E2E "Pablo" & <River>'
    assert notify.notify_project_decision('u1', title, True, project_slug='river')
    args, kwargs = mail[0]
    document = kwargs['body_html']
    assert title in args[2] and title in args[3]
    assert '<River>' not in document
    assert '&lt;River&gt;' in document and '&amp;' in document
    url = 'https://portal.test/catalog/citizen-science/project/river'
    assert url in args[3] and 'href="' + url + '"' in document
    assert 'View project' in document
    assert 'You are its project manager.' in document


@pytest.mark.parametrize('language,direction', [('en', 'ltr'), ('ar', 'rtl')])
def test_renderer_escapes_every_field_and_sets_card_direction(mail, monkeypatch,
                                                            language, direction):
    monkeypatch.setattr(tk.h, 'lang', lambda: language)
    document = email_templates.render_notification(
        subject='<script>alert(1)</script>', message='A < B & C',
        cta_label='<Open>', cta_url='https://portal.test/?a=1&b="two"',
        footer_note='<footer>',
    )
    assert '<script>' not in document and '<footer>' not in document
    assert '&lt;script&gt;' in document and '&lt;Open&gt;' in document
    assert 'href="https://portal.test/?a=1&amp;b=&#34;two&#34;"' in document
    assert 'dir="%s" class="container"' % direction in document


def test_decision_emails_carry_the_reply_to(mail):
    assert notify.notify_project_decision('u1', 'River', True, project_slug='river')
    assert mail[0][1]['headers'] == {'Reply-To': 'ihp-wins@unesco.org'}


def test_configured_reply_to_and_support_mailbox_win(mail, monkeypatch):
    monkeypatch.setitem(tk.config, 'smtp.reply_to', 'replies@example.org')
    monkeypatch.setitem(tk.config, email_templates.SUPPORT_EMAIL_OPTION,
                        'help@example.org')
    assert registration._send_verification_email('Pablo', 'pablo@example.org', 'token-123')
    args, kwargs = mail[0]
    assert kwargs['headers'] == {'Reply-To': 'replies@example.org'}
    assert 'mailto:help@example.org' in kwargs['body_html']
    assert 'ihp-wins@unesco.org' not in kwargs['body_html']


def test_legacy_approval_without_slug_has_no_broken_button(mail):
    assert notify.notify_project_decision('u1', 'River', True)
    assert '<!DOCTYPE html>' in mail[0][1]['body_html']
    assert '>View project</a>' not in mail[0][1]['body_html']


def test_decision_catalog_has_complete_translations_and_matching_placeholders():
    assert set(DECISION_COPY) == {'en', 'es', 'fr', 'pt', 'uk', 'ar', 'quh'}
    reference = DECISION_COPY['en']
    def fields(value):
        return {name for _, name, _, _ in Formatter().parse(value) if name}
    for language, copy in DECISION_COPY.items():
        assert set(copy) == set(reference), language
        for key, value in copy.items():
            assert value.strip(), (language, key)
            assert fields(value) == fields(reference[key]), (language, key)
            if language != 'en':
                assert value != reference[key], (language, key)


@pytest.mark.parametrize('kind', DECISIONS)
@pytest.mark.parametrize('language', sorted(DECISION_COPY))
def test_decisions_use_recipient_language_for_all_parts(mail, monkeypatch, kind, language):
    # The reviewer/API request locale must not select the applicant's language.
    monkeypatch.setattr(tk.h, 'lang', lambda: 'fr' if language != 'fr' else 'ar')
    monkeypatch.setattr(db, 'get_citizen_scientist', lambda user_id: SimpleNamespace(language=language))
    assert send_kind(kind, project='Río & <River>')
    args, kwargs = mail[0]
    copy = DECISION_COPY[language]
    assert args[2] == copy[kind + '_subject'].format(project='Río & <River>')
    expected_body = copy[kind + '_body'].format(project='Río & <River>')
    assert expected_body in args[3]
    document = kwargs['body_html']
    for paragraph in expected_body.split('\n\n'):
        assert paragraph in unescape(document)
    assert '<!DOCTYPE html>' in document
    assert f'<html lang="{language}"' in document
    assert ('dir="rtl"' in document) is (language == 'ar')
    assert copy[kind.split('_')[0] + '_footer'] in unescape(document)
    assert copy['support'] in unescape(document) and copy['brand'] in document
    assert 'mailto:ihp-wins@unesco.org' in document
    assert email_templates.LOGO_PATH in document
    assert kwargs['headers'] == {'Reply-To': 'ihp-wins@unesco.org'}
    assert '<River>' not in document
    if kind.endswith('_rejected'):
        note = copy['reviewer_note'].format(reason='Try again')
        assert note in args[3] and note in unescape(document)
    if kind == 'project_approved':
        assert copy['project_cta'] in unescape(document)
    else:
        assert 'target="_blank"' not in document


@pytest.mark.parametrize('kind', ['project_rejected', 'join_rejected', 'manager_rejected'])
@pytest.mark.parametrize('language', sorted(DECISION_COPY))
def test_missing_reason_is_explicit_and_translated(mail, monkeypatch, kind, language):
    monkeypatch.setattr(db, 'get_citizen_scientist', lambda user_id: SimpleNamespace(language=language))
    assert send_kind(kind, reason=None)
    args, kwargs = mail[0]
    expected = DECISION_COPY[language]['reason_not_provided']
    assert expected in args[3] and expected in unescape(kwargs['body_html'])


@pytest.mark.parametrize('reason', ['', '  ', '\n\t\r\n'])
def test_blank_reason_is_not_a_reviewer_note(mail, reason):
    assert send_kind('project_rejected', reason=reason)
    args, kwargs = mail[0]
    assert 'No reason was provided.' in args[3]
    assert 'No reason was provided.' in kwargs['body_html']
    assert 'Reviewer note:' not in args[3]


@pytest.mark.parametrize('kind', ['project_rejected', 'join_rejected', 'manager_rejected'])
def test_reason_is_plain_text_and_preserves_line_breaks(mail, kind):
    reason = 'First <script>alert(1)</script> & "quote"\r\nSecond line\n\nLast {project}'
    assert send_kind(kind, reason=reason)
    args, kwargs = mail[0]
    assert reason in args[3]
    document = kwargs['body_html']
    assert '<script>' not in document
    assert '&lt;script&gt;' in document
    assert '"quote"<br>Second line' in unescape(document)
    assert 'Last {project}' in document
    assert 'No reason was provided.' not in args[3]


@pytest.mark.parametrize('profile_language,user_language,expected', [
    (' FR_fr ', 'es', 'fr'), ('pt-BR', 'en', 'pt'), ('quh', 'en', 'quh'),
    (None, 'uk', 'uk'), ('', 'es', 'es'), (None, None, 'en'), ('unknown', 'fr', 'en'),
])
def test_decision_language_normalization_and_fallback(mail, monkeypatch,
        profile_language, user_language, expected):
    user = model.User.get('u1')
    user.language = user_language
    monkeypatch.setattr(model.User, 'get', staticmethod(lambda key: user))
    monkeypatch.setattr(db, 'get_citizen_scientist', lambda user_id:
                        SimpleNamespace(language=profile_language) if profile_language is not None else None)
    assert send_kind('project_rejected')
    assert mail[0][0][2] == DECISION_COPY[expected]['project_rejected_subject'].format(project='River')


def test_language_lookup_failure_still_delivers(mail, monkeypatch):
    def fail(user_id):
        raise RuntimeError('profile unavailable')
    monkeypatch.setattr(db, 'get_citizen_scientist', fail)
    assert send_kind('project_rejected')
    assert mail[0][0][2] == 'About your project River'


@pytest.mark.parametrize('kind', DECISIONS)
@pytest.mark.parametrize('missing', ['user', 'email'])
def test_decisions_without_recipient_send_nothing(mail, monkeypatch, kind, missing):
    user = model.User.get('u1')
    user.email = None
    monkeypatch.setattr(model.User, 'get', staticmethod(lambda key: None if missing == 'user' else user))
    assert not send_kind(kind)
    assert mail == []


@pytest.mark.parametrize('kind', ['verification'] + DECISIONS)
def test_ckan_transport_builds_multipart_alternative(mail, monkeypatch, kind):
    messages = []
    monkeypatch.setattr(mailer, 'mail_recipient', _REAL_MAIL_RECIPIENT)
    monkeypatch.setattr(mailer.smtplib, 'SMTP', lambda host, port: SimpleNamespace(
        ehlo=lambda: None, quit=lambda: None,
        sendmail=lambda sender, recipients, message: messages.append(message),
    ))
    for key, value in {'smtp.server': 'localhost:1025', 'smtp.starttls': False,
                       'smtp.user': '', 'smtp.mail_from': 'sender@example.invalid',
                       'ckan.site_title': 'Citizen Science'}.items():
        monkeypatch.setitem(tk.config, key, value)
    assert send_kind(kind)
    message = message_from_string(messages[0], policy=policy.default)
    assert message.get_content_type() == 'multipart/alternative'
    assert message.get_body(preferencelist=('plain',)).get_content().strip()
    assert message.get_body(preferencelist=('html',)).get_content().startswith('<!DOCTYPE html>')


@pytest.mark.parametrize('kind', ['verification'] + DECISIONS)
def test_transport_failure_never_raises(mail, monkeypatch, kind):
    def fail(*args, **kwargs):
        raise RuntimeError('transport unavailable')

    monkeypatch.setattr(mailer, 'mail_recipient', fail)
    assert not send_kind(kind)


@pytest.mark.parametrize('kind', ['verification'] + DECISIONS)
def test_render_failure_preserves_plain_text_delivery(mail, monkeypatch, kind):
    def fail(**kwargs):
        raise RuntimeError('template unavailable')

    monkeypatch.setattr(email_templates, 'render_notification', fail)
    assert send_kind(kind)
    assert mail[0][0][3]
    assert mail[0][1]['body_html'] is None


@pytest.mark.parametrize('profile_type', ['citizen', 'manager'])
def test_resend_uses_branded_verification_and_fresh_token(mail, monkeypatch, profile_type):
    user = SimpleNamespace(id='u1', fullname='Pablo', name='pablo', state='pending', is_pending=lambda: True)
    query = SimpleNamespace(filter=lambda *args: SimpleNamespace(all=lambda: [user]))
    monkeypatch.setattr(model.Session, 'query', lambda *args: query)
    monkeypatch.setattr(db, 'get_citizen_scientist', lambda user_id:
                        SimpleNamespace(email_verified=False, profile_type=profile_type, language='fr'))
    tokens = []
    monkeypatch.setattr(db, 'set_verification_token', lambda *args: tokens.append(args))
    monkeypatch.setattr(registration.secrets, 'token_urlsafe', lambda size: 'fresh-token')
    monkeypatch.setattr(registration, '_registration_retry_after', lambda: None)
    monkeypatch.setattr(tk, 'render', lambda name, extra_vars: extra_vars)
    app = Flask(__name__)
    with app.test_request_context('/resend-verification', method='POST',
                                  data={'email': 'pablo@example.org'}):
        assert registration.resend_verification() == {'sent': True}
    assert tokens == [('u1', 'fresh-token')]
    assert mail[0][0][2] == 'Vérifiez votre compte de science citoyenne de l’UNESCO'
    assert '<html lang="fr"' in mail[0][1]['body_html']
    assert 'href="https://portal.test/catalog/citizen-science/verify/fresh-token"' in mail[0][1]['body_html']
