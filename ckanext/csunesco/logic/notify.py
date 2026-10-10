# encoding: utf-8
"""Best-effort email notifications for moderation decisions.

The spec's flows end with a person waiting on a decision (project proposal,
join request, manager account) and, historically, nothing told them the
decision happened -- the only mail this plugin ever sent was the verification
link. Every helper here is BEST-EFFORT: a mailer failure is logged and
swallowed, because a notification must never roll back or fail the decision
it reports.
"""
import logging

import ckan.plugins.toolkit as tk

log = logging.getLogger(__name__)


def notify_user(user_id, subject, body, body_html=None):
    """Email ``user_id`` (a CKAN user id or name). Returns True on success."""
    try:
        import ckan.model as model
        from ckan.lib.mailer import mail_recipient
    except ImportError:
        log.warning('csunesco: mailer unavailable; notification skipped')
        return False
    try:
        user = model.User.get(user_id)
        if user is None or not getattr(user, 'email', None):
            return False
        from ckanext.csunesco.logic.email_templates import mail_headers
        mail_recipient(user.fullname or user.name, user.email, subject, body,
                       body_html=body_html, headers=mail_headers())
        return True
    except Exception as e:
        # CKAN's mailer leaks raw smtplib errors; best-effort means catching
        # everything (same rationale as the verification mail).
        log.warning('csunesco: notification email failed: %s',
                    type(e).__name__)
        return False


def _recipient_language(user_id):
    """Use the applicant's saved language, never the reviewer's request locale."""
    from ckanext.csunesco.logic.verification_copy import language_code
    language = None
    try:
        import ckan.model as model
        from ckanext.csunesco import db
        user = model.User.get(user_id)
        if user is not None:
            language = getattr(user, 'language', None)
            profile = db.get_citizen_scientist(user.id)
            language = getattr(profile, 'language', None) or language
    except Exception as e:
        log.warning('csunesco: notification language unavailable: %s',
                    type(e).__name__)
    return language_code(language)


def _notify_decision(user_id, kind, approved, *, project_title='', reason=None,
                     project_slug=None):
    from ckanext.csunesco.logic.decision_copy import COPY
    language = _recipient_language(user_id)
    copy = COPY[language]
    prefix = kind + ('_approved' if approved else '_rejected')
    subject = copy[prefix + '_subject'].format(project=project_title)
    body = copy[prefix + '_body'].format(project=project_title)
    if not approved:
        note = str(reason or '').strip()
        body += '\n\n' + (copy['reviewer_note'].format(reason=note)
                            if note else copy['reason_not_provided'])

    project_url = None
    if kind == 'project' and approved and project_slug:
        try:
            project_url = tk.url_for('csunesco.project_landing',
                                     slug=project_slug, _external=True)
            body += '\n\n' + project_url
        except Exception as e:
            log.warning('csunesco: project email URL unavailable: %s',
                        type(e).__name__)

    body_html = None
    try:
        from ckanext.csunesco.logic.email_templates import render_notification
        body_html = render_notification(
            subject=subject, message=body,
            cta_label=copy['project_cta'], cta_url=project_url,
            footer_note=copy[kind + '_footer'], language=language,
            brand_name=copy['brand'], support_label=copy['support'],
        )
    except Exception as e:
        log.warning('csunesco: decision email HTML unavailable: %s',
                    type(e).__name__)
    return notify_user(user_id, subject, body, body_html=body_html)


def notify_join_decision(user_id, project_title, approved, reason=None):
    return _notify_decision(user_id, 'join', approved,
                            project_title=project_title, reason=reason)


def notify_project_decision(user_id, project_title, approved, reason=None,
                            project_slug=None):
    return _notify_decision(user_id, 'project', approved,
                            project_title=project_title, reason=reason,
                            project_slug=project_slug)


def notify_manager_decision(user_id, approved, reason=None):
    return _notify_decision(user_id, 'manager', approved, reason=reason)
