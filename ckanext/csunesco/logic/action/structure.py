# encoding: utf-8
"""Phase-2 project structure mirror (spec section 7): the app -> portal push.

The CS Toolbox app is the ONLY writer of the phase-2 structure and the
workplan; the portal is a read-only mirror that renders them on the public
landing page (audience-filtered) and in the participant "My projects" view.
``csunesco_project_structure_upsert`` is therefore sysadmin-only (the app's
service token -- same trust model as ``csunesco_join_approve``), REPLACES the
whole snapshot each call (one idempotency stream per project on the app side)
and stores everything under two extras keys, ``structure`` and ``workplan``,
which ``project_dictize`` merges top-level for free.

Free text is sanitized to plain text and bounded; option values arrive
pre-validated by the app but are still length-capped here -- the portal never
trusts sizes it did not check.
"""
import datetime
import json
import logging
import re

import ckan.plugins.toolkit as tk
import ckan.model as model

from ckanext.csunesco import db

log = logging.getLogger(__name__)

# Caps mirrored from the app's own limits (cs_constants / cs_structure).
MAX_TEXT = 3000
MAX_ITEM = 200
MAX_LIST_ITEMS = 50
MAX_WORKPLAN_STEPS = 100

_TAG_RE = re.compile(r'<[^>]*>')

# The structure keys the mirror accepts, by shape.
_TEXT_FIELDS = ('aim', 'how_to_participate', 'indigenous_knowledge_notes')
_LIST_FIELDS = ('focus_areas', 'engagement_activities', 'target_groups',
                'incentives')
_CHOICE_FIELDS = ('engagement_level', 'training_level',
                  'duration_of_involvement')
_BOOL_FIELDS = ('local_govt_engagement', 'indigenous_knowledge')
_DATE_FIELDS = ('timeframe_start', 'timeframe_end')

_WORKPLAN_STATUSES = ('upcoming', 'in_progress', 'completed')
_WORKPLAN_KINDS = ('step', 'milestone')


def _clean_text(value, limit=MAX_TEXT):
    if value in (None, ''):
        return None
    text = _TAG_RE.sub('', str(value)).strip()
    return text[:limit] or None


def _clean_list(value):
    if not isinstance(value, (list, tuple)):
        return []
    cleaned = []
    for item in value[:MAX_LIST_ITEMS]:
        text = _clean_text(item, MAX_ITEM)
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def _clean_date(value):
    if value in (None, ''):
        return None
    try:
        return datetime.date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        return None


def _clean_structure(raw):
    """The accepted subset of the app's structure snapshot, sanitized."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key in _TEXT_FIELDS:
        value = _clean_text(raw.get(key))
        if value is not None:
            out[key] = value
    for key in _LIST_FIELDS:
        items = _clean_list(raw.get(key))
        if items:
            out[key] = items
    for key in _CHOICE_FIELDS:
        value = _clean_text(raw.get(key), MAX_ITEM)
        if value is not None:
            out[key] = value
    for key in _BOOL_FIELDS:
        if isinstance(raw.get(key), bool):
            out[key] = raw[key]
    for key in _DATE_FIELDS:
        value = _clean_date(raw.get(key))
        if value is not None:
            out[key] = value
    parameters = raw.get('water_parameters')
    if isinstance(parameters, dict):
        cleaned = {}
        for group, params in list(parameters.items())[:20]:
            group_name = _clean_text(group, MAX_ITEM)
            items = _clean_list(params)
            if group_name and items:
                cleaned[group_name] = items
        if cleaned:
            out['water_parameters'] = cleaned
    return out


def _clean_workplan(raw):
    """The workplan snapshot: bounded list of sanitized steps/milestones."""
    if not isinstance(raw, (list, tuple)):
        return []
    steps = []
    for item in raw[:MAX_WORKPLAN_STEPS]:
        if not isinstance(item, dict):
            continue
        title = _clean_text(item.get('title'), MAX_ITEM)
        if not title:
            continue
        step = {'title': title}
        kind = str(item.get('kind') or 'step')
        step['kind'] = kind if kind in _WORKPLAN_KINDS else 'step'
        status = str(item.get('status') or 'upcoming')
        step['status'] = (status if status in _WORKPLAN_STATUSES
                          else 'upcoming')
        description = _clean_text(item.get('description'))
        if description:
            step['description'] = description
        starts_on = _clean_date(item.get('starts_on'))
        if starts_on:
            step['starts_on'] = starts_on
        ends_on = _clean_date(item.get('ends_on'))
        if ends_on:
            step['ends_on'] = ends_on
        steps.append(step)
    return steps


def csunesco_project_structure_upsert(context, data_dict):
    """Replace a project's mirrored phase-2 structure + workplan snapshot."""
    tk.check_access('csunesco_project_structure_upsert', context, data_dict)
    data_dict = data_dict or {}
    key = (data_dict.get('project_id') or data_dict.get('id')
           or data_dict.get('project') or data_dict.get('project_slug')
           or data_dict.get('slug'))
    project = db.get_project(key)
    if project is None:
        raise tk.ObjectNotFound(tk._('Project not found'))

    extras = db._load_json(project.extras, {})
    extras = dict(extras) if isinstance(extras, dict) else {}
    # REPLACE semantics: the app pushes the whole snapshot every time, so a
    # field cleared in the app disappears here too instead of lingering.
    structure = _clean_structure(data_dict.get('structure'))
    workplan = _clean_workplan(data_dict.get('workplan'))
    if structure:
        extras['structure'] = structure
    else:
        extras.pop('structure', None)
    if workplan:
        extras['workplan'] = workplan
    else:
        extras.pop('workplan', None)
    project.extras = json.dumps(extras)
    project.modified = datetime.datetime.utcnow()
    model.Session.commit()
    return {
        'project_id': project.id,
        'slug': project.slug,
        'structure': structure,
        'workplan': workplan,
    }


def get_actions():
    return {
        'csunesco_project_structure_upsert':
            csunesco_project_structure_upsert,
    }
