# encoding: utf-8
"""Server-to-server registration actions.

Increment 9: a SYSADMIN-only API action that lets the ofform backend register a
Citizen Scientist account CKAN-first and idempotently. It reuses the core
``create_citizen_scientist`` flow (``logic/registration.py``) so the web view and
the API share a single implementation.

Idempotency: if a CKAN user with the requested name already exists AND already
carries a ``cs_citizen_scientist`` profile, a previous (possibly retried)
registration already succeeded -- we return success with ``existed=True`` instead
of raising when the email, password and active account match. Otherwise we
create the account or return a public, actionable validation code.

Manager approval: Project Manager accounts are double-gated (email
verification, then a sysadmin decision). ``csunesco_manager_approve`` is the
step that activates the account AND materializes the declared organization --
creating it when the manager asked for a new one, then adding them as a member
with the derived capacity (new org -> admin, existing org -> requested Member/Admin).
``csunesco_manager_reject`` records the decline and leaves the account pending
(never deleted: the decision is reversible and the email stays reachable).
"""
import datetime
import logging
import re
import secrets

import ckan.plugins.toolkit as tk
import ckan.model as model

from ckanext.csunesco import db
from ckanext.csunesco.logic.action import current_user_id
from ckanext.csunesco.logic.registration_errors import problem, from_validation
from ckanext.csunesco.logic.registration import (
    create_citizen_scientist,
)

log = logging.getLogger(__name__)


def csunesco_register_citizen_scientist(context, data_dict):
    """Register a Citizen Scientist account (server-to-server, idempotent)."""
    tk.check_access('csunesco_register_citizen_scientist', context, data_dict)
    data_dict = data_dict or {}
    verification_required = tk.asbool(data_dict.get('require_email_verification', False))
    motivation = str(data_dict.get('motivation') or '').strip()
    if verification_required and not 20 <= len(motivation) <= 500:
        raise tk.ValidationError(problem('motivation_invalid', 'motivation'))

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
    # profile must also match the credentials of the original active account.
    existing_user = model.User.get(username) if username else None
    if existing_user is not None:
        db.ensure_mappers()
        profile = (
            model.Session.query(db.CsCitizenScientist)
            .filter(db.CsCitizenScientist.user_id == existing_user.id)
            .first()
        )
        if profile is not None:
            if ((existing_user.state != 'active' and not (existing_user.state == 'pending'
                    and profile.profile_type == 'citizen' and profile.verification_token and not profile.email_verified))
                    or str(existing_user.email or '').lower() != email.lower()
                    or not existing_user.validate_password(password)):
                raise tk.ValidationError(problem('username_taken', 'username'))
            return {
                'status': 'success',
                'username': existing_user.name,
                'id': existing_user.id,
                'existed': True,
                'verification_pending': not profile.email_verified,
            }

    verification_token = secrets.token_urlsafe(32) if verification_required else None
    try:
        new_user = create_citizen_scientist(context, {
            'email': email,
            'username': username,
            'fullname': fullname,
            'password': password,
            'country': country,
            'motivation': motivation,
            'registration_project_slug': data_dict.get('project_slug'),
            'language': str(data_dict.get('language') or ''),
            'language_other': str(data_dict.get('language_other') or ''),
            'date_of_birth': date_of_birth,
            'nationality': nationality,
            'gender': gender,
            # Optional for this trusted action: ofform already enforces terms
            # before sending its legacy payload, which must remain unchanged.
            'terms_accepted': terms_accepted,
        }, verification_token=verification_token)
    except tk.ValidationError as exc:
        # Mantener los códigos públicos y ocultar detalles internos.
        raise tk.ValidationError(from_validation(exc))

    if verification_token:
        from ckanext.csunesco.logic.registration import _send_verification_email
        profile = db.get_citizen_scientist(new_user['id'])
        _send_verification_email(fullname or username, email, verification_token,
                                 language=getattr(profile, 'language', None))
    return {
        'verification_pending': bool(verification_token),
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
        .with_for_update()
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
    from ckanext.csunesco.logic.onboarding import profile_dict
    return profile_dict(user, profile)


def csunesco_manager_approve(context, data_dict):
    """Approve a pending Project Manager account (sysadmin-only).

    Activates the CKAN user, creates the requested organization when the
    manager asked for a new one, and adds them as an org member with the
    derived capacity. Idempotent on re-approve: an already-approved manager
    returns success without duplicating the membership.
    """
    from ckanext.csunesco.logic.onboarding import review_context
    context = review_context(context, data_dict or {})
    tk.check_access('csunesco_manager_approve', context, data_dict)
    user, profile = _resolve_manager_profile(data_dict)

    if profile.manager_decision == 'approved':
        return dict(_profile_dictize(user, profile), existed=True)
    existing_account = profile.manager_application_origin == 'existing_account'
    if existing_account and user.state != 'active':
        raise tk.ValidationError({'account': [tk._(
            'This account is no longer active. Manage the account before reviewing its PM request.')]})
    if not profile.email_verified:
        raise tk.ValidationError({'email_verified': [tk._(
            'The manager has not verified their email address yet')]})

    org_id = profile.org_id
    # A reviewer can explicitly resolve a new-organization request against an
    # existing organization, without implicitly granting its admin role.
    resolved = (data_dict or {}).get('organization_id')
    if resolved:
        org = model.Group.get(resolved)
        capacity = (data_dict or {}).get('organization_role')
        if not org or not org.is_organization or org.state != 'active' or capacity not in ('member', 'admin'):
            raise tk.ValidationError({'organization': ['Select an active organization and Member or Admin.']})
        org_id = org.id
    capacity = 'admin' if profile.org_name_requested else (profile.org_role or 'member')
    if resolved:
        capacity = data_dict['organization_role']
    if capacity not in ('member', 'admin', 'editor'):
        raise tk.ValidationError({'org_role': ['Invalid organization role']})
    # Materialize the organization intent BEFORE activating so a failure here
    # leaves the account pending and the action safely retryable.
    if profile.org_name_requested and not org_id:
        from ckanext.csunesco.logic.onboarding import exact_org_match, lock_org_creation
        lock_org_creation(profile.org_name_requested)
        if exact_org_match(profile.org_name_requested):
            raise tk.ValidationError({'organization': [tk._('An organization with this name now exists. Resolve the affiliation before approving.')]})
        org = tk.get_action('organization_create')(
            dict(context, defer_commit=True), {
                'description': profile.org_description or '',
                'image_url': profile.org_image_url or '',
                'name': _org_slug(profile.org_name_requested),
                'title': profile.org_name_requested,
                'extras': [
                    {'key': 'csunesco_org_type',
                     'value': profile.org_type or ''},
                ],
            })
        org_id = org['id']
    if org_id:
        tk.get_action('organization_member_create')(dict(context, defer_commit=True), {
            'id': org_id,
            'username': user.name,
            'role': capacity,
        })

    if not existing_account:
        user.activate()
    profile.org_id = org_id
    profile.org_role = capacity
    profile.manager_decision = 'approved'
    profile.manager_review_reason = str((data_dict or {}).get('reason') or '').strip()[:1000] or None
    profile.manager_reviewed_by = current_user_id(context)
    profile.manager_reviewed_at = datetime.datetime.utcnow()
    model.Session.commit()

    try:
        from ckanext.colab.controller import get_all_organizations_cached
        get_all_organizations_cached.cache_clear()
    except ImportError:
        pass
    _send_decision_email(user, approved=True)
    return dict(_profile_dictize(user, profile), existed=False)


def csunesco_manager_reject(context, data_dict):
    """Decline a pending Project Manager account (sysadmin-only).

    New registrations stay CKAN-pending; existing accounts retain their state.
    The decision and reviewer are recorded, and the person is told by email. Nothing is
    deleted -- a wrong call can be reversed by approving afterwards.
    """
    from ckanext.csunesco.logic.onboarding import review_context
    context = review_context(context, data_dict or {})
    tk.check_access('csunesco_manager_reject', context, data_dict)
    user, profile = _resolve_manager_profile(data_dict)

    if profile.manager_decision == 'approved':
        raise tk.ValidationError({'status': ['An approved account must be managed through account administration.']})
    reason = str((data_dict or {}).get('reason') or '').strip()[:1000]
    if profile.manager_decision == 'rejected' and (profile.manager_review_reason or '') == reason:
        return _profile_dictize(user, profile)
    profile.manager_decision = 'rejected'
    profile.manager_review_reason = reason
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
