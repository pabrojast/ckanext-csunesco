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
            'content_requests': [], 'data_requests': [],
            'counts': {'project_requests': 0, 'join_requests': 0,
                       'content_requests': 0, 'data_requests': 0, 'total': 0},
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
        # chip, never an error page.
        try:
            from ckanext.csunesco.logic import ofform
            for row in data['data_requests']:
                row['probe'] = ofform.probe_form(row.get('form_id'))
                row['app_url'] = ofform.public_form_url(row.get('form_id'))
        except Exception:
            log.warning('csunesco: data-source probes unavailable')

    return tk.render('csunesco/cs-admin-dashboard.html', extra_vars={
        'is_sysadmin': is_sysadmin,
        'can_review_projects': can_review_initiative,
        'can_review_data': can_review_initiative,
        'admin_initiatives': admin_initiatives,
        'project_requests': data.get('project_requests', []),
        'join_requests': data.get('join_requests', []),
        'content_requests': data.get('content_requests', []),
        'data_requests': data.get('data_requests', []),
        'counts': data.get('counts', {}),
        'organizations': organizations,
        'default_owner_org': (
            tk.config.get('ckanext.csunesco.dataset_owner_org') or '').strip(),
    })


# ---------------------------------------------------------------------------
# Moderation POST handlers (each delegates to a domain action)
# ---------------------------------------------------------------------------

def _decide(action_name, data_dict, tab, ok_message):
    """Run a moderation action, flash the outcome and PRG back to ``tab``."""
    context = _context()
    try:
        tk.get_action(action_name)(context, data_dict)
    except tk.NotAuthorized:
        return _not_authorized_response()
    except tk.ObjectNotFound:
        return tk.abort(404, tk._('Not found'))
    except tk.ValidationError:
        tk.h.flash_error(tk._('That item could not be updated.'))
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
