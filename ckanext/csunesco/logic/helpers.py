# encoding: utf-8
"""Template helpers for the public Citizen Science presentation layer.

Increment 4: thin helpers consumed by the ``csunesco/*`` templates. They read
plain data from ``constants``, build URLs via the toolkit, and -- crucially --
delegate any aggregation to the action layer so a helper NEVER touches the DB
directly. Optional dependencies degrade gracefully (the QR helper returns
``None`` when ``qrcode`` / Pillow are not installed).

Registered with CKAN through the plugin's ``ITemplateHelpers`` (``get_helpers``)
and imported lazily there so there is no import-time dependency on CKAN.
"""
import base64
import functools
import io
import logging

import ckan.plugins.toolkit as tk

from ckanext.csunesco import constants

log = logging.getLogger(__name__)

# The parent group whose active children are the valid member states.
MEMBER_STATES_GROUP = constants.MEMBER_STATES_GROUP

# Neutral zeros returned whenever the aggregate stats cannot be computed, so the
# "At a Glance" band always renders (shows 0 gracefully).
_ZERO_STATS = {
    'citizen_scientists': 0,
    'observations': 0,
    'sites_monitored': 0,
    'member_states': 0,
}


def csunesco_initiatives():
    """Return the four Citizen Science initiatives (list of ``{name, title}``)."""
    return list(constants.CS_INITIATIVES)


def csunesco_aggregate_stats():
    """At-a-glance totals across approved projects (via the domain action).

    Delegates to the ``csunesco_aggregate_stats`` action so the helper never
    queries the DB directly. Returns zeros on ANY error so the band still
    renders rather than breaking the page.
    """
    try:
        return tk.get_action('csunesco_aggregate_stats')({}, {})
    except Exception:
        log.warning('csunesco: aggregate stats could not be computed')
        return dict(_ZERO_STATS)


def csunesco_recent_news(limit=3):
    """Most recent APPROVED news items for the hub's "Latest News" band.

    Delegates to the ``csunesco_content_list`` action (which pins anonymous /
    non-sysadmin callers to ``status='approved'``) and returns just the
    summarized rows. Fails soft to an empty list so the band -- and the whole
    page -- always render even if content listing errors.
    """
    try:
        result = tk.get_action('csunesco_content_list')(
            {}, {'content_type': 'cs-news', 'limit': limit})
        return result.get('results', [])
    except Exception:
        log.warning('csunesco: recent news could not be listed')
        return []


def csunesco_project_url(slug):
    """Path of a project's public landing page (same-origin; safe for fetch)."""
    return tk.url_for('csunesco.project_landing', slug=slug)


def csunesco_join_url(slug):
    """Path of a project's join endpoint (POST target for the join form)."""
    return tk.url_for('csunesco.join_project', slug=slug)


@functools.lru_cache(maxsize=256)
def csunesco_qr_data_uri(text):
    """Return a PNG ``data:`` URI QR code for ``text`` (cached per URL).

    Degrades gracefully: returns ``None`` when the optional ``qrcode`` package
    (or its Pillow image backend) is not installed, or if generation fails for
    any reason -- templates then show only the short link. The ``lru_cache``
    keys on the exact ``text`` so a given URL's PNG is encoded once per process.
    """
    if not text:
        return None
    try:
        import qrcode
        import qrcode.image.pil  # noqa: F401 -- ensure a PIL backend exists
    except ImportError:
        return None
    try:
        image = qrcode.make(text)
        buffer = io.BytesIO()
        image.save(buffer, format='PNG')
        encoded = base64.b64encode(buffer.getvalue()).decode('ascii')
    except Exception:
        log.warning('csunesco: QR code could not be generated')
        return None
    return 'data:image/png;base64,{0}'.format(encoded)


def csunesco_pending_count():
    """Total pending items awaiting the acting user's review (0 for anon).

    Reuses the SAME per-request cached counts as the admin panel so the header
    badge and the panel tabs can never disagree. Fails soft to 0 so a template
    that always renders the badge never breaks the page.
    """
    if not tk.g.user:
        return 0
    try:
        import ckan.model as model
        from ckanext.csunesco.logic.action.admin import _get_pending_counts
        context = {'model': model, 'session': model.Session, 'user': tk.g.user}
        return _get_pending_counts(context).get('total', 0)
    except Exception:
        log.warning('csunesco: pending count could not be computed')
        return 0


def csunesco_can_edit_project(project):
    """True when the acting user may edit ``project``'s own details.

    Takes the dictized project, not an id: the rule includes "the author of a
    request that has not been approved yet", and both facts that needs live on
    the row. Distinct from ``csunesco_can_manage_project``, which answers the
    narrower "may add content here" and is False for the author of a pending
    request -- their admin membership row does not exist until approval.

    Any failure degrades to False.
    """
    if not project or not tk.g.user:
        return False
    try:
        import ckan.model as model
        from ckanext.csunesco.logic import auth
        context = {'model': model, 'user': tk.g.user}
        return auth.can_edit_project_details(context, project)
    except Exception:
        log.warning('csunesco: project edit permission could not be resolved')
        return False


def csunesco_can_manage_project(project_id):
    """True when the acting user may add/edit content for ``project_id``.

    Thin wrapper over the ``csunesco_content_create`` auth (sysadmin OR the
    project's admin) so templates can conditionally show manager-only affordances
    (status badges, "Add news/event" links) without touching the DB or auth
    internals. Any failure degrades to False.
    """
    if not project_id or not tk.g.user:
        return False
    try:
        import ckan.model as model
        context = {'model': model, 'user': tk.g.user}
        return tk.check_access(
            'csunesco_content_create', context, {'project_id': project_id})
    except tk.NotAuthorized:
        # The expected answer for most visitors -- not an error, and logging it
        # would put a line in the log for every card on every public page.
        return False
    except Exception:
        # Anything else IS an error, and it silently strips a manager of every
        # affordance on their own project. Same visible outcome, very different
        # cause, so they must not share a branch.
        log.warning('csunesco: manage-project check failed for %s', project_id)
        return False


def csunesco_data_stories_new_url():
    """URL of the Data Stories editor, or ``None`` when the feature is off.

    Data Stories (ckanext-pages) is the portal's tool for combining datasets
    into narrative visualizations. Gated on its own config flag AND on the
    route actually existing, so the button simply disappears on portals
    without the plugin -- never a broken link.
    """
    try:
        if not tk.asbool(tk.config.get('ckanext.data_stories.enabled')):
            return None
        return tk.url_for('data_stories.create')
    except Exception:
        return None


def csunesco_block_type(key):
    """The registry entry for a block type, or ``None`` when it is unknown.

    The block-render dispatcher uses this to resolve a type to its snippet.
    Returning ``None`` (rather than raising) is what lets a page whose JSON
    predates a removed block type still render -- the stale block is simply
    skipped instead of taking down a public URL.
    """
    from ckanext.csunesco.logic import blocks
    return blocks.BLOCK_TYPES.get(key)


def csunesco_icon(name, size=20):
    """Inline SVG for an icon slug (see ``logic/icons.py``).

    A helper rather than a Jinja macro because CKAN's template environment
    does not export macros across imports, and rather than a snippet because
    an editor page draws ~100 icons -- a dict lookup, not a template render.
    """
    from markupsafe import Markup
    from ckanext.csunesco.logic import icons
    return Markup(icons.svg(name, size))


IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.avif')


def csunesco_content_image(media):
    """First image URL in a content row's ``media`` list, or None.

    Pure heuristic by path extension (querystring ignored) -- good enough for
    a card cover without fetching anything. Unit-tested.
    """
    try:
        from urllib.parse import urlparse
    except ImportError:  # pragma: no cover - py2 leftover guard
        from urlparse import urlparse
    for url in (media or []):
        if not isinstance(url, str) or not url.strip():
            continue
        try:
            path = urlparse(url).path.lower()
        except Exception:
            continue
        if path.endswith(IMAGE_EXTENSIONS):
            return url
    return None


def csunesco_format_number(value):
    """Group a count for reading: 1605 -> "1,605" in the request's locale.

    CKAN ships a localised formatter; fall back to the raw value rather than
    letting a display detail break a counter.
    """
    try:
        return tk.h.localised_number(int(value))
    except Exception:
        return value


def csunesco_blocks_need_review(blocks):
    """True when a page embeds content from an origin we do not control.

    Drives the editor's honest "what happens when I publish" line, and mirrors
    the rule the submit action applies -- a trusted project auto-publishes only
    while this is False.
    """
    from ckanext.csunesco.logic import blocks as blocks_module
    return bool(blocks_module.blocks_requiring_review(blocks))


def csunesco_uploads_enabled():
    """Whether the page editor may offer a working file picker."""
    from ckanext.csunesco.logic import uploads
    return uploads.uploads_enabled()


def csunesco_image_upload_max_size():
    """Configured per-file image limit in MiB for editor hint text."""
    from ckanext.csunesco.logic import uploads
    return uploads.max_image_size()


def csunesco_block_content(ctx, block):
    """Rows a ``content_list`` block should render (resolved in the view).

    The resolution is memoised per QUERY in ``page_render``, so several blocks
    asking for the same thing share one lookup. Templates go through this
    helper because the memo is keyed by a tuple they cannot build.
    """
    from ckanext.csunesco.logic import page_render
    lists = (ctx or {}).get('content_lists') or {}
    return lists.get(page_render.content_list_key(block), [])


def csunesco_block_datasets(ctx, block):
    """Packages a ``datasets_list`` block should render (resolved in the view)."""
    from ckanext.csunesco.logic import page_render
    lists = (ctx or {}).get('dataset_lists') or {}
    return lists.get(page_render.dataset_list_key(block), [])


def csunesco_video_embed_url(provider, video_id):
    """The embed URL for a recognised video, or ``None``.

    Built HERE from the id extracted at save time -- the author's URL is never
    used as an iframe ``src``, which is what keeps a video block from becoming
    an arbitrary-embed block. YouTube goes through the nocookie host so merely
    loading a project page does not hand the visitor to an ad profile.
    """
    if not provider or not video_id:
        return None
    if provider == 'youtube':
        return 'https://www.youtube-nocookie.com/embed/%s' % video_id
    if provider == 'vimeo':
        return 'https://player.vimeo.com/video/%s' % video_id
    return None


def csunesco_project_data_sources(project_id):
    """Approved data sources of a project, for the editor's source pickers.

    Memoised per request: the editor calls this once per chart and per
    observation-map block, which on a full page meant ten identical lookups.

    Only approved rows: a block may only ever point at data the public can
    already reach through the proxy. Fails soft to an empty list so the editor
    still renders when the listing errors.
    """
    if not project_id:
        return []
    cache_attr = '_csunesco_sources_%s' % project_id
    cached = getattr(tk.g, cache_attr, None)
    if cached is not None:
        return cached
    try:
        result = tk.get_action('csunesco_data_source_list')(
            {}, {'project_id': project_id, 'limit': 50})
        sources = [source for source in result.get('results', [])
                   if source.get('status') == 'approved']
    except Exception:
        log.warning('csunesco: data sources for the page editor unavailable')
        sources = []
    try:
        setattr(tk.g, cache_attr, sources)
    except Exception:
        pass          # no request context: just do not cache
    return sources


def csunesco_block_palette(scope='project'):
    """The block types an author may ADD in ``scope``, in palette order."""
    from ckanext.csunesco.logic import blocks
    return [blocks.BLOCK_TYPES[key] for key in blocks.addable_types(scope)]


def csunesco_terria_embed_url(url):
    """Return ``url`` when it is under an allowlisted Terria base, else None.

    Re-runs the SAME base-allowlist check as the ``csunesco_valid_terria_url``
    validator at render time (defense in depth: a row stored before a config
    change must not become an embeddable iframe). Templates fall back to a plain
    link -- or nothing -- when this returns ``None``.
    """
    if not url:
        return None
    try:
        from ckanext.csunesco.logic import validators
        validators.csunesco_valid_terria_url(url)
        return url
    except Exception:
        return None


def csunesco_member_state_title(name):
    """Human title for a member-state group name (falls back to the name).

    Best-effort lookup of the CKAN group's display title; any failure (missing
    group, DB error) simply returns the raw ``name`` so a card/label never
    breaks.
    """
    if not name:
        return name
    try:
        import ckan.model as model
        group = model.Group.get(name)
        if group is not None and group.title:
            return group.title
    except Exception:
        log.warning('csunesco: member-state title lookup failed')
    return name


def csunesco_member_state_titles(names):
    """``[{'name', 'title'}, ...]`` for ``names``, in order, in ONE query.

    The landing band and the review panel both list a project's countries.
    Calling ``csunesco_member_state_title`` per name is one ``Group.get()``
    each, so a project declaring twenty countries would issue twenty queries
    to render one line.

    Unknown names fall back to themselves, so a country that was later
    unpublished still renders as its slug rather than vanishing.
    """
    if not names:
        return []
    try:
        result = tk.get_action('csunesco_member_state_list')({}, {})
        titles = {row['name']: row['title']
                  for row in (result.get('member_states') or [])}
    except Exception:
        log.warning('csunesco: member-state titles unavailable')
        titles = {}
    return [{'name': name, 'title': titles.get(name, name)} for name in names]
