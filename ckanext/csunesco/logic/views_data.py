# encoding: utf-8
"""HTTP orchestration for the app-data pipeline: live proxy + connect flow.

Thin views (same contract as ``logic/views.py``): build a context, call the
``csunesco_data_source_*`` actions (never the ORM) and render, stream or
Post/Redirect/Get.

The two proxy routes serve ONLY approved data sources; they fetch ofform's
public endpoints through ``logic/ofform.py`` (config-pinned base URL, TTL
cache, size cap) and surface any upstream problem as a generic 502 so nothing
internal leaks.
"""
import json
import logging

from flask import request, Response

import ckan.plugins.toolkit as tk
import ckan.model as model

log = logging.getLogger(__name__)

GENERIC_ERROR = 'Something went wrong. Please try again.'

# Matches the proxy TTL: downstream caches (browser/Terria) may hold a response
# for this long, which keeps repeat map interactions cheap.
_CACHE_CONTROL = 'public, max-age=60'

# Machine-readable failure reasons in the JSON error envelope. The HTTP status
# stays 502 for BOTH -- the request really did fail at the gateway, and 503
# would claim THIS server is down when it is fine -- and the reason is what
# lets a chart tell "the app's form went dark, it will come back" apart from
# "something in the portal broke". Same shape as the chat envelope's ``status``
# below, which the browser already parses on a non-OK response; the GeoJSON
# proxy is also consumed by Terria, and an extra JSON key is ignorable where a
# changed status code would alter its retry and caching behaviour.
#
# NOT translated, and never displayed: the reason is compared literally in JS,
# which paints its OWN already-translated data-label-*. The CSV proxy's
# text/plain body IS read by a human, so that one keeps tk._().
UPSTREAM_UNAVAILABLE = 'upstream_unavailable'
INTERNAL_ERROR = 'internal_error'
UNAVAILABLE_MESSAGE = 'The data source is temporarily unavailable.'


def _upstream_error(reason):
    """502 JSON envelope tagged so the client can word its own message."""
    return Response(
        json.dumps({'error': UNAVAILABLE_MESSAGE, 'reason': reason}),
        status=502, mimetype='application/json')


def _context():
    return {'model': model, 'session': model.Session, 'user': tk.g.user}


def _not_authorized_response():
    if not tk.g.user:
        return tk.redirect_to('user.login')
    return tk.abort(403, tk._('You are not authorized to view this page'))


def _approved_source(id):
    """Resolve an APPROVED data source dict, or ``None`` (callers 404)."""
    try:
        source = tk.get_action('csunesco_data_source_show')(
            _context(), {'id': id})
    except (tk.ObjectNotFound, tk.NotAuthorized):
        return None
    except Exception:
        log.warning('csunesco: data source could not be loaded')
        return None
    # The action already hides unapproved rows from the public, but a manager
    # CAN see their own pending row -- the proxy must still refuse to serve it.
    if source.get('status') != 'approved':
        return None
    return source


# ---------------------------------------------------------------------------
# Live proxy (public, approved sources only)
# ---------------------------------------------------------------------------

def data_source_csv(id):
    """Stream the form's public CSV export (live, TTL-cached)."""
    source = _approved_source(id)
    if source is None:
        return tk.abort(404, tk._('Data source not found'))
    from ckanext.csunesco.logic import ofform
    try:
        text = ofform.fetch_csv(source['form_id'])
    except ofform.OfformError:
        # text/plain a human may actually read in a browser tab, so unlike the
        # JSON envelopes this one stays translated.
        return Response(tk._(UNAVAILABLE_MESSAGE),
                        status=502, mimetype='text/plain')
    response = Response(text, mimetype='text/csv')
    response.headers['Content-Disposition'] = (
        'attachment; filename="cs-data-{0}.csv"'.format(source['form_id']))
    response.headers['Cache-Control'] = _CACHE_CONTROL
    return response


def data_source_geojson(id):
    """Serve the form's observations as GeoJSON (live, TTL-cached)."""
    source = _approved_source(id)
    if source is None:
        return tk.abort(404, tk._('Data source not found'))
    from ckanext.csunesco.logic import ofform
    try:
        data = ofform.fetch_dashboard_data(source['form_id'])
        geojson = ofform.rows_to_geojson(data)
    except ofform.OfformError:
        return _upstream_error(UPSTREAM_UNAVAILABLE)
    # Piggyback: the freshly fetched data keeps the project's observation
    # counters current (every landing-page map view refreshes them). Fail-soft.
    try:
        from ckanext.csunesco.logic.action.data import refresh_project_stats
        refresh_project_stats(source['project_id'])
    except Exception:
        log.warning('csunesco: stats refresh from proxy failed')
    response = Response(json.dumps(geojson), mimetype='application/json')
    response.headers['Cache-Control'] = _CACHE_CONTROL
    return response


def _json_action(action_name, data_dict):
    """Run a read action and return its result as a JSON response.

    Upstream trouble and an unexpected exception both answer 502 -- neither
    leaks a detail about the upstream -- but they carry different ``reason``
    codes, so the chart JS can tell a temporary source outage from a bug on our
    side and word its message accordingly. Deliberately does NOT piggyback
    ``refresh_project_stats`` -- a page with six chart blocks would otherwise do
    six DB writes per view.
    """
    from ckanext.csunesco.logic import ofform
    try:
        result = tk.get_action(action_name)(_context(), data_dict)
    except tk.ObjectNotFound:
        return tk.abort(404, tk._('Data source not found'))
    except tk.ValidationError as error:
        return Response(json.dumps({'error': error.error_dict}),
                        status=400, mimetype='application/json')
    except ofform.OfformError:
        # Not our bug: the CS Toolbox form is unreachable, unpublished or no
        # longer public. Already logged with its form id by ofform._fetch, at
        # the rate the negative cache allows.
        return _upstream_error(UPSTREAM_UNAVAILABLE)
    except Exception:
        log.warning('csunesco: %s failed', action_name)
        return _upstream_error(INTERNAL_ERROR)
    response = Response(json.dumps(result), mimetype='application/json')
    response.headers['Cache-Control'] = _CACHE_CONTROL
    return response


def data_source_series(id):
    """Aggregated chart series for an approved data source (live, TTL-cached)."""
    data_dict = {'id': id}
    for key in ('mode', 'field', 'agg', 'group_by', 'bucket', 'range',
                'start', 'end', 'project_id', 'max_series', 'max_categories'):
        value = request.args.get(key)
        if value is not None:
            data_dict[key] = value
    return _json_action('csunesco_data_source_series', data_dict)


def data_source_fields(id):
    """The chartable field list of an approved data source (editor picker)."""
    return _json_action('csunesco_data_source_fields', {'id': id})


# Never cached: an answer is per-user, per-question, and charged to a quota.
_NO_STORE = 'no-store'


def _chat_json(payload, status=200):
    response = Response(json.dumps(payload), status=status,
                        mimetype='application/json')
    response.headers['Cache-Control'] = _NO_STORE
    return response


def data_source_chat(id):
    """Answer one plain-language question about an approved data source.

    XHR-only, so every outcome is JSON: a browser fetch cannot follow the login
    redirect the other views hand to anonymous visitors, and a redirect body
    parsed as JSON is the kind of failure that looks like a bug in the chat.
    """
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _chat_json({'status': 'bad_request'}, status=400)

    data_dict = {
        'id': id,
        'question': body.get('question'),
        'history': body.get('history'),
        'language': body.get('language'),
    }
    try:
        result = tk.get_action('csunesco_data_chat')(_context(), data_dict)
    except tk.NotAuthorized:
        return _chat_json({'status': 'unauthenticated'}, status=403)
    except tk.ObjectNotFound:
        return _chat_json({'status': 'not_found'}, status=404)
    except tk.ValidationError as error:
        return _chat_json({'status': 'bad_request',
                           'errors': error.error_dict}, status=400)
    except Exception:
        # The action already degrades to an 'unavailable' envelope for the
        # failures it can foresee; anything reaching here is ours, so it is
        # logged and answered generically rather than surfaced.
        log.warning('csunesco: data chat failed')
        return _chat_json({'status': 'unavailable'}, status=502)
    return _chat_json(result)


# ---------------------------------------------------------------------------
# Connect flow (project managers)
# ---------------------------------------------------------------------------

def _render_connect_form(project, data, errors):
    return tk.render('csunesco/data_connect_form.html', extra_vars={
        'project': project,
        'data': data,
        'errors': errors,
    })


def data_connect(slug):
    """GET the connect-data form for project ``slug``; POST creates the request."""
    if not tk.g.user:
        return _not_authorized_response()

    context = _context()
    try:
        project = tk.get_action('csunesco_project_show')(
            context, {'slug': slug})
    except tk.ObjectNotFound:
        return tk.abort(404, tk._('Project not found'))
    except tk.NotAuthorized:
        return _not_authorized_response()
    except Exception:
        log.warning('csunesco: connect form could not resolve project')
        return tk.abort(404, tk._('Project not found'))

    if not tk.h.csunesco_can_manage_project(project.get('id')):
        return _not_authorized_response()

    if request.method == 'GET':
        return _render_connect_form(project, {}, {})

    # --- POST ---------------------------------------------------------------
    form = request.form
    data = {
        'form_id': (form.get('form_id') or '').strip(),
        'title': (form.get('title') or '').strip(),
        'description': (form.get('description') or '').strip(),
    }
    data_dict = dict(data)
    data_dict['project_id'] = project['id']
    try:
        result = tk.get_action('csunesco_data_source_create')(
            context, data_dict)
    except tk.NotAuthorized:
        return _not_authorized_response()
    except tk.ValidationError as error:
        return _render_connect_form(project, data, error.error_dict or {})
    except Exception:
        log.warning('csunesco: data source could not be created')
        return _render_connect_form(project, data, {'message': GENERIC_ERROR})

    if result.get('already_requested'):
        tk.h.flash_notice(tk._(
            'That form is already connected (status: %s).') % result['status'])
    else:
        tk.h.flash_success(tk._(
            'Your data has been submitted and is awaiting review. It will '
            'appear on the project page once a UNESCO administrator '
            'approves it.'))
    return tk.redirect_to('csunesco.project_landing', slug=project['slug'])


def data_viewer():
    """Portal-wide data viewer (spec section 4): every APPROVED data source.

    A public index over the per-project data connections: each row links to
    its project's landing page (where the charts and the map live) and to the
    raw CSV/GeoJSON proxies. The action pins anonymous callers to approved
    sources, so nothing unreviewed can appear here.
    """
    from ckanext.csunesco.logic.views import (
        _decorate_projects, _positive_int as _pos)

    page = _pos(request.args.get('page'), 1)
    per_page = 20
    try:
        listing = tk.get_action('csunesco_data_source_list')(_context(), {
            'limit': per_page,
            'offset': (page - 1) * per_page,
        })
    except Exception:
        log.warning('csunesco: data viewer listing unavailable')
        listing = {'count': 0, 'results': []}

    sources = listing.get('results') or []
    # ONE bounded project sweep for titles/slugs instead of a show() per row.
    projects = {}
    try:
        project_listing = tk.get_action('csunesco_project_list')(
            _context(), {'limit': 100})
        for project in project_listing.get('results') or []:
            projects[project['id']] = project
    except Exception:
        log.warning('csunesco: data viewer projects unavailable')
    for source in sources:
        project = projects.get(source.get('project_id')) or {}
        source['project_title'] = project.get('title')
        source['project_slug'] = project.get('slug')

    count = listing.get('count', 0)
    total_pages = max(1, (count + per_page - 1) // per_page)
    return tk.render('csunesco/data_viewer.html', extra_vars={
        'sources': sources,
        'count': count,
        'page': page,
        'total_pages': total_pages,
    })
