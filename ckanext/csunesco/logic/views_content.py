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
    initiative = (request.args.get('initiative') or '').strip()
    list_template = _TYPE_VIEW[content_type][0]
    data_dict = {
        'content_type': content_type,
        # Public indexes are publication surfaces even for a logged-in
        # moderator.  Never let the caller's wider permissions turn them into
        # an implicit review queue.
        'status': 'approved',
        'limit': CONTENT_PER_PAGE,
        'offset': (page - 1) * CONTENT_PER_PAGE,
    }
    if initiative:
        data_dict['initiative'] = initiative
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
        'selected_initiative': initiative,
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

    # Decorate the owning scope for the shared meta line (fail-soft: the
    # detail page must render even if the owner lookup hiccups).
    try:
        if content.get('project_id'):
            project = tk.get_action('csunesco_project_show')(
                _context(), {'id': content['project_id']})
            content['owner_kind'] = 'project'
            content['owner_title'] = project.get('title')
            content['owner_url'] = tk.h.url_for(
                'csunesco.project_landing', slug=project.get('slug'))
        elif content.get('organization_id'):
            organization = tk.get_action('organization_show')(
                _context(), {'id': content['organization_id']})
            content['owner_kind'] = 'organization'
            content['owner_title'] = (organization.get('title')
                                      or organization.get('name'))
            content['owner_url'] = tk.h.url_for(
                'organization.read', id=organization.get('name'))
    except Exception:
        log.warning('csunesco: content owner unavailable for the detail page')

    return tk.render(detail_template, extra_vars={'content': content})


def cs_content_index():
    """The Knowledge Hub index: ALL content types, chip filter + search.

    Spec section 4: "Knowledge Hub -- resources and learning". The action has
    supported ``q`` since it was written; the hub view simply never passed it
    through, so the hub had no search.
    """
    page = _positive_int(request.args.get('page'), 1)
    selected = (request.args.get('type') or '').strip()
    initiative = (request.args.get('initiative') or '').strip()
    q = (request.args.get('q') or '').strip()
    if selected and selected not in _TYPE_VIEW:
        return tk.abort(404, tk._('Unknown content type'))
    data_dict = {
        'status': 'approved',
        'limit': CONTENT_PER_PAGE,
        'offset': (page - 1) * CONTENT_PER_PAGE,
        'include_project': True,
    }
    if selected:
        data_dict['content_type'] = selected
    if initiative:
        data_dict['initiative'] = initiative
    if q:
        data_dict['q'] = q
    try:
        listing = tk.get_action('csunesco_content_list')(
            _context(), data_dict)
    except Exception:
        log.warning('csunesco: combined content list unavailable')
        listing = {'results': [], 'count': 0}

    count = listing.get('count', 0)
    total_pages = max(1, (count + CONTENT_PER_PAGE - 1) // CONTENT_PER_PAGE)
    return tk.render('csunesco/cs-content_list.html', extra_vars={
        'items': listing.get('results', []),
        'count': count,
        'page': page,
        'total_pages': total_pages,
        'selected_type': selected,
        'type_choices': _CONTENT_TYPE_CHOICES,
        'selected_initiative': initiative,
        'q': q,
    })


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
    gallery = []
    gallery_urls = form.getlist('gallery_url')
    gallery_alts = form.getlist('gallery_alt')
    gallery_captions = form.getlist('gallery_caption')
    for index, url in enumerate(gallery_urls):
        if url.strip():
            gallery.append({
                'url': url.strip(),
                'alt': gallery_alts[index].strip()
                       if index < len(gallery_alts) else '',
                'caption': gallery_captions[index].strip()
                           if index < len(gallery_captions) else '',
            })
    related_links = []
    link_urls = form.getlist('related_link_url')
    link_labels = form.getlist('related_link_label')
    for index, url in enumerate(link_urls):
        if url.strip():
            related_links.append({
                'url': url.strip(),
                'label': link_labels[index].strip()
                         if index < len(link_labels) else '',
            })
    data = {
        'title': (form.get('title') or '').strip(),
        'content_type': (form.get('content_type') or '').strip(),
        'body': (form.get('body') or '').strip(),
        'publish_date': (form.get('publish_date') or '').strip(),
        'end_date': (form.get('end_date') or '').strip(),
        'media': [u.strip() for u in form.getlist('media') if u.strip()],
        'visibility': (form.get('visibility') or 'public').strip(),
        'terria_url': (form.get('terria_url') or '').strip(),
        'doi': (form.get('doi') or '').strip(),
        'authors': (form.get('authors') or '').strip(),
        'excerpt': (form.get('excerpt') or '').strip(),
        'author': (form.get('author') or '').strip(),
        'source_url': (form.get('source_url') or '').strip(),
        'header_image_url': (form.get('header_image_url') or '').strip(),
        'header_image_alt': (form.get('header_image_alt') or '').strip(),
        'header_focal_x': (form.get('header_focal_x') or '50').strip(),
        'header_focal_y': (form.get('header_focal_y') or '50').strip(),
        'gallery': gallery,
        'related_links': related_links,
        'attachment_url': (form.get('attachment_url') or '').strip(),
        'attachment_label': (form.get('attachment_label') or '').strip(),
    }
    # ``featured`` travels ONLY when its (sysadmin-only) checkbox was actually
    # rendered -- the hidden ``featured_present`` marker says so. Sending the
    # key unconditionally made every sysadmin edit silently un-feature the row
    # (an absent checkbox reads as False).
    if form.get('featured_present'):
        data['featured'] = bool(form.get('featured'))
    return data


def _process_content_uploads(data):
    """Apply multipart news uploads to ``data`` and return their batch."""
    from ckanext.csunesco.logic import uploads
    header = {
        'url': data.get('header_image_url') or '',
        'upload': request.files.get('header_image_upload'),
        'clear': request.form.get('header_image_clear'),
    }
    gallery_holders = [dict(item) for item in data.get('gallery') or []]
    available = max(0, 12 - len(gallery_holders))
    for upload in request.files.getlist('gallery_upload')[:available]:
        if getattr(upload, 'filename', None):
            gallery_holders.append({
                'url': '', 'upload': upload, 'alt': '',
                'caption': upload.filename,
            })
    attachment = {
        'url': data.get('attachment_url') or '',
        'upload': request.files.get('attachment_upload'),
        'clear': request.form.get('attachment_clear'),
    }
    batch = uploads.process_content_files(
        header=header, gallery=gallery_holders, attachment=attachment)
    data['header_image_url'] = header.get('url') or ''
    data['gallery'] = [
        {'url': item.get('url') or '', 'alt': item.get('alt') or '',
         'caption': item.get('caption') or ''}
        for item in gallery_holders if item.get('url')
    ]
    data['attachment_url'] = attachment.get('url') or ''
    return batch


def _render_content_form(mode, project, content, data, errors,
                         organization=None):
    from ckanext.csunesco.logic import auth as cs_auth
    return tk.render('csunesco/content_form.html', extra_vars={
        'mode': mode,
        'project': project,
        'organization': organization,
        'content': content,
        'data': data,
        'errors': errors,
        'content_type_choices': _CONTENT_TYPE_CHOICES,
        # Gates the featured checkbox (cosmetic; the action re-checks).
        'is_sysadmin': cs_auth._is_sysadmin(_context()),
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
            {'content_type': 'cs-news', 'media': [], 'visibility': 'public',
             'header_focal_x': 50, 'header_focal_y': 50,
             'gallery': [], 'related_links': []}, {})

    # --- POST ---------------------------------------------------------------
    data = _read_content_form()
    try:
        upload_batch = _process_content_uploads(data)
    except Exception:
        return _render_content_form(
            'new', project, None, data,
            {'message': tk._('One or more uploaded files could not be saved.')})
    data_dict = dict(data)
    data_dict['project_id'] = project['id']
    try:
        content = tk.get_action('csunesco_content_create')(context, data_dict)
    except tk.NotAuthorized:
        upload_batch.rollback()
        return _not_authorized_response()
    except tk.ValidationError as error:
        upload_batch.rollback()
        return _render_content_form(
            'new', project, None, data, error.error_dict or {})
    except Exception:
        upload_batch.rollback()
        log.warning('csunesco: content could not be created')
        return _render_content_form(
            'new', project, None, data, {'message': GENERIC_ERROR})

    if content.get('status') == 'approved':
        tk.h.flash_success(tk._('Your content has been published.'))
    else:
        tk.h.flash_success(tk._(
            'Your content has been submitted and is awaiting review.'))
    return tk.redirect_to('csunesco.project_landing', slug=project['slug'])


def org_content_new(org):
    """GET the editor for a new ORG-scoped item; POST creates it.

    Same thin contract as ``content_new``: resolve the organization, gate on
    the composite scope authorization (cosmetic here -- the action re-checks),
    and map ValidationError back to inline field errors.
    """
    if not tk.g.user:
        return _not_authorized_response()

    from ckanext.csunesco.logic import auth as cs_auth
    context = _context()
    try:
        organization = tk.get_action('organization_show')(
            context, {'id': org})
    except (tk.ObjectNotFound, tk.NotAuthorized):
        return tk.abort(404, tk._('Organization not found'))
    except Exception:
        log.warning('csunesco: content editor could not resolve organization')
        return tk.abort(404, tk._('Organization not found'))

    if not cs_auth.can_manage_content_scope(context, None, organization['id']):
        return _not_authorized_response()

    if request.method == 'GET':
        return _render_content_form(
            'new', None, None,
            {'content_type': 'cs-news', 'media': [], 'visibility': 'public',
             'header_focal_x': 50, 'header_focal_y': 50,
             'gallery': [], 'related_links': []},
            {}, organization=organization)

    data = _read_content_form()
    try:
        upload_batch = _process_content_uploads(data)
    except Exception:
        return _render_content_form(
            'new', None, None, data,
            {'message': tk._('One or more uploaded files could not be saved.')},
            organization=organization)
    data_dict = dict(data)
    data_dict['owner_org'] = organization['id']
    try:
        content = tk.get_action('csunesco_content_create')(context, data_dict)
    except tk.NotAuthorized:
        upload_batch.rollback()
        return _not_authorized_response()
    except tk.ValidationError as error:
        upload_batch.rollback()
        return _render_content_form(
            'new', None, None, data, error.error_dict or {},
            organization=organization)
    except Exception:
        upload_batch.rollback()
        log.warning('csunesco: org content could not be created')
        return _render_content_form(
            'new', None, None, data, {'message': GENERIC_ERROR},
            organization=organization)

    if content.get('status') == 'approved':
        tk.h.flash_success(tk._('Your content has been published.'))
    else:
        tk.h.flash_success(tk._(
            'Your content has been submitted and is awaiting review.'))
    detail = _detail_url(content)
    if detail:
        return tk.redirect_to(detail)
    return tk.redirect_to('csunesco.cs_news_index')


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

    from ckanext.csunesco.logic import auth as cs_auth
    if not cs_auth.can_manage_content_scope(
            context, content.get('project_id'),
            content.get('organization_id')):
        return _not_authorized_response()

    project = None
    organization = None
    if content.get('project_id'):
        try:
            project = tk.get_action('csunesco_project_show')(
                context, {'id': content['project_id']})
        except Exception:
            # Genuinely unexpected: the scope check just said yes about THIS
            # project, so it exists and this user may read it. The editor
            # still renders without it, but nobody should have to guess why.
            log.warning(
                'csunesco: project %s unavailable for the content editor',
                content.get('project_id'))
    elif content.get('organization_id'):
        try:
            organization = tk.get_action('organization_show')(
                context, {'id': content['organization_id']})
        except Exception:
            log.warning(
                'csunesco: organization %s unavailable for the content editor',
                content.get('organization_id'))

    if request.method == 'GET':
        data = {
            'title': content.get('title') or '',
            'content_type': content.get('content_type') or 'cs-news',
            'body': content.get('body') or '',
            'publish_date': content.get('publish_date') or '',
            'end_date': content.get('end_date') or '',
            'media': content.get('media') or [],
            'visibility': content.get('visibility') or 'public',
            'featured': bool(content.get('featured')),
            'terria_url': content.get('terria_url') or '',
            'doi': content.get('doi') or '',
            'authors': content.get('authors') or '',
            'excerpt': content.get('excerpt') or '',
            'author': content.get('author') or content.get('app_author') or '',
            'source_url': content.get('source_url') or '',
            'header_image_url': content.get('header_image_url') or '',
            'header_image_alt': content.get('header_image_alt') or '',
            'header_focal_x': content.get('header_focal_x', 50),
            'header_focal_y': content.get('header_focal_y', 50),
            'gallery': content.get('gallery') or [],
            'related_links': content.get('related_links') or [],
            'attachment_url': content.get('attachment_url') or '',
            'attachment_label': content.get('attachment_label') or '',
        }
        return _render_content_form('edit', project, content, data, {},
                                    organization=organization)

    # --- POST ---------------------------------------------------------------
    data = _read_content_form()
    try:
        upload_batch = _process_content_uploads(data)
    except Exception:
        return _render_content_form(
            'edit', project, content, data,
            {'message': tk._('One or more uploaded files could not be saved.')},
            organization=organization)
    data_dict = dict(data)
    data_dict['id'] = content['id']
    try:
        updated = tk.get_action('csunesco_content_update')(context, data_dict)
    except tk.NotAuthorized:
        upload_batch.rollback()
        return _not_authorized_response()
    except tk.ValidationError as error:
        upload_batch.rollback()
        return _render_content_form(
            'edit', project, content, data, error.error_dict or {},
            organization=organization)
    except Exception:
        upload_batch.rollback()
        log.warning('csunesco: content could not be updated')
        return _render_content_form(
            'edit', project, content, data, {'message': GENERIC_ERROR},
            organization=organization)

    tk.h.flash_success(tk._('Your content has been saved.'))
    detail = _detail_url(updated)
    if detail:
        return tk.redirect_to(detail)
    if project:
        return tk.redirect_to('csunesco.project_landing', slug=project['slug'])
    return tk.redirect_to('csunesco.cs_news_index')
