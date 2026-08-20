# encoding: utf-8
"""Build the ONE render context a project page's blocks share.

Every block snippet is rendered as ``{% snippet tpl, block=block, ctx=ctx %}``
with the same ``ctx`` dict. CKAN snippets do not inherit the caller's template
context, so each one would otherwise need its own hand-picked kwargs -- sixteen
signatures to keep in step. One dict, one signature, no drift.

Two things this module exists to prevent:

* **Fan-out.** The landing used to make exactly two extra action calls. A block
  list is author-controlled, so a page could ask for six charts, four content
  lists and two dataset lists. Everything is resolved ONCE here, keyed and
  memoised, so N blocks of a kind cost one query, not N.
* **Stale references.** A block stores a ``data_source_id``; that source may
  since have been rejected, or the JSON may have been copied from another
  project. Ids are re-checked against THIS project's approved set at render
  time -- never trusted from storage. Same reasoning as
  ``helpers.csunesco_terria_embed_url``, which re-validates a Terria URL on
  every render rather than trusting what was stored.

Every lookup fails soft: a block that cannot resolve renders empty, never a 500
on a public URL.
"""
import logging

import ckan.plugins.toolkit as tk

log = logging.getLogger(__name__)

# Block types that need the project's connected data sources. datasets_list is
# in here because its default mode lists the packages those approvals created;
# fetching them for its other modes too costs one cheap query and keeps the
# dependency rule a single set rather than a per-mode special case.
_DATA_BLOCKS = frozenset(
    ('builtin_data', 'chart', 'observation_map', 'datasets_list', 'data_chat'))

# Block types that need the project's content rows.
_CONTENT_BLOCKS = frozenset(('builtin_news_events', 'content_list'))

# What the built-in "News, Events & More" band has always shown.
BUILTIN_CONTENT_LIMIT = 6


def build_context(context, project, blocks, has_region=False,
                  can_manage=False, preview=False, scope=None,
                  initiative=None):
    """Resolve everything ``blocks`` needs into a single dict.

    ``preview`` marks a manager previewing their unpublished draft, so snippets
    can label the page accordingly.

    ``project=None`` means SITE scope -- the hub page. There, ``stats`` holds
    the portal-wide aggregate (same four keys as a project's stats row, which
    is what lets the shared ``stats``/at-a-glance snippets render both) and
    the data-source machinery stays empty: the data-bound block types are
    project-only, so a forged one in stored JSON simply resolves to nothing.
    """
    types = {block.get('type') for block in blocks or []}
    scope = scope or ('site' if project is None else 'project')
    ctx = {
        'scope': scope,
        'project': project,
        'initiative': initiative,
        'stats': (_aggregate_stats(context, initiative) if initiative
                  else _aggregate_stats(context) if project is None
                  else project.get('stats') or {}),
        'has_region': has_region,
        'can_manage': can_manage,
        'preview': preview,
        'data_sources': [],
        'approved_sources': {},
        'news_events': [],
        'recent_projects': [],
        'initiative_projects': [],
        'initiative_news': [],
        'initiative_events': [],
        # Asset gating: a page with no chart must not pay for a 208 KB
        # Chart.js bundle, and one with no gallery must not load the lightbox.
        # The chat draws its answers with the same painter as a chart block, so
        # it pulls in Chart.js too -- one flag, or a page whose only data block
        # is the chat would load the chat script and then have nothing to draw
        # with.
        'has_charts': bool(types & {'chart', 'data_chat'}),
        'has_chat': 'data_chat' in types,
        'has_lightbox': any(block.get('type') == 'image'
                            and block.get('lightbox')
                            for block in blocks or []),
        'has_carousel': any(block.get('type') == 'image'
                            and block.get('layout') == 'carousel'
                            for block in blocks or []),
        # Where the per-section "Edit" pencil points (managers only, and only
        # outside the preview -- the preview IS the editor's output).
        'edit_url': (_edit_url(project, initiative)
                     if can_manage and not preview else None),
    }

    if project is not None and types & _DATA_BLOCKS:
        data_sources = _list_data_sources(context, project['id'])
        ctx['data_sources'] = data_sources
        # The allowlist every block that names a source is checked against.
        ctx['approved_sources'] = dict(
            (source['id'], source) for source in data_sources
            if source.get('status') == 'approved')

    if project is not None and types & _CONTENT_BLOCKS:
        ctx['news_events'] = _list_content(
            context, project['id'], limit=BUILTIN_CONTENT_LIMIT)

    if 'site_projects' in types:
        # One query even if (somehow) several site_projects blocks exist:
        # fetch the largest limit any of them asks for; each block slices.
        limit = max([int(block.get('limit') or 6) for block in blocks or []
                     if block.get('type') == 'site_projects'] or [6])
        ctx['recent_projects'] = _recent_projects(context, limit)

    if initiative is not None:
        project_limit = max(
            [int(block.get('limit') or 6) for block in blocks or []
             if block.get('type') == 'initiative_projects'] or [6])
        ctx['initiative_projects'] = _recent_projects(
            context, project_limit, initiative['name'])
        content_limit = max(
            [int(block.get('limit') or 3) for block in blocks or []
             if block.get('type') in ('initiative_news',
                                      'initiative_events')] or [3])
        ctx['initiative_news'] = _list_initiative_content(
            context, initiative['name'], content_limit, 'cs-news')
        ctx['initiative_events'] = _list_initiative_content(
            context, initiative['name'], content_limit, 'cs-event',
            upcoming=True)

    # Author-configurable listings, resolved ONCE per distinct configuration.
    # Without the memo, four content_list blocks with the same settings would
    # each run their own query on a public page.
    ctx['content_lists'] = _resolve_content_lists(
        context, project, blocks, initiative=initiative)
    ctx['dataset_lists'] = _resolve_dataset_lists(context, project, blocks,
                                                  ctx['data_sources'])
    return ctx


def content_list_key(block):
    """The identity of a content_list block's QUERY (not of the block)."""
    return (block.get('content_type') or 'all', block.get('scope') or 'project',
            int(block.get('limit') or 6), bool(block.get('featured_only')))


def _resolve_content_lists(context, project, blocks, initiative=None):
    """``{query key: [rows]}`` for every distinct content_list configuration."""
    out = {}
    for block in blocks or []:
        if block.get('type') != 'content_list':
            continue
        key = content_list_key(block)
        if key in out:
            continue
        content_type, scope, limit, featured = key
        data_dict = {'limit': limit, 'offset': 0, 'status': 'approved'}
        if content_type != 'all':
            data_dict['content_type'] = content_type
        # 'site' means portal-wide (no scope filter). On the hub page
        # (project is None) the project/initiative scopes DEGRADE to
        # portal-wide too -- fail-soft: a copied or forged block must render
        # a duller list, never break the page.
        if scope == 'initiative' and initiative is not None:
            data_dict['initiative'] = initiative.get('name')
        elif scope == 'initiative' and project is not None:
            data_dict['initiative'] = project.get('initiative_group')
        elif initiative is not None and scope != 'site':
            data_dict['initiative'] = initiative.get('name')
        elif scope != 'site' and project is not None:
            data_dict['project_id'] = project['id']
        if featured:
            data_dict['featured'] = True
        try:
            listing = tk.get_action('csunesco_content_list')(context, data_dict)
            out[key] = listing.get('results', [])
        except Exception:
            log.warning('csunesco: content list block unavailable')
            out[key] = []
    return out


def dataset_list_key(block):
    """The identity of a datasets_list block's QUERY."""
    return (block.get('source') or 'project', block.get('org') or '',
            tuple(block.get('ids') or ()), int(block.get('limit') or 6))


def _resolve_dataset_lists(context, project, blocks, data_sources):
    """``{query key: [package dicts]}`` for every distinct datasets_list config."""
    out = {}
    for block in blocks or []:
        if block.get('type') != 'datasets_list':
            continue
        key = dataset_list_key(block)
        if key in out:
            continue
        source, org, ids, limit = key
        if source == 'organization' and org:
            out[key] = _search_datasets('owner_org:"%s" OR organization:"%s"'
                                        % (org, org), limit)
        elif source == 'ids' and ids:
            out[key] = _show_datasets(context, ids[:limit])
        else:
            # "This project's connected data": the packages the data-source
            # approvals created, in the order the sources are listed.
            names = [item.get('ckan_package_id') for item in data_sources or []
                     if item.get('status') == 'approved'
                     and item.get('ckan_package_id')]
            out[key] = _show_datasets(context, names[:limit])
    return out


def _show_datasets(context, names):
    """package_show for each reference, skipping the ones we cannot read.

    Fails soft per item: one deleted or private dataset must not empty the
    whole block.
    """
    packages = []
    for name in names:
        try:
            packages.append(tk.get_action('package_show')(
                dict(context, for_view=False), {'id': name}))
        except Exception:
            continue
    return packages


def _search_datasets(query, limit):
    try:
        result = tk.get_action('package_search')(
            {}, {'fq': query, 'rows': limit, 'include_private': False})
        return result.get('results', [])
    except Exception:
        log.warning('csunesco: dataset search block unavailable')
        return []


def _edit_url(project, initiative=None):
    """The editor URL for this page's scope, or None (fail-soft)."""
    try:
        if initiative is not None:
            return tk.url_for('csunesco.initiative_page_edit',
                              name=initiative['name'])
        if project is None:
            return tk.url_for('csunesco.site_page_edit')
        return tk.url_for('csunesco.project_page_edit',
                          slug=project['slug'])
    except Exception:
        return None


def _aggregate_stats(context, initiative=None):
    """Portal-wide counters for the hub (fail-soft to zeros)."""
    try:
        data_dict = {'initiative': initiative['name']} if initiative else {}
        return tk.get_action('csunesco_aggregate_stats')(
            context, data_dict) or {}
    except Exception:
        log.warning('csunesco: aggregate stats unavailable')
        return {}


def _recent_projects(context, limit, initiative=None):
    """The newest approved projects, decorated with ``initiative_title``."""
    try:
        data_dict = {'limit': limit, 'offset': 0}
        if initiative:
            data_dict['initiative'] = initiative
        listing = tk.get_action('csunesco_project_list')(context, data_dict)
        results = listing.get('results', [])
    except Exception:
        log.warning('csunesco: site page project list unavailable')
        return []
    from ckanext.csunesco import constants
    titles = {initiative['name']: initiative['title']
              for initiative in constants.CS_INITIATIVES}
    for project in results:
        project['initiative_title'] = titles.get(
            project.get('initiative_group'), project.get('initiative_group'))
    return results


def _list_initiative_content(context, initiative, limit, content_type,
                             upcoming=False):
    data_dict = {'initiative': initiative, 'content_type': content_type,
                 'status': 'approved',
                 'limit': limit, 'offset': 0, 'include_project': True}
    if upcoming:
        data_dict['upcoming'] = True
    try:
        return tk.get_action('csunesco_content_list')(
            context, data_dict).get('results', [])
    except Exception:
        log.warning('csunesco: initiative content unavailable')
        return []


def _list_data_sources(context, project_id):
    """Connected app-data sources. The action widens scope for managers (they
    also see pending/rejected rows with a status badge); the public gets
    approved ones only."""
    try:
        listing = tk.get_action('csunesco_data_source_list')(
            context, {'project_id': project_id, 'limit': 20})
        return listing.get('results', [])
    except Exception:
        log.warning('csunesco: project page data sources unavailable')
        return []


def _list_content(context, project_id, limit=BUILTIN_CONTENT_LIMIT,
                  content_type=None):
    """Summarized content rows for a project (approved only for the public)."""
    data_dict = {'project_id': project_id, 'status': 'approved',
                 'limit': limit, 'offset': 0}
    if content_type:
        data_dict['content_type'] = content_type
    try:
        listing = tk.get_action('csunesco_content_list')(context, data_dict)
        return listing.get('results', [])
    except Exception:
        log.warning('csunesco: project page content unavailable')
        return []


def visible_blocks(blocks, scope='project'):
    """The blocks that actually render: not hidden, of a known type, and
    allowed in ``scope`` -- a site_hero forged into a project page's JSON is
    stored harmlessly (normalize is scope-agnostic) and filtered out HERE."""
    from ckanext.csunesco.logic import blocks as blocks_module
    return [block for block in blocks or []
            if not block.get('hidden')
            and block.get('type') in blocks_module.BLOCK_TYPES
            and scope in blocks_module.BLOCK_TYPES[block['type']].scopes]
