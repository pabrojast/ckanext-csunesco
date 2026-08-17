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


def is_reviewer(context):
    """True when the user actually has something to moderate.

    Membership-based on purpose, and distinct from ``_has_own_projects``: the
    approval panel is open to anyone with a project of their own (so the author
    of a rejected request can reach the resubmit button), but *reviewing* is
    still sysadmin, initiative admin, or an admin MEMBER of some project.

    Without the distinction the header offered a "Review" tab to every citizen
    scientist who had ever filed a request, pointing at five empty queues.
    """
    if _is_sysadmin(context) or _is_any_initiative_admin(context):
        return True
    user_obj = _user_obj(context)
    if user_obj is None:
        return False
    from ckanext.csunesco import db
    return bool(db.admin_project_ids(user_obj.id))


def _has_own_projects(context):
    """True when the acting user administers OR authored at least one project.

    Gates the approval panel, which the view itself calls "the manager's home"
    -- it is where your projects are listed. Authorship counts because the
    admin membership row is only inserted on approval: someone whose request
    was rejected administers nothing, so the membership-only test used to shut
    them out of the one page that shows the rejection reason and the way back.

    Matches ``projects_administered``'s "member OR creator" rule exactly, so
    "may open the panel" and "has a row in the panel's 'Your projects' band"
    can never disagree -- but as an EXISTS, because this runs on every page
    render for every authenticated user and that helper sorts by an unindexed
    title before it can apply a LIMIT.
    """
    user_obj = _user_obj(context)
    if user_obj is None:
        return False
    from ckanext.csunesco import db
    return db.user_has_any_project(user_obj.id)


# --- Organization roles (org-scoped content) ---------------------------------

def _is_org_editor(context, org_id):
    """True when the acting user may author content FOR the organization:
    a CKAN admin or editor of the org (the standard ``create_dataset``
    capacity check). Sysadmins pass implicitly through CKAN's authz."""
    if not org_id:
        return False
    user_obj = _user_obj(context)
    if user_obj is None:
        return False
    import ckan.authz as authz
    try:
        return bool(authz.has_user_permission_for_group_or_org(
            org_id, user_obj.name, 'create_dataset'))
    except Exception:
        return False


def _is_org_member(context, org_id):
    """True when the acting user belongs to the organization with ANY capacity
    (admin/editor/member) -- the audience of its *private* content."""
    if not org_id:
        return False
    user_obj = _user_obj(context)
    if user_obj is None:
        return False
    import ckan.authz as authz
    try:
        return authz.users_role_for_group_or_org(
            org_id, user_obj.name) is not None
    except Exception:
        return False


def can_manage_content_scope(context, project_id, organization_id):
    """THE write authorization for a content row, whatever its scope:
    sysadmin, OR the composite project check (PM/ADM) for project content,
    OR an org admin/editor for organization content."""
    if _is_sysadmin(context):
        return True
    if project_id:
        return can_manage_project(context, project_id)
    if organization_id:
        return _is_org_editor(context, organization_id)
    return False


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


# --- The composite every "may this user edit the project's stuff" check uses -

def can_manage_project(context, project_id):
    """Sysadmin OR the project's admin (PM) OR its initiative's admin (ADM).

    THE write authorization for anything owned by a project: content, data
    connections and the project page. It lives here, not in an action module,
    because ``logic/action/data.py`` happens to define a same-named helper with
    a NARROWER meaning (sysadmin OR PM only, no ADM) -- so importing "the"
    ``_can_manage_project`` from wherever was a coin flip. Callers that want the
    full composite must use this one.
    """
    return (_is_sysadmin(context)
            or _is_project_admin(context, project_id)
            or _is_project_initiative_admin(context, project_id))


def can_edit_project_details(context, project):
    """``can_manage_project`` OR the creator of a NOT-YET-APPROVED project.

    Takes the row, not an id, because both extra facts it needs -- who created
    the project and whether it is approved -- live on it.

    The widening is load-bearing rather than generous. ``can_manage_project``
    is membership-based, and the admin membership row is inserted by
    ``csunesco_project_approve``: until a request is approved, its own author
    is not an admin *of it* and so could neither correct nor resubmit the
    thing they just wrote -- exactly the two states, pending and rejected,
    where fixing a mistake is the entire point.

    Deliberately mirrors the read-side rule in
    ``action/projects._can_view_unapproved``: the author owns their request
    until it is approved, after which the membership row exists and the
    ordinary manager rule takes over on its own. It grants nothing on an
    approved project that ``can_manage_project`` did not already grant.
    """
    if project is None:
        return False
    project_id = (project.get('id') if isinstance(project, dict)
                  else getattr(project, 'id', None))
    if can_manage_project(context, project_id):
        return True
    status = (project.get('status') if isinstance(project, dict)
              else getattr(project, 'status', None))
    if status == 'approved':
        return False
    created_by = (project.get('created_by') if isinstance(project, dict)
                  else getattr(project, 'created_by', None))
    user_obj = _user_obj(context)
    return bool(created_by and user_obj is not None
                and created_by == user_obj.id)


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


def _join_scope_id(data_dict):
    """The project's UUID from whichever key the caller used.

    Resolved rather than passed through, because ``_is_project_admin`` filters
    the raw ``cs_project_member`` columns: handed a slug it matches nothing and
    would deny the project's own admin. The actions accept ``project_slug``
    (the CS Toolbox app sends only that), so the auth has to speak the same
    key set or it becomes the thing that rejects a legitimate reviewer.
    """
    data_dict = data_dict or {}
    key = (data_dict.get('project_id') or data_dict.get('id')
           or data_dict.get('project') or data_dict.get('project_slug'))
    if not key:
        return None
    from ckanext.csunesco import db
    project = db.get_project(key)
    return project.id if project is not None else None


def csunesco_join_approve(context, data_dict):
    project_id = _join_scope_id(data_dict)
    if (_is_sysadmin(context)
            or _is_project_admin(context, project_id)
            or _is_project_initiative_admin(context, project_id)):
        return {'success': True}
    return {'success': False,
            'msg': tk._('Only sysadmins, the project admin or the initiative '
                        'admin can approve join requests')}


def csunesco_join_reject(context, data_dict):
    project_id = _join_scope_id(data_dict)
    if (_is_sysadmin(context)
            or _is_project_admin(context, project_id)
            or _is_project_initiative_admin(context, project_id)):
        return {'success': True}
    return {'success': False,
            'msg': tk._('Only sysadmins, the project admin or the initiative '
                        'admin can reject join requests')}


def csunesco_project_trusted_set(context, data_dict):
    # Policy lever: trusted projects publish news/events without review.
    # Deliberately SYSADMIN-only (not the initiative admin): it disables a
    # review step portal-wide for that project.
    if _is_sysadmin(context):
        return {'success': True}
    return {'success': False,
            'msg': tk._('Only sysadmins can change the trusted flag')}


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


@tk.auth_allow_anonymous_access
def csunesco_member_state_list(context, data_dict):
    # Public read: the member-state groups are public CKAN groups already.
    return {'success': True}


def _project_write_access(context, data_dict, message):
    """Shared gate for the project-detail writes (update / resubmit).

    Only a cheap pre-check: it can say yes on ``can_manage_project`` alone,
    but the creator-of-an-unapproved-project case needs the row, which only
    the action has. So an authenticated caller is let through here and the
    action re-checks with ``can_edit_project_details`` against the RESOLVED
    project -- defence in depth, the ``csunesco_content_create`` shape.
    """
    project_id = ((data_dict or {}).get('id')
                  or (data_dict or {}).get('project_id'))
    if project_id and can_manage_project(context, project_id):
        return {'success': True}
    if context.get('user'):
        return {'success': True}
    return {'success': False, 'msg': message}


def csunesco_project_update(context, data_dict):
    # Sysadmin / project admin / initiative admin, OR the creator of a project
    # that is not approved yet. Resolved for real in the action.
    return _project_write_access(context, data_dict, tk._(
        'You must be logged in to edit a project'))


def csunesco_project_resubmit(context, data_dict):
    # Same set as editing: whoever may fix a rejected request may ask for it
    # to be looked at again. Sending it back to review is not a review
    # decision -- approving it still requires a sysadmin or initiative admin.
    return _project_write_access(context, data_dict, tk._(
        'You must be logged in to resubmit a project'))


# ---------------------------------------------------------------------------
# Admin approval panel + content (Increment 5)
# ---------------------------------------------------------------------------

def csunesco_admin_pending_list(context, data_dict):
    # The panel is visible to any sysadmin, initiative admin, or anyone with a
    # project of their own -- administered OR merely submitted;
    # the action itself scopes what each of them actually sees.
    if (_is_sysadmin(context)
            or _has_own_projects(context)
            or _is_any_initiative_admin(context)):
        return {'success': True}
    return {'success': False,
            'msg': tk._('You do not have access to the approval panel')}


def csunesco_project_review_show(context, data_dict):
    project_id = (data_dict or {}).get('id')
    if _is_sysadmin(context) or _is_project_initiative_admin(
            context, project_id):
        return {'success': True}
    return {'success': False,
            'msg': tk._('Only a project reviewer can see this request')}


def csunesco_content_create(context, data_dict):
    # Sysadmin, the target project's admin/initiative admin (project content)
    # or an org admin/editor (organization content). When neither scope is
    # resolvable from the auth payload we allow any authenticated user through
    # and let the action re-check against the RESOLVED scope (defence in depth).
    project_id = (data_dict or {}).get('project_id')
    owner_org = (data_dict or {}).get('owner_org')
    if project_id:
        if (_is_sysadmin(context)
                or _is_project_admin(context, project_id)
                or _is_project_initiative_admin(context, project_id)):
            return {'success': True}
        return {'success': False,
                'msg': tk._('Only the project admin or the initiative admin '
                            'can add content')}
    if owner_org:
        if _is_sysadmin(context) or _is_org_editor(context, owner_org):
            return {'success': True}
        return {'success': False,
                'msg': tk._('Only an admin or editor of the organization '
                            'can add its content')}
    if context.get('user'):
        return {'success': True}
    return {'success': False,
            'msg': tk._('You must be logged in to add content')}


def csunesco_content_update(context, data_dict):
    project_id = (data_dict or {}).get('project_id')
    organization_id = (data_dict or {}).get('organization_id')
    if project_id or organization_id:
        if can_manage_content_scope(context, project_id, organization_id):
            return {'success': True}
        return {'success': False,
                'msg': tk._('Only the scope\'s managers can edit this '
                            'content')}
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


def csunesco_content_withdraw(context, data_dict):
    # Same moderators as reject: sysadmin or the item's initiative admin.
    # Content with no initiative_group collapses to sysadmin-only because
    # _is_content_initiative_admin returns False on NULL.
    content_id = (data_dict or {}).get('id')
    if _is_sysadmin(context) or _is_content_initiative_admin(context, content_id):
        return {'success': True}
    return {'success': False,
            'msg': tk._('Only sysadmins or the initiative admin can withdraw '
                        'content')}


def csunesco_content_delete(context, data_dict):
    # Hard delete is the nuclear option: sysadmin only (trusted_set pattern).
    if _is_sysadmin(context):
        return {'success': True}
    return {'success': False,
            'msg': tk._('Only sysadmins can delete content')}


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
# Project pages (block-composed landing)
# ---------------------------------------------------------------------------

@tk.auth_allow_anonymous_access
def csunesco_data_source_fields(context, data_dict):
    # Public read: the action serves only APPROVED sources, whose data is
    # already downloadable through the CSV/GeoJSON proxy.
    return {'success': True}


@tk.auth_allow_anonymous_access
def csunesco_data_source_series(context, data_dict):
    # Public read, same reasoning as _fields: an aggregate of data the public
    # can already download row by row.
    return {'success': True}


def csunesco_my_projects(context, data_dict):
    # Inherently per-user: it answers "which projects do *I* administer", so
    # there is nothing an anonymous caller could be asking.
    if context.get('user'):
        return {'success': True}
    return {'success': False, 'msg': tk._('You must be logged in')}


def csunesco_data_chat(context, data_dict):
    """Ask a question about an approved data source's observations.

    Deliberately NOT anonymous, unlike the ``_fields``/``_series`` reads it is
    built on. Those serve cached aggregates of data anyone can already download;
    this one spends a metered call to a paid provider on every request, so it
    needs an account to attribute the spend to and a daily quota to cap it. The
    data itself stays public -- the *asking* is what is gated.
    """
    if context.get('user'):
        return {'success': True}
    return {'success': False,
            'msg': tk._('You must be logged in to ask about the data')}


@tk.auth_allow_anonymous_access
def csunesco_project_page_show(context, data_dict):
    # Public read. The action returns only the PUBLISHED blocks unless the
    # caller both asks for the draft and passes can_manage_project.
    return {'success': True}


def csunesco_project_page_update(context, data_dict):
    # Sysadmin, the project's admin or its initiative admin. When the project
    # is not resolvable here we let any authenticated user through and rely on
    # the action's re-check against the RESOLVED project (defence in depth,
    # same shape as csunesco_content_create).
    project_id = (data_dict or {}).get('project_id')
    if project_id:
        if can_manage_project(context, project_id):
            return {'success': True}
        return {'success': False,
                'msg': tk._('Only the project admin or the initiative admin '
                            'can edit this page')}
    if context.get('user'):
        return {'success': True}
    return {'success': False, 'msg': tk._('You must be logged in')}


def csunesco_project_page_submit(context, data_dict):
    """Publicar la página exige lo mismo que editarla.

    La acción ya llama a ``check_access('csunesco_project_page_update')`` y
    vuelve a comprobar contra el proyecto resuelto, así que esto no abre ni
    cierra ningún permiso. Existe porque era la ÚNICA acción registrada sin
    entrada propia en el registro de auth: cualquier ``check_access`` o
    ``h.check_access`` que algún día la nombre por su nombre real reventaría
    con ``ValueError: Authorization function not found`` — un 500, no un 403.
    """
    return csunesco_project_page_update(context, data_dict)


@tk.auth_allow_anonymous_access
def csunesco_site_page_show(context, data_dict):
    # Public read. The action returns only the PUBLISHED blocks unless the
    # caller both asks for the draft and passes csunesco_site_page_update.
    return {'success': True}


def csunesco_site_page_update(context, data_dict):
    """Only a sysadmin edits the hub page -- it is the portal's front door."""
    if _is_sysadmin(context):
        return {'success': True}
    return {'success': False,
            'msg': tk._('Only a UNESCO administrator can edit the Citizen '
                        'Science home page')}


def csunesco_site_page_publish(context, data_dict):
    """Publishing the hub page requires exactly what editing it does."""
    return csunesco_site_page_update(context, data_dict)


@tk.auth_allow_anonymous_access
def csunesco_initiative_page_show(context, data_dict):
    return {'success': True}


def csunesco_initiative_page_update(context, data_dict):
    initiative = (data_dict or {}).get('initiative')
    if _is_sysadmin(context) or initiative in _admin_initiative_groups(context):
        return {'success': True}
    return {'success': False,
            'msg': tk._('Only the initiative admin can edit this page')}


def csunesco_initiative_page_publish(context, data_dict):
    return csunesco_initiative_page_update(context, data_dict)


def _is_page_initiative_admin(context, project_id):
    """ADM of the project whose page this is."""
    return _is_project_initiative_admin(context, project_id)


def csunesco_project_page_approve(context, data_dict):
    # Sysadmin or the project's initiative admin -- the SAME rule content and
    # data-source approval use. A project's own admin may not approve their own
    # page, which is the whole point of the queue.
    project_id = (data_dict or {}).get('project_id')
    if _is_sysadmin(context) or _is_page_initiative_admin(context, project_id):
        return {'success': True}
    return {'success': False,
            'msg': tk._('Only sysadmins or the initiative admin can approve '
                        'a project page')}


def csunesco_project_page_reject(context, data_dict):
    project_id = (data_dict or {}).get('project_id')
    if _is_sysadmin(context) or _is_page_initiative_admin(context, project_id):
        return {'success': True}
    return {'success': False,
            'msg': tk._('Only sysadmins or the initiative admin can reject '
                        'a project page')}


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
        'csunesco_project_trusted_set': csunesco_project_trusted_set,
        'csunesco_project_request_create': csunesco_project_request_create,
        'csunesco_join_request_create': csunesco_join_request_create,
        'csunesco_project_list': csunesco_project_list,
        'csunesco_project_show': csunesco_project_show,
        'csunesco_project_stats_show': csunesco_project_stats_show,
        'csunesco_aggregate_stats': csunesco_aggregate_stats,
        'csunesco_member_state_list': csunesco_member_state_list,
        'csunesco_project_update': csunesco_project_update,
        'csunesco_project_resubmit': csunesco_project_resubmit,
        'csunesco_admin_pending_list': csunesco_admin_pending_list,
        'csunesco_project_review_show': csunesco_project_review_show,
        'csunesco_content_create': csunesco_content_create,
        'csunesco_content_update': csunesco_content_update,
        'csunesco_content_approve': csunesco_content_approve,
        'csunesco_content_reject': csunesco_content_reject,
        'csunesco_content_withdraw': csunesco_content_withdraw,
        'csunesco_content_delete': csunesco_content_delete,
        'csunesco_content_list': csunesco_content_list,
        'csunesco_content_show': csunesco_content_show,
        'csunesco_data_source_create': csunesco_data_source_create,
        'csunesco_data_source_approve': csunesco_data_source_approve,
        'csunesco_data_source_reject': csunesco_data_source_reject,
        'csunesco_data_source_list': csunesco_data_source_list,
        'csunesco_data_source_show': csunesco_data_source_show,
        'csunesco_data_source_fields': csunesco_data_source_fields,
        'csunesco_data_source_series': csunesco_data_source_series,
        'csunesco_data_chat': csunesco_data_chat,
        'csunesco_my_projects': csunesco_my_projects,
        'csunesco_project_page_show': csunesco_project_page_show,
        'csunesco_project_page_update': csunesco_project_page_update,
        'csunesco_project_page_submit': csunesco_project_page_submit,
        'csunesco_project_page_approve': csunesco_project_page_approve,
        'csunesco_project_page_reject': csunesco_project_page_reject,
        'csunesco_site_page_show': csunesco_site_page_show,
        'csunesco_site_page_update': csunesco_site_page_update,
        'csunesco_site_page_publish': csunesco_site_page_publish,
        'csunesco_initiative_page_show': csunesco_initiative_page_show,
        'csunesco_initiative_page_update': csunesco_initiative_page_update,
        'csunesco_initiative_page_publish':
            csunesco_initiative_page_publish,
        'csunesco_register_citizen_scientist':
            csunesco_register_citizen_scientist,
    }
