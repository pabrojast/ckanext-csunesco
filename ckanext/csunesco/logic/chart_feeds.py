"""Public chart presentation and snapshot-bound, anonymous calculation feeds.

Selectors and history mappings remain server-side. A public page revision or an
authorized, expiring preview ticket identifies the complete stored definition;
neither the browser's identity nor submitted query settings authorize data.
"""
import copy
import hashlib
import json
import re
import time

from flask import Response, request
import ckan.plugins.toolkit as tk

from ckanext.csunesco import db


DISPLAY_FIELDS = ('version', 'title', 'caption', 'mode', 'chart', 'bucket',
                  'height', 'axes', 'presentation', 'template')
_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
_HASH = re.compile(r'^[a-f0-9]{64}$')


def revision(raw):
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def presentation(definition):
    return {key: copy.deepcopy(definition[key]) for key in DISPLAY_FIELDS if key in definition}


def _charts(items):
    for block in items or []:
        if not isinstance(block, dict):
            continue
        if block.get('type') == 'chart':
            yield block, None
        elif block.get('type') == 'builtin_data':
            for child in block.get('charts') or []:
                if isinstance(child, dict):
                    yield child, block


def public_blocks(items):
    """Project read DTO only; never rewrite stored or editable definitions."""
    result = copy.deepcopy(items)
    for chart, _ in _charts(result):
        definition = chart.get('definition')
        if isinstance(definition, dict) and definition.get('version') == 2:
            chart['definition'] = presentation(definition)
            for key in ('history_sources', 'sites', 'filters', 'field', 'group_by',
                        'agg', 'range', 'start', 'end', 'include_history'):
                chart.pop(key, None)
    return result


def _native_preview_blocks(items):
    """Freeze only chart inputs; chart previews do not need to copy media."""
    result = []
    for block in items or []:
        if block.get('type') == 'chart':
            result.append({key: copy.deepcopy(block[key]) for key in (
                'id', 'type', 'hidden', 'data_source_id', 'definition', 'history_sources') if key in block})
        elif block.get('type') == 'builtin_data' and block.get('charts'):
            parent = {key: copy.deepcopy(block[key]) for key in ('id', 'type', 'hidden', 'source_ids') if key in block}
            parent['charts'] = _native_preview_blocks(block['charts'])
            result.append(parent)
    return result


def prepare_context(context, ctx, items):
    modern = [(chart, parent) for chart, parent in _charts(items)
              if isinstance(chart.get('definition'), dict) and chart['definition'].get('version') == 2]
    ctx['chart_sources'] = {}
    if not modern or not ctx.get('project'):
        return
    ticket = ctx.get('chart_preview_ticket')
    if ctx.get('preview') and not ticket and context.get('user') and ctx.get('can_manage'):
        from ckanext.csunesco.logic import portal
        project = db.get_project(ctx['project']['id'])
        if project is not None:
            candidate = dict(blocks=_native_preview_blocks(items), project={}, structure={},
                             workplan=[], contents=[], media=[])
            saved = portal.create_preview_ticket(project, candidate,
                {'username': context['user']}, revision=1)
            ticket = ctx['chart_preview_ticket'] = saved['ticket']
    counts = {}
    for chart, _ in modern:
        counts[chart.get('id')] = counts.get(chart.get('id'), 0) + 1
    for chart, _ in modern:
        block_id = chart.get('id')
        feed = {'url': None, 'definition': presentation(chart['definition'])}
        if isinstance(block_id, str) and _ID.fullmatch(block_id) and counts[block_id] == 1:
            if ctx.get('preview') and ticket:
                feed['url'] = tk.url_for('csunesco.preview_chart_series', ticket=ticket, block_id=block_id)
            elif not ctx.get('preview') and _HASH.fullmatch(ctx.get('chart_revision') or ''):
                feed['url'] = tk.url_for('csunesco.project_chart_series', slug=ctx['project']['slug'],
                    block_id=block_id, revision=ctx['chart_revision'])
        ctx['chart_sources'][block_id] = feed


def chart_feed(block, ctx):
    definition = block.get('definition')
    if not isinstance(definition, dict) or definition.get('version') != 2:
        return None
    return (ctx.get('chart_sources') or {}).get(block.get('id')) or {
        'url': None, 'definition': presentation(definition)}


def _find_chart(items, block_id, preview=False):
    if not isinstance(block_id, str) or not _ID.fullmatch(block_id):
        raise tk.ObjectNotFound('Chart unavailable')
    matches = [(chart, parent) for chart, parent in _charts(items) if chart.get('id') == block_id]
    if len(matches) != 1:
        raise tk.ObjectNotFound('Chart unavailable')
    chart, parent = matches[0]
    if ((chart.get('hidden') and (parent is not None or not preview))
            or parent and (parent.get('hidden') and not preview
                           or 'source_ids' in parent and chart.get('data_source_id') not in parent['source_ids'])):
        raise tk.ObjectNotFound('Chart unavailable')
    if not isinstance(chart.get('definition'), dict) or chart['definition'].get('version') != 2:
        raise tk.ObjectNotFound('Chart unavailable')
    return chart


def _anonymous():
    import ckan.model as model
    return {'model': model, 'session': model.Session, 'user': None}


def _calculate(project, chart):
    # The existing action rechecks each source's current policy and each history
    # mapping. The fixed project pin also rejects cross-project source IDs.
    definition = chart['definition']
    if definition.get('version') == 2:
        definition = {key: value for key, value in definition.items()
                      if key not in {'presentation', 'template', 'title', 'caption'}}
    return tk.get_action('csunesco_data_source_series')(_anonymous(), {
        'id': chart.get('data_source_id'), 'project_id': project.id,
        'definition': definition, 'history_sources': chart.get('history_sources') or []})


def _json(result, status=200):
    response = Response(json.dumps(result), status=status, mimetype='application/json')
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


def _serve(load):
    from ckanext.csunesco.logic import ofform
    try:
        project, chart = load()
        return _json(_calculate(project, chart))
    except (tk.ObjectNotFound, tk.NotAuthorized):
        return _json({'error': 'Chart unavailable'}, 404)
    except tk.ValidationError:
        return _json({'error': 'Chart unavailable'}, 400)
    except ofform.OfformError:
        return _json({'error': 'Data source unavailable', 'reason': 'upstream_unavailable'}, 502)
    except Exception:
        return _json({'error': 'Chart unavailable', 'reason': 'internal_error'}, 502)


def project_chart_series(slug, block_id):
    from ckanext.csunesco.logic import portal
    def load():
        project = db.get_project(slug)
        if project is None or project.status != 'approved' or portal.withdrawn(project):
            raise tk.ObjectNotFound('Chart unavailable')
        tk.get_action('csunesco_project_show')(_anonymous(), {'id': project.id})
        page = db.get_project_page(project.id)
        requested = request.args.get('revision', '')
        if (page is None or page.published_json is None or not _HASH.fullmatch(requested)
                or revision(page.published_json) != requested):
            raise tk.ObjectNotFound('Chart unavailable')
        # No caller-supplied definition, source, filter or history is read.
        return project, _find_chart(json.loads(page.published_json), block_id)
    return _serve(load)


def preview_chart_series(ticket, block_id):
    from ckanext.csunesco.logic import portal, snapshots
    def load():
        if len(ticket) > 100:
            raise tk.ObjectNotFound('Preview expired')
        saved = snapshots.read_private('preview', hashlib.sha256(ticket.encode()).hexdigest())
        if (not saved or saved.get('expires', 0) <= time.time()
                or saved.get('scope') in ('site', 'initiative')):
            raise tk.ObjectNotFound('Preview expired')
        project = db.get_project(saved.get('project_id'))
        if project is None:
            raise tk.ObjectNotFound('Preview expired')
        portal.authorize_saved_preview(saved, project)
        return project, _find_chart(saved['candidate']['blocks'], block_id, preview=True)
    return _serve(load)
