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

from flask import request, session

import ckan.plugins.toolkit as tk
import ckan.model as model

from ckanext.csunesco.logic import blocks as blocks_module
from ckanext.csunesco.logic import page_render

log = logging.getLogger(__name__)

GENERIC_ERROR = 'Something went wrong. Please try again.'

# Session key the drop report rides on across the PRG redirect.
_DROPS_KEY = 'csunesco_page_drops'


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


def _render_editor(project, page, blocks, errors=None, notice=None,
                   drops=None):
    """Render the editor.

    ``drops`` is what normalization threw away on the last save, indexed by
    block id so each editor snippet can show it beside the field the author
    actually typed into.
    """
    by_block = {}
    for drop in drops or []:
        by_block.setdefault(drop['block'], {}) \
                .setdefault(drop['field'], []).append(drop)
    attention = set(by_block)
    attention.update(id for id in (errors or {}).get('block_ids') or [])
    # What opens: anything that needs the author's attention, plus whatever the
    # last operation acted on. The fragment never reaches the server, so the
    # anchor also travels as ?open=<id>.
    known = {block['id'] for block in blocks}
    open_ids = set(attention)
    open_ids.update(id for id in request.args.getlist('open')[:8] if id in known)
    return tk.render('csunesco/project_page_form.html', extra_vars={
        'project': project,
        'page': page or {},
        'blocks': blocks,
        'errors': errors or {},
        'notice': notice,
        'drops_by_block': by_block,
        'attention_ids': attention,
        'open_ids': open_ids & known,
        'palette': tk.h.csunesco_block_palette(),
        'max_blocks': blocks_module.MAX_BLOCKS,
    })


def _stash_drops(project_id, drops):
    """Park a drop report across the PRG redirect.

    The session, not the query string: this is a one-shot notice about the save
    that just happened and must not come back on a reload. Same mechanism as
    the flashes this view already uses.
    """
    if drops:
        session[_DROPS_KEY] = {'project': project_id,
                               'drops': drops[:blocks_module.MAX_DROPS]}


def _take_drops(project_id):
    """Pop the drop report for this project (never another's)."""
    stash = session.pop(_DROPS_KEY, None)
    if isinstance(stash, dict) and stash.get('project') == project_id:
        return stash.get('drops') or []
    return []


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
        return _render_editor(project, page, _initial_blocks(page),
                              drops=_take_drops(project['id']))

    # --- POST ---------------------------------------------------------------
    # Parse the whole list back out of the form, then apply exactly one op.
    # `op` is only ever the pressed button; `op_js` only ever what the script
    # wrote. Two names, so nothing here depends on their order in the DOM.
    raw_op = blocks_module.choose_op(request.form.get('op'),
                                     request.form.get('op_js'))
    report = blocks_module.DropReport()
    blocks = blocks_module.blocks_from_form(request.form.items(multi=True),
                                           report=report)
    blocks = blocks_module.ensure_builtins(blocks)
    op_name, _argument = blocks_module.parse_op(raw_op)
    blocks, anchor = blocks_module.apply_op(
        blocks, raw_op, convert_html=project.get('landing_content'))
    # A drop against a block the author just deleted is noise.
    alive = {block['id'] for block in blocks}
    drops = [drop for drop in report.drops if drop['block'] in alive]

    try:
        result = tk.get_action('csunesco_project_page_update')(
            _context(), {'project_id': project['id'], 'blocks': blocks})
    except tk.NotAuthorized:
        return _not_authorized_response()
    except tk.ValidationError as error:
        # Re-render (no redirect) so the manager keeps their unsaved edits.
        return _render_editor(project, page, blocks,
                              errors=error.error_dict or {}, drops=drops)
    except Exception:
        log.warning('csunesco: page draft could not be saved')
        return _render_editor(project, page, blocks,
                              errors={'message': GENERIC_ERROR},
                              drops=drops)

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
                                  errors=error.error_dict or {},
                                  drops=drops)
        except Exception:
            log.warning('csunesco: page could not be submitted')
            return _render_editor(project, page, blocks,
                                  errors={'message': GENERIC_ERROR},
                                  drops=drops)
        if outcome.get('published'):
            tk.h.flash_success(tk._('Your page is live.'))
        else:
            tk.h.flash_success(tk._(
                'Your page was sent for review. It will go live once a UNESCO '
                'administrator approves it.'))
        return tk.redirect_to('csunesco.project_landing', slug=project['slug'])

    # "Preview draft" saves first, so the preview always shows what the manager
    # is looking at. As a plain link it discarded every unsaved edit AND showed
    # the previously saved draft -- the one thing a manager does constantly was
    # the one thing that lost their work.
    _stash_drops(project['id'], drops)

    if op_name == 'preview':
        return tk.redirect_to('csunesco.project_page_preview',
                              slug=project['slug'])

    if result.get('withdrawn'):
        tk.h.flash_notice(tk._(
            'Your changes took this page out of the review queue. Publish it '
            'again when you are ready.'))
    else:
        # Every operation says what it did. Silence after a full page reload
        # reads as "nothing happened".
        tk.h.flash_success({
            'move_up': tk._('Section moved up.'),
            'move_down': tk._('Section moved down.'),
            'delete': tk._('Section deleted.'),
            'add': tk._('Section added.'),
            'convert': tk._(
                'The old description is now an editable text block, and the '
                'standard section was hidden so it is not shown twice.'),
        }.get(op_name, tk._('Draft saved.')))

    return tk.redirect_to(
        tk.url_for('csunesco.project_page_edit', slug=project['slug'],
                   **({'open': anchor} if anchor else {}))
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
    # The preview shows HIDDEN blocks too, marked -- otherwise a manager
    # cannot check what they switched off without switching it back on.
    blocks = [block for block in _initial_blocks(page)
              if block.get('type') in blocks_module.BLOCK_TYPES]
    ctx = page_render.build_context(
        _context(), project, blocks, has_region=has_region,
        can_manage=True, preview=True)

    return tk.render('csunesco/project_landing.html', extra_vars={
        'project': project,
        'blocks': blocks,
        'ctx': ctx,
        'is_draft_preview': True,
    })
