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


def csunesco_join_request_create(context, data_dict):
    """Request to join an APPROVED project (idempotent for the acting user)."""
    if not context.get('user'):
        raise tk.NotAuthorized(
            tk._('You must be logged in to join a project'))
    tk.check_access('csunesco_join_request_create', context, data_dict)

    data_dict = data_dict or {}
    project_id = data_dict.get('project_id') or data_dict.get('id')
    project = db.get_project(project_id)
    if project is None or project.status != 'approved':
        raise tk.ValidationError({'project_id': [tk._(
            'Project not found or not open for join requests')]})

    user_id = current_user_id(context)

    # Idempotent upsert: if the user already has a membership row (in ANY state)
    # do NOT error -- return it flagged so the UI can show a soft notice.
    existing = db.project_member(project.id, user_id)
    if existing is not None:
        result = db.member_dictize(existing)
        result['already_requested'] = True
        return result

    member = db.CsProjectMember()
    member.project_id = project.id
    member.user_id = user_id
    member.role = 'scientist'
    member.status = 'pending'
    member.source = data_dict.get('source', 'ckan')
    # The applicant's own words. Until this column existed a reviewer had a
    # username and nothing else to decide on -- and the CS Toolbox app was
    # already sending ``note`` on every join, only for it to be dropped here.
    member.note = _clean_note(data_dict.get('note'))
    member.created = _utcnow()
    model.Session.add(member)
    model.Session.commit()

    result = db.member_dictize(member)
    result['already_requested'] = False
    return result


def csunesco_join_approve(context, data_dict):
    """Approve a pending join-request; bump ``citizen_scientists`` once."""
    tk.check_access('csunesco_join_approve', context, data_dict)
    data_dict = data_dict or {}
    project_id = data_dict.get('project_id')
    user_id = data_dict.get('user_id')
    if not project_id or not user_id:
        raise tk.ValidationError({'project_id': [tk._('Missing value')],
                                  'user_id': [tk._('Missing value')]})

    member = db.project_member(project_id, user_id)
    if member is None:
        raise tk.ObjectNotFound(tk._('Membership not found'))
    # GUARD: only the pending -> active transition should increment the counter.
    if member.status != 'pending':
        raise tk.ValidationError({'status': [tk._(
            'Only pending memberships can be approved (current status: %s)'
        ) % member.status]})

    db.set_member_status(project_id, user_id, 'active',
                         reviewed_by=current_user_id(context))
    db.ensure_stats(project_id)
    new_count = db.stats_increment(project_id, 'citizen_scientists', 1)
    model.Session.commit()

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
    project_id = data_dict.get('project_id')
    user_id = data_dict.get('user_id')
    if not project_id or not user_id:
        raise tk.ValidationError({'project_id': [tk._('Missing value')],
                                  'user_id': [tk._('Missing value')]})

    member = db.project_member(project_id, user_id)
    if member is None:
        raise tk.ObjectNotFound(tk._('Membership not found'))
    was_active = member.status == 'active'

    db.set_member_status(project_id, user_id, 'rejected',
                         reviewed_by=current_user_id(context))
    if was_active:
        db.ensure_stats(project_id)
        db.stats_increment(project_id, 'citizen_scientists', -1)
    model.Session.commit()

    return db.member_dictize(db.project_member(project_id, user_id))


def get_actions():
    return {
        'csunesco_join_request_create': csunesco_join_request_create,
        'csunesco_join_approve': csunesco_join_approve,
        'csunesco_join_reject': csunesco_join_reject,
    }
