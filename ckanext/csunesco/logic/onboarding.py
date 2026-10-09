"""Private registration review shared by the portal and the trusted Toolbox."""
import datetime
import unicodedata

import ckan.model as model
import ckan.plugins.toolkit as tk
from ckanext.csunesco import db
from ckanext.csunesco.logic import auth

PROFILE_FIELDS = ('profile_type', 'date_of_birth', 'nationality', 'gender',
                  'language', 'language_other', 'motivation', 'terms_accepted_at',
                  'responsibilities_accepted_at', 'org_id', 'org_name_requested',
                  'org_type', 'org_title', 'org_role', 'org_description',
                  'org_image_url', 'manager_decision', 'manager_application_origin', 'manager_reviewed_at',
                  'manager_reviewed_by', 'manager_review_reason', 'created')


def review_context(context, data):
    """Service credentials do not confer their powers on the actual reviewer."""
    name = data.get('actor_username')
    if not name:
        return context
    if not auth._is_sysadmin(context):
        raise tk.NotAuthorized('Only the trusted backend may identify a reviewer')
    user = model.User.get(name)
    if user is None or user.state != 'active' or data.get('actor_id') != user.id:
        raise tk.NotAuthorized('Reviewer account is unavailable')
    return {'model': model, 'session': model.Session, 'user': user.name,
            'auth_user_obj': user}


def profile_dict(user, profile=None):
    if user is None:
        return {}
    profile = profile or db.get_citizen_scientist(user.id)
    result = {'user_id': user.id, 'username': user.name,
              'fullname': user.fullname, 'email': user.email}
    for key in PROFILE_FIELDS:
        value = getattr(profile, key, None)
        result[key] = value.isoformat() if isinstance(value, (datetime.date, datetime.datetime)) else value
    result['email_verified'] = bool(getattr(profile, 'email_verified', False))
    result['email_verification_required'] = bool(profile and getattr(profile, 'verification_token', None))
    org = model.Group.get(result['org_id']) if result['org_id'] else None
    result['organization_title'] = (org.title or org.name) if org else result['org_name_requested']
    reviewer = model.User.get(result['manager_reviewed_by']) if result['manager_reviewed_by'] else None
    result['reviewer_name'] = (reviewer.fullname or reviewer.name) if reviewer else None
    from ckanext.csunesco.logic.registration_profile import completeness
    result.update(completeness(user, profile))
    return result


def csunesco_registration_review_show(context, data_dict):
    context = review_context(context, data_dict)
    user = model.User.get(data_dict.get('id'))
    if user is None:
        raise tk.ObjectNotFound('Registration not found')
    actor = auth._user_obj(context)
    permitted = auth._is_sysadmin(context) or (actor and actor.id == user.id)
    if not permitted:
        project = db.get_project(data_dict.get('project_id'))
        if project and db.project_member(project.id, user.id):
            permitted = (auth._is_project_admin(context, project.id)
                         or auth._is_project_initiative_admin(context, project.id))
    if not permitted:
        raise tk.NotAuthorized('Not a reviewer of this registration')
    return profile_dict(user)


def csunesco_manager_list(context, data_dict):
    context = review_context(context, data_dict)
    if not auth._is_sysadmin(context):
        raise tk.NotAuthorized('Only IHP sysadmins can review manager accounts')
    db.ensure_mappers()
    query = model.Session.query(db.CsCitizenScientist).filter(
        db.CsCitizenScientist.profile_type == 'manager')
    state = data_dict.get('status', 'pending')
    if state == 'pending':
        query = query.filter(db.CsCitizenScientist.email_verified.is_(True),
                             db.CsCitizenScientist.manager_decision.is_(None))
    elif state in ('approved', 'rejected'):
        query = query.filter(db.CsCitizenScientist.manager_decision == state)
    elif state == 'unverified':
        query = query.filter(db.CsCitizenScientist.email_verified.is_(False))
    elif state != 'all':
        raise tk.ValidationError({'status': ['Invalid status']})
    search = str(data_dict.get('q') or '').strip()[:100]
    if search:
        query = query.join(model.User, model.User.id == db.CsCitizenScientist.user_id)
        from sqlalchemy import or_
        query = query.filter(or_(model.User.name.contains(search, autoescape=True),
                                 model.User.fullname.contains(search, autoescape=True),
                                 model.User.email.contains(search, autoescape=True)))
    count = query.count()
    try:
        offset = max(0, int(data_dict.get('offset', 0)))
        limit = min(100, max(1, int(data_dict.get('limit', 20))))
    except (TypeError, ValueError):
        raise tk.ValidationError({'offset': ['Invalid pagination']})
    rows = query.order_by(db.CsCitizenScientist.created.desc()).offset(offset).limit(limit).all()
    from ckanext.csunesco.logic.registration import _organization_options
    return {'count': count, 'results': [profile_dict(model.User.get(p.user_id), p) for p in rows],
            'organizations': _organization_options()}


def csunesco_registration_project_review(context, data_dict):
    context = review_context(context, data_dict)
    if data_dict.get('id'):
        return tk.get_action('csunesco_project_review_show')(context, {'id': data_dict['id']})
    return tk.get_action('csunesco_admin_pending_list')(context, data_dict)


def csunesco_registration_join_decide(context, data_dict):
    """Compatibility bridge: the actual reviewer must have CKAN permissions."""
    context = review_context(context, data_dict)
    decision = data_dict.get('decision')
    if decision not in ('approve', 'reject'):
        raise tk.ValidationError({'decision': ['Invalid decision']})
    project = db.get_project(data_dict.get('project_slug'))
    if project is None:
        raise tk.ObjectNotFound('Project not found')
    payload = {'project_id': project.id, 'user_id': data_dict.get('user_id')}
    action = 'csunesco_join_' + decision
    tk.check_access(action, context, payload)
    member = db.project_member(project.id, payload['user_id'])
    if member is None:
        raise tk.ObjectNotFound('The join request has not reached the portal yet')
    desired = 'active' if decision == 'approve' else 'rejected'
    if member.status == desired:
        return {'membership': db.member_dictize(member)}
    if member.status != 'pending':
        raise tk.ValidationError({'status': ['This request has already been decided.']})
    return tk.get_action(action)(context, payload)


def csunesco_registration_resend(context, data_dict):
    if not auth._is_sysadmin(context):
        raise tk.NotAuthorized('Trusted backend required')
    from ckanext.csunesco.logic import registration
    import secrets
    user = model.User.get(data_dict.get('username'))
    profile = db.get_citizen_scientist(user.id) if user else None
    from ckanext.csunesco.logic.registration_profile import needs_verification_link
    if profile and (not profile.email_verified or needs_verification_link(user, profile)):
        stamp = profile.token_created
        # Keep both anonymous and trusted entrypoints bounded.
        if not stamp or datetime.datetime.utcnow() - stamp >= datetime.timedelta(minutes=2):
            token = secrets.token_urlsafe(32)
            db.set_verification_token(user.id, token)
            registration._send_verification_email(user.fullname or user.name, user.email, token,
                                                 language=getattr(profile, 'language', None))
    return {'sent': True}


def lock_org_creation(title):
    # Serialize approvals for the same normalized title until their transaction
    # commits. PostgreSQL is CKAN's production database; SQLite test harnesses
    # need no concurrent-writer advisory lock.
    if model.Session.get_bind().dialect.name == 'postgresql':
        import hashlib
        from sqlalchemy import text
        key = int.from_bytes(hashlib.sha256(fold_org(title).encode()).digest()[:8], 'big', signed=True)
        model.Session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': key})


def fold_org(value):
    value = unicodedata.normalize('NFKD', value or '')
    return ''.join(c for c in value.casefold() if c.isalnum() and not unicodedata.combining(c))


def exact_org_match(title):
    # Query the canonical database afresh while holding the creation lock;
    # an earlier form/catalog response must not authorize a duplicate.
    rows = model.Session.query(model.Group).filter(
        model.Group.is_organization.is_(True), model.Group.state == 'active').all()
    wanted = fold_org(title)
    return any(wanted in (fold_org(o.name), fold_org(o.title)) for o in rows)


def store_org_logo(upload):
    """Reuse CS FileStore/S3 upload validation and transactional cleanup."""
    if not upload or not upload.filename:
        return None, None
    from ckanext.csunesco.logic.uploads import UploadBatch
    from ckanext.csunesco.logic.registration_errors import problem
    batch = UploadBatch(max_uploads=1)
    holder = {'upload': upload}
    try:
        upload.stream.seek(0, 2)
        if upload.stream.tell() > 2 * 1024 * 1024:
            raise ValueError('Image too large')
        upload.stream.seek(0)
        batch.add_picker(holder, 'url', 'upload', 'clear', max_size=2)
        batch.write()
        return holder['url'], batch
    except Exception:
        batch.rollback()
        raise tk.ValidationError(problem('logo_invalid', 'org_logo'))


def get_actions():
    return {fn.__name__: fn for fn in (csunesco_registration_review_show,
        csunesco_manager_list, csunesco_registration_project_review,
        csunesco_registration_join_decide, csunesco_registration_resend)}
