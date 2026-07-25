# encoding: utf-8
"""Data-source actions: connect / approve / reject / list / show.

A *data source* links an approved CS project to a PUBLIC form in the CS Toolbox
app (ofform) whose observations should be published on IHP-WINS. The moderation
contract is stricter than content: EVERY new source starts ``pending`` -- even
when created by a sysadmin or pushed by the app's service token -- because
approval is what creates a real CKAN dataset on the portal.

On approval (sysadmin or the project's initiative admin)
``package_sync.ensure_dataset`` creates/refreshes the CKAN package whose
resources point at this plugin's live proxy routes
(``/citizen-science/data/<id>.csv`` / ``.geojson``). If package creation fails
the row STAYS pending so the reviewer can retry after fixing configuration.
"""
import datetime
import json
import logging

import ckan.plugins.toolkit as tk
import ckan.model as model

from ckanext.csunesco import db
from ckanext.csunesco.logic import auth
from ckanext.csunesco.logic import package_sync
from ckanext.csunesco.logic.sanitize import sanitize_html
from ckanext.csunesco.logic.action import current_user_id

log = logging.getLogger(__name__)

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 100

DATA_SOURCES = {'ckan', 'app'}


def _utcnow():
    return datetime.datetime.utcnow()


def _positive_int(value, default, maximum=None):
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    if result < 0:
        return default
    if maximum is not None and result > maximum:
        return maximum
    return result


def _resolve_project(data_dict):
    key = (data_dict.get('project_id') or data_dict.get('project')
           or data_dict.get('project_slug'))
    return db.get_project(key)


def _can_manage_project(context, project_id):
    return (auth._is_sysadmin(context)
            or auth._is_project_admin(context, project_id))


def _required_form_id(data_dict):
    try:
        form_id = int(data_dict.get('form_id'))
    except (TypeError, ValueError):
        raise tk.ValidationError({'form_id': [tk._(
            'A numeric CS Toolbox form id is required')]})
    if form_id <= 0:
        raise tk.ValidationError({'form_id': [tk._(
            'A numeric CS Toolbox form id is required')]})
    return form_id


def csunesco_data_source_create(context, data_dict):
    """Request publication of an ofform form's data for an APPROVED project.

    Idempotent on ``(project, form_id)``: a rejected row is re-queued as
    pending (fresh title/description, reason cleared); a pending/approved row
    is returned unchanged with ``already_requested: True``.
    """
    if not context.get('user'):
        raise tk.NotAuthorized(tk._('You must be logged in to connect data'))
    tk.check_access('csunesco_data_source_create', context, data_dict)

    data_dict = data_dict or {}
    project = _resolve_project(data_dict)
    if project is None:
        raise tk.ValidationError({'project_id': [tk._('Project not found')]})
    if project.status != 'approved':
        raise tk.ValidationError({'project_id': [tk._(
            'Data can only be connected to an approved project')]})
    if not _can_manage_project(context, project.id):
        raise tk.NotAuthorized(tk._(
            'Only the project admin or a sysadmin can connect data'))

    form_id = _required_form_id(data_dict)
    title = (data_dict.get('title') or '').strip()
    if not title:
        raise tk.ValidationError({'title': [tk._('Missing value')]})
    description = sanitize_html((data_dict.get('description') or '').strip())
    source = (data_dict.get('source') or 'ckan').strip().lower()
    if source not in DATA_SOURCES:
        raise tk.ValidationError({'source': [tk._(
            'Source must be one of: %s') % ', '.join(sorted(DATA_SOURCES))]})
    # Suggested CKAN organization for the dataset (the app keeps its orgs
    # synchronized with the portal). NOT validated here -- it is requester
    # data, honored ONLY on a sysadmin approval (who sees and may change it);
    # non-sysadmin approvals ignore it (see csunesco_data_source_approve).
    owner_org = (data_dict.get('owner_org') or '').strip() or None

    now = _utcnow()
    existing = db.get_data_source_by_form(project.id, form_id)
    if existing is not None:
        if existing.status == 'rejected':
            # Re-request after rejection: back through review, fresh details.
            existing.status = 'pending'
            existing.title = title
            existing.description = description
            existing.source = source
            existing.rejection_reason = None
            existing.reviewed_by = None
            existing.reviewed_at = None
            extras = db._load_json(existing.extras, {})
            if not isinstance(extras, dict):
                extras = {}
            if owner_org:
                extras['owner_org'] = owner_org
            else:
                extras.pop('owner_org', None)
            existing.extras = json.dumps(extras)
            existing.modified = now
            model.Session.commit()
            return db.data_source_dictize(existing)
        result = db.data_source_dictize(existing)
        result['already_requested'] = True
        return result

    data_source = db.CsDataSource()
    data_source.project_id = project.id
    data_source.form_id = form_id
    data_source.title = title
    data_source.description = description
    # ALWAYS pending: approval is what publishes a dataset on the portal, so
    # not even a sysadmin author skips review here.
    data_source.status = 'pending'
    data_source.source = source
    data_source.created_by = current_user_id(context)
    if owner_org:
        data_source.extras = json.dumps({'owner_org': owner_org})
    data_source.created = now
    data_source.modified = now
    model.Session.add(data_source)
    model.Session.commit()
    return db.data_source_dictize(data_source)


def csunesco_data_source_approve(context, data_dict):
    """Approve a pending data source (sysadmin or initiative admin): creates
    the CKAN dataset.

    Optional ``owner_org`` overrides which organization owns the dataset
    (default: the app-suggested org when it exists on the portal, else the
    configured fallback). The org override is a SYSADMIN-only lever — an
    initiative admin's approval always uses the suggested/default resolution.
    The dataset is created BEFORE the status flips; if creation fails the row
    stays pending and a generic error is raised (details go to the log only).
    """
    tk.check_access('csunesco_data_source_approve', context, data_dict)
    data_dict = data_dict or {}
    data_source = db.get_data_source(data_dict.get('id'))
    if data_source is None:
        raise tk.ObjectNotFound(tk._('Data source not found'))
    if data_source.status != 'pending':
        raise tk.ValidationError({'status': [tk._(
            'Only pending data sources can be approved (current status: %s)'
        ) % data_source.status]})
    project = db.get_project(data_source.project_id)
    if project is None:
        raise tk.ValidationError({'project_id': [tk._('Project not found')]})
    is_sysadmin = auth._is_sysadmin(context)
    override_org = (data_dict.get('owner_org') or '').strip() or None
    if override_org and not is_sysadmin:
        override_org = None

    # An initiative admin passed the csunesco auth above but holds no CKAN org
    # rights, so the dataset side effect runs with an elevated context: the
    # moderated approval itself IS the authorization for package creation.
    # BOTH org levers stay sysadmin-only: the explicit override above AND the
    # requester-planted suggestion (honor_suggestion) — otherwise an ADM could
    # publish into an arbitrary org by seeding extras.owner_org and
    # self-approving. Non-sysadmin approvals land on the project/default org.
    sync_context = dict(context)
    if not is_sysadmin:
        sync_context['ignore_auth'] = True

    try:
        sync = package_sync.ensure_dataset(
            sync_context, project, data_source, override_org=override_org,
            honor_suggestion=is_sysadmin)
    except tk.ValidationError:
        raise
    except Exception:
        log.warning('csunesco: dataset creation failed for data source %s',
                    data_source.id, exc_info=True)
        raise tk.ValidationError({'package': [tk._(
            'The dataset could not be created on the portal. '
            'The request remains pending.')]})

    now = _utcnow()
    data_source.status = 'approved'
    data_source.reviewed_by = current_user_id(context)
    data_source.reviewed_at = now
    data_source.rejection_reason = None
    data_source.ckan_package_id = sync['package_id']
    extras = db._load_json(data_source.extras, {})
    if not isinstance(extras, dict):
        extras = {}
    extras['resource_ids'] = sync['resource_ids']
    # Record the org that actually took the dataset so re-approvals and the
    # admin UI show the real owner (not just the original suggestion).
    extras['owner_org'] = sync['owner_org']
    data_source.extras = json.dumps(extras)
    data_source.modified = now
    model.Session.commit()
    result = db.data_source_dictize(data_source)
    # Newly approved data should reflect in the At-a-Glance counters right
    # away (the probe already warmed the cache). Never fails the approval.
    try:
        refresh_project_stats(data_source.project_id)
    except Exception:
        log.warning('csunesco: stats refresh after approval failed')
    return result


def csunesco_data_source_reject(context, data_dict):
    """Reject a pending data source (sysadmin), with a sanitized reason."""
    tk.check_access('csunesco_data_source_reject', context, data_dict)
    data_dict = data_dict or {}
    data_source = db.get_data_source(data_dict.get('id'))
    if data_source is None:
        raise tk.ObjectNotFound(tk._('Data source not found'))
    if data_source.status != 'pending':
        raise tk.ValidationError({'status': [tk._(
            'Only pending data sources can be rejected (current status: %s)'
        ) % data_source.status]})

    now = _utcnow()
    data_source.status = 'rejected'
    data_source.reviewed_by = current_user_id(context)
    data_source.reviewed_at = now
    data_source.rejection_reason = sanitize_html(
        (data_dict.get('reason') or '').strip()) or None
    data_source.modified = now
    model.Session.commit()
    return db.data_source_dictize(data_source)


def refresh_project_stats(project_id):
    """Recompute observations/sites for a project from its APPROVED sources.

    Fetches each connected form's public dashboard data (TTL-cached), sums the
    observation totals and unions the distinct site coordinates across
    sources. Fail-soft by design: an unreachable source contributes nothing,
    and when EVERY upstream is down the last stored values are kept (never
    zeroed by an outage). Commits on success; returns the stored dict or
    ``None`` when nothing was refreshed.
    """
    from ckanext.csunesco.logic import ofform
    _total, sources = db.list_data_sources(
        project_id=project_id, status='approved', limit=100)
    if not sources:
        return None
    observations = 0
    sites = set()
    fetched = 0
    for source in sources:
        try:
            data = ofform.fetch_dashboard_data(source.form_id)
        except ofform.OfformError:
            continue
        observations += ofform.observation_stats(data)['observations']
        sites |= ofform.observation_site_keys(data)
        fetched += 1
    if not fetched:
        return None
    # The per-project people counter tracks ACTIVE members (the join counter
    # drifts: project approval seeds the creator without incrementing it).
    members = db.count_active_members(project_id)
    db.stats_set(project_id, observations=observations,
                 sites_monitored=len(sites), citizen_scientists=members)
    model.Session.commit()
    return {'observations': observations, 'sites_monitored': len(sites),
            'citizen_scientists': members}


def _can_view_unapproved(context, data_source):
    if auth._is_sysadmin(context):
        return True
    user_id = current_user_id(context)
    if not user_id:
        return False
    if data_source.created_by == user_id:
        return True
    return auth._is_project_admin(context, data_source.project_id)


@tk.side_effect_free
def csunesco_data_source_list(context, data_dict):
    """List data sources for a project (public callers see approved only)."""
    tk.check_access('csunesco_data_source_list', context, data_dict)
    db.ensure_mappers()
    data_dict = data_dict or {}

    project = _resolve_project(data_dict) if (
        data_dict.get('project_id') or data_dict.get('project')
        or data_dict.get('project_slug')) else None
    project_id = project.id if project is not None else None

    privileged = auth._is_sysadmin(context) or (
        project_id and auth._is_project_admin(context, project_id))
    if privileged:
        status = data_dict.get('status')      # None -> all statuses
    else:
        status = 'approved'

    limit = _positive_int(data_dict.get('limit'),
                          default=DEFAULT_LIST_LIMIT, maximum=MAX_LIST_LIMIT)
    offset = _positive_int(data_dict.get('offset'), default=0)
    total, rows = db.list_data_sources(
        project_id=project_id, status=status, limit=limit, offset=offset)
    return {
        'count': total,
        'results': [db.data_source_dictize(row) for row in rows],
        'limit': limit,
        'offset': offset,
    }


@tk.side_effect_free
def csunesco_data_source_show(context, data_dict):
    """Show one data source (approved is public; else creator/admin/sysadmin)."""
    tk.check_access('csunesco_data_source_show', context, data_dict)
    data_dict = data_dict or {}
    data_source = db.get_data_source(data_dict.get('id'))
    if data_source is None:
        raise tk.ObjectNotFound(tk._('Data source not found'))
    if (data_source.status != 'approved'
            and not _can_view_unapproved(context, data_source)):
        raise tk.NotAuthorized(tk._('Not authorized to view this data source'))
    return db.data_source_dictize(data_source)


# ---------------------------------------------------------------------------
# Chart feeds: aggregated series + the field list behind the editor's picker
# ---------------------------------------------------------------------------
#
# Both reuse ofform.fetch_dashboard_data and therefore its TTL cache, and both
# aggregate SERVER-side. That is the whole point: the raw payload for a real
# form is ~1.6 MB (1605 rows), which must never be shipped to a browser just to
# draw a line. What goes out is a few KB of dense arrays.
#
# Neither is hung off csunesco_data_source_show: the CSV/GeoJSON proxy calls
# that action on EVERY request, and adding an upstream fetch to it would make
# every download pay for a feature it does not use.

def _approved_source_or_404(data_dict):
    """Resolve an APPROVED data source, honouring an optional project pin.

    Approval is checked here rather than trusted from the caller: a source can
    be rejected after a block was saved pointing at it, and a block's stored id
    may have been copied from another project. Same rule the CSV/GeoJSON proxy
    applies in ``views_data._approved_source``.
    """
    data_source = db.get_data_source((data_dict or {}).get('id'))
    if data_source is None or data_source.status != 'approved':
        raise tk.ObjectNotFound(tk._('Data source not found'))
    project_key = (data_dict or {}).get('project_id')
    if project_key:
        project = db.get_project(project_key)
        if project is None or project.id != data_source.project_id:
            raise tk.ObjectNotFound(tk._('Data source not found'))
    return data_source


@tk.side_effect_free
def csunesco_data_source_fields(context, data_dict):
    """The chartable fields of a data source's form (public, TTL-cached).

    Feeds the page editor's field picker: which columns hold numbers, which are
    categorical and how the observations are spread in time.
    """
    tk.check_access('csunesco_data_source_fields', context, data_dict)
    data_source = _approved_source_or_404(data_dict)

    from ckanext.csunesco.logic import aggregate, ofform
    payload = ofform.fetch_dashboard_data(data_source.form_id)
    rows = payload.get('rows') or []
    schema = payload.get('schema') or {}
    site_field = aggregate.detect_site_field(schema, rows)
    first, last = aggregate.date_span(rows)

    return {
        'data_source_id': data_source.id,
        'form_id': data_source.form_id,
        'title': data_source.title,
        'total': payload.get('total', len(rows)),
        'truncated': bool(payload.get('truncated')),
        'first_date': first.isoformat() if first else None,
        'last_date': last.isoformat() if last else None,
        'site_field': site_field,
        # The site column is excluded from `categorical` (its cardinality is
        # well past the facet ceiling), so its human label would otherwise have
        # nowhere to come from and every caller would fall back to the raw
        # column name.
        'site_label': (aggregate.field_label(schema, site_field)
                       if site_field else None),
        'numeric': aggregate.numeric_fields_with_data(schema, rows),
        'categorical': aggregate.categorical_field_options(
            schema, rows, site_field),
    }


@tk.side_effect_free
def csunesco_data_source_series(context, data_dict):
    """Aggregated, display-ready series for one chart block (public).

    Returns dense arrays aligned to ``labels`` -- ``points[i]`` is ``None``
    where a period has no observation, so a gap stays a visible hole instead of
    a straight line drawn across it. Labels are sortable, locale-free period
    keys, which is why the browser needs no date adapter.
    """
    tk.check_access('csunesco_data_source_series', context, data_dict)
    data_dict = data_dict or {}
    data_source = _approved_source_or_404(data_dict)

    from ckanext.csunesco.logic import aggregate, ofform
    payload = ofform.fetch_dashboard_data(data_source.form_id)
    rows = payload.get('rows') or []
    schema = payload.get('schema') or {}
    total_rows = payload.get('total', len(rows))

    mode = data_dict.get('mode')
    if mode not in ('numeric', 'category', 'count'):
        mode = 'count'
    field = (data_dict.get('field') or '').strip()
    agg = data_dict.get('agg')
    if agg not in aggregate.AGGREGATIONS:
        agg = 'mean'

    # Time window: an explicit start/end wins over the preset.
    start = aggregate.parse_iso_day(data_dict.get('start'))
    end = aggregate.parse_iso_day(data_dict.get('end'))
    if start is None and end is None:
        start = aggregate.preset_start(data_dict.get('range'),
                                       datetime.datetime.utcnow().date())
    rows = aggregate.filter_rows_by_date(rows, start, end)

    result = {
        'data_source_id': data_source.id,
        'form_id': data_source.form_id,
        'mode': mode,
        'total_rows': total_rows,
        'truncated': bool(payload.get('truncated')),
    }

    if mode == 'category':
        if not field:
            raise tk.ValidationError({'field': [tk._('Missing value')]})
        out = aggregate.aggregate_categories(
            rows, field, top_n=_bounded(data_dict.get('max_categories'),
                                        aggregate.MAX_CATEGORIES, 2, 24))
        result.update({
            'field': field,
            'field_label': aggregate.field_label(schema, field),
            'labels': out['labels'],
            'series': aggregate.round_series(out['series']),
            'used_rows': out['used_rows'],
        })
        return result

    # Both remaining modes are time series, so they share the bucketing.
    first, last = aggregate.date_span(rows)
    granularity = data_dict.get('bucket')
    if granularity not in ('day', 'week', 'month'):
        granularity = aggregate.choose_bucket(first, last)
    elif aggregate.estimate_labels(first, last, granularity) \
            > aggregate.MAX_LABELS:
        # An author-chosen granularity must still respect the axis ceiling.
        granularity = aggregate.choose_bucket(first, last)
    labels = aggregate.bucket_labels(first, last, granularity)

    if mode == 'numeric':
        if not field:
            raise tk.ValidationError({'field': [tk._('Missing value')]})
        group_by = data_dict.get('group_by')
        if group_by == 'auto' or group_by is None:
            site_field = aggregate.detect_site_field(schema, rows)
        else:
            site_field = group_by or None
        out = aggregate.aggregate_numeric(
            rows, field, granularity, labels, site_field=site_field, agg=agg,
            max_series=_bounded(data_dict.get('max_series'),
                                aggregate.MAX_SERIES, 1, aggregate.MAX_SERIES))
        result.update({
            'field': field,
            'field_label': aggregate.field_label(schema, field),
            'agg': agg,
            'group_by': site_field or '',
            'used_rows': out['used_rows'],
        })
        # Only sent when the data actually crosses a Tukey fence: a lone bad
        # reading must not flatten the real signal, but clean data is left to
        # auto-scale (the keys are simply absent then).
        clamp = aggregate.robust_range(out['values'])
        if 'min' in clamp:
            result['y_min'] = clamp['min']
        if 'max' in clamp:
            result['y_max'] = clamp['max']
        series = out['series']
    else:
        out = aggregate.aggregate_counts(rows, granularity, labels,
                                         series_name='observations')
        result.update({'field': '', 'field_label': tk._('Observations'),
                       'used_rows': out['used_rows']})
        series = out['series']

    result.update({
        'bucket': granularity,
        'labels': labels,
        'series': aggregate.round_series(series),
        'first_date': first.isoformat() if first else None,
        'last_date': last.isoformat() if last else None,
    })
    return result


def _bounded(value, default, minimum, maximum):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def get_actions():
    return {
        'csunesco_data_source_create': csunesco_data_source_create,
        'csunesco_data_source_approve': csunesco_data_source_approve,
        'csunesco_data_source_reject': csunesco_data_source_reject,
        'csunesco_data_source_list': csunesco_data_source_list,
        'csunesco_data_source_show': csunesco_data_source_show,
        'csunesco_data_source_fields': csunesco_data_source_fields,
        'csunesco_data_source_series': csunesco_data_source_series,
    }
