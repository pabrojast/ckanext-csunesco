# encoding: utf-8
"""CS membership (join) actions: request / approve / reject.

A join-request is modelled as ``cs_project_member.status`` (pending -> active/
rejected). The ``citizen_scientists`` counter reflects the number of ACTIVE
members, updated atomically on each state transition.
"""
import datetime
import re

import ckan.plugins.toolkit as tk
import ckan.model as model

from ckanext.csunesco import constants as C
from ckanext.csunesco import db
from ckanext.csunesco.logic.action import current_user_id


# Hard cap on the join note. Long enough for a real paragraph of motivation,
# short enough that a review table stays a review table.
MAX_NOTE_LENGTH = 1000


def _utcnow():
    return datetime.datetime.utcnow()


def _clean_note(value):
    """Plain text, tags stripped, length capped -- or ``None`` when empty.

    Stripped rather than allowlisted: the note is rendered autoescaped with
    ``white-space: pre-line``, so no markup is wanted and none is stored.
    Truncates instead of raising, matching how the project form treats its own
    optional free text -- losing a join request over an over-long paragraph
    would be a worse outcome than trimming it.
    """
    if not value:
        return None
    text = re.sub(r'<[^>]*>', '', str(value)).strip()
    return text[:MAX_NOTE_LENGTH] or None


def _resolve_project(data_dict):
    """Resolve the project from an id OR a slug, under any of its key names.

    Same idiom as ``action/content._resolve_project`` and
    ``action/data._resolve_project``. Joining was the odd one out: it read only
    ``project_id``/``id``, while the CS Toolbox app posts ``project_slug`` --
    so ``db.get_project(None)`` returned None and EVERY app-mediated join was
    rejected as "project not found", retried five times and left `failed` in
    the outbox with nothing surfaced to the person who clicked Join.
    """
    key = (data_dict.get('project_id') or data_dict.get('id')
           or data_dict.get('project') or data_dict.get('project_slug'))
    return db.get_project(key)


def _resolve_actor(context, data_dict):
    """Who the membership is FOR: the caller, or a named user a sysadmin acts for.

    ``current_user_id`` resolves the API token's own account, which is right
    for a person clicking Join on the portal and wrong for the CS Toolbox
    app: it posts through one sysadmin service token on behalf of whichever
    citizen scientist tapped the button, so every app join would otherwise be
    filed under the service account.

    Impersonation is therefore allowed ONLY for a sysadmin caller -- the same
    trusted-proxy shape content already uses for ``source='app'`` + ``author``.
    A non-sysadmin sending ``username`` is simply ignored, never honoured and
    never rejected, so an ordinary caller cannot probe for account existence.

    Returns ``(user_id, on_behalf)``. An unknown username is a hard error: the
    alternative is silently filing the request under the service account,
    which is exactly the confusion this exists to prevent.
    """
    from ckanext.csunesco.logic import auth as cs_auth
    username = (data_dict.get('username') or '').strip()
    if username and cs_auth._is_sysadmin(context):
        user = model.User.get(username)
        if user is None:
            raise tk.ValidationError({'username': [tk._(
                'Unknown user: %s') % username]})
        return user.id, True
    return current_user_id(context), False


def _resolve_decider(context, project_id, data_dict):
    """Who decides, as what, through which channel: ``(reviewer_id, role,
    via, actor_name)``.

    The CS Toolbox app approves/rejects through ONE sysadmin service token
    and names the real decider in ``decided_by_username`` + their role in
    ``decided_by_role``. Honoured ONLY for a sysadmin caller (the join's own
    trusted-proxy rule); an unknown username falls back to the token but the
    name still reaches the history, so the person is never lost. A portal
    click records the caller, with the role derived from the auth branch
    that let them in (``auth.decider_role``).
    """
    from ckanext.csunesco.logic import auth as cs_auth
    data_dict = data_dict or {}
    username = (data_dict.get('decided_by_username') or '').strip()
    if username and cs_auth._is_sysadmin(context):
        role = data_dict.get('decided_by_role')
        if role not in C.APPROVER_ROLES:
            role = None
        try:
            user = model.User.get(username)
        except Exception:
            user = None
        reviewer_id = user.id if user is not None else current_user_id(context)
        return reviewer_id, role, C.MEMBER_SOURCE_APP, username
    return (current_user_id(context),
            cs_auth.decider_role(context, project_id),
            C.MEMBER_SOURCE_PORTAL,
            context.get('user'))


def _participation_closed(project):
    """True when the project EXPLICITLY closed public participation.

    An absent ``open_participation`` extra is treated as open: the flag
    predates this gate, so legacy projects that never set it must stay
    joinable. Only a stored ``False`` (the manager unchecked the box) closes
    the project to new join requests.
    """
    extras = db._load_json(project.extras, {})
    return (isinstance(extras, dict)
            and extras.get('open_participation') is False)


def csunesco_join_request_create(context, data_dict):
    """Request to join an APPROVED project (idempotent for the acting user)."""
    if not context.get('user'):
        raise tk.NotAuthorized(
            tk._('You must be logged in to join a project'))
    tk.check_access('csunesco_join_request_create', context, data_dict)

    data_dict = data_dict or {}
    project = _resolve_project(data_dict)
    if project is None or project.status != 'approved':
        raise tk.ValidationError({'project_id': [tk._(
            'Project not found or not open for join requests')]})

    user_id, on_behalf = _resolve_actor(context, data_dict)

    # A project that explicitly closed public participation takes no new join
    # requests from the web. Sysadmin callers bypass the gate: that is the
    # trusted-proxy path (the CS Toolbox outbox and PM-invited joins), which
    # must keep working for invitation-based projects.
    from ckanext.csunesco.logic import auth as cs_auth
    if _participation_closed(project) and not cs_auth._is_sysadmin(context):
        raise tk.ValidationError({'project_id': [tk._(
            'Project not found or not open for join requests')]})
    note = _clean_note(data_dict.get('note'))
    # Provenance is DERIVED, never taken from the payload. It is rendered to a
    # reviewer as "via the CS Toolbox app", so letting a caller assert it would
    # let anyone dress their own request up as an app-mediated one -- and the
    # app does not send a `source` key at all, so trusting the payload also
    # meant the badge never appeared for the real thing.
    source = 'app' if on_behalf else 'ckan'

    # Idempotent upsert: an existing row is never an error. But it must not be
    # a black hole either -- the note used to be dropped here, so a rejected
    # applicant could write a fresh case for themselves, get a friendly
    # "already requested" notice and never learn their words went nowhere.
    existing = db.project_member(project.id, user_id)
    if existing is not None:
        reopened = False
        if existing.status == C.MEMBER_STATUS_REJECTED:
            # Let them ask again, the way a rejected PROJECT can be resubmitted.
            # Approving is still the reviewer's call; this only re-queues.
            # The previous decision STAYS on the row (reviewed_*) and in the
            # history: re-asking must not erase who said no, and when.
            existing.status = C.MEMBER_STATUS_PENDING
            db.append_member_event(
                existing, 'reopened', actor_id=user_id,
                actor_name=(data_dict.get('username') if on_behalf
                            else context.get('user')),
                actor_role='citizen_scientist', via=source, note=note)
            reopened = True
        if note and existing.status == C.MEMBER_STATUS_PENDING:
            existing.note = note
        if reopened or note:
            existing.source = source
            model.Session.commit()
        result = db.member_dictize(existing)
        result['already_requested'] = not reopened
        result['reopened'] = reopened
        return result

    member = db.CsProjectMember()
    member.project_id = project.id
    member.user_id = user_id
    member.role = C.MEMBER_ROLE_CS
    member.status = 'pending'
    member.source = source
    # The applicant's own words. Until this column existed a reviewer had a
    # username and nothing else to decide on -- and the CS Toolbox app was
    # already sending ``note`` on every join, only for it to be dropped here.
    member.note = note
    member.created = _utcnow()
    db.append_member_event(
        member, 'requested', actor_id=user_id,
        actor_name=(data_dict.get('username') if on_behalf
                    else context.get('user')),
        actor_role='citizen_scientist', via=source, note=note)
    model.Session.add(member)
    model.Session.commit()

    result = db.member_dictize(member)
    result['already_requested'] = False
    result['reopened'] = False
    return result


def _resolve_membership_keys(data_dict):
    """``(project_uuid, user_id)`` from ids, a slug and/or a username.

    ``db.project_member`` filters the raw columns, so it needs the project's
    UUID and the CKAN user id -- a slug or a username silently matches nothing
    and surfaces as a misleading "Membership not found". The CS Toolbox app
    posts exactly those unusable forms (``project_slug`` + ``username``), which
    is why every app-mediated approval failed before this resolved them.
    """
    project = _resolve_project(data_dict)
    user_id = data_dict.get('user_id')
    if not user_id:
        username = (data_dict.get('username') or '').strip()
        if username:
            user = model.User.get(username)
            user_id = user.id if user is not None else None
    if project is None or not user_id:
        raise tk.ValidationError({'project_id': [tk._('Missing value')],
                                  'user_id': [tk._('Missing value')]})
    return project.id, user_id


def csunesco_join_approve(context, data_dict):
    """Approve a pending join-request; bump ``citizen_scientists`` once.

    The reviewer of record is the authenticated caller -- unless the caller
    is the sysadmin service token acting for a named person
    (``decided_by_username`` + ``decided_by_role``, see ``_resolve_decider``),
    in which case THAT person is recorded, with ``reviewed_via='app'``. The
    legacy ``approved_by`` (an ofform-local integer) is still ignored.
    """
    tk.check_access('csunesco_join_approve', context, data_dict)
    data_dict = data_dict or {}
    project_id, user_id = _resolve_membership_keys(data_dict)

    member = db.project_member(project_id, user_id)
    if member is None:
        raise tk.ObjectNotFound(tk._('Membership not found'))
    # GUARD: only the pending -> active transition should increment the counter.
    if member.status != 'pending':
        raise tk.ValidationError({'status': [tk._(
            'Only pending memberships can be approved (current status: %s)'
        ) % member.status]})

    reviewer_id, role, via, actor_name = _resolve_decider(
        context, project_id, data_dict)
    member = db.set_member_status(
        project_id, user_id, C.MEMBER_STATUS_ACTIVE, reviewed_by=reviewer_id,
        reviewed_role=role, reviewed_via=via)
    db.append_member_event(member, 'approved', actor_id=reviewer_id,
                           actor_name=actor_name, actor_role=role, via=via)
    db.ensure_stats(project_id)
    new_count = db.stats_increment(project_id, 'citizen_scientists', 1)
    model.Session.commit()

    # AFTER the commit: the applicant finally hears back (best-effort).
    from ckanext.csunesco.logic import notify
    project = db.get_project(project_id)
    notify.notify_join_decision(
        user_id, project.title if project else '', approved=True)

    return {
        'membership': db.member_dictize(db.project_member(project_id, user_id)),
        'citizen_scientists': new_count,
    }


def csunesco_join_reject(context, data_dict):
    """Reject a join-request (or revoke an active member).

    SEMANTIC: ``citizen_scientists`` counts CURRENTLY-active members. If the
    member being rejected was previously ``active`` we decrement the counter so
    the count stays consistent; rejecting a still-``pending`` request does not
    touch the counter (it was never counted).
    """
    tk.check_access('csunesco_join_reject', context, data_dict)
    data_dict = data_dict or {}
    project_id, user_id = _resolve_membership_keys(data_dict)

    member = db.project_member(project_id, user_id)
    if member is None:
        raise tk.ObjectNotFound(tk._('Membership not found'))
    was_active = member.status == C.MEMBER_STATUS_ACTIVE

    reviewer_id, role, via, actor_name = _resolve_decider(
        context, project_id, data_dict)
    member = db.set_member_status(
        project_id, user_id, C.MEMBER_STATUS_REJECTED, reviewed_by=reviewer_id,
        reviewed_role=role, reviewed_via=via)
    db.append_member_event(
        member, 'revoked' if was_active else 'rejected', actor_id=reviewer_id,
        actor_name=actor_name, actor_role=role, via=via,
        note=_clean_note(data_dict.get('reason')))
    if was_active:
        db.ensure_stats(project_id)
        db.stats_increment(project_id, 'citizen_scientists', -1)
    model.Session.commit()

    from ckanext.csunesco.logic import notify
    project = db.get_project(project_id)
    notify.notify_join_decision(
        user_id, project.title if project else '', approved=False)

    return db.member_dictize(db.project_member(project_id, user_id))


def csunesco_project_manager_set(context, data_dict):
    """Assign the Project Manager (admin) role to a named user.

    Spec 6.E: the PM "defaults to the creator; can be reassigned". ADDITIVE
    by default -- the spec explicitly allows more than one manager -- and
    ``replace: true`` additionally demotes every other current admin to
    editor, which is the actual hand-over gesture.
    """
    tk.check_access('csunesco_project_manager_set', context, data_dict)
    data_dict = data_dict or {}
    project = _resolve_project(data_dict)
    if project is None:
        raise tk.ObjectNotFound(tk._('Project not found'))

    username = (data_dict.get('username') or '').strip()
    if not username:
        raise tk.ValidationError({'username': [tk._('Missing value')]})
    user = model.User.get(username)
    if user is None:
        raise tk.ValidationError({'username': [tk._(
            'Unknown user: %s') % username]})

    now = _utcnow()
    member = db.project_member(project.id, user.id)
    if member is None:
        member = db.CsProjectMember()
        member.project_id = project.id
        member.user_id = user.id
        member.source = 'ckan'
        member.created = now
        model.Session.add(member)
    member.role = C.MEMBER_ROLE_PM
    member.status = C.MEMBER_STATUS_ACTIVE
    # Assigning a manager is a decision: record who made it, as what, and
    # when (the same audit trail a join decision carries), never silently.
    reviewer_id, role, via, actor_name = _resolve_decider(
        context, project.id, data_dict)
    member.reviewed_by = reviewer_id
    member.reviewed_at = now
    member.reviewed_role = role
    member.reviewed_via = via
    db.append_member_event(member, 'manager_assigned', actor_id=reviewer_id,
                           actor_name=actor_name, actor_role=role, via=via)

    if tk.asbool(data_dict.get('replace', False)):
        others = (
            model.Session.query(db.CsProjectMember)
            .filter(db.CsProjectMember.project_id == project.id)
            .filter(db.CsProjectMember.role == C.MEMBER_ROLE_PM)
            .filter(db.CsProjectMember.user_id != user.id)
            .all()
        )
        for other in others:
            other.role = C.MEMBER_ROLE_EDITOR
            other.reviewed_by = reviewer_id
            other.reviewed_at = now
            other.reviewed_role = role
            other.reviewed_via = via
            db.append_member_event(
                other, 'manager_demoted', actor_id=reviewer_id,
                actor_name=actor_name, actor_role=role, via=via)
    model.Session.commit()
    return db.member_dictize(member)


def get_actions():
    return {
        'csunesco_join_request_create': csunesco_join_request_create,
        'csunesco_join_approve': csunesco_join_approve,
        'csunesco_join_reject': csunesco_join_reject,
        'csunesco_project_manager_set': csunesco_project_manager_set,
    }
