# encoding: utf-8
"""HTTP orchestration for the project-page editor.

The editor is built so that **the JavaScript-free path is the primary path**,
not a degraded fallback. Every action a manager can take -- add a block, move
one, delete one, hide one, save, submit for review -- is an ordinary submit
button in one form:

    <button name="op" value="move_up:3">

The server parses the whole block list out of the POST, applies that single
operation, saves the draft and redirects back to the block's anchor (PRG). The
enhanced experience layers on top of the SAME endpoint, so there is one code
path to reason about and the no-JS path cannot silently rot.

Two things this shape gets right that are easy to get wrong:

* A ``disabled`` button submits neither its name nor its value. Any
  disable-on-submit behaviour must therefore leave the op in a hidden field --
  never rely on the pressed button alone once JS is involved.
* After a client-side delete the indices arrive with GAPS
  (``blocks[0]``, ``blocks[2]``, ``blocks[7]``). They are used only to ORDER the
  blocks, never as a count; ``blocks.blocks_from_form`` sorts numerically and
  renumbers.
"""
import logging

from flask import request

import ckan.plugins.toolkit as tk
import ckan.model as model

from ckanext.csunesco.logic import blocks as blocks_module
from ckanext.csunesco.logic import page_render

log = logging.getLogger(__name__)

GENERIC_ERROR = 'Something went wrong. Please try again.'


def _context():
    return {'model': model, 'session': model.Session, 'user': tk.g.user}


def _not_authorized_response():
    if not tk.g.user:
        return tk.redirect_to('user.login')
    return tk.abort(403, tk._('You are not authorized to view this page'))


def _load_project(slug, include_geojson=False):
    """Resolve the project, or ``None`` (callers 404).

    ``include_geojson`` is off by default: the region blob can be large and the
    editor only ever needs the project's identity. The preview asks for it only
    to decide whether the region-map block has anything to draw.
    """
    try:
        return tk.get_action('csunesco_project_show')(
            _context(), {'slug': slug, 'include_geojson': include_geojson})
    except tk.ObjectNotFound:
        return None
    except tk.NotAuthorized:
        return None
    except Exception:
        log.warning('csunesco: page editor could not resolve the project')
        return None


def _load_page(project_id):
    """The stored page dict including the draft, or ``None`` on any failure."""
    try:
        return tk.get_action('csunesco_project_page_show')(
            _context(), {'project_id': project_id, 'include_draft': True})
    except Exception:
        log.warning('csunesco: page draft could not be loaded')
        return None


def _initial_blocks(page):
    """What the editor opens with.

    First visit seeds the draft from the published page, or from the default
    layout -- so a manager starts from the page they already have rather than a
    blank slate they must rebuild from memory.
    """
    draft = (page or {}).get('draft_blocks')
    if draft:
        return blocks_module.ensure_builtins(draft)
    published = (page or {}).get('published_blocks')
    if published:
        return blocks_module.ensure_builtins(published)
    return blocks_module.default_blocks()


def _render_editor(project, page, blocks, errors=None, notice=None):
    return tk.render('csunesco/project_page_form.html', extra_vars={
        'project': project,
        'page': page or {},
        'blocks': blocks,
        'errors': errors or {},
        'notice': notice,
        'palette': tk.h.csunesco_block_palette(),
        'max_blocks': blocks_module.MAX_BLOCKS,
    })


def project_page_edit(slug):
    """GET the page editor for ``slug``; POST applies one operation and saves."""
    if not tk.g.user:
        return _not_authorized_response()

    project = _load_project(slug)
    if project is None:
        return tk.abort(404, tk._('Project not found'))
    if not tk.h.csunesco_can_manage_project(project.get('id')):
        return _not_authorized_response()

    page = _load_page(project['id'])

    if request.method == 'GET':
        return _render_editor(project, page, _initial_blocks(page))

    # --- POST ---------------------------------------------------------------
    # Parse the whole list back out of the form, then apply exactly one op.
    blocks = blocks_module.blocks_from_form(request.form.items(multi=True))
    blocks = blocks_module.ensure_builtins(blocks)
    op_name, _argument = blocks_module.parse_op(request.form.get('op'))
    blocks, anchor = blocks_module.apply_op(blocks, request.form.get('op'))

    try:
        result = tk.get_action('csunesco_project_page_update')(
            _context(), {'project_id': project['id'], 'blocks': blocks})
    except tk.NotAuthorized:
        return _not_authorized_response()
    except tk.ValidationError as error:
        # Re-render (no redirect) so the manager keeps their unsaved edits.
        return _render_editor(project, page, blocks,
                              errors=error.error_dict or {})
    except Exception:
        log.warning('csunesco: page draft could not be saved')
        return _render_editor(project, page, blocks,
                              errors={'message': GENERIC_ERROR})

    # "Save and publish" is save-then-submit, so the reviewer always sees the
    # version the manager was looking at when they pressed the button.
    if op_name == 'submit':
        try:
            outcome = tk.get_action('csunesco_project_page_submit')(
                _context(), {'project_id': project['id']})
        except tk.NotAuthorized:
            return _not_authorized_response()
        except tk.ValidationError as error:
            return _render_editor(project, page, blocks,
                                  errors=error.error_dict or {})
        except Exception:
            log.warning('csunesco: page could not be submitted')
            return _render_editor(project, page, blocks,
                                  errors={'message': GENERIC_ERROR})
        if outcome.get('published'):
            tk.h.flash_success(tk._('Your page is live.'))
        else:
            tk.h.flash_success(tk._(
                'Your page was sent for review. It will go live once a UNESCO '
                'administrator approves it.'))
        return tk.redirect_to('csunesco.project_landing', slug=project['slug'])

    if result.get('withdrawn'):
        tk.h.flash_notice(tk._(
            'Your changes took this page out of the review queue. Publish it '
            'again when you are ready.'))
    elif op_name == 'save':
        tk.h.flash_success(tk._('Draft saved.'))

    return tk.redirect_to(
        tk.url_for('csunesco.project_page_edit', slug=project['slug'])
        + ('#block-%s' % anchor if anchor else ''))


def project_page_preview(slug):
    """Render the DRAFT through the public landing template.

    Managers only: this is the unpublished page. Hidden blocks are shown with a
    marker so the manager can see what they switched off without leaving the
    editor's mental model.
    """
    if not tk.g.user:
        return _not_authorized_response()

    project = _load_project(slug, include_geojson=True)
    if project is None:
        return tk.abort(404, tk._('Project not found'))
    if not tk.h.csunesco_can_manage_project(project.get('id')):
        return _not_authorized_response()

    # Same treatment as the public landing: keep only the boolean, never inline
    # the (potentially large) region payload -- the map JS fetches it.
    from ckanext.csunesco.logic.views import _INITIATIVE_TITLES
    project['initiative_title'] = _INITIATIVE_TITLES.get(
        project.get('initiative_group'), project.get('initiative_group'))
    has_region = bool(project.get('region_geojson'))
    project.pop('region_geojson', None)

    page = _load_page(project['id'])
    blocks = page_render.visible_blocks(_initial_blocks(page))
    ctx = page_render.build_context(
        _context(), project, blocks, has_region=has_region,
        can_manage=True, preview=True)

    return tk.render('csunesco/project_landing.html', extra_vars={
        'project': project,
        'blocks': blocks,
        'ctx': ctx,
        'is_draft_preview': True,
    })
