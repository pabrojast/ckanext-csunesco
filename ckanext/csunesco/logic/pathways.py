"""Canonical v10 onboarding. Colab and Toolbox share these authorization rules."""
import datetime
import json
import secrets

import ckan.model as model
import ckan.plugins.toolkit as tk
from sqlalchemy.exc import IntegrityError

from ckanext.csunesco import constants as C, db
from ckanext.csunesco.logic import auth
from ckanext.csunesco.logic.onboarding import review_context
from ckanext.csunesco.logic.registration_profile import require_registered


def entry_auth(context, data):
    # Public intake options contain only the existing public organization list.
    # Every mutating action also checks the actual actor, never just the service.
    return {'success': True}


def actor(context, data):
    context = review_context(context, data or {})
    user = auth._user_obj(context)
    if user is None or user.state != 'active':
        raise tk.NotAuthorized('An active account is required')
    return context, user


def extras(project):
    return db._load_json(project.extras, {})


def policy(project):
    return getattr(project, 'join_policy', None) or (
        'closed' if extras(project).get('open_participation') is False else 'public')


def public(project):
    return getattr(project, 'discovery_visibility', None) != 'unlisted'


def visible(context, project):
    if public(project):
        return True
    user = auth._user_obj(context)
    member = db.project_member(project.id, user.id) if user else None
    return bool(auth._is_sysadmin(context) or (
        member and member.status == 'active') or (
        user and project.created_by == user.id))


def clean_note(value):
    value = str(value or '').strip()
    if not 20 <= len(value) <= 500:
        raise tk.ValidationError({'note': ['Write between 20 and 500 characters.']})
    return value


def organization_intent(data):
    from ckanext.csunesco.logic.registration import _organization_options
    from ckanext.csunesco.logic.onboarding import exact_org_match
    fields = {key: str(data.get(key) or '').strip() for key in (
        'org_name', 'new_org_name', 'org_type', 'org_title', 'org_role', 'org_description')}
    creating = fields['org_name'] == '__new__'
    if (not tk.asbool(data.get('responsibilities', False))
            or fields['org_type'] not in {o['name'] for o in C.ORG_TYPES}
            or not 1 <= len(fields['org_title']) <= 200
            or fields['org_role'] not in ('member', 'admin')
            or len(fields['org_description']) > 5000
            or creating and (not 1 <= len(fields['new_org_name']) <= 200 or exact_org_match(fields['new_org_name']))
            or not creating and fields['org_name'] not in {o['name'] for o in _organization_options()}):
        from ckanext.csunesco.logic.registration_errors import problem
        raise tk.ValidationError(problem('organization_invalid', 'org_name', 'org_type', 'org_title', 'org_role', 'responsibilities'))
    org = model.Group.get(fields['org_name']) if not creating else None
    return {'org_id': org.id if org else None,
            'org_name_requested': fields['new_org_name'] if creating else None,
            'org_type': fields['org_type'], 'org_title': fields['org_title'],
            'org_role': 'admin' if creating else fields['org_role'],
            'org_description': fields['org_description'] if creating else None}


def manager_approved(user_id):
    profile = db.get_citizen_scientist(user_id)
    return bool(profile and profile.email_verified and profile.profile_type == 'manager'
                and profile.manager_decision == 'approved' and profile.org_id)


def event(row, name, user_id, reason=None):
    history = db._load_json(row.history, [])
    now = datetime.datetime.utcnow()
    history.append({'event': name, 'actor_id': user_id, 'at': now.isoformat(),
                    'reason': reason, 'note': row.note})
    row.history = json.dumps(history)
    row.modified = now


def project_summary(project):
    return {'id': project.id, 'slug': project.slug, 'name': project.title,
            'summary': project.short_description or '',
            'visibility': getattr(project, 'discovery_visibility', None) or 'public',
            'join_policy': policy(project)}


def request_out(row):
    project, user = db.get_project(row.project_id), model.User.get(row.user_id)
    state = row.status
    if state == 'pending' and row.requested_role == 'project_manager' and not manager_approved(row.user_id):
        state = 'waiting_for_account_approval'
    return {'id': row.id, 'project_id': row.project_id,
            'project_slug': project.slug if project else '',
            'project_name': project.title if project else '',
            'user_id': row.user_id, 'username': user.name if user else '',
            'fullname': user.fullname if user else '', 'requested_role': row.requested_role,
            'status': row.status, 'review_state': state, 'note': row.note,
            'reason': row.reason, 'reviewed_by': row.reviewed_by,
            'created': row.created.isoformat() if row.created else None,
            'history': db._load_json(row.history, [])}


def reviewer(context, row):
    if row.requested_role == 'project_manager':
        return auth._is_sysadmin(context)
    return bool(auth._is_sysadmin(context) or auth._is_project_admin(context, row.project_id)
                or auth._is_project_initiative_admin(context, row.project_id))


def adopt_legacy():
    """Idempotent migration of pending requests, preserving their original note."""
    db.ensure_mappers()
    for member in model.Session.query(db.CsProjectMember).filter_by(status='pending').all():
        exists = model.Session.query(db.CsJoinRequest.id).filter_by(
            project_id=member.project_id, user_id=member.user_id, requested_role='citizen').first()
        if exists:
            continue
        row = db.CsJoinRequest(project_id=member.project_id, user_id=member.user_id,
            requested_role='citizen', status='pending', note=member.note,
            created=member.created, history=member.history or '[]')
        model.Session.add(row)
    model.Session.flush()


@tk.side_effect_free
def csunesco_pathway_options(context, data):
    from ckanext.csunesco.logic.registration import _organization_options
    return {'organizations': _organization_options(), 'org_types': list(C.ORG_TYPES)}


@tk.side_effect_free
def csunesco_pathway_projects(context, data):
    ctx, user = actor(context, data)
    db.ensure_mappers()
    rows = model.Session.query(db.CsProject).filter_by(status='approved').order_by(db.CsProject.title).all()
    token = str(data.get('invitation') or '')
    invitation = model.Session.query(db.CsInvitation).filter_by(token=token).first() if token else None
    selected = []
    for project in rows:
        invited = bool(invitation and invitation.project_id == project.id and policy(project) == 'invitation')
        if not invited and (not public(project) or policy(project) != 'public'):
            continue
        member = db.project_member(project.id, user.id)
        selected.append(dict(project_summary(project),
            role=member.role if member and member.status == 'active' else None,
            invited=invited))
    if token and not any(row['invited'] for row in selected):
        raise tk.ObjectNotFound('Invitation unavailable')
    return selected


def csunesco_pathway_request(context, data):
    ctx, user = actor(context, data)
    db.ensure_mappers()
    model.Session.query(db.CsCitizenScientist).filter_by(user_id=user.id).populate_existing().with_for_update().first()
    profile = require_registered(user)
    project = db.get_project(data.get('project_id') or data.get('project_slug'))
    if not project or project.status != 'approved':
        raise tk.ObjectNotFound('Project unavailable')
    model.Session.query(db.CsProject).filter_by(id=project.id).populate_existing().with_for_update().one()
    role = data.get('requested_role', 'citizen')
    if role not in ('citizen', 'project_manager'):
        raise tk.ValidationError({'requested_role': ['Invalid requested role']})
    if role == 'project_manager' and (profile.profile_type != 'manager' or profile.manager_decision == 'rejected'):
        raise tk.ValidationError({'manager_application_required': ['Complete your Project Manager application first.']})
    note = clean_note(data.get('note'))
    member = db.project_member(project.id, user.id)
    if member and member.status == 'active' and (
            role == 'citizen' or member.role == C.MEMBER_ROLE_PM):
        raise tk.ValidationError({'already_member': ['You already have this project role.']})
    invitation = model.Session.query(db.CsInvitation).filter_by(
        project_id=project.id, token=str(data.get('invitation') or '')).first()
    if not (policy(project) == 'public' and public(project) or
            policy(project) == 'invitation' and invitation):
        raise tk.NotAuthorized('A valid project invitation is required')
    # Serialize competing requests for either role without modifying active membership.
    row = model.Session.query(db.CsJoinRequest).filter_by(
        project_id=project.id, user_id=user.id, requested_role=role).first()
    if row and row.status == 'pending':
        return request_out(row)
    if row is None:
        row = db.CsJoinRequest(project_id=project.id, user_id=user.id, requested_role=role)
        model.Session.add(row)
    row.status, row.note, row.reason = 'pending', note, None
    event(row, 'submitted', user.id)
    # Compatibility projection for old app/portal readers. A PM request must
    # never replace a scientist's active membership.
    if role == 'citizen' and (member is None or member.status != 'active'):
        if member is None:
            member = db.CsProjectMember(project_id=project.id, user_id=user.id, role=C.MEMBER_ROLE_CS)
            model.Session.add(member)
        member.status, member.note, member.source = 'pending', note, 'app' if data.get('actor_id') else 'ckan'
    try:
        model.Session.commit()
    except IntegrityError:
        model.Session.rollback()
        raise tk.ValidationError({'request': ['This request changed. Refresh and try again.']})
    notify_request(row, project)
    return request_out(row)


@tk.side_effect_free
def csunesco_pathway_requests(context, data):
    ctx, user = actor(context, data)
    db.ensure_mappers()
    query = model.Session.query(db.CsJoinRequest)
    review = tk.asbool(data.get('review', False))
    if not review:
        query = query.filter_by(user_id=user.id)
    rows = query.order_by(db.CsJoinRequest.modified.desc()).all()
    return [request_out(row) for row in rows if not review or reviewer(ctx, row)]


def csunesco_pathway_decide(context, data):
    ctx, user = actor(context, data)
    db.ensure_mappers()
    candidate = model.Session.query(db.CsJoinRequest).filter_by(id=data.get('id')).first()
    if candidate and candidate.requested_role == 'project_manager':
        model.Session.query(db.CsCitizenScientist).filter_by(user_id=candidate.user_id).populate_existing().with_for_update().first()
    row = model.Session.query(db.CsJoinRequest).filter_by(id=data.get('id')).populate_existing().with_for_update().first()
    if row is None:
        raise tk.ObjectNotFound('Request unavailable')
    decision = data.get('decision')
    if decision == 'cancel':
        if row.user_id != user.id:
            raise tk.NotAuthorized('Only the requester can withdraw a request')
    elif not reviewer(ctx, row):
        raise tk.NotAuthorized('You cannot review this request')
    if decision not in ('approve', 'reject', 'cancel'):
        raise tk.ValidationError({'decision': ['Invalid decision']})
    desired = {'approve': 'approved', 'reject': 'rejected', 'cancel': 'cancelled'}[decision]
    if row.status == desired:
        return request_out(row)
    if row.status != 'pending':
        raise tk.ValidationError({'status': ['This request has already been decided.']})
    target = model.User.get(row.user_id)
    if decision == 'approve':
        require_registered(target)
        if row.requested_role == 'project_manager' and not manager_approved(target.id):
            raise tk.ValidationError({'manager_approval_required': ['Approve the Project Manager account first.']})
    member = db.project_member(row.project_id, row.user_id)
    if decision == 'approve':
        was_active = bool(member and member.status == 'active')
        if member is None:
            member = db.CsProjectMember(project_id=row.project_id, user_id=row.user_id)
            model.Session.add(member)
        if row.requested_role == 'project_manager':
            member.role = C.MEMBER_ROLE_PM
        elif not was_active:
            member.role = C.MEMBER_ROLE_CS
        member.status, member.source = 'active', 'ckan'
        member.reviewed_by, member.reviewed_at = user.id, datetime.datetime.utcnow()
        member.reviewed_role, member.reviewed_via = auth.decider_role(ctx, row.project_id), 'app' if data.get('actor_id') else 'ckan'
        db.append_member_event(member, 'approved', actor_id=user.id,
            actor_name=user.name, actor_role=member.reviewed_role, via=member.reviewed_via, note=row.note)
        if not was_active:
            db.ensure_stats(row.project_id)
            db.stats_increment(row.project_id, 'citizen_scientists', 1)
    elif row.requested_role == 'citizen' and member and member.status == 'pending':
        member.status = 'rejected'
    row.status, row.reason, row.reviewed_by = desired, str(data.get('reason') or '').strip()[:1000], user.id
    event(row, desired, user.id, row.reason)
    model.Session.commit()
    from ckanext.csunesco.logic import notify
    project = db.get_project(row.project_id)
    if decision != 'cancel':
        notify.notify_join_decision(row.user_id, project.title, approved=decision == 'approve', reason=row.reason)
    return request_out(row)


def cancel_manager_work(user_id, reason, reviewer_id=None):
    """Caller commits with the PM decision; no deletion and no CS revocation."""
    for row in model.Session.query(db.CsJoinRequest).filter_by(
            user_id=user_id, requested_role='project_manager', status='pending').all():
        row.status, row.reason = 'cancelled', reason
        event(row, 'cancelled', reviewer_id, reason)
    for project in model.Session.query(db.CsProject).filter_by(created_by=user_id, status='pending').all():
        info = extras(project)
        if info.get('manager_registration_user_id') != user_id:
            continue
        info['submission_cancelled'] = {'reason': reason, 'at': datetime.datetime.utcnow().isoformat()}
        project.extras, project.status = json.dumps(info), 'draft'


def csunesco_pathway_invitation(context, data):
    ctx, user = actor(context, data)
    project = db.get_project(data.get('project_id') or data.get('project_slug'))
    if not project or not (auth._is_sysadmin(ctx) or auth._is_project_admin(ctx, project.id)):
        raise tk.NotAuthorized('Only the project manager can manage invitations')
    if project.status != 'approved':
        raise tk.ValidationError({'project': ['Approve the project before inviting participants.']})
    model.Session.query(db.CsProject).filter_by(id=project.id).populate_existing().with_for_update().one()
    visibility = data.get('visibility')
    mode = data.get('join_policy')
    if visibility is not None:
        if visibility not in ('public', 'unlisted'):
            raise tk.ValidationError({'visibility': ['Invalid visibility']})
        project.discovery_visibility = visibility
    if mode is not None:
        if mode not in ('public', 'invitation', 'closed'):
            raise tk.ValidationError({'join_policy': ['Invalid admission policy']})
        project.join_policy = mode
    if not public(project) and policy(project) == 'public':
        raise tk.ValidationError({'join_policy': ['Non-public projects require invitations or closed admission.']})
    row = model.Session.query(db.CsInvitation).filter_by(project_id=project.id).first()
    if row is None or tk.asbool(data.get('rotate', False)):
        if row is None:
            row = db.CsInvitation(project_id=project.id)
            model.Session.add(row)
        row.token, row.created_by = secrets.token_urlsafe(32), user.id
        row.created = datetime.datetime.utcnow()
    model.Session.commit()
    return dict(project_summary(project), invitation=row.token)


def notify_request(row, project):
    from ckanext.csunesco.logic import approval_events
    # The existing dispatcher owns localization and delivery receipts.
    if row.requested_role == 'citizen':
        approval_events.record(project, 'join_request', row.user_id, row.note)
        model.Session.commit()
        approval_events.flush(project)
    else:
        approval_events.record(project, 'manager_join_request', row.user_id, row.note)
        model.Session.commit()
        approval_events.flush(project)


def get_actions():
    return {fn.__name__: fn for fn in (
        csunesco_pathway_options, csunesco_pathway_projects, csunesco_pathway_request,
        csunesco_pathway_requests, csunesco_pathway_decide, csunesco_pathway_invitation)}
