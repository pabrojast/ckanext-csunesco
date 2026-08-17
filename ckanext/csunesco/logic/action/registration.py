# encoding: utf-8
"""Server-to-server registration actions.

Increment 9: a SYSADMIN-only API action that lets the ofform backend register a
Citizen Scientist account CKAN-first and idempotently. It reuses the core
``create_citizen_scientist`` flow (``logic/registration.py``) so the web view and
the API share a single implementation.

Idempotency: if a CKAN user with the requested name already exists AND already
carries a ``cs_citizen_scientist`` profile, a previous (possibly retried)
registration already succeeded -- we return success with ``existed=True`` instead
of raising, so retries are safe. Otherwise we create the account. Every failure
is collapsed into a single generic error (no account enumeration).

Manager approval: Project Manager accounts are double-gated (email
verification, then a sysadmin decision). ``csunesco_manager_approve`` is the
step that activates the account AND materializes the declared organization --
creating it when the manager asked for a new one, then adding them as a member
with the derived capacity (new org -> admin, existing org -> editor).
``csunesco_manager_reject`` records the decline and leaves the account pending
(never deleted: the decision is reversible and the email stays reachable).
"""
import datetime
import logging
import re

import ckan.plugins.toolkit as tk
import ckan.model as model

from ckanext.csunesco import db
from ckanext.csunesco.logic.action import current_user_id
from ckanext.csunesco.logic.registration import (
    create_citizen_scientist,
    GENERIC_ERROR,
)

log = logging.getLogger(__name__)


def csunesco_register_citizen_scientist(context, data_dict):
    """Register a Citizen Scientist account (server-to-server, idempotent)."""
    tk.check_access('csunesco_register_citizen_scientist', context, data_dict)
    data_dict = data_dict or {}

    email = (data_dict.get('email') or '').strip()
    username = (data_dict.get('username') or '').lower().strip()
    fullname = (data_dict.get('fullname') or '').strip()
    password = data_dict.get('password') or ''
    country = (data_dict.get('country') or '').strip()
    date_of_birth = data_dict.get('date_of_birth')
    nationality = (data_dict.get('nationality') or '').strip()
    gender = (data_dict.get('gender') or '').strip()
    try:
        terms_accepted = tk.asbool(data_dict.get('terms_accepted', False))
    except (TypeError, ValueError):
        terms_accepted = False

    # IDEMPOTENT fast-path: an existing CKAN user that already carries a CS
    # profile means a previous registration succeeded. Return success WITHOUT
    # touching anything -- never raise, never re-create the account.
    existing_user = model.User.get(username) if username else None
    if existing_user is not None:
        db.ensure_mappers()
        profile = (
            model.Session.query(db.CsCitizenScientist)
            .filter(db.CsCitizenScientist.user_id == existing_user.id)
            .first()
        )
        if profile is not None:
            return {
                'status': 'success',
                'username': existing_user.name,
                'id': existing_user.id,
                'existed': True,
            }

    try:
        new_user = create_citizen_scientist(context, {
            'email': email,
            'username': username,
            'fullname': fullname,
            'password': password,
            'country': country,
            'date_of_birth': date_of_birth,
            'nationality': nationality,
            'gender': gender,
            # Optional for this trusted action: ofform already enforces terms
            # before sending its legacy payload, which must remain unchanged.
            'terms_accepted': terms_accepted,
        })
    except tk.ValidationError:
        # Collapse to a single generic error (no account enumeration).
        raise tk.ValidationError({'message': GENERIC_ERROR})

    return {
        'status': 'success',
        'username': new_user['name'],
        'id': new_user['id'],
        'existed': False,
    }


def _resolve_manager_profile(data_dict):
    """``(user, profile)`` for a manager decision, from ``username`` or ``id``.

    Raises ObjectNotFound/ValidationError with SPECIFIC messages: these are
    sysadmin-only actions, so the anti-enumeration discipline of the public
    registration surface does not apply -- a reviewer needs to know what is
    wrong.
    """
    key = ((data_dict or {}).get('username')
           or (data_dict or {}).get('id') or '').strip()
    if not key:
        raise tk.ValidationError({'username': [tk._('Missing value')]})
    user = model.User.get(key)
    if user is None:
        raise tk.ObjectNotFound(tk._('User not found'))
    db.ensure_mappers()
    profile = (
        model.Session.query(db.CsCitizenScientist)
        .filter(db.CsCitizenScientist.user_id == user.id)
        .first()
    )
    if profile is None or profile.profile_type != 'manager':
        raise tk.ObjectNotFound(
            tk._('No Project Manager registration found for this user'))
    return user, profile


def _org_slug(title):
    """An available CKAN group slug derived from an organization title."""
    base = re.sub(r'[^a-z0-9_-]+', '-', (title or '').lower()).strip('-_')
    base = re.sub(r'-{2,}', '-', base)[:80] or 'organization'
    candidate = base
    for suffix in range(2, 200):
        if model.Group.get(candidate) is None:
            return candidate
        candidate = '%s-%d' % (base, suffix)
    return '%s-%d' % (base, datetime.datetime.utcnow().microsecond)


def _profile_dictize(user, profile):
    return {
        'user_id': user.id,
        'username': user.name,
        'fullname': user.fullname,
        'email': user.email,
        'profile_type': profile.profile_type,
        'org_id': profile.org_id,
        'org_name_requested': profile.org_name_requested,
        'org_type': profile.org_type,
        'org_title': profile.org_title,
        'org_role': profile.org_role,
        'email_verified': bool(profile.email_verified),
        'manager_decision': profile.manager_decision,
    }


def csunesco_manager_approve(context, data_dict):
    """Approve a pending Project Manager account (sysadmin-only).

    Activates the CKAN user, creates the requested organization when the
    manager asked for a new one, and adds them as an org member with the
    derived capacity. Idempotent on re-approve: an already-approved manager
    returns success without duplicating the membership.
    """
    tk.check_access('csunesco_manager_approve', context, data_dict)
    user, profile = _resolve_manager_profile(data_dict)

    if profile.manager_decision == 'approved':
        return dict(_profile_dictize(user, profile), existed=True)
    if not profile.email_verified:
        raise tk.ValidationError({'email_verified': [tk._(
            'The manager has not verified their email address yet')]})

    org_id = profile.org_id
    capacity = 'admin' if profile.org_name_requested else 'editor'
    # Materialize the organization intent BEFORE activating so a failure here
    # leaves the account pending and the action safely retryable.
    if profile.org_name_requested and not profile.org_id:
        org = tk.get_action('organization_create')(
            dict(context), {
                'name': _org_slug(profile.org_name_requested),
                'title': profile.org_name_requested,
                'extras': [
                    {'key': 'csunesco_org_type',
                     'value': profile.org_type or ''},
                ],
            })
        org_id = org['id']
    if org_id:
        tk.get_action('organization_member_create')(dict(context), {
            'id': org_id,
            'username': user.name,
            'role': capacity,
        })

    user.activate()
    profile.org_id = org_id
    profile.org_role = capacity
    profile.manager_decision = 'approved'
    profile.manager_reviewed_by = current_user_id(context)
    profile.manager_reviewed_at = datetime.datetime.utcnow()
    model.Session.commit()

    _send_decision_email(user, approved=True)
    return dict(_profile_dictize(user, profile), existed=False)


def csunesco_manager_reject(context, data_dict):
    """Decline a pending Project Manager account (sysadmin-only).

    The account stays CKAN-pending (it simply never gains login), the decision
    and reviewer are recorded, and the person is told by email. Nothing is
    deleted -- a wrong call can be reversed by approving afterwards.
    """
    tk.check_access('csunesco_manager_reject', context, data_dict)
    user, profile = _resolve_manager_profile(data_dict)

    profile.manager_decision = 'rejected'
    profile.manager_reviewed_by = current_user_id(context)
    profile.manager_reviewed_at = datetime.datetime.utcnow()
    model.Session.commit()

    _send_decision_email(user, approved=False,
                         reason=(data_dict or {}).get('reason'))
    return _profile_dictize(user, profile)


def _send_decision_email(user, approved, reason=None):
    """Best-effort notification of the manager decision (never raises)."""
    try:
        from ckan.lib.mailer import mail_recipient
    except ImportError:
        log.warning('csunesco: mailer unavailable; decision email skipped')
        return False
    if not getattr(user, 'email', None):
        return False
    if approved:
        subject = tk._('Your UNESCO Citizen Science account was approved')
        body = tk._(
            'Good news! Your Project Manager account has been approved.\n\n'
            'You can now log in and propose a citizen science project.')
    else:
        subject = tk._('About your UNESCO Citizen Science account')
        body = tk._(
            'Your Project Manager account request was not approved at this '
            'time.')
        if reason:
            body += '\n\n' + tk._('Reviewer note: {reason}').format(
                reason=reason)
    try:
        mail_recipient(user.fullname or user.name, user.email, subject, body)
        return True
    except Exception as e:
        log.warning('csunesco: manager decision email failed: %s',
                    type(e).__name__)
        return False


def get_actions():
    return {
        'csunesco_register_citizen_scientist': csunesco_register_citizen_scientist,
        'csunesco_manager_approve': csunesco_manager_approve,
        'csunesco_manager_reject': csunesco_manager_reject,
    }
