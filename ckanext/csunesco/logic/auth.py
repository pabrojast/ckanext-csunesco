# encoding: utf-8
"""Authorization functions for ckanext-csunesco.

Increment 3: gating for the CS project + membership domains.

  * project approve/reject  -> sysadmin only.
  * join approve/reject      -> sysadmin OR the project's project_admin.
  * project/join request     -> any authenticated (non-anonymous) user.
  * side-effect-free reads    -> public (allow anonymous); the fine-grained
    filtering (e.g. hiding unapproved projects) happens in the action itself.

Citizen Scientist self-registration keeps reusing CKAN's own ``user_create``
auth (increment 2) and is intentionally not duplicated here.
"""
import ckan.plugins.toolkit as tk
import ckan.model as model


# ---------------------------------------------------------------------------
# Role helpers
# ---------------------------------------------------------------------------

def _user_obj(context):
    """Resolve the acting ``User`` object from context (None when anonymous)."""
    user_obj = context.get('auth_user_obj')
    if user_obj is not None:
        return user_obj
    username = context.get('user')
    if not username:
        return None
    return model.User.get(username)


def _is_sysadmin(context):
    """True when the acting user is a CKAN sysadmin (IHP admin)."""
    user_obj = _user_obj(context)
    return bool(user_obj and user_obj.sysadmin)


def _is_project_admin(context, project_id):
    """True when the acting user is an active ``admin`` member of the project."""
    if not project_id:
        return False
    user_obj = _user_obj(context)
    if user_obj is None:
        return False
    from ckanext.csunesco import db
    member = db.project_member(project_id, user_obj.id)
    return bool(member
                and member.role == 'admin'
                and member.status == 'active')


def _is_any_project_admin(context):
    """True when the acting user is an active ``admin`` of at least one project."""
    user_obj = _user_obj(context)
    if user_obj is None:
        return False
    from ckanext.csunesco import db
    return bool(db.admin_project_ids(user_obj.id))


# ---------------------------------------------------------------------------
# Auth functions (CKAN contract: return {'success': bool, 'msg': ...})
# ---------------------------------------------------------------------------

def csunesco_project_approve(context, data_dict):
    if _is_sysadmin(context):
        return {'success': True}
    return {'success': False,
            'msg': tk._('Only sysadmins can approve projects')}


def csunesco_project_reject(context, data_dict):
    if _is_sysadmin(context):
        return {'success': True}
    return {'success': False,
            'msg': tk._('Only sysadmins can reject projects')}


def csunesco_join_approve(context, data_dict):
    project_id = (data_dict or {}).get('project_id') or (data_dict or {}).get('id')
    if _is_sysadmin(context) or _is_project_admin(context, project_id):
        return {'success': True}
    return {'success': False,
            'msg': tk._('Only sysadmins or the project admin can approve '
                        'join requests')}


def csunesco_join_reject(context, data_dict):
    project_id = (data_dict or {}).get('project_id') or (data_dict or {}).get('id')
    if _is_sysadmin(context) or _is_project_admin(context, project_id):
        return {'success': True}
    return {'success': False,
            'msg': tk._('Only sysadmins or the project admin can reject '
                        'join requests')}


def csunesco_project_request_create(context, data_dict):
    # Any authenticated user may request a project; anonymous is denied.
    if context.get('user'):
        return {'success': True}
    return {'success': False,
            'msg': tk._('You must be logged in to request a project')}


def csunesco_join_request_create(context, data_dict):
    # Any authenticated user may request to join; anonymous is denied.
    if context.get('user'):
        return {'success': True}
    return {'success': False,
            'msg': tk._('You must be logged in to join a project')}


@tk.auth_allow_anonymous_access
def csunesco_project_list(context, data_dict):
    # Public read; the action pins non-sysadmins to approved projects.
    return {'success': True}


@tk.auth_allow_anonymous_access
def csunesco_project_show(context, data_dict):
    # Public read; the action hides unapproved projects from unauthorized users.
    return {'success': True}


@tk.auth_allow_anonymous_access
def csunesco_project_stats_show(context, data_dict):
    # Public read.
    return {'success': True}


@tk.auth_allow_anonymous_access
def csunesco_aggregate_stats(context, data_dict):
    # Public read: aggregate counters across approved projects only.
    return {'success': True}


# ---------------------------------------------------------------------------
# Admin approval panel + content (Increment 5)
# ---------------------------------------------------------------------------

def csunesco_admin_pending_list(context, data_dict):
    # The panel is visible to any sysadmin OR any project admin; the action
    # itself scopes what each of them actually sees.
    if _is_sysadmin(context) or _is_any_project_admin(context):
        return {'success': True}
    return {'success': False,
            'msg': tk._('You do not have access to the approval panel')}


def csunesco_content_create(context, data_dict):
    # Sysadmin or the target project's admin. When the project is not resolvable
    # from the auth payload we allow any authenticated user through and let the
    # action re-check against the resolved project (defence in depth).
    project_id = (data_dict or {}).get('project_id')
    if project_id:
        if _is_sysadmin(context) or _is_project_admin(context, project_id):
            return {'success': True}
        return {'success': False,
                'msg': tk._('Only the project admin can add content')}
    if context.get('user'):
        return {'success': True}
    return {'success': False,
            'msg': tk._('You must be logged in to add content')}


def csunesco_content_update(context, data_dict):
    project_id = (data_dict or {}).get('project_id')
    if project_id:
        if _is_sysadmin(context) or _is_project_admin(context, project_id):
            return {'success': True}
        return {'success': False,
                'msg': tk._('Only the project admin can edit this content')}
    if context.get('user'):
        return {'success': True}
    return {'success': False,
            'msg': tk._('You must be logged in to edit content')}


def csunesco_content_approve(context, data_dict):
    if _is_sysadmin(context):
        return {'success': True}
    return {'success': False,
            'msg': tk._('Only sysadmins can approve content')}


def csunesco_content_reject(context, data_dict):
    if _is_sysadmin(context):
        return {'success': True}
    return {'success': False,
            'msg': tk._('Only sysadmins can reject content')}


@tk.auth_allow_anonymous_access
def csunesco_content_list(context, data_dict):
    # Public read; the action pins non-sysadmins to approved content.
    return {'success': True}


@tk.auth_allow_anonymous_access
def csunesco_content_show(context, data_dict):
    # Public read; the action hides unapproved content from unauthorized users.
    return {'success': True}


# ---------------------------------------------------------------------------
# Server-to-server Citizen Scientist registration (Increment 9)
# ---------------------------------------------------------------------------

def csunesco_register_citizen_scientist(context, data_dict):
    # Server-to-server ONLY: this action creates active CKAN accounts, so it is
    # gated on a SYSADMIN token (the ofform backend's CKAN write token) rather
    # than on a merely-authenticated user. This keeps the public web flow on
    # CKAN's own user_create auth and prevents anyone but the trusted backend
    # from driving the idempotent API path.
    if _is_sysadmin(context):
        return {'success': True}
    return {'success': False,
            'msg': tk._('Only sysadmins can register citizen scientists')}


def get_auth_functions():
    return {
        'csunesco_project_approve': csunesco_project_approve,
        'csunesco_project_reject': csunesco_project_reject,
        'csunesco_join_approve': csunesco_join_approve,
        'csunesco_join_reject': csunesco_join_reject,
        'csunesco_project_request_create': csunesco_project_request_create,
        'csunesco_join_request_create': csunesco_join_request_create,
        'csunesco_project_list': csunesco_project_list,
        'csunesco_project_show': csunesco_project_show,
        'csunesco_project_stats_show': csunesco_project_stats_show,
        'csunesco_aggregate_stats': csunesco_aggregate_stats,
        'csunesco_admin_pending_list': csunesco_admin_pending_list,
        'csunesco_content_create': csunesco_content_create,
        'csunesco_content_update': csunesco_content_update,
        'csunesco_content_approve': csunesco_content_approve,
        'csunesco_content_reject': csunesco_content_reject,
        'csunesco_content_list': csunesco_content_list,
        'csunesco_content_show': csunesco_content_show,
        'csunesco_register_citizen_scientist':
            csunesco_register_citizen_scientist,
    }
