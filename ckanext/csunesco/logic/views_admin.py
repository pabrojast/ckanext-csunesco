# encoding: utf-8
"""HTTP orchestration for the CS admin approval panel.

Increment 5, Part A. Thin views (same contract as ``logic/views.py``): build a
context, call ONE ``csunesco_*`` action (never the ORM), and either render the
dashboard or Post/Redirect/Get after a moderation decision. Every POST handler
delegates to an existing domain action and flashes a GENERIC message on failure
so internals never leak.

The active tab is preserved across the PRG via a URL fragment (``#tab-...``) so a
reviewer stays on the list they were working through.
"""
import logging
import time

from flask import request, redirect

import ckan.plugins.toolkit as tk
import ckan.model as model

from ckanext.csunesco.logic.sanitize import sanitize_html

log = logging.getLogger(__name__)

GENERIC_ERROR = 'Something went wrong. Please try again.'

# Server-side page size for each panel list.
PANEL_PAGE_SIZE = 20


def _context():
    return {'model': model, 'session': model.Session, 'user': tk.g.user}


def _positive_int(value, default):
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    return result if result >= 0 else default


def _is_sysadmin():
    user_obj = getattr(tk.g, 'userobj', None)
    return bool(user_obj and getattr(user_obj, 'sysadmin', False))


def _admin_initiatives():
    """Initiative-group names the acting user ADMs ([] if none; fail-soft)."""
    user_obj = getattr(tk.g, 'userobj', None)
    if not user_obj or getattr(user_obj, 'is_anonymous', False):
        return []
    try:
        from ckanext.csunesco import db
        return db.admin_initiative_groups(user_obj.id)
    except Exception:
        log.warning('csunesco: initiative-admin lookup failed')
        return []


def _not_authorized_response():
    if not tk.g.user:
        return tk.redirect_to('user.login')
    return tk.abort(403, tk._('You are not authorized to view this page'))


def _redirect_dashboard(tab):
    """PRG back to the dashboard, re-opening ``tab`` via a URL fragment."""
    url = tk.h.url_for('csunesco.admin_dashboard')
    return redirect('{0}#tab-{1}'.format(url, tab))


# Health probes on the data tab: a dark form costs PROBE_TIMEOUT of wall clock,
# and the incident this exists for is EIGHT of them at once. The real ceiling is
# the wall-clock budget, and it is deliberately the SELF-HEALING one: rows past
# it carry no ``probe`` key and render no chip, but ofform's probe cache
# (PROBE_CACHE_TTL) makes the already-checked rows free on the next render, so
# the budget reaches further each time and the strip fills in within a refresh
# or two. The row cap is only a backstop against an absurd list; it is kept at
# the panel's page size ON PURPOSE -- lowering it below PANEL_PAGE_SIZE would
# permanently hide the chip on the tail of a full pending page, and unlike the
# budget that truncation never heals.
HEALTH_PROBE_MAX = PANEL_PAGE_SIZE
HEALTH_PROBE_BUDGET = 6.0


def _probe_rows(rows, limit=HEALTH_PROBE_MAX, budget=HEALTH_PROBE_BUDGET):
    """Decorate ``rows`` with ``probe``/``app_url`` under a wall-clock budget.

    Fail-soft per row: one bad form id must never cost the reviewer their
    queues. Returns the rows actually probed.
    """
    try:
        from ckanext.csunesco.logic import ofform
    except Exception:
        log.warning('csunesco: data-source probes unavailable')
        return []
    deadline = time.monotonic() + budget
    probed = []
    for row in rows[:limit]:
        if time.monotonic() >= deadline:
            break
        try:
            row['probe'] = ofform.probe_form(row.get('form_id'))
            row['app_url'] = ofform.public_form_url(row.get('form_id'))
        except Exception:
            log.warning('csunesco: data-source probe failed for form %s',
                        row.get('form_id'))
        else:
            probed.append(row)
    return probed


def _health_rank(row):
    """Sort key: unreachable first, then answering, then not probed."""
    probe = row.get('probe')
    if probe is None:
        return 2
    return 0 if not probe.get('ok') else 1


# ---------------------------------------------------------------------------
# Dashboard (GET)
# ---------------------------------------------------------------------------

def admin_dashboard():
    """Render the approval panel with the acting user's pending work."""
    if not tk.g.user:
        return _not_authorized_response()

    offset = _positive_int(request.args.get('offset'), 0)
    context = _context()
    try:
        data = tk.get_action('csunesco_admin_pending_list')(
            context, {'limit': PANEL_PAGE_SIZE, 'offset': offset})
    except tk.NotAuthorized:
        return _not_authorized_response()
    except Exception:
        log.warning('csunesco: admin pending list unavailable')
        data = {
            'project_requests': [], 'join_requests': [],
            'content_requests': [], 'data_requests': [], 'page_requests': [],
            'counts': {'project_requests': 0, 'join_requests': 0,
                       'content_requests': 0, 'data_requests': 0,
                       'page_requests': 0, 'total': 0},
        }

    is_sysadmin = _is_sysadmin()
    # Initiative admins (ADM) review projects + data sources of their
    # initiatives, so those tabs open for them too (rows already scoped by the
    # action). The group list also gates the per-row content buttons: a user
    # who is BOTH an ADM and a plain PM elsewhere must not see approve buttons
    # on the other initiative's content (auth would 403 the POST anyway).
    admin_initiatives = _admin_initiatives()
    can_review_initiative = is_sysadmin or bool(admin_initiatives)

    # Organization picker for the data tab (sysadmin only, fail-soft): the
    # approve form preselects the app-suggested org when it exists on the
    # portal, else the configured default -- and the reviewer can change it.
    # Initiative admins get no picker: their approvals always use the
    # suggested/default org resolution (the override is a sysadmin lever).
    organizations = []
    if data.get('data_requests'):
        if is_sysadmin:
            try:
                organizations = tk.get_action('organization_list')(context, {})
            except Exception:
                log.warning('csunesco: organization list unavailable')
        # Review context per pending source: is the form live/public, how many
        # observations, date range -- plus an "open in the app" link. Probes
        # are short-timeout + TTL-cached; any failure degrades to a warning
        # chip, never an error page. Budgeted: this list was unbounded, so a
        # dead upstream made the panel wait on every single pending row.
        _probe_rows(data['data_requests'])

    # Connected sources are probed too. Approving one is not the end of the
    # story: a form can go private or unpublished in the app long after review,
    # and nothing on the portal said so -- the charts, maps and downloads on the
    # public project page just started failing. Unreachable first: the
    # actionable rows belong at the top of a list nobody scrolls.
    data_connected = []
    if can_review_initiative:
        data_connected = data.get('data_connected') or []
        _probe_rows(data_connected)
        data_connected.sort(key=_health_rank)   # stable: keeps created-desc

    # The panel doubles as the manager's home, so it also answers "where are my
    # projects?" -- the one question the rest of the UI could not. Fail-soft:
    # losing this band must never cost a reviewer their queues.
    try:
        my_projects = tk.get_action('csunesco_my_projects')(
            context, {}).get('projects') or []
    except Exception:
        log.warning('csunesco: administered project list unavailable')
        my_projects = []
    # The question a manager actually brings here is "is my page live?".
    # One cheap read per administered project (the list is personal and
    # short); any failure just leaves the chip off that card.
    for project in my_projects:
        if project.get('status') != 'approved':
            continue
        try:
            page = tk.get_action('csunesco_project_page_show')(
                context, {'project_id': project['id']})
        except Exception:
            continue
        if page.get('status') == 'pending':
            project['page_state'] = 'in_review'
        elif page.get('published_blocks') is not None:
            project['page_state'] = 'published'
        else:
            project['page_state'] = 'draft'


    return tk.render('csunesco/cs-admin-dashboard.html', extra_vars={
        'my_projects': my_projects,
        'admin_initiatives': admin_initiatives,
        'is_sysadmin': is_sysadmin,
        'can_review_projects': can_review_initiative,
        'can_review_data': can_review_initiative,
        'can_review_pages': can_review_initiative,
        'project_requests': data.get('project_requests', []),
        'join_requests': data.get('join_requests', []),
        'content_requests': data.get('content_requests', []),
        'content_moderated': data.get('content_moderated', []),
        'data_requests': data.get('data_requests', []),
        'data_connected': data_connected,
        'page_requests': data.get('page_requests', []),
        'counts': data.get('counts', {}),
        'organizations': organizations,
        'default_owner_org': (
            tk.config.get('ckanext.csunesco.dataset_owner_org') or '').strip(),
    })


# ---------------------------------------------------------------------------
# Moderation POST handlers (each delegates to a domain action)
# ---------------------------------------------------------------------------

def _validation_messages(error):
    """Flatten a ``ValidationError``'s ``error_dict`` into readable sentences.

    The actions phrase these for the reviewer, so they are worth surfacing
    verbatim rather than collapsing into "something went wrong".
    """
    messages = []
    for value in (getattr(error, 'error_dict', None) or {}).values():
        if isinstance(value, str):
            messages.append(value)
        else:
            messages.extend(str(item) for item in value)
    return messages


def _decide(action_name, data_dict, tab, ok_message, gone_message=None):
    """Run a moderation action, flash the outcome and PRG back to ``tab``.

    ``gone_message`` turns an ``ObjectNotFound`` into a flash + redirect instead
    of a 404. Aborting is right when a URL is wrong, but wrong when the row
    simply moved on while the panel was open -- it throws the reviewer off the
    dashboard and loses every rejection reason they had typed into other rows.
    """
    context = _context()
    try:
        tk.get_action(action_name)(context, data_dict)
    except tk.NotAuthorized:
        return _not_authorized_response()
    except tk.ObjectNotFound:
        if gone_message is None:
            return tk.abort(404, tk._('Not found'))
        tk.h.flash_notice(gone_message)
        return _redirect_dashboard(tab)
    except tk.ValidationError as error:
        # The actions write careful, actionable messages ("this page changed
        # after you opened the review"). Throwing them away and flashing a
        # generic failure just makes the reviewer press the button again.
        tk.h.flash_error(' '.join(_validation_messages(error))
                         or tk._('That item could not be updated.'))
        return _redirect_dashboard(tab)
    except Exception:
        log.warning('csunesco: moderation action %s failed', action_name)
        tk.h.flash_error(tk._(GENERIC_ERROR))
        return _redirect_dashboard(tab)
    tk.h.flash_success(ok_message)
    return _redirect_dashboard(tab)


def project_approve(id):
    return _decide('csunesco_project_approve', {'id': id}, 'projects',
                   tk._('Project approved.'))


def project_reject(id):
    reason = sanitize_html((request.form.get('reason') or '').strip())
    return _decide('csunesco_project_reject', {'id': id, 'reason': reason},
                   'projects', tk._('Project rejected.'))


def join_approve(project_id, user_id):
    return _decide('csunesco_join_approve',
                   {'project_id': project_id, 'user_id': user_id},
                   'joins', tk._('Join request approved.'))


def join_reject(project_id, user_id):
    return _decide('csunesco_join_reject',
                   {'project_id': project_id, 'user_id': user_id},
                   'joins', tk._('Join request rejected.'))


def content_approve(id):
    return _decide('csunesco_content_approve', {'id': id}, 'content',
                   tk._('Content approved.'))


def content_reject(id):
    reason = sanitize_html((request.form.get('reason') or '').strip())
    return _decide('csunesco_content_reject', {'id': id, 'reason': reason},
                   'content', tk._('Content rejected.'))


def content_withdraw(id):
    reason = sanitize_html((request.form.get('reason') or '').strip())
    return _decide('csunesco_content_withdraw', {'id': id, 'reason': reason},
                   'content', tk._('Content withdrawn from the portal.'))


def content_delete(id):
    return _decide('csunesco_content_delete', {'id': id}, 'content',
                   tk._('Content permanently deleted.'),
                   gone_message=tk._('That content was already gone.'))


# Tope defensivo de filas por request de bulk-approve (el panel pagina de a 20).
# Distinct causes shown in the failure flash. Rows are deduplicated by reason,
# and in practice a batch fails for one or two reasons (a missing owner org, a
# portal-schema field), so this is generous rather than restrictive.
MAX_BULK_REASONS = 3


def _bulk_approve(action_name, ids):
    """Approve each id best-effort. Returns ``(approved, reasons)``.

    Both halves of this exist because "3 item(s) could not be approved" on its
    own is a dead end: the reviewer cannot act on it and no one can reconstruct
    it afterwards. So

    * every failure is **logged with its row id** and cause, which is how a
      sysadmin finds the row when the reviewer only reports a count, and
    * the distinct causes come back **deduplicated** for the flash -- the
      single-row path already surfaces the actions' own wording (see
      :func:`_decide`), and the usual causes here are fixable configuration.

    Per-row failures never abort the batch: authorization is re-checked for
    every row, and one row a reviewer may not touch must not cost them the
    other ninety-nine.
    """
    context = _context()
    approved = 0
    reasons = []

    def remember(found):
        for reason in found:
            if reason not in reasons:
                reasons.append(reason)

    for row_id in ids:
        try:
            tk.get_action(action_name)(dict(context), {'id': row_id})
        except tk.ValidationError as error:
            found = _validation_messages(error)
            log.warning('csunesco: bulk %s rejected %s: %s', action_name,
                        row_id, '; '.join(found) or 'no message')
            remember(found or [tk._(GENERIC_ERROR)])
        except tk.NotAuthorized:
            log.warning('csunesco: bulk %s not authorized for %s',
                        action_name, row_id)
            remember([tk._('You are not authorized to approve some of these.')])
        except tk.ObjectNotFound:
            log.warning('csunesco: bulk %s: %s no longer exists',
                        action_name, row_id)
            remember([tk._('Some of them were no longer there.')])
        except Exception as error:
            # Type only, never the message: an unexpected error can carry
            # anything, including upstream detail that has no business on a
            # flash. The type is what makes it findable in the log.
            log.warning('csunesco: bulk %s failed on %s (%s)', action_name,
                        row_id, type(error).__name__)
            remember([tk._(GENERIC_ERROR)])
        else:
            approved += 1
    return approved, reasons


def _bulk_error_message(summary, reasons):
    """``summary`` followed by the distinct causes, capped."""
    if not reasons:
        return summary
    shown = ' '.join(reasons[:MAX_BULK_REASONS])
    if len(reasons) > MAX_BULK_REASONS:
        shown = '%s %s' % (shown, tk._('There were other problems too.'))
    return '%s %s' % (summary, shown)


BULK_APPROVE_MAX = 100
# Data sources: cada aprobación crea un dataset CKAN (lento, puede fallar por
# fila), así que el lote es más chico.
BULK_DATA_APPROVE_MAX = 20


def content_bulk_approve():
    """Approve a batch of content rows (checkbox selection, P2).

    Best-effort per row: each id goes through ``csunesco_content_approve`` (its
    auth re-checks sysadmin/initiative-admin per row), failures never abort the
    rest, and the flash summarizes approved/failed counts.
    """
    if not tk.g.user:
        return _not_authorized_response()
    ids = [i for i in request.form.getlist('content_ids') if i][:BULK_APPROVE_MAX]
    if not ids:
        tk.h.flash_error(tk._('Select at least one content item.'))
        return _redirect_dashboard('content')
    approved, reasons = _bulk_approve('csunesco_content_approve', ids)
    if approved:
        tk.h.flash_success(
            tk._('Approved %(n)s content item(s).') % {'n': approved})
    failed = len(ids) - approved
    if failed:
        tk.h.flash_error(_bulk_error_message(
            tk._('%(n)s item(s) could not be approved.') % {'n': failed},
            reasons))
    return _redirect_dashboard('content')


def data_source_bulk_approve():
    """Approve a batch of data sources (checkbox selection, P2b).

    Best-effort per row WITHOUT an org override: a sysadmin approval honors
    the app-suggested org (as the single-row form preselects it) and an
    initiative-admin approval falls to the project/default org. Rows whose
    org cannot be resolved fail individually (ValidationError) and are
    reported in the summary — the rest of the batch still lands.
    """
    if not tk.g.user:
        return _not_authorized_response()
    ids = [i for i in request.form.getlist('data_ids')
           if i][:BULK_DATA_APPROVE_MAX]
    if not ids:
        tk.h.flash_error(tk._('Select at least one data source.'))
        return _redirect_dashboard('data')
    approved, reasons = _bulk_approve('csunesco_data_source_approve', ids)
    if approved:
        tk.h.flash_success(
            tk._('Approved %(n)s data source(s); their datasets are live.')
            % {'n': approved})
    failed = len(ids) - approved
    if failed:
        tk.h.flash_error(_bulk_error_message(
            tk._('%(n)s data source(s) could not be approved (they remain '
                 'pending).') % {'n': failed},
            reasons))
    return _redirect_dashboard('data')


def project_trusted_set(slug):
    """Toggle a project's trusted flag from its landing page (sysadmin-only)."""
    value = (request.form.get('trusted') or '').strip().lower() in (
        'true', '1', 'on', 'yes')
    context = _context()
    try:
        tk.get_action('csunesco_project_trusted_set')(
            context, {'id': slug, 'trusted': value})
    except tk.NotAuthorized:
        return _not_authorized_response()
    except tk.ObjectNotFound:
        return tk.abort(404, tk._('Not found'))
    except Exception:
        log.warning('csunesco: trusted toggle failed')
        tk.h.flash_error(tk._(GENERIC_ERROR))
        return redirect(tk.h.url_for('csunesco.project_landing', slug=slug))
    tk.h.flash_success(
        tk._('Project marked as trusted: its news and events now publish '
             'without review.') if value
        else tk._('Project trust removed: news and events queue for review '
                  'again.'))
    return redirect(tk.h.url_for('csunesco.project_landing', slug=slug))


def data_source_approve(id):
    data_dict = {'id': id}
    owner_org = (request.form.get('owner_org') or '').strip()
    if owner_org:
        data_dict['owner_org'] = owner_org
    return _decide('csunesco_data_source_approve', data_dict, 'data',
                   tk._('Data source approved. The dataset is now live.'))


def data_source_reject(id):
    reason = sanitize_html((request.form.get('reason') or '').strip())
    return _decide('csunesco_data_source_reject', {'id': id, 'reason': reason},
                   'data', tk._('Data source rejected.'))


def page_approve(project_id):
    """Publish a project page that is awaiting review.

    ``draft_hash`` comes back from the form: the action refuses the approval if
    the manager changed the draft after this panel was rendered, so nobody can
    publish a version they never read.
    """
    return _decide('csunesco_project_page_approve',
                   {'project_id': project_id,
                    'draft_hash': (request.form.get('draft_hash') or '').strip()},
                   'pages', tk._('Project page published.'),
                   gone_message=tk._('That page is no longer awaiting review — its author has edited it again.'))


def page_reject(project_id):
    reason = sanitize_html((request.form.get('reason') or '').strip())
    return _decide('csunesco_project_page_reject',
                   {'project_id': project_id, 'reason': reason},
                   'pages', tk._('Project page sent back to its author.'),
                   gone_message=tk._('That page is no longer awaiting review — its author has edited it again.'))
