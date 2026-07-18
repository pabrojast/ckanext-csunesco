# encoding: utf-8
"""HTTP orchestration for the public news/events pages + the content editor.

Increment 5, Part B. Thin views (same contract as ``logic/views.py``): build a
context, call the ``csunesco_content_*`` actions (never the ORM) and render or
Post/Redirect/Get. Public index/detail pages read approved content; the editor
(``content_new`` / ``content_edit``) is gated by the action's own authorization
and maps ``ValidationError`` back to inline field errors.
"""
import logging

from flask import request

import ckan.plugins.toolkit as tk
import ckan.model as model

log = logging.getLogger(__name__)

GENERIC_ERROR = 'Something went wrong. Please try again.'

# Page size for the public /news and /events indexes.
CONTENT_PER_PAGE = 9

# content_type -> (list template, detail template, list endpoint, show endpoint).
_TYPE_VIEW = {
    'cs-news': ('csunesco/cs-news_list.html', 'csunesco/cs-news.html',
                'csunesco.cs_news_index', 'csunesco.cs_news_show'),
    'cs-event': ('csunesco/cs-events_list.html', 'csunesco/cs-events.html',
                 'csunesco.cs_events_index', 'csunesco.cs_events_show'),
    'cs-publication': (
        'csunesco/cs-publications_list.html', 'csunesco/cs-publications.html',
        'csunesco.cs_publications_index', 'csunesco.cs_publications_show'),
    'cs-map': ('csunesco/cs-maps_list.html', 'csunesco/cs-maps.html',
               'csunesco.cs_maps_index', 'csunesco.cs_maps_show'),
}

# Selectable content types for the editor form.
_CONTENT_TYPE_CHOICES = [
    {'value': 'cs-news', 'label': 'News'},
    {'value': 'cs-event', 'label': 'Event'},
    {'value': 'cs-publication', 'label': 'Publication'},
    {'value': 'cs-map', 'label': 'Map'},
]


def _context():
    return {'model': model, 'session': model.Session, 'user': tk.g.user}


def _positive_int(value, default):
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    return result if result >= 1 else default


def _not_authorized_response():
    if not tk.g.user:
        return tk.redirect_to('user.login')
    return tk.abort(403, tk._('You are not authorized to view this page'))


# ---------------------------------------------------------------------------
# Public index + detail pages
# ---------------------------------------------------------------------------

def _content_index(content_type):
    page = _positive_int(request.args.get('page'), 1)
    list_template = _TYPE_VIEW[content_type][0]
    data_dict = {
        'content_type': content_type,
        'limit': CONTENT_PER_PAGE,
        'offset': (page - 1) * CONTENT_PER_PAGE,
    }
    try:
        listing = tk.get_action('csunesco_content_list')(
            _context(), data_dict)
    except Exception:
        log.warning('csunesco: content list unavailable (%s)', content_type)
        listing = {'results': [], 'count': 0}

    count = listing.get('count', 0)
    total_pages = max(1, (count + CONTENT_PER_PAGE - 1) // CONTENT_PER_PAGE)
    return tk.render(list_template, extra_vars={
        'items': listing.get('results', []),
        'count': count,
        'page': page,
        'total_pages': total_pages,
    })


def _content_show(content_type, slug):
    detail_template = _TYPE_VIEW[content_type][1]
    try:
        content = tk.get_action('csunesco_content_show')(
            _context(), {'slug': slug})
    except tk.ObjectNotFound:
        return tk.abort(404, tk._('Content not found'))
    except tk.NotAuthorized:
        return _not_authorized_response()
    except Exception:
        log.warning('csunesco: content detail could not be loaded')
        return tk.abort(404, tk._('Content not found'))

    # A news slug must not resolve an event (and vice versa).
    if content.get('content_type') != content_type:
        return tk.abort(404, tk._('Content not found'))

    return tk.render(detail_template, extra_vars={'content': content})


def cs_news_index():
    return _content_index('cs-news')


def cs_news_show(slug):
    return _content_show('cs-news', slug)


def cs_events_index():
    return _content_index('cs-event')


def cs_events_show(slug):
    return _content_show('cs-event', slug)


def cs_publications_index():
    return _content_index('cs-publication')


def cs_publications_show(slug):
    return _content_show('cs-publication', slug)


def cs_maps_index():
    return _content_index('cs-map')


def cs_maps_show(slug):
    return _content_show('cs-map', slug)


# ---------------------------------------------------------------------------
# Editor (create / edit)
# ---------------------------------------------------------------------------

def _read_content_form():
    """Read the editor POST into an action ``data_dict`` (echo-friendly)."""
    form = request.form
    return {
        'title': (form.get('title') or '').strip(),
        'content_type': (form.get('content_type') or '').strip(),
        'body': (form.get('body') or '').strip(),
        'publish_date': (form.get('publish_date') or '').strip(),
        'end_date': (form.get('end_date') or '').strip(),
        'media': [u.strip() for u in form.getlist('media') if u.strip()],
        'featured': bool(form.get('featured')),
        'terria_url': (form.get('terria_url') or '').strip(),
        'doi': (form.get('doi') or '').strip(),
        'authors': (form.get('authors') or '').strip(),
    }


def _render_content_form(mode, project, content, data, errors):
    return tk.render('csunesco/content_form.html', extra_vars={
        'mode': mode,
        'project': project,
        'content': content,
        'data': data,
        'errors': errors,
        'content_type_choices': _CONTENT_TYPE_CHOICES,
    })


def _detail_url(content):
    """Best-effort URL of a content item's own page (falls back to project)."""
    view = _TYPE_VIEW.get(content.get('content_type'))
    if view and content.get('slug'):
        return tk.h.url_for(view[3], slug=content['slug'])
    return None


def content_new(slug):
    """GET the editor for a new item under project ``slug``; POST creates it."""
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
        log.warning('csunesco: content editor could not resolve project')
        return tk.abort(404, tk._('Project not found'))

    # Only a manager of this project may open the editor.
    if not tk.h.csunesco_can_manage_project(project.get('id')):
        return _not_authorized_response()

    if request.method == 'GET':
        return _render_content_form(
            'new', project, None,
            {'content_type': 'cs-news', 'media': []}, {})

    # --- POST ---------------------------------------------------------------
    data = _read_content_form()
    data_dict = dict(data)
    data_dict['project_id'] = project['id']
    try:
        content = tk.get_action('csunesco_content_create')(context, data_dict)
    except tk.NotAuthorized:
        return _not_authorized_response()
    except tk.ValidationError as error:
        return _render_content_form(
            'new', project, None, data, error.error_dict or {})
    except Exception:
        log.warning('csunesco: content could not be created')
        return _render_content_form(
            'new', project, None, data, {'message': GENERIC_ERROR})

    if content.get('status') == 'approved':
        tk.h.flash_success(tk._('Your content has been published.'))
    else:
        tk.h.flash_success(tk._(
            'Your content has been submitted and is awaiting review.'))
    return tk.redirect_to('csunesco.project_landing', slug=project['slug'])


def content_edit(id):
    """GET the editor pre-filled for content ``id``; POST updates it."""
    if not tk.g.user:
        return _not_authorized_response()

    context = _context()
    try:
        content = tk.get_action('csunesco_content_show')(context, {'id': id})
    except tk.ObjectNotFound:
        return tk.abort(404, tk._('Content not found'))
    except tk.NotAuthorized:
        return _not_authorized_response()
    except Exception:
        log.warning('csunesco: content editor could not load content')
        return tk.abort(404, tk._('Content not found'))

    if not tk.h.csunesco_can_manage_project(content.get('project_id')):
        return _not_authorized_response()

    try:
        project = tk.get_action('csunesco_project_show')(
            context, {'id': content['project_id']})
    except Exception:
        project = None

    if request.method == 'GET':
        data = {
            'title': content.get('title') or '',
            'content_type': content.get('content_type') or 'cs-news',
            'body': content.get('body') or '',
            'publish_date': content.get('publish_date') or '',
            'end_date': content.get('end_date') or '',
            'media': content.get('media') or [],
            'featured': bool(content.get('featured')),
            'terria_url': content.get('terria_url') or '',
            'doi': content.get('doi') or '',
            'authors': content.get('authors') or '',
        }
        return _render_content_form('edit', project, content, data, {})

    # --- POST ---------------------------------------------------------------
    data = _read_content_form()
    data_dict = dict(data)
    data_dict['id'] = content['id']
    try:
        updated = tk.get_action('csunesco_content_update')(context, data_dict)
    except tk.NotAuthorized:
        return _not_authorized_response()
    except tk.ValidationError as error:
        return _render_content_form(
            'edit', project, content, data, error.error_dict or {})
    except Exception:
        log.warning('csunesco: content could not be updated')
        return _render_content_form(
            'edit', project, content, data, {'message': GENERIC_ERROR})

    tk.h.flash_success(tk._('Your content has been saved.'))
    detail = _detail_url(updated)
    if detail:
        return tk.redirect_to(detail)
    if project:
        return tk.redirect_to('csunesco.project_landing', slug=project['slug'])
    return tk.redirect_to('csunesco.cs_news_index')
