# encoding: utf-8
"""Project-page actions: show / update (the draft-and-published block list).

A project landing page is an ordered list of blocks stored twice per project:
``draft_json`` (what its manager is editing) and ``published_json`` (what the
public sees). Keeping them apart is what lets a manager work on the page over
several sittings, and lets a reviewer approve a specific version, without the
public page changing under anyone.

Only ONE representation is ever stored -- the block JSON. Deliberately unlike
the Data Stories editor in ``ckanext-pages``, which stores both the blocks and a
baked HTML copy and relies on client-side JS to keep them in sync; a stale
editor there silently drops blocks on save.

Layering: :mod:`ckanext.csunesco.logic.blocks` owns SHAPE (it is CKAN-free and
unit-tested without a container); this module owns POLICY that needs config or
the database -- who may write, whether the project exists, and whether a Terria
URL sits under an allowlisted base.
"""
import datetime
import hashlib
import json

import ckan.plugins.toolkit as tk

from ckanext.csunesco import db
from ckanext.csunesco.logic import auth
from ckanext.csunesco.logic import blocks as blocks_module
from ckanext.csunesco.logic import validators
from ckanext.csunesco.logic.action import current_user_id


def _utcnow():
    return datetime.datetime.utcnow()


def _resolve_project(data_dict):
    """Resolve the project from an id or a slug (None when unresolved)."""
    key = (data_dict.get('project_id') or data_dict.get('project')
           or data_dict.get('project_slug') or data_dict.get('id'))
    return db.get_project(key)


def _require_project(data_dict):
    project = _resolve_project(data_dict or {})
    if project is None:
        raise tk.ObjectNotFound(tk._('Project not found'))
    return project


def draft_hash(blocks):
    """A stable fingerprint of a draft, used for optimistic concurrency.

    The review panel echoes this back with the approve request; approving a
    draft whose hash has moved on is refused, so a manager who keeps editing
    after submitting can never have an unseen version published on them.
    """
    payload = blocks_module.blocks_to_json(blocks or [])
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _validate_policy(blocks):
    """Config-dependent checks that :mod:`blocks` cannot make on its own.

    Right now that is only the Terria allowlist: ``blocks.normalize_blocks``
    verifies the URL is https and well formed, but whether it sits under an
    allowlisted base comes from ``ckanext.csunesco.terria_base_url``. Raises
    ``ValidationError`` so the editor can show the problem inline instead of
    silently dropping what the manager typed.
    """
    errors = []
    bad_ids = []
    for block in blocks or []:
        if block.get('type') != 'terria_map':
            continue
        url = block.get('url')
        if not url:
            continue
        try:
            validators.csunesco_valid_terria_url(url)
        except Exception:
            # Name the block, not its position: the editor shows no numbers,
            # and the count includes hidden standard sections, so "Block 9" was
            # not something a manager could resolve.
            errors.append(tk._(
                'The map link in "%(name)s" was not accepted: only links to '
                'the official IHP-WINS map viewer can be embedded.')
                % {'name': block.get('title') or tk._('Interactive map')})
            bad_ids.append(block.get('id'))
    if errors:
        # block_ids is machinery for opening the offending blocks, not a
        # message; the template skips it when listing errors.
        raise tk.ValidationError({'blocks': errors, 'block_ids': bad_ids})


def _user_display_name(user_id):
    """A readable name for the submitter, or ``''`` (never breaks a submit)."""
    if not user_id:
        return u''
    try:
        import ckan.model as model
        user = model.User.get(user_id)
        return user.display_name or user.name if user else u''
    except Exception:
        return u''


def _can_view_draft(context, project_id):
    """Managers and reviewers may read an unpublished draft; nobody else."""
    return auth.can_manage_project(context, project_id)


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

@tk.side_effect_free
def csunesco_project_page_show(context, data_dict):
    """The project's page. Public callers only ever see the published version.

    ``published_blocks`` is ``None`` when the page has never been published --
    the caller then renders the default layout. ``draft_blocks`` is included
    only for a manager/reviewer, and only when ``include_draft`` is asked for.
    """
    tk.check_access('csunesco_project_page_show', context, data_dict)
    data_dict = data_dict or {}
    project = _require_project(data_dict)

    include_draft = tk.asbool(data_dict.get('include_draft', False))
    if include_draft and not _can_view_draft(context, project.id):
        raise tk.NotAuthorized(tk._('Not authorized to view this draft'))

    page = db.get_project_page(project.id)
    if page is None:
        # No row yet: an unwritten page, not an error. Same contract as a page
        # that exists but was never published.
        result = {
            'project_id': project.id,
            'status': 'draft',
            'published_blocks': None,
        }
        if include_draft:
            result['draft_blocks'] = None
        return result
    return db.page_dictize(page, include_draft=include_draft)


def csunesco_project_page_update(context, data_dict):
    """Save the project page DRAFT. Never touches the published version.

    Saving a draft that was already awaiting review WITHDRAWS it from the queue
    (``withdrawn: True`` in the result). That is the honest behaviour: the
    reviewer queued version N and the manager has just written N+1, so the
    queue must not keep pointing at something nobody will ever see again.
    """
    tk.check_access('csunesco_project_page_update', context, data_dict)
    data_dict = data_dict or {}
    project = _require_project(data_dict)

    # Defence in depth: re-check on the RESOLVED project, not on whatever id
    # the caller put in the auth payload.
    if not auth.can_manage_project(context, project.id):
        raise tk.NotAuthorized(tk._('Not authorized to edit this page'))
    if project.status != 'approved':
        raise tk.ValidationError({'project_id': [tk._(
            'Only approved projects have a page')]})

    raw_blocks = data_dict.get('blocks')
    if raw_blocks is None:
        raise tk.ValidationError({'blocks': [tk._('Missing value')]})
    if isinstance(raw_blocks, str):
        blocks = blocks_module.blocks_from_json(raw_blocks)
    else:
        blocks = blocks_module.normalize_blocks(raw_blocks)
    blocks = blocks_module.ensure_builtins(blocks)
    _validate_policy(blocks)
    if blocks_module.oversized(blocks):
        raise tk.ValidationError({'blocks': [tk._(
            'This page is too large. Shorten the text blocks and try again.')]})

    user_id = current_user_id(context)
    page = db.get_or_create_project_page(project.id, created_by=user_id)
    withdrawn = page.status == 'pending'

    page.draft_json = blocks_module.blocks_to_json(blocks)
    page.draft_hash = draft_hash(blocks)
    page.status = u'draft'
    page.modified = _utcnow()
    if withdrawn:
        # It is back in the manager's hands: clear the submission trail so the
        # panel cannot show a reviewer stale "submitted by / at" metadata.
        page.submitted_by = None
        page.submitted_at = None
    db.Session.add(page)
    db.Session.commit()

    result = db.page_dictize(page, include_draft=True)
    result['withdrawn'] = withdrawn
    return result


def csunesco_project_page_submit(context, data_dict):
    """Publish the draft, or queue it for review.

    A sysadmin's page publishes immediately. A TRUSTED project's page publishes
    immediately too -- but only while it embeds nothing from an origin we do not
    control. That nuance mirrors ``content.content_initial_status``, which
    deliberately keeps publications and maps out of the trusted fast path
    because they carry external links; a page can hold images, video and Terria
    embeds, i.e. exactly the same surface.
    """
    tk.check_access('csunesco_project_page_update', context, data_dict)
    data_dict = data_dict or {}
    project = _require_project(data_dict)
    if not auth.can_manage_project(context, project.id):
        raise tk.NotAuthorized(tk._('Not authorized to edit this page'))

    page = db.get_project_page(project.id)
    if page is None or not page.draft_json:
        raise tk.ValidationError({'blocks': [tk._('There is nothing to publish yet')]})

    blocks = blocks_module.blocks_from_json(page.draft_json)
    _validate_policy(blocks)

    status = blocks_module.page_initial_status(
        auth._is_sysadmin(context), bool(project.trusted), blocks)

    now = _utcnow()
    user_id = current_user_id(context)
    page.submitted_by = user_id
    page.submitted_at = now
    # Stashed here rather than recomputed in the review panel: describing a row
    # would otherwise mean parsing every pending page's draft JSON.
    extras = db._load_json(page.extras, {})
    if not isinstance(extras, dict):
        extras = {}
    extras['requires_review'] = blocks_module.blocks_requiring_review(blocks)
    extras['submitted_by_name'] = _user_display_name(user_id)
    extras['first_publication'] = page.published_json is None
    page.extras = json.dumps(extras)
    page.rejection_reason = None
    page.modified = now
    if status == 'approved':
        page.published_json = page.draft_json
        page.published_at = now
        page.reviewed_by = user_id
        page.reviewed_at = now
        page.status = u'approved'
    else:
        page.status = u'pending'
    db.Session.add(page)
    db.Session.commit()

    result = db.page_dictize(page, include_draft=True)
    result['published'] = status == 'approved'
    result['requires_review'] = blocks_module.blocks_requiring_review(blocks)
    return result


def csunesco_project_page_approve(context, data_dict):
    """Publish a page that is awaiting review.

    Requires the caller to pass back the ``draft_hash`` they were shown. If the
    manager edited the draft in the meantime the hash no longer matches and the
    approval is refused -- otherwise a reviewer could publish a version they
    never saw. (Editing a pending draft also withdraws it, so this is the
    narrow race, not the common path.)
    """
    tk.check_access('csunesco_project_page_approve', context, data_dict)
    data_dict = data_dict or {}
    project = _require_project(data_dict)
    page = db.get_project_page(project.id)
    if page is None or page.status != 'pending':
        raise tk.ObjectNotFound(tk._('No page is awaiting review'))

    expected = data_dict.get('draft_hash')
    if expected and expected != page.draft_hash:
        raise tk.ValidationError({'draft_hash': [tk._(
            'This page changed after you opened the review. Look at it again '
            'before approving.')]})

    now = _utcnow()
    page.published_json = page.draft_json
    page.published_at = now
    page.status = u'approved'
    page.reviewed_by = current_user_id(context)
    page.reviewed_at = now
    page.rejection_reason = None
    page.modified = now
    db.Session.add(page)
    db.Session.commit()
    return db.page_dictize(page)


def csunesco_project_page_reject(context, data_dict):
    """Send a pending page back to its manager with a reason.

    The published version (if any) is left untouched: rejecting a new draft
    must never take the live page down.
    """
    tk.check_access('csunesco_project_page_reject', context, data_dict)
    data_dict = data_dict or {}
    project = _require_project(data_dict)
    page = db.get_project_page(project.id)
    if page is None or page.status != 'pending':
        raise tk.ObjectNotFound(tk._('No page is awaiting review'))

    from ckanext.csunesco.logic.sanitize import sanitize_html
    now = _utcnow()
    page.status = u'rejected'
    page.rejection_reason = sanitize_html(data_dict.get('reason') or u'')
    page.reviewed_by = current_user_id(context)
    page.reviewed_at = now
    page.modified = now
    db.Session.add(page)
    db.Session.commit()
    return db.page_dictize(page)


def get_actions():
    return {
        'csunesco_project_page_show': csunesco_project_page_show,
        'csunesco_project_page_update': csunesco_project_page_update,
        'csunesco_project_page_submit': csunesco_project_page_submit,
        'csunesco_project_page_approve': csunesco_project_page_approve,
        'csunesco_project_page_reject': csunesco_project_page_reject,
    }
