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
        return Response(tk._('The data source is temporarily unavailable.'),
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
        return Response(
            json.dumps({'error': 'The data source is temporarily unavailable.'}),
            status=502, mimetype='application/json')
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

    Upstream trouble collapses to a generic 502, exactly like the CSV/GeoJSON
    proxy: a chart that cannot load must say so without leaking anything about
    the upstream. Deliberately does NOT piggyback ``refresh_project_stats`` --
    a page with six chart blocks would otherwise do six DB writes per view.
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
        return Response(
            json.dumps({'error': 'The data source is temporarily unavailable.'}),
            status=502, mimetype='application/json')
    except Exception:
        log.warning('csunesco: %s failed', action_name)
        return Response(
            json.dumps({'error': 'The data source is temporarily unavailable.'}),
            status=502, mimetype='application/json')
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
