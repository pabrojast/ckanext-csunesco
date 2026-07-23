# encoding: utf-8
"""Authorization functions for ckanext-csunesco.

Three privileged levels (matriz CST: CS / PM / ADM):

  * sysadmin (IHP admin)      -> everything.
  * initiative admin (ADM)    -> an ACTIVE ``admin``-capacity member of one of
    the four initiative CKAN groups (managed via the standard group-members
    page). May approve/reject projects, content and data sources OF THEIR
    INITIATIVE, plus everything a project admin can do within it.
  * project admin (PM)        -> join approve/reject + content/data create for
    THEIR project.
  * project/join request      -> any authenticated (non-anonymous) user.
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
    """Resolve the acting ``User`` object from context (None when anonymous).

    On portals with flask-login-style auth plugins (e.g. ckanext-auth on
    IHP-WINS), anonymous API calls put an ``AnonymousUser`` object -- with no
    ``sysadmin``/``id`` attributes -- into ``auth_user_obj``; treat it as "no
    user" instead of returning it.
    """
    user_obj = context.get('auth_user_obj')
    if user_obj is not None and not getattr(user_obj, 'is_anonymous', False):
        return user_obj
    username = context.get('user')
    if not username:
        return None
    return model.User.get(username)


def _is_sysadmin(context):
    """True when the acting user is a CKAN sysadmin (IHP admin)."""
    user_obj = _user_obj(context)
    return bool(user_obj is not None
                and getattr(user_obj, 'sysadmin', False))


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


# --- Initiative admin (ADM): admin-capacity member of an initiative group ----

def _admin_initiative_groups(context):
    """Initiative-group names where the acting user is an ADM ([] if none)."""
    user_obj = _user_obj(context)
    if user_obj is None:
        return []
    from ckanext.csunesco import db
    return db.admin_initiative_groups(user_obj.id)


def _is_any_initiative_admin(context):
    """True when the acting user is an ADM of at least one initiative."""
    return bool(_admin_initiative_groups(context))


def _is_project_initiative_admin(context, project_id):
    """True when the acting user is an ADM of the project's initiative."""
    if not project_id:
        return False
    from ckanext.csunesco import db
    project = db.get_project(project_id)
    if project is None or not project.initiative_group:
        return False
    return project.initiative_group in _admin_initiative_groups(context)


def _is_content_initiative_admin(context, content_id):
    """True when the acting user is an ADM of the content's initiative."""
    if not content_id:
        return False
    from ckanext.csunesco import db
    content = db.get_content(content_id)
    if content is None or not content.initiative_group:
        return False
    return content.initiative_group in _admin_initiative_groups(context)


def _is_data_source_initiative_admin(context, data_source_id):
    """True when the acting user is an ADM of the data source's initiative."""
    if not data_source_id:
        return False
    from ckanext.csunesco import db
    source = db.get_data_source(data_source_id)
    if source is None:
        return False
    return _is_project_initiative_admin(context, source.project_id)


# ---------------------------------------------------------------------------
# Auth functions (CKAN contract: return {'success': bool, 'msg': ...})
# ---------------------------------------------------------------------------

def csunesco_project_approve(context, data_dict):
    # Sysadmin, or the initiative admin (ADM) of the project's initiative.
    project_id = (data_dict or {}).get('id') or (data_dict or {}).get('project_id')
    if _is_sysadmin(context) or _is_project_initiative_admin(context, project_id):
        return {'success': True}
    return {'success': False,
            'msg': tk._('Only sysadmins or the initiative admin can approve '
                        'projects')}


def csunesco_project_reject(context, data_dict):
    project_id = (data_dict or {}).get('id') or (data_dict or {}).get('project_id')
    if _is_sysadmin(context) or _is_project_initiative_admin(context, project_id):
        return {'success': True}
    return {'success': False,
            'msg': tk._('Only sysadmins or the initiative admin can reject '
                        'projects')}


def csunesco_join_approve(context, data_dict):
    project_id = (data_dict or {}).get('project_id') or (data_dict or {}).get('id')
    if (_is_sysadmin(context)
            or _is_project_admin(context, project_id)
            or _is_project_initiative_admin(context, project_id)):
        return {'success': True}
    return {'success': False,
            'msg': tk._('Only sysadmins, the project admin or the initiative '
                        'admin can approve join requests')}


def csunesco_join_reject(context, data_dict):
    project_id = (data_dict or {}).get('project_id') or (data_dict or {}).get('id')
    if (_is_sysadmin(context)
            or _is_project_admin(context, project_id)
            or _is_project_initiative_admin(context, project_id)):
        return {'success': True}
    return {'success': False,
            'msg': tk._('Only sysadmins, the project admin or the initiative '
                        'admin can reject join requests')}


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
    # The panel is visible to any sysadmin, project admin OR initiative admin;
    # the action itself scopes what each of them actually sees.
    if (_is_sysadmin(context)
            or _is_any_project_admin(context)
            or _is_any_initiative_admin(context)):
        return {'success': True}
    return {'success': False,
            'msg': tk._('You do not have access to the approval panel')}


def csunesco_content_create(context, data_dict):
    # Sysadmin, the target project's admin or its initiative admin. When the
    # project is not resolvable from the auth payload we allow any authenticated
    # user through and let the action re-check against the resolved project
    # (defence in depth).
    project_id = (data_dict or {}).get('project_id')
    if project_id:
        if (_is_sysadmin(context)
                or _is_project_admin(context, project_id)
                or _is_project_initiative_admin(context, project_id)):
            return {'success': True}
        return {'success': False,
                'msg': tk._('Only the project admin or the initiative admin '
                            'can add content')}
    if context.get('user'):
        return {'success': True}
    return {'success': False,
            'msg': tk._('You must be logged in to add content')}


def csunesco_content_update(context, data_dict):
    project_id = (data_dict or {}).get('project_id')
    if project_id:
        if (_is_sysadmin(context)
                or _is_project_admin(context, project_id)
                or _is_project_initiative_admin(context, project_id)):
            return {'success': True}
        return {'success': False,
                'msg': tk._('Only the project admin or the initiative admin '
                            'can edit this content')}
    if context.get('user'):
        return {'success': True}
    return {'success': False,
            'msg': tk._('You must be logged in to edit content')}


def csunesco_content_approve(context, data_dict):
    content_id = (data_dict or {}).get('id')
    if _is_sysadmin(context) or _is_content_initiative_admin(context, content_id):
        return {'success': True}
    return {'success': False,
            'msg': tk._('Only sysadmins or the initiative admin can approve '
                        'content')}


def csunesco_content_reject(context, data_dict):
    content_id = (data_dict or {}).get('id')
    if _is_sysadmin(context) or _is_content_initiative_admin(context, content_id):
        return {'success': True}
    return {'success': False,
            'msg': tk._('Only sysadmins or the initiative admin can reject '
                        'content')}


@tk.auth_allow_anonymous_access
def csunesco_content_list(context, data_dict):
    # Public read; the action pins non-sysadmins to approved content.
    return {'success': True}


@tk.auth_allow_anonymous_access
def csunesco_content_show(context, data_dict):
    # Public read; the action hides unapproved content from unauthorized users.
    return {'success': True}


# ---------------------------------------------------------------------------
# Data sources (app-data pipeline)
# ---------------------------------------------------------------------------

def csunesco_data_source_create(context, data_dict):
    # Sysadmin, the target project's admin or its initiative admin. Same shape
    # as content_create: when the project is not resolvable from the auth
    # payload we let any authenticated user through and the action re-checks on
    # the resolved project (defence in depth).
    project_id = (data_dict or {}).get('project_id')
    if project_id:
        if (_is_sysadmin(context)
                or _is_project_admin(context, project_id)
                or _is_project_initiative_admin(context, project_id)):
            return {'success': True}
        return {'success': False,
                'msg': tk._('Only the project admin or the initiative admin '
                            'can connect data')}
    if context.get('user'):
        return {'success': True}
    return {'success': False,
            'msg': tk._('You must be logged in to connect data')}


def csunesco_data_source_approve(context, data_dict):
    source_id = (data_dict or {}).get('id')
    if (_is_sysadmin(context)
            or _is_data_source_initiative_admin(context, source_id)):
        return {'success': True}
    return {'success': False,
            'msg': tk._('Only sysadmins or the initiative admin can approve '
                        'data sources')}


def csunesco_data_source_reject(context, data_dict):
    source_id = (data_dict or {}).get('id')
    if (_is_sysadmin(context)
            or _is_data_source_initiative_admin(context, source_id)):
        return {'success': True}
    return {'success': False,
            'msg': tk._('Only sysadmins or the initiative admin can reject '
                        'data sources')}


@tk.auth_allow_anonymous_access
def csunesco_data_source_list(context, data_dict):
    # Public read; the action pins non-privileged callers to approved sources.
    return {'success': True}


@tk.auth_allow_anonymous_access
def csunesco_data_source_show(context, data_dict):
    # Public read; the action hides unapproved sources from unauthorized users.
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
        'csunesco_data_source_create': csunesco_data_source_create,
        'csunesco_data_source_approve': csunesco_data_source_approve,
        'csunesco_data_source_reject': csunesco_data_source_reject,
        'csunesco_data_source_list': csunesco_data_source_list,
        'csunesco_data_source_show': csunesco_data_source_show,
        'csunesco_register_citizen_scientist':
            csunesco_register_citizen_scientist,
    }
