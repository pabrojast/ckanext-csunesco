# encoding: utf-8
"""Flask blueprint for ckanext-csunesco.

Increment 4: the full PUBLIC presentation layer under ``/citizen-science`` --
the hub, initiative pages, the project listing, project landings, the async
region-GeoJSON endpoint, the project-request form and the join action -- plus
the Citizen Scientist self-registration form kept from increment 2. The admin
approval panel, news/events and the ofform bridge arrive later (see .mix/plan.md).

Every view here is a THIN wrapper that lazily imports ``logic.views`` (or
``logic.registration``) so the blueprint has no import-time dependency on CKAN
action/model internals. All orchestration lives in ``logic/views.py``.
"""
from flask import Blueprint

csunesco_bp = Blueprint(
    'csunesco', __name__, url_prefix='/citizen-science',
)


def index():
    """Public Citizen Science hub."""
    from ckanext.csunesco.logic import views
    return views.hub()


def initiative_index(name):
    """Single initiative page (header + its approved projects)."""
    from ckanext.csunesco.logic import views
    return views.initiative_index(name)


def project_list():
    """Filterable, paginated public project listing."""
    from ckanext.csunesco.logic import views
    return views.project_list()


def project_new():
    """Project-request form (GET) / create request (POST)."""
    from ckanext.csunesco.logic import views
    return views.project_new()


def project_landing(slug):
    """Public landing page for a single project."""
    from ckanext.csunesco.logic import views
    return views.project_landing(slug)


def project_geojson(slug):
    """Async region GeoJSON for a project's map (application/json)."""
    from ckanext.csunesco.logic import views
    return views.project_geojson(slug)


def join_project(slug):
    """POST: request to join a project (PRG back to its landing)."""
    from ckanext.csunesco.logic import views
    return views.join_project(slug)


def register_citizen():
    """Citizen Scientist self-registration (GET form / POST create account)."""
    from ckanext.csunesco.logic import registration
    return registration.register_citizen()


def verify_citizen(token):
    """GET: activate a pending Citizen Scientist account via its email token."""
    from ckanext.csunesco.logic import registration
    return registration.verify_citizen(token)


def resend_verification():
    """GET form / POST re-issue a Citizen Scientist verification link."""
    from ckanext.csunesco.logic import registration
    return registration.resend_verification()


# ---------------------------------------------------------------------------
# Increment 5, Part A -- admin approval panel (thin lazy wrappers).
# ---------------------------------------------------------------------------

def admin_dashboard():
    """Approval panel: pending projects / joins / content (role-scoped)."""
    from ckanext.csunesco.logic import views_admin
    return views_admin.admin_dashboard()


def project_approve(id):
    """POST: approve a pending project (PRG back to the panel)."""
    from ckanext.csunesco.logic import views_admin
    return views_admin.project_approve(id)


def project_reject(id):
    """POST: reject a pending project with an optional reason."""
    from ckanext.csunesco.logic import views_admin
    return views_admin.project_reject(id)


def join_approve(project_id, user_id):
    """POST: approve a pending join request."""
    from ckanext.csunesco.logic import views_admin
    return views_admin.join_approve(project_id, user_id)


def join_reject(project_id, user_id):
    """POST: reject a pending join request."""
    from ckanext.csunesco.logic import views_admin
    return views_admin.join_reject(project_id, user_id)


def content_approve(id):
    """POST: approve pending content."""
    from ckanext.csunesco.logic import views_admin
    return views_admin.content_approve(id)


def content_reject(id):
    """POST: reject content with an optional reason."""
    from ckanext.csunesco.logic import views_admin
    return views_admin.content_reject(id)


def content_bulk_approve():
    """POST: approve a checkbox selection of content rows (best-effort)."""
    from ckanext.csunesco.logic import views_admin
    return views_admin.content_bulk_approve()


def project_trusted_set(slug):
    """POST: toggle a project's trusted flag (sysadmin-only)."""
    from ckanext.csunesco.logic import views_admin
    return views_admin.project_trusted_set(slug)


# ---------------------------------------------------------------------------
# Increment 5, Part B -- public news/events + content editor.
# ---------------------------------------------------------------------------

def cs_news_index():
    """Public paginated news index."""
    from ckanext.csunesco.logic import views_content
    return views_content.cs_news_index()


def cs_news_show(slug):
    """Public news detail page (sanitized body)."""
    from ckanext.csunesco.logic import views_content
    return views_content.cs_news_show(slug)


def cs_events_index():
    """Public paginated events index."""
    from ckanext.csunesco.logic import views_content
    return views_content.cs_events_index()


def cs_events_show(slug):
    """Public event detail page (sanitized body)."""
    from ckanext.csunesco.logic import views_content
    return views_content.cs_events_show(slug)


def cs_publications_index():
    """Public paginated publications index."""
    from ckanext.csunesco.logic import views_content
    return views_content.cs_publications_index()


def cs_publications_show(slug):
    """Public publication detail page (documents + sanitized body)."""
    from ckanext.csunesco.logic import views_content
    return views_content.cs_publications_show(slug)


def cs_maps_index():
    """Public paginated maps index."""
    from ckanext.csunesco.logic import views_content
    return views_content.cs_maps_index()


def cs_maps_show(slug):
    """Public map detail page (allowlisted Terria embed)."""
    from ckanext.csunesco.logic import views_content
    return views_content.cs_maps_show(slug)


def content_new(slug):
    """Content editor for a project (GET form / POST create)."""
    from ckanext.csunesco.logic import views_content
    return views_content.content_new(slug)


# ---------------------------------------------------------------------------
# App-data pipeline -- live proxy, connect flow and its admin decisions.
# ---------------------------------------------------------------------------

def data_source_csv(id):
    """Live CSV proxy for an APPROVED data source (public)."""
    from ckanext.csunesco.logic import views_data
    return views_data.data_source_csv(id)


def data_source_geojson(id):
    """Live GeoJSON proxy for an APPROVED data source (public)."""
    from ckanext.csunesco.logic import views_data
    return views_data.data_source_geojson(id)


def data_connect(slug):
    """Connect-app-data form for a project (GET form / POST request)."""
    from ckanext.csunesco.logic import views_data
    return views_data.data_connect(slug)


def data_source_approve(id):
    """POST: approve a pending data source (creates the CKAN dataset)."""
    from ckanext.csunesco.logic import views_admin
    return views_admin.data_source_approve(id)


def data_source_reject(id):
    """POST: reject a pending data source with an optional reason."""
    from ckanext.csunesco.logic import views_admin
    return views_admin.data_source_reject(id)


def content_edit(id):
    """Content editor for an existing item (GET form / POST update)."""
    from ckanext.csunesco.logic import views_content
    return views_content.content_edit(id)


# ---------------------------------------------------------------------------
# Routes (all under the blueprint's /citizen-science prefix). ``/project/new``
# is registered before ``/project/<slug>`` for clarity; Flask matches the static
# rule first regardless, so "new" is never captured as a slug.
# ---------------------------------------------------------------------------
csunesco_bp.add_url_rule('/', 'index', index, methods=['GET'])
csunesco_bp.add_url_rule(
    '/initiative/<name>', 'initiative_index', initiative_index,
    methods=['GET'],
)
csunesco_bp.add_url_rule('/projects', 'project_list', project_list,
                         methods=['GET'])
csunesco_bp.add_url_rule('/project/new', 'project_new', project_new,
                         methods=['GET', 'POST'])
csunesco_bp.add_url_rule('/project/<slug>', 'project_landing', project_landing,
                         methods=['GET'])
csunesco_bp.add_url_rule(
    '/project/<slug>/geojson', 'project_geojson', project_geojson,
    methods=['GET'],
)
csunesco_bp.add_url_rule('/project/<slug>/join', 'join_project', join_project,
                         methods=['POST'])
# Resolves to /citizen-science/register-citizen (blueprint prefix). Parallel to
# CKAN's /user/register but with no organization step. Keep the endpoint name
# ``register_citizen`` stable -- templates and tests reference it.
csunesco_bp.add_url_rule(
    '/register-citizen', 'register_citizen', register_citizen,
    methods=['GET', 'POST'],
)
# Email verification for the web self-registration flow. ``/verify/resend`` is a
# static rule so Flask always matches it before the ``/verify/<token>`` pattern,
# regardless of registration order.
csunesco_bp.add_url_rule(
    '/verify/resend', 'resend_verification', resend_verification,
    methods=['GET', 'POST'],
)
csunesco_bp.add_url_rule(
    '/verify/<token>', 'verify_citizen', verify_citizen, methods=['GET'],
)

# ---------------------------------------------------------------------------
# Increment 5 routes -- all still under the blueprint's /citizen-science prefix
# so they never collide with CKAN core (admin at /citizen-science/admin, news at
# /citizen-science/news, events at /citizen-science/events).
# ---------------------------------------------------------------------------

# Admin approval panel.
csunesco_bp.add_url_rule('/admin', 'admin_dashboard', admin_dashboard,
                         methods=['GET'])
csunesco_bp.add_url_rule(
    '/admin/project/<id>/approve', 'project_approve', project_approve,
    methods=['POST'])
csunesco_bp.add_url_rule(
    '/admin/project/<id>/reject', 'project_reject', project_reject,
    methods=['POST'])
csunesco_bp.add_url_rule(
    '/admin/join/<project_id>/<user_id>/approve', 'join_approve', join_approve,
    methods=['POST'])
csunesco_bp.add_url_rule(
    '/admin/join/<project_id>/<user_id>/reject', 'join_reject', join_reject,
    methods=['POST'])
csunesco_bp.add_url_rule(
    '/admin/content/<id>/approve', 'content_approve', content_approve,
    methods=['POST'])
csunesco_bp.add_url_rule(
    '/admin/content/<id>/reject', 'content_reject', content_reject,
    methods=['POST'])
csunesco_bp.add_url_rule(
    '/admin/content/bulk-approve', 'content_bulk_approve',
    content_bulk_approve, methods=['POST'])
csunesco_bp.add_url_rule(
    '/project/<slug>/trusted', 'project_trusted_set', project_trusted_set,
    methods=['POST'])
csunesco_bp.add_url_rule(
    '/admin/data/<id>/approve', 'data_source_approve', data_source_approve,
    methods=['POST'])
csunesco_bp.add_url_rule(
    '/admin/data/<id>/reject', 'data_source_reject', data_source_reject,
    methods=['POST'])

# Public news + events.
csunesco_bp.add_url_rule('/news', 'cs_news_index', cs_news_index,
                         methods=['GET'])
csunesco_bp.add_url_rule('/news/<slug>', 'cs_news_show', cs_news_show,
                         methods=['GET'])
csunesco_bp.add_url_rule('/events', 'cs_events_index', cs_events_index,
                         methods=['GET'])
csunesco_bp.add_url_rule('/events/<slug>', 'cs_events_show', cs_events_show,
                         methods=['GET'])
csunesco_bp.add_url_rule(
    '/publications', 'cs_publications_index', cs_publications_index,
    methods=['GET'])
csunesco_bp.add_url_rule(
    '/publications/<slug>', 'cs_publications_show', cs_publications_show,
    methods=['GET'])
csunesco_bp.add_url_rule('/maps', 'cs_maps_index', cs_maps_index,
                         methods=['GET'])
csunesco_bp.add_url_rule('/maps/<slug>', 'cs_maps_show', cs_maps_show,
                         methods=['GET'])

# Content editor (create under a project / edit an existing item).
csunesco_bp.add_url_rule(
    '/project/<slug>/content/new', 'content_new', content_new,
    methods=['GET', 'POST'])
csunesco_bp.add_url_rule('/content/<id>/edit', 'content_edit', content_edit,
                         methods=['GET', 'POST'])

# App-data pipeline: public live proxy + the manager connect flow.
csunesco_bp.add_url_rule(
    '/data/<id>.csv', 'data_source_csv', data_source_csv, methods=['GET'])
csunesco_bp.add_url_rule(
    '/data/<id>.geojson', 'data_source_geojson', data_source_geojson,
    methods=['GET'])
csunesco_bp.add_url_rule(
    '/project/<slug>/data/connect', 'data_connect', data_connect,
    methods=['GET', 'POST'])


def get_blueprints():
    return [csunesco_bp]
