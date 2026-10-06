"""Capture actual notification calls without contacting an email provider."""
from types import SimpleNamespace
from email import message_from_string, policy

import pytest
from flask import Flask
import ckan.model as model
import ckan.plugins.toolkit as tk
from ckan.lib import mailer

from ckanext.csunesco import constants, db
from ckanext.csunesco.logic import email_templates, notify, registration

_REAL_MAIL_RECIPIENT = mailer.mail_recipient


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
        fullname='Pablo <Example>', name='pablo', email='pablo@example.org')))
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


def test_other_decisions_keep_their_existing_content(mail):
    assert notify.notify_project_decision('u1', 'River', False, reason='Try again')
    assert 'Reviewer note: Try again' in mail[0][0][3]
    assert mail[0][1]['body_html'] is None
    assert notify.notify_join_decision('u1', 'River', True)
    assert mail[1][1]['body_html'] is None


@pytest.mark.parametrize('kind', ['verification', 'project'])
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
    if kind == 'verification':
        assert registration._send_verification_email('Pablo', 'pablo@example.org', 'token')
    else:
        assert notify.notify_project_decision('u1', 'River', True, project_slug='river')
    message = message_from_string(messages[0], policy=policy.default)
    assert message.get_content_type() == 'multipart/alternative'
    assert message.get_body(preferencelist=('plain',)).get_content().strip()
    assert message.get_body(preferencelist=('html',)).get_content().startswith('<!DOCTYPE html>')


@pytest.mark.parametrize('kind', ['verification', 'project'])
def test_transport_failure_never_raises(mail, monkeypatch, kind):
    def fail(*args, **kwargs):
        raise RuntimeError('transport unavailable')

    monkeypatch.setattr(mailer, 'mail_recipient', fail)
    if kind == 'verification':
        assert not registration._send_verification_email('Pablo', 'pablo@example.org', 'token')
    else:
        assert not notify.notify_project_decision('u1', 'River', True, project_slug='river')


@pytest.mark.parametrize('kind', ['verification', 'project'])
def test_render_failure_preserves_plain_text_delivery(mail, monkeypatch, kind):
    def fail(**kwargs):
        raise RuntimeError('template unavailable')

    monkeypatch.setattr(email_templates, 'render_notification', fail)
    if kind == 'verification':
        assert registration._send_verification_email('Pablo', 'pablo@example.org', 'token')
    else:
        assert notify.notify_project_decision('u1', 'River', True, project_slug='river')
    assert mail[0][0][3]
    assert mail[0][1]['body_html'] is None


@pytest.mark.parametrize('profile_type', ['citizen', 'manager'])
def test_resend_uses_branded_verification_and_fresh_token(mail, monkeypatch, profile_type):
    user = SimpleNamespace(id='u1', fullname='Pablo', name='pablo', is_pending=lambda: True)
    query = SimpleNamespace(filter=lambda *args: SimpleNamespace(all=lambda: [user]))
    monkeypatch.setattr(model.Session, 'query', lambda *args: query)
    monkeypatch.setattr(db, 'get_citizen_scientist', lambda user_id:
                        SimpleNamespace(email_verified=False, profile_type=profile_type))
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
    assert 'href="https://portal.test/catalog/citizen-science/verify/fresh-token"' in mail[0][1]['body_html']
