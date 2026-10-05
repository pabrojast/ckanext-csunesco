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
        mail_recipient(user.fullname or user.name, user.email, subject, body,
                       body_html=body_html)
        return True
    except Exception as e:
        # CKAN's mailer leaks raw smtplib errors; best-effort means catching
        # everything (same rationale as the verification mail).
        log.warning('csunesco: notification email failed: %s',
                    type(e).__name__)
        return False


def notify_join_decision(user_id, project_title, approved):
    if approved:
        subject = tk._('You joined {project}').format(project=project_title)
        body = tk._(
            'Your request to join "{project}" was approved. You can now '
            'contribute through the Citizen Science Toolbox.'
        ).format(project=project_title)
    else:
        subject = tk._('About your request to join {project}').format(
            project=project_title)
        body = tk._(
            'Your request to join "{project}" was not approved this time.'
        ).format(project=project_title)
    return notify_user(user_id, subject, body)


def notify_project_decision(user_id, project_title, approved, reason=None,
                            project_slug=None):
    body_html = None
    if approved:
        subject = tk._('Your project {project} was approved').format(
            project=project_title)
        body = tk._(
            'Good news! "{project}" was approved and now has a public '
            'landing page on the Citizen Science Portal. You are its '
            'project manager.'
        ).format(project=project_title)
        project_url = None
        try:
            if project_slug:
                project_url = tk.url_for('csunesco.project_landing',
                                         slug=project_slug, _external=True)
                body += '\n\n' + project_url
            from ckanext.csunesco.logic.email_templates import render_notification
            body_html = render_notification(
                subject=subject, message=body,
                cta_label=tk._('View project'), cta_url=project_url,
                footer_note=tk._('You receive this email because you proposed a project.'),
            )
        except Exception as e:
            log.warning('csunesco: project email HTML unavailable: %s',
                        type(e).__name__)
    else:
        subject = tk._('About your project {project}').format(
            project=project_title)
        body = tk._(
            'Your project request "{project}" was not approved.'
        ).format(project=project_title)
        if reason:
            body += '\n\n' + tk._('Reviewer note: {reason}').format(
                reason=reason)
    return notify_user(user_id, subject, body, body_html=body_html)
