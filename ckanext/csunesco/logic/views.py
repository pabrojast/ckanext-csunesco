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
from ckanext.csunesco.logic import page_render
from ckanext.csunesco.logic import schema as cs_schema

log = logging.getLogger(__name__)

# Single generic message for unexpected failures -- never leak internals.
GENERIC_ERROR = 'Something went wrong. Please try again.'

# Shown against the cover-image field when the upload batch rejects the file.
# One message rather than a per-reason map: views_page does the same, and the
# picker's own hint already states the accepted formats and size.
UPLOAD_ERROR = 'The cover image could not be saved. Check the file format ' \
               'and size, then select it again.'

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


def _not_authorized_response(came_from=None):
    """Redirect anonymous users to log in; deny logged-in users with 403.

    ``came_from`` is forwarded to CKAN's login view so the person returns to
    where they were headed instead of the site root -- without it, a join or
    proposal attempt dead-ends on the dashboard after signing in.
    """
    if not tk.g.user:
        if came_from:
            return tk.redirect_to('user.login', came_from=came_from)
        return tk.redirect_to('user.login')
    return tk.abort(403, tk._('You are not authorized to view this page'))


def _decorate_projects(projects):
    """Attach a human ``initiative_title`` to each list row for display."""
    for project in projects:
        project['initiative_title'] = _INITIATIVE_TITLES.get(
            project.get('initiative_group'), project.get('initiative_group'))
    return projects


def _member_state_choices():
    """``(choices, available)`` for the country picker.

    Delegates to ``csunesco_member_state_list`` so the view never queries the DB
    directly (water-family pattern).

    This used to call ``group_show(include_groups=True)`` and read
    ``child['title'] or child['name']``. CKAN's child-group dictization returns
    ``title: None``, so the fallback fired for EVERY option and the form listed
    raw slugs -- ``afghanistan``, ``-land-islands`` -- instead of ``Afghanistan``
    and ``Åland Islands``. The action reads the titles in one query.

    ``available`` is False ONLY when the list could not be read at all. An
    un-seeded portal (empty but readable) and a broken one render identically
    today -- both an empty box that reads as a broken form -- so the template
    says which it is. Neither blocks submission: countries are optional.
    """
    try:
        result = tk.get_action('csunesco_member_state_list')(_context(), {})
    except Exception:
        log.warning('csunesco: member-state choices unavailable')
        return [], False
    return result.get('member_states') or [], True


# ---------------------------------------------------------------------------
# Public read views
# ---------------------------------------------------------------------------

def hub():
    """Public Citizen Science hub -- a block page a sysadmin can edit.

    Renders the PUBLISHED site-page blocks; a hub that was never edited falls
    back to ``default_site_blocks()``, which reproduces the pre-block layout
    exactly. This page must never 500: any failure degrades to that default.
    """
    from ckanext.csunesco.logic import blocks as blocks_module

    user = getattr(tk.g, 'userobj', None)
    is_sysadmin = bool(user and getattr(user, 'sysadmin', False))
    try:
        page = tk.get_action('csunesco_site_page_show')(_context(), {})
        published = page.get('published_blocks')
        blocks = (published if published is not None
                  else blocks_module.default_site_blocks())
        blocks = page_render.visible_blocks(blocks, scope='site')
        ctx = page_render.build_context(
            _context(), None, blocks, can_manage=is_sysadmin)
    except Exception:
        log.warning('csunesco: hub page failed; rendering the default layout')
        blocks = page_render.visible_blocks(
            blocks_module.default_site_blocks(), scope='site')
        ctx = _fallback_site_ctx()

    return tk.render('csunesco/citizen-science.html', extra_vars={
        'blocks': blocks,
        'ctx': ctx,
        'is_draft_preview': False,
    })


def _fallback_site_ctx():
    """The minimal ctx the site block templates can render from (all empty).

    Only reached when even build_context failed -- its lookups are already
    individually fail-soft, so this is the last line of the never-500 rule.
    """
    return {
        'scope': 'site', 'project': None, 'stats': {},
        'has_region': False, 'can_manage': False, 'preview': False,
        'data_sources': [], 'approved_sources': {}, 'news_events': [],
        'recent_projects': [], 'content_lists': {}, 'dataset_lists': {},
        'has_charts': False, 'has_chat': False, 'has_lightbox': False,
    }


def initiative_index(name):
    """Block-driven initiative page with live projects, news and events."""
    from ckanext.csunesco.logic import blocks as blocks_module
    initiative = next(
        (i for i in constants.CS_INITIATIVES if i['name'] == name), None)
    if initiative is None:
        return tk.abort(404, tk._('Initiative not found'))

    can_manage = False
    try:
        tk.check_access('csunesco_initiative_page_update', _context(),
                        {'initiative': name})
        can_manage = True
    except Exception:
        pass

    try:
        page = tk.get_action('csunesco_initiative_page_show')(
            _context(), {'initiative': name})
        published = page.get('published_blocks')
        blocks = (published if published is not None
                  else blocks_module.default_initiative_blocks())
        blocks = page_render.visible_blocks(blocks, scope='initiative')
        ctx = page_render.build_context(
            _context(), None, blocks, can_manage=can_manage,
            scope='initiative', initiative=initiative)
    except Exception:
        log.warning('csunesco: initiative page unavailable; using defaults')
        blocks = page_render.visible_blocks(
            blocks_module.default_initiative_blocks(), scope='initiative')
        ctx = page_render.build_context(
            _context(), None, blocks, scope='initiative',
            initiative=initiative)

    return tk.render('csunesco/initiative.html', extra_vars={
        'initiative': initiative,
        'blocks': blocks,
        'ctx': ctx,
        'is_draft_preview': False,
    })


# Explorer facet -> (query param, option list). The country options come from
# the member-state action instead (they are data, not constants).
_EXPLORER_FACETS = (
    ('water_type', 'water_types'),
    ('water_data_type', 'water_data_types'),
    ('activity_status', 'activity_statuses'),
    ('geographic_extent', 'geographic_extents'),
)


def project_list():
    """Public project explorer: search + the spec's facet set, with paging."""
    page = _positive_int(request.args.get('page'), 1)
    initiative = (request.args.get('initiative') or '').strip()
    country = (request.args.get('country') or '').strip()
    q = (request.args.get('q') or '').strip()
    facets = {
        facet: (request.args.get(facet) or '').strip()
        for facet, _options in _EXPLORER_FACETS
    }

    data_dict = {
        'limit': PROJECTS_PER_PAGE,
        'offset': (page - 1) * PROJECTS_PER_PAGE,
    }
    if initiative:
        data_dict['initiative'] = initiative
    if country:
        data_dict['country'] = country
    if q:
        data_dict['q'] = q
    for facet, value in facets.items():
        if value:
            data_dict[facet] = value

    try:
        listing = tk.get_action('csunesco_project_list')(_context(), data_dict)
    except Exception:
        # ValidationError (e.g. an unknown initiative filter) and any unexpected
        # error both collapse to an empty, safe result set.
        log.warning('csunesco: project list unavailable')
        listing = {'results': [], 'count': 0}

    count = listing.get('count', 0)
    total_pages = max(1, (count + PROJECTS_PER_PAGE - 1) // PROJECTS_PER_PAGE)

    member_states, _available = _member_state_choices()
    return tk.render('csunesco/project_list.html', extra_vars={
        'projects': _decorate_projects(listing.get('results', [])),
        'count': count,
        'page': page,
        'total_pages': total_pages,
        'initiatives': constants.CS_INITIATIVES,
        'selected_initiative': initiative,
        'member_states': member_states,
        'selected_country': country,
        'q': q,
        'facet_options': {
            'water_type': constants.WATER_TYPES,
            'water_data_type': constants.WATER_DATA_TYPES,
            'activity_status': constants.ACTIVITY_STATUSES,
            'geographic_extent': constants.GEOGRAPHIC_EXTENTS,
        },
        'selected_facets': facets,
    })


def project_landing(slug):
    """Public project landing page (hero, stats, region map, join block)."""
    try:
        project = tk.get_action('csunesco_project_show')(
            _context(), {'slug': slug, 'include_geojson': True})
    except tk.ObjectNotFound:
        return tk.abort(404, tk._('Project not found'))
    except tk.NotAuthorized:
        # SAME response as a nonexistent slug. A login redirect here is an
        # existence oracle: it tells an anonymous visitor that a pending or
        # rejected project lives at this slug. Whoever may actually see it
        # (creator, member, reviewer) is already authorized above.
        return tk.abort(404, tk._('Project not found'))
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

    # The page body is the project's PUBLISHED block list. A project whose
    # manager never published one falls back to the default layout, which is
    # exactly the section order this page had before it became block-driven --
    # so there is one rendering path, not a default template plus a custom one.
    blocks = _published_blocks(project['id'])
    blocks = page_render.visible_blocks(blocks)
    ctx = page_render.build_context(
        _context(), project, blocks,
        has_region=has_region,
        can_manage=tk.h.csunesco_can_manage_project(project['id']))

    return tk.render('csunesco/project_landing.html', extra_vars={
        'project': project,
        'blocks': blocks,
        'ctx': ctx,
        'is_draft_preview': False,
    })


def _published_blocks(project_id):
    """The project's published blocks, or the default layout.

    ``published_blocks`` is ``None`` (never published) rather than ``[]``
    (deliberately emptied) -- only the first falls back to the default. Fails
    soft to the default so a page-storage problem degrades the landing to the
    standard sections instead of breaking it.
    """
    from ckanext.csunesco.logic import blocks as blocks_module
    try:
        page = tk.get_action('csunesco_project_page_show')(
            _context(), {'project_id': project_id})
    except Exception:
        log.warning('csunesco: project page could not be loaded')
        return blocks_module.default_blocks()
    published = page.get('published_blocks')
    if published is None:
        return blocks_module.default_blocks()
    return published


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
        # 404, not a login redirect -- same anti-oracle rule as the landing.
        return tk.abort(404, tk._('Project not found'))
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

def _first_error_step(errors):
    """The lowest-numbered stage carrying a field error (1 when there is none).

    Computed on the SERVER so a re-render after a failed POST paints the right
    stage in the very first frame: no flash of stage 1, and the wizard script
    reads its current stage from the DOM instead of keeping a counter that can
    drift out of step with it.
    """
    bad = set(errors or {})
    for step in constants.PROJECT_FORM_STEPS:
        if bad.intersection(step['fields']):
            return step['step']
    return 1


def _organization_choices(eligible_only=False, include_id=None):
    """CKAN organizations, optionally restricted to the acting user's roles."""
    try:
        from ckanext.csunesco.logic import registration
        choices = registration._organization_options()
        if not eligible_only:
            return choices
        from ckanext.csunesco.logic import auth
        context = _context()
        if auth._is_sysadmin(context):
            return choices
        allowed = []
        for choice in choices:
            group = model.Group.get(choice['name'])
            group_id = group.id if group is not None else choice['name']
            if auth._is_org_editor(context, group_id) or group_id == include_id:
                item = dict(choice)
                item['id'] = group_id
                allowed.append(item)
        return allowed
    except Exception:
        log.warning('csunesco: organization list unavailable for the form')
        return []


def _render_project_form(data, errors, success=False, mode='new',
                         project=None, return_to=None):
    """Render the staged project form, for BOTH create and edit.

    One template, one ``mode`` flag -- the shape ``views_content`` already uses
    for its content form.
    """
    choices, states_available = _member_state_choices()
    return tk.render('csunesco/project_request.html', extra_vars={
        'mode': mode,
        'project': project,
        'data': data,
        'errors': errors,
        'success': success,
        'initiatives': constants.CS_INITIATIVES,
        'member_states': choices,
        'member_states_available': states_available,
        'steps': constants.PROJECT_FORM_STEPS,
        'open_step': _first_error_step(errors),
        'return_to': return_to if return_to == 'review' else None,
        # Spec phase-1 option lists (all from constants; single source).
        'water_types': constants.WATER_TYPES,
        'water_data_types': constants.WATER_DATA_TYPES,
        'geographic_extents': constants.GEOGRAPHIC_EXTENTS,
        'stakeholder_groups': constants.STAKEHOLDER_GROUPS,
        'activity_statuses': constants.ACTIVITY_STATUSES,
        'lead_partner_types': constants.LEAD_PARTNER_TYPES,
        'funding_bodies': constants.FUNDING_BODIES,
        'intl_frameworks': constants.INTL_FRAMEWORKS,
        'organizations': _organization_choices(
            eligible_only=True,
            include_id=(project or {}).get('organization_id')),
        'is_draft': bool(project and project.get('status') == 'draft'),
    })


def _lines_or_commas(raw):
    """A textarea accepting one-per-line entries -> comma-joined string the
    list validator understands."""
    return ', '.join(part.strip() for part in (raw or '').replace(
        '\r', '').split('\n') if part.strip())


def _multi_with_other(form, name):
    """A checkbox group plus its free-text "other" input -> one list."""
    values = [item for item in form.getlist(name) if item]
    other = (form.get(name + '_other') or '').strip()
    if other:
        values.extend(part.strip() for part in other.split(',')
                      if part.strip())
    return values


def _read_project_form():
    """Read the project form POST into an action ``data_dict``."""
    form = request.form
    data = {
        'title': (form.get('title') or '').strip(),
        'slug': (form.get('slug') or '').strip(),
        'initiative': (form.get('initiative') or '').strip(),
        'biosphere_reserve': (form.get('biosphere_reserve') or '').strip(),
        'region_geojson': (form.get('region_geojson') or '').strip(),
        'short_description': (form.get('short_description') or '').strip(),
        'project_document_url':
            (form.get('project_document_url') or '').strip(),
        'image_url': (form.get('image_url') or '').strip(),
        'logo_url': (form.get('logo_url') or '').strip(),
        'heading_image_url': (form.get('heading_image_url') or '').strip(),
        'organization_id': (form.get('organization_id') or '').strip(),
        'image_focal_x': (form.get('image_focal_x') or '50').strip(),
        'image_focal_y': (form.get('image_focal_y') or '50').strip(),
        'heading_focal_x': (form.get('heading_focal_x') or '50').strip(),
        'heading_focal_y': (form.get('heading_focal_y') or '50').strip(),
        'how_to_participate': (form.get('how_to_participate') or '').strip(),
        'start_date': (form.get('start_date') or '').strip(),
        'end_date': (form.get('end_date') or '').strip(),
        'target_group': (form.get('target_group') or '').strip(),
        'contact_person': (form.get('contact_person') or '').strip(),
        'contact_email': (form.get('contact_email') or '').strip(),
        # --- spec phase-1 fields ------------------------------------------
        # These controls always render, so the key is always sent and an
        # empty selection genuinely means "cleared" (unlike countries below,
        # whose options come from a fallible external list).
        'keywords': (form.get('keywords') or '').strip(),
        'geographic_extent': (form.get('geographic_extent') or '').strip(),
        'locality': (form.get('locality') or '').strip(),
        'point_lat': (form.get('point_lat') or '').strip(),
        'point_lng': (form.get('point_lng') or '').strip(),
        'point_radius_km': (form.get('point_radius_km') or '').strip(),
        'water_type': _multi_with_other(form, 'water_type'),
        'water_data_type': _multi_with_other(form, 'water_data_type'),
        'stakeholders': _multi_with_other(form, 'stakeholders'),
        'activity_status': [item for item in form.getlist('activity_status')
                            if item],
        'languages': (form.get('languages') or '').strip(),
        'allowed_participants': _lines_or_commas(
            form.get('allowed_participants')),
        'lead_partner_type': (form.get('lead_partner_type') or '').strip(),
        'lead_organisation': (form.get('lead_organisation') or '').strip(),
        'other_organisations': _lines_or_commas(
            form.get('other_organisations')),
        'editors': (form.get('editors') or '').strip(),
        'funding_body': _multi_with_other(form, 'funding_body'),
        'funding_programme': (form.get('funding_programme') or '').strip(),
        'international_frameworks': [
            item for item in form.getlist('international_frameworks')
            if item],
    }
    # Radios submit nothing while unchosen; only trust a real choice.
    if form.get('participation_mode'):
        data['participation_mode'] = form.get('participation_mode')
    # An empty multi-select submits nothing, so a picker that could not render
    # its options looked exactly like "the user deselected every country" and
    # the update wiped them. Only trust an empty selection when the control
    # actually rendered.
    if form.get('countries_present'):
        data['countries'] = [c for c in form.getlist('countries') if c]
    return data


# The three project images and their form control names.
_PROJECT_IMAGE_FIELDS = (
    ('image_url', 'image_upload', 'image_clear'),
    ('logo_url', 'logo_upload', 'logo_clear'),
    ('heading_image_url', 'heading_upload', 'heading_clear'),
)


def _resolve_cover(form, files):
    """Run the shared image picker for the project's three images.

    ``process_page_images([], project_images=...)`` is the page editor's
    upload batch with no block jobs, so the MIME check, the size cap, the
    server-side resize, the per-request limit and the rollback are literally
    the same code rather than a second implementation that drifts.

    Returns ``(batch, problems)``. The BATCH is returned, not just its URLs:
    every later failure path has to call ``batch.rollback()`` or a validation
    error leaves an orphaned file in the FileStore.
    """
    from ckanext.csunesco.logic import uploads
    images = {}
    for url_field, upload_field, clear_field in _PROJECT_IMAGE_FIELDS:
        images[url_field] = {
            'url': (form.get(url_field) or '').strip(),
            'upload': files.get(upload_field),
            'clear': form.get(clear_field),
        }
    try:
        return uploads.process_page_images([], project_images=images), None
    except uploads.PageImageUploadError as error:
        return None, error.problems


def _apply_image_urls(data_dict, batch):
    """Copy the batch's stored URLs into the action payload."""
    for url_field, url in (batch.project_image_urls or {}).items():
        data_dict[url_field] = url
    return data_dict


def _project_to_form(project):
    """A dictized project -> the ``data`` shape the template echoes back.

    Two traps are encoded here. The column is ``initiative_group`` but the
    schema and the form both call it ``initiative``; and the extras dates come
    back as ISO strings, which is exactly what ``<input type="date">`` wants --
    sliced to 10 characters in case an older row stored a full datetime.
    """
    def _list(name):
        value = project.get(name)
        return list(value) if isinstance(value, (list, tuple)) else []

    participation_mode = project.get('participation_mode') or ''
    if not participation_mode and 'open_participation' in project:
        participation_mode = ('open' if project.get('open_participation')
                              else 'limited')
    return {
        'title': project.get('title') or '',
        'slug': project.get('slug') or '',
        'initiative': project.get('initiative_group') or '',
        'countries': project.get('countries') or [],
        'biosphere_reserve': project.get('biosphere_reserve') or '',
        'region_geojson': project.get('region_geojson') or '',
        'short_description': project.get('short_description') or '',
        'how_to_participate': project.get('how_to_participate') or '',
        'start_date': (project.get('start_date') or '')[:10],
        'end_date': (project.get('end_date') or '')[:10],
        'open_participation': bool(project.get('open_participation')),
        'participation_mode': participation_mode,
        'target_group': project.get('target_group') or '',
        'contact_person': project.get('contact_person') or '',
        'contact_email': project.get('contact_email') or '',
        'project_document_url': project.get('project_document_url') or '',
        'image_url': project.get('image_url') or '',
        'logo_url': project.get('logo_url') or '',
        'heading_image_url': project.get('heading_image_url') or '',
        'organization_id': project.get('organization_id') or '',
        'image_focal_x': project.get('image_focal_x', 50),
        'image_focal_y': project.get('image_focal_y', 50),
        'heading_focal_x': project.get('heading_focal_x', 50),
        'heading_focal_y': project.get('heading_focal_y', 50),
        'keywords': ', '.join(_list('keywords')),
        'geographic_extent': project.get('geographic_extent') or '',
        'locality': project.get('locality') or '',
        # A 0.0 latitude/longitude is the equator/prime meridian, not "unset";
        # only None means the field was never filled.
        'point_lat': ('' if project.get('point_lat') is None
                      else project.get('point_lat')),
        'point_lng': ('' if project.get('point_lng') is None
                      else project.get('point_lng')),
        'point_radius_km': ('' if project.get('point_radius_km') is None
                            else project.get('point_radius_km')),
        'water_type': _list('water_type'),
        'water_data_type': _list('water_data_type'),
        'stakeholders': _list('stakeholders'),
        'activity_status': _list('activity_status'),
        'languages': ', '.join(_list('languages')),
        'allowed_participants': '\n'.join(_list('allowed_participants')),
        'lead_partner_type': project.get('lead_partner_type') or '',
        'lead_organisation': project.get('lead_organisation') or '',
        'other_organisations': '\n'.join(_list('other_organisations')),
        'editors': ', '.join(_list('editors')),
        'funding_body': _list('funding_body'),
        'funding_programme': project.get('funding_programme') or '',
        'international_frameworks': _list('international_frameworks'),
    }


def project_new():
    """GET the project-request form; POST creates a PENDING project request."""
    # Login is required at ENTRY, not discovered on submit. An anonymous
    # visitor could previously fill in the whole 5-step form and lose
    # everything to the login redirect when posting it.
    from ckanext.csunesco.logic import auth
    context = _context()
    eligible_organizations = _organization_choices(eligible_only=True)
    if (not tk.g.user or not auth._is_sysadmin(context)
            and not eligible_organizations):
        return tk.render('csunesco/project_eligibility.html', extra_vars={
            'logged_in': bool(tk.g.user),
        })

    if request.method == 'GET':
        # The PRG target lands here with ?submitted=1 -> show the success state.
        if request.args.get('submitted'):
            return _render_project_form({}, {}, success=True)
        return _render_project_form({}, {})

    # --- POST ---------------------------------------------------------------

    data_dict = _read_project_form()
    save_draft = bool(request.form.get('save_draft'))
    batch, problems = _resolve_cover(request.form, request.files)
    if problems:
        return _render_project_form(data_dict, {'image_url': [UPLOAD_ERROR]})
    _apply_image_urls(data_dict, batch)

    context = _context()
    if save_draft:
        # "Save for later": only the lenient rules apply (a draft needs no
        # more than a title), and the row lands as status='draft'.
        context['csunesco_draft'] = True
    else:
        # The per-caller strictness split: the SPEC's required fields are
        # enforced HERE, in the web view, before the (deliberately lenient)
        # action ever runs -- the CS Toolbox outbox posts to the same action
        # and must keep working with its fixed payload.
        _validated, form_errors = tk.navl_validate(
            dict(data_dict), cs_schema.project_request_form_schema(),
            _context())
        if form_errors:
            batch.rollback()
            return _render_project_form(data_dict, form_errors)

    try:
        created = tk.get_action('csunesco_project_request_create')(
            context, data_dict)
    except tk.NotAuthorized:
        batch.rollback()
        return _not_authorized_response()
    except tk.ValidationError as error:
        batch.rollback()
        return _render_project_form(data_dict, error.error_dict or {})
    except Exception:
        batch.rollback()
        log.warning('csunesco: project request could not be created')
        return _render_project_form(data_dict, {'message': GENERIC_ERROR})

    if save_draft:
        tk.h.flash_success(tk._(
            'Draft saved. You can keep editing and submit it for review '
            'when it is ready.'))
        return tk.redirect_to('csunesco.project_edit', slug=created['slug'])

    # PRG: flash + redirect to the GET success state so a refresh cannot resend.
    tk.h.flash_success(tk._(
        'Your project request has been submitted and is awaiting review.'))
    return tk.redirect_to('csunesco.project_new', submitted=1)


def project_edit(slug):
    """GET the staged form pre-filled for ``slug``; POST saves the changes.

    Reuses the very same template as ``project_new`` in ``mode='edit'`` -- the
    fields, their stages and their validation are identical, only the target
    action differs.
    """
    if not tk.g.user:
        return _not_authorized_response(
            came_from=tk.h.url_for('csunesco.project_edit', slug=slug))

    context = _context()
    try:
        # include_geojson is LOAD-BEARING: csunesco_project_show strips the
        # region unless asked for it, and an edit form that rendered an empty
        # textarea would post an empty string and silently wipe the project's
        # region on the first save.
        project = tk.get_action('csunesco_project_show')(
            context, {'slug': slug, 'include_geojson': True})
    except tk.ObjectNotFound:
        return tk.abort(404, tk._('Project not found'))
    except tk.NotAuthorized:
        return _not_authorized_response()
    except Exception:
        log.warning('csunesco: project editor could not resolve the project')
        return tk.abort(404, tk._('Project not found'))

    # NOT csunesco_can_manage_project: that is membership-based, and the
    # author of a pending or rejected request has no membership row yet --
    # they would be locked out of correcting their own submission.
    if not tk.h.csunesco_can_edit_project(project):
        return _not_authorized_response()

    return_to = ('review' if request.values.get('return_to') == 'review'
                 else None)

    if request.method == 'GET':
        return _render_project_form(_project_to_form(project), {},
                                    mode='edit', project=project,
                                    return_to=return_to)

    # --- POST ---------------------------------------------------------------
    data_dict = _read_project_form()
    # A draft has one extra exit: "Submit for review" (strictly validated,
    # then draft -> pending). Plain saves of pending/approved projects stay
    # LENIENT on purpose -- legacy projects predate the spec's required
    # fields, and locking their managers out of a title fix until they
    # backfill six new fields would repeat the member-state-outage bug.
    submit_review = (project.get('status') == 'draft'
                     and bool(request.form.get('submit_review')))
    batch, problems = _resolve_cover(request.form, request.files)
    if problems:
        return _render_project_form(data_dict, {'image_url': [UPLOAD_ERROR]},
                                    mode='edit', project=project,
                                    return_to=return_to)
    _apply_image_urls(data_dict, batch)
    data_dict['id'] = project['id']

    if submit_review:
        strict_context = _context()
        strict_context['csunesco_existing_countries'] = (
            project.get('countries') or [])
        _validated, form_errors = tk.navl_validate(
            dict(data_dict), cs_schema.project_request_form_schema(),
            strict_context)
        form_errors.pop('slug', None)  # the URL is fixed after creation
        if form_errors:
            batch.rollback()
            return _render_project_form(data_dict, form_errors,
                                        mode='edit', project=project,
                                        return_to=return_to)

    try:
        tk.get_action('csunesco_project_update')(context, data_dict)
        if submit_review:
            tk.get_action('csunesco_project_resubmit')(
                context, {'id': project['id']})
    except tk.NotAuthorized:
        batch.rollback()
        return _not_authorized_response()
    except tk.ValidationError as error:
        batch.rollback()
        return _render_project_form(data_dict, error.error_dict or {},
                                    mode='edit', project=project,
                                    return_to=return_to)
    except Exception:
        batch.rollback()
        log.warning('csunesco: project could not be updated')
        return _render_project_form(data_dict, {'message': GENERIC_ERROR},
                                    mode='edit', project=project,
                                    return_to=return_to)

    if submit_review:
        tk.h.flash_success(tk._(
            'Your project request has been submitted and is awaiting '
            'review.'))
        return tk.redirect_to('csunesco.project_landing',
                              slug=project['slug'])
    tk.h.flash_success(tk._('Your project details have been saved.'))
    if return_to == 'review':
        return tk.redirect_to('csunesco.project_review', id=project['id'])
    return tk.redirect_to('csunesco.project_landing', slug=project['slug'])


def _project_state_post(slug, action, success, destination='admin'):
    """Shared CSRF-protected project state transition view."""
    if not tk.g.user:
        return _not_authorized_response()
    data = {'slug': slug}
    reason = (request.form.get('reason') or '').strip()
    if reason:
        data['reason'] = reason
    try:
        result = tk.get_action(action)(_context(), data)
    except tk.ObjectNotFound:
        return tk.abort(404, tk._('Project not found'))
    except tk.NotAuthorized:
        return _not_authorized_response()
    except tk.ValidationError as error:
        tk.h.flash_error(next(iter(error.error_dict.values()))[0]
                         if error.error_dict else GENERIC_ERROR)
        return tk.redirect_to('csunesco.admin_dashboard')
    tk.h.flash_success(tk._(success))
    if destination == 'project':
        return tk.redirect_to('csunesco.project_landing', slug=result['slug'])
    return tk.redirect_to('csunesco.admin_dashboard')


def project_delete(slug):
    return _project_state_post(
        slug, 'csunesco_project_delete', 'The project proposal was deleted.')


def project_archive(slug):
    return _project_state_post(
        slug, 'csunesco_project_archive', 'The project was archived.')


def project_restore(slug):
    return _project_state_post(
        slug, 'csunesco_project_restore', 'The project was restored.',
        destination='project')


def _ofform_app_url():
    """Configured CS Toolbox base URL, or None when app links are off."""
    try:
        from ckanext.csunesco.logic import ofform
        base = (tk.config.get(ofform.APP_URL_OPTION) or '').strip().rstrip('/')
    except Exception:
        return None
    return base or None


def my_projects():
    """The PARTICIPANT'S project hub (spec section 4).

    The admin dashboard already serves managers and authors; this page is for
    the citizen scientist with an approved membership, who previously had no
    list of their projects at all. Structure and workplan render on each
    project's landing page (audience-aware); the forum lives in the CS
    Toolbox app, which is linked rather than duplicated.
    """
    if not tk.g.user:
        return _not_authorized_response(
            came_from=tk.h.url_for('csunesco.my_projects'))
    try:
        result = tk.get_action('csunesco_my_joined_projects')(_context(), {})
        projects = result.get('projects') or []
    except Exception:
        log.warning('csunesco: joined projects could not be listed')
        projects = []
    return tk.render('csunesco/my_projects.html', extra_vars={
        'projects': _decorate_projects(projects),
        'app_url': _ofform_app_url(),
    })


def project_resubmit(slug):
    """POST: put a rejected project back in the review queue, then PRG."""
    if not tk.g.user:
        return _not_authorized_response()

    context = _context()
    try:
        tk.get_action('csunesco_project_resubmit')(context, {'slug': slug})
    except tk.ObjectNotFound:
        return tk.abort(404, tk._('Project not found'))
    except tk.NotAuthorized:
        return _not_authorized_response()
    except tk.ValidationError:
        # The only validation this action raises is the status guard, so the
        # message can be specific without leaking anything.
        tk.h.flash_error(tk._('Only rejected projects can be resubmitted.'))
        return tk.redirect_to('csunesco.admin_dashboard')
    except Exception:
        log.warning('csunesco: project could not be resubmitted')
        tk.h.flash_error(tk._(GENERIC_ERROR))
        return tk.redirect_to('csunesco.admin_dashboard')

    tk.h.flash_success(tk._(
        'Your project has been sent back for review.'))
    return tk.redirect_to('csunesco.admin_dashboard')


def join_project(slug):
    """POST: request to join a project, then PRG back to its landing page."""
    if not tk.g.user:
        tk.h.flash_notice(tk._('Please log in to join this project.'))
        # came_from returns the person to the project they tried to join.
        return tk.redirect_to(
            'user.login',
            came_from=tk.h.url_for('csunesco.project_landing', slug=slug))

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
            context, {'project_id': project['id'],
                      'note': (request.form.get('note') or '').strip()})
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
