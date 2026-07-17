# encoding: utf-8
"""HTTP orchestration for the public Citizen Science presentation layer.

Increment 4: these view functions are deliberately THIN. Each one builds a CKAN
context, calls one or more ``csunesco_*`` actions (NEVER the DB / ORM directly),
and either renders a template or issues a Post/Redirect/Get. All domain logic
lives in the action layer; the views only translate between HTTP and actions and
map action exceptions to the right HTTP response:

  * ``tk.ObjectNotFound``  -> 404
  * ``tk.NotAuthorized``   -> redirect to login (anonymous) / 403 (logged in)
  * ``tk.ValidationError`` -> re-render the form with field errors
  * anything unexpected     -> a GENERIC message, never internals

The blueprint wraps these behind lazily-imported thin functions so there is no
import-time dependency on CKAN internals.
"""
import logging

from flask import request, Response

import ckan.plugins.toolkit as tk
import ckan.model as model

from ckanext.csunesco import constants

log = logging.getLogger(__name__)

# Single generic message for unexpected failures -- never leak internals.
GENERIC_ERROR = 'Something went wrong. Please try again.'

# The parent group whose active children are the valid member states.
MEMBER_STATES_GROUP = 'member-states'

# Server-side page size for the public project listing.
PROJECTS_PER_PAGE = 12

# name -> title map for decorating list rows with a human initiative label.
_INITIATIVE_TITLES = {
    initiative['name']: initiative['title']
    for initiative in constants.CS_INITIATIVES
}


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------

def _context():
    """Build the standard CKAN action context for the acting request."""
    return {'model': model, 'session': model.Session, 'user': tk.g.user}


def _positive_int(value, default):
    """Coerce ``value`` to a positive int, falling back to ``default``."""
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    return result if result >= 1 else default


def _not_authorized_response():
    """Redirect anonymous users to log in; deny logged-in users with 403."""
    if not tk.g.user:
        return tk.redirect_to('user.login')
    return tk.abort(403, tk._('You are not authorized to view this page'))


def _decorate_projects(projects):
    """Attach a human ``initiative_title`` to each list row for display."""
    for project in projects:
        project['initiative_title'] = _INITIATIVE_TITLES.get(
            project.get('initiative_group'), project.get('initiative_group'))
    return projects


def _member_state_choices():
    """Member-state options for the country multi-select (empty on any error).

    Reads the active child groups of the ``member-states`` parent group through
    the core ``group_show`` action (water-family pattern) so the view never
    queries the DB directly. Fails soft: an un-seeded deployment just yields an
    empty option list and the form still works.
    """
    try:
        parent = tk.get_action('group_show')(
            _context(),
            {'id': MEMBER_STATES_GROUP, 'include_groups': True},
        )
    except Exception:
        return []
    choices = []
    for child in (parent.get('groups') or []):
        name = child.get('name')
        if not name:
            continue
        choices.append({'name': name, 'title': child.get('title') or name})
    return sorted(choices, key=lambda c: c['title'].lower())


# ---------------------------------------------------------------------------
# Public read views
# ---------------------------------------------------------------------------

def hub():
    """Public Citizen Science hub: initiatives + recent projects + at-a-glance."""
    try:
        listing = tk.get_action('csunesco_project_list')(
            _context(), {'limit': 6, 'offset': 0})
    except Exception:
        log.warning('csunesco: hub project list unavailable')
        listing = {'results': [], 'count': 0}

    return tk.render('csunesco/citizen-science.html', extra_vars={
        'initiatives': constants.CS_INITIATIVES,
        'projects': _decorate_projects(listing.get('results', [])),
        'project_count': listing.get('count', 0),
    })


def initiative_index(name):
    """Initiative page: header + the approved projects filed under it."""
    initiative = next(
        (i for i in constants.CS_INITIATIVES if i['name'] == name), None)
    if initiative is None:
        return tk.abort(404, tk._('Initiative not found'))

    try:
        listing = tk.get_action('csunesco_project_list')(
            _context(), {'initiative': name, 'limit': 50, 'offset': 0})
    except Exception:
        log.warning('csunesco: initiative project list unavailable')
        listing = {'results': [], 'count': 0}

    return tk.render('csunesco/initiative.html', extra_vars={
        'initiative': initiative,
        'projects': _decorate_projects(listing.get('results', [])),
        'project_count': listing.get('count', 0),
    })


def project_list():
    """Public project listing with initiative + q filters and paging."""
    page = _positive_int(request.args.get('page'), 1)
    initiative = (request.args.get('initiative') or '').strip()
    q = (request.args.get('q') or '').strip()

    data_dict = {
        'limit': PROJECTS_PER_PAGE,
        'offset': (page - 1) * PROJECTS_PER_PAGE,
    }
    if initiative:
        data_dict['initiative'] = initiative
    if q:
        data_dict['q'] = q

    try:
        listing = tk.get_action('csunesco_project_list')(_context(), data_dict)
    except Exception:
        # ValidationError (e.g. an unknown initiative filter) and any unexpected
        # error both collapse to an empty, safe result set.
        log.warning('csunesco: project list unavailable')
        listing = {'results': [], 'count': 0}

    count = listing.get('count', 0)
    total_pages = max(1, (count + PROJECTS_PER_PAGE - 1) // PROJECTS_PER_PAGE)

    return tk.render('csunesco/project_list.html', extra_vars={
        'projects': _decorate_projects(listing.get('results', [])),
        'count': count,
        'page': page,
        'total_pages': total_pages,
        'initiatives': constants.CS_INITIATIVES,
        'selected_initiative': initiative,
        'q': q,
    })


def project_landing(slug):
    """Public project landing page (hero, stats, region map, join block)."""
    try:
        project = tk.get_action('csunesco_project_show')(
            _context(), {'slug': slug, 'include_geojson': True})
    except tk.ObjectNotFound:
        return tk.abort(404, tk._('Project not found'))
    except tk.NotAuthorized:
        return _not_authorized_response()
    except Exception:
        log.warning('csunesco: project landing could not be loaded')
        return tk.abort(404, tk._('Project not found'))

    # Advisor refinement: DO NOT embed the (potentially large) GeoJSON in the
    # HTML. Keep only a boolean so the template can render its skeleton/fallback;
    # the map JS fetches the payload asynchronously from the /geojson endpoint.
    has_region = bool(project.get('region_geojson'))
    project.pop('region_geojson', None)
    project['initiative_title'] = _INITIATIVE_TITLES.get(
        project.get('initiative_group'), project.get('initiative_group'))

    # Recent news/events for this project (summarized cards). Fetched HERE via
    # the action so the template never calls actions from Jinja. Fails soft to an
    # empty list so a content error never breaks the landing page.
    try:
        content_listing = tk.get_action('csunesco_content_list')(
            _context(), {'project_id': project['id'], 'limit': 6, 'offset': 0})
        news_events = content_listing.get('results', [])
    except Exception:
        log.warning('csunesco: project landing news/events unavailable')
        news_events = []

    return tk.render('csunesco/project_landing.html', extra_vars={
        'project': project,
        'stats': project.get('stats') or {},
        'has_region': has_region,
        'news_events': news_events,
    })


def project_geojson(slug):
    """Serve a project's region GeoJSON as ``application/json`` (async source).

    A lightweight, separate endpoint so the landing page stays small and the map
    loads its geometry on demand. Returns ``204 No Content`` when the project has
    no region, which the map JS treats as its "no region" fallback signal.
    """
    try:
        project = tk.get_action('csunesco_project_show')(
            _context(), {'slug': slug, 'include_geojson': True})
    except tk.ObjectNotFound:
        return tk.abort(404, tk._('Project not found'))
    except tk.NotAuthorized:
        return _not_authorized_response()
    except Exception:
        log.warning('csunesco: project geojson could not be loaded')
        return tk.abort(404, tk._('Project not found'))

    raw = project.get('region_geojson')
    if not raw:
        return Response(status=204)
    # ``region_geojson`` was validated + normalized to a JSON string on the way
    # in (csunesco_valid_geojson), so it is safe to serve verbatim.
    return Response(raw, mimetype='application/json')


# ---------------------------------------------------------------------------
# Write views (Post/Redirect/Get)
# ---------------------------------------------------------------------------

def _render_project_form(data, errors, success=False):
    """Render the project-request form with echoed values + field errors."""
    return tk.render('csunesco/project_request.html', extra_vars={
        'data': data,
        'errors': errors,
        'success': success,
        'initiatives': constants.CS_INITIATIVES,
        'member_states': _member_state_choices(),
    })


def _read_project_form():
    """Read the project-request POST into an action ``data_dict``."""
    form = request.form
    return {
        'title': (form.get('title') or '').strip(),
        'initiative': (form.get('initiative') or '').strip(),
        'countries': [c for c in form.getlist('countries') if c],
        'biosphere_reserve': (form.get('biosphere_reserve') or '').strip(),
        'region_geojson': (form.get('region_geojson') or '').strip(),
        'short_description': (form.get('short_description') or '').strip(),
        'project_document_url':
            (form.get('project_document_url') or '').strip(),
    }


def project_new():
    """GET the project-request form; POST creates a PENDING project request."""
    if request.method == 'GET':
        # The PRG target lands here with ?submitted=1 -> show the success state.
        if request.args.get('submitted'):
            return _render_project_form({}, {}, success=True)
        return _render_project_form({}, {})

    # --- POST ---------------------------------------------------------------
    if not tk.g.user:
        return _not_authorized_response()

    data_dict = _read_project_form()
    try:
        tk.get_action('csunesco_project_request_create')(_context(), data_dict)
    except tk.NotAuthorized:
        return _not_authorized_response()
    except tk.ValidationError as error:
        return _render_project_form(data_dict, error.error_dict or {})
    except Exception:
        log.warning('csunesco: project request could not be created')
        return _render_project_form(data_dict, {'message': GENERIC_ERROR})

    # PRG: flash + redirect to the GET success state so a refresh cannot resend.
    tk.h.flash_success(tk._(
        'Your project request has been submitted and is awaiting review.'))
    return tk.redirect_to('csunesco.project_new', submitted=1)


def join_project(slug):
    """POST: request to join a project, then PRG back to its landing page."""
    if not tk.g.user:
        tk.h.flash_notice(tk._('Please log in to join this project.'))
        return tk.redirect_to('user.login')

    context = _context()
    # Resolve the project first so a valid redirect target exists on every path.
    try:
        project = tk.get_action('csunesco_project_show')(
            context, {'slug': slug})
    except tk.ObjectNotFound:
        return tk.abort(404, tk._('Project not found'))
    except tk.NotAuthorized:
        return _not_authorized_response()
    except Exception:
        log.warning('csunesco: join could not resolve project')
        return tk.abort(404, tk._('Project not found'))

    try:
        result = tk.get_action('csunesco_join_request_create')(
            context, {'project_id': project['id']})
    except tk.NotAuthorized:
        return _not_authorized_response()
    except tk.ValidationError:
        tk.h.flash_error(tk._('This project is not open for join requests.'))
        return tk.redirect_to('csunesco.project_landing', slug=slug)
    except Exception:
        log.warning('csunesco: join request could not be created')
        tk.h.flash_error(tk._(GENERIC_ERROR))
        return tk.redirect_to('csunesco.project_landing', slug=slug)

    if result.get('already_requested'):
        tk.h.flash_notice(tk._(
            'You have already requested to join this project.'))
    else:
        tk.h.flash_success(tk._(
            'Your request to join has been submitted and is awaiting '
            'approval.'))
    return tk.redirect_to('csunesco.project_landing', slug=slug)
