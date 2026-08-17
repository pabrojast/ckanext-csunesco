# encoding: utf-8
"""Click CLI commands for ckanext-csunesco.

Registered with CKAN via IClick as the ``csunesco`` command group, e.g.::

    ckan -c /etc/ckan/default/ckan.ini csunesco init-db
    ckan -c /etc/ckan/default/ckan.ini csunesco seed-initiatives
"""
import click


@click.group()
def csunesco():
    """Citizen Science (UNESCO/IHP-WINS) management commands."""
    pass


@csunesco.command('init-db')
def init_db():
    """Create the ckanext-csunesco database tables."""
    from ckanext.csunesco import db
    db.ensure_tables()
    click.echo('ckanext-csunesco: database tables ensured.')


@csunesco.command('stats-refresh')
def stats_refresh():
    """Recompute observation counters from the connected app data (cron-able).

    Iterates every APPROVED data source, fetches its public dashboard data
    from the CS Toolbox app and stores per-project totals (observations +
    distinct monitored sites). Safe to run any time; outages keep the last
    known values instead of zeroing.
    """
    from ckanext.csunesco import db
    from ckanext.csunesco.logic.action.data import refresh_project_stats

    db.ensure_mappers()
    _total, sources = db.list_data_sources(status='approved', limit=1000)
    project_ids = sorted({s.project_id for s in sources if s.project_id})
    if not project_ids:
        click.echo('no approved data sources; nothing to refresh.')
        return
    for project_id in project_ids:
        try:
            result = refresh_project_stats(project_id)
        except Exception as exc:
            click.echo('failed:  %s (%s)' % (project_id, type(exc).__name__))
            continue
        if result is None:
            click.echo('skipped: %s (upstream unavailable)' % project_id)
        else:
            click.echo('updated: %s (observations=%s, sites=%s)' % (
                project_id, result['observations'],
                result['sites_monitored']))


@csunesco.command('backfill-stats')
def backfill_stats():
    """One-shot: derive ``member_states`` counters from declared countries.

    The per-project counter was historically never fed, so every landing page
    showed 0. New writes keep it in sync (approve/update); this backfills the
    rows that predate that. Idempotent -- safe to re-run.
    """
    from ckanext.csunesco import db
    from ckanext.csunesco.logic.action.projects import _member_states_count
    import ckan.model as model

    db.ensure_mappers()
    projects = (model.Session.query(db.CsProject)
                .filter(db.CsProject.status == 'approved').all())
    if not projects:
        click.echo('no approved projects; nothing to backfill.')
        return
    for project in projects:
        count = _member_states_count(project)
        db.stats_set(project.id, member_states=count)
        click.echo('updated: %s (member_states=%s)' % (project.slug, count))
    model.Session.commit()
    click.echo('backfilled %d project(s).' % len(projects))


@csunesco.command('repair-image-urls')
def repair_image_urls():
    """One-shot: fix image URLs double-prefixed before the _stored_url guard.

    Old deployments stored ``/uploads/csunesco/https://...`` for Azure
    asset-storage uploads (the batch prefixed unconditionally). Idempotent --
    safe to run on any portal, does nothing when there is nothing to heal.
    """
    from ckanext.csunesco import db
    import ckan.model as model

    db.ensure_mappers()
    count = db.repair_image_urls()
    model.Session.commit()
    click.echo('repaired %d image url(s).' % count)


def _site_context():
    """A sysadmin-equivalent action context for CLI operation."""
    import ckan.plugins.toolkit as tk
    import ckan.model as model
    site_user = tk.get_action('get_site_user')({'ignore_auth': True}, {})
    return {'model': model, 'session': model.Session,
            'user': site_user['name'], 'ignore_auth': True}


@csunesco.command('manager-pending')
def manager_pending():
    """List Project Manager accounts awaiting an approve/decline decision."""
    import ckan.model as model
    from ckanext.csunesco import db

    db.ensure_mappers()
    profiles = db.pending_managers()
    if not profiles:
        click.echo('no pending manager accounts.')
        return
    for profile in profiles:
        user = model.User.get(profile.user_id)
        click.echo('%s | %s | org=%s (%s) | role=%s' % (
            user.name if user else profile.user_id,
            (user.email if user else None) or '-',
            profile.org_name_requested or profile.org_id or '-',
            profile.org_type or '-',
            profile.org_role or '-'))


@csunesco.command('manager-approve')
@click.argument('username')
def manager_approve(username):
    """Approve a pending Project Manager account (activates + creates org)."""
    import ckan.plugins.toolkit as tk

    result = tk.get_action('csunesco_manager_approve')(
        _site_context(), {'username': username})
    click.echo('approved: %s (org=%s, role=%s%s)' % (
        result['username'], result.get('org_id') or '-',
        result.get('org_role') or '-',
        ', already approved' if result.get('existed') else ''))


@csunesco.command('manager-reject')
@click.argument('username')
@click.option('--reason', default=None, help='Optional note emailed to the '
              'applicant.')
def manager_reject(username, reason):
    """Decline a pending Project Manager account (kept pending, notified)."""
    import ckan.plugins.toolkit as tk

    result = tk.get_action('csunesco_manager_reject')(
        _site_context(), {'username': username, 'reason': reason})
    click.echo('rejected: %s' % result['username'])


@csunesco.command('seed-legacy-projects')
@click.option('--force', is_flag=True,
              help='Re-impose the seed values on fields an admin already '
                   'filled in (never touches status, trusted, landing '
                   'content or extras).')
def seed_legacy_projects(force):
    """Idempotently create/refresh the legacy CS Toolbox projects.

    Creates each ``constants.LEGACY_PROJECTS`` entry as an APPROVED project
    (bundled banner image + description from the retired
    cstoolbox.quartex.co.za site). Existing projects only get EMPTY fields
    filled in, so later admin edits survive a re-run; ``--force`` overwrites
    the seed-managed fields (``db.LEGACY_SEED_FIELDS``) instead. Each item is
    wrapped in its own try/except so one failure never aborts the whole run.
    """
    import datetime
    import json

    import ckan.model as model
    from ckan.logic import get_action

    from ckanext.csunesco import constants, db
    from ckanext.csunesco.logic import validators

    db.ensure_mappers()
    site_user = get_action('get_site_user')({'ignore_auth': True}, {})
    site_user_obj = model.User.get(site_user['name'])
    site_user_id = site_user_obj.id if site_user_obj else None

    # One query up front; unknown countries are dropped per item (warned),
    # never fatal -- an un-seeded member-states group must not block projects.
    valid_states = validators._member_state_names(model)

    for seed in constants.LEGACY_PROJECTS:
        slug = seed['slug']
        try:
            countries = [c for c in seed.get('countries', [])
                         if c in valid_states]
            dropped = sorted(set(seed.get('countries', [])) - set(countries))
            if dropped:
                click.echo('warning: %s: unknown member state(s): %s'
                           % (slug, ', '.join(dropped)))
            fields = dict(seed, countries=json.dumps(countries))

            now = datetime.datetime.utcnow()
            project = db.get_project(slug)
            if project is None:
                project = db.CsProject()
                project.id = db.make_uuid()
                project.slug = slug
                project.status = 'approved'
                project.created_by = site_user_id
                project.reviewed_by = site_user_id
                project.reviewed_at = now
                project.created = now
                project.modified = now
                db.merge_legacy_fields(project, fields, force=True)
                model.Session.add(project)
                db.ensure_stats(project.id)
                model.Session.commit()
                click.echo('created: %s (%s)' % (slug, project.title))
                continue

            changed = db.merge_legacy_fields(project, fields, force=force)
            if changed:
                project.modified = now
                model.Session.commit()
                click.echo('updated: %s (%s)' % (slug, ', '.join(changed)))
            else:
                click.echo('skipped: %s (already up to date)' % slug)
        except Exception as exc:
            model.Session.rollback()
            click.echo('failed:  %s (%s)' % (slug, type(exc).__name__))


@csunesco.command('seed-initiatives')
def seed_initiatives():
    """Idempotently create/sync the Citizen Science initiative groups.

    For each entry in ``constants.CS_INITIATIVES`` this creates a CKAN group
    when missing, or syncs its title when it already exists. Each item is
    wrapped in its own try/except so one failure never aborts the whole run.
    """
    import ckan.model as model
    from ckan.logic import get_action, NotAuthorized, ValidationError
    from ckan.logic import NotFound as ObjectNotFound

    from ckanext.csunesco import constants

    # Run as the site sysadmin (standard CKAN CLI pattern) with ignore_auth so
    # group create/update always succeeds regardless of the invoking shell user.
    site_user = get_action('get_site_user')({'ignore_auth': True}, {})['name']
    context = {
        'model': model,
        'session': model.Session,
        'user': site_user,
        'ignore_auth': True,
    }

    for initiative in constants.CS_INITIATIVES:
        name = initiative['name']
        title = initiative['title']
        try:
            try:
                existing = get_action('group_show')(dict(context), {'id': name})
            except ObjectNotFound:
                get_action('group_create')(dict(context), {
                    'name': name,
                    'title': title,
                    'type': constants.CS_INITIATIVE_GROUP_TYPE,
                })
                click.echo('created: %s (%s)' % (name, title))
                continue

            # Group exists -> keep the title in sync (idempotent no-op when
            # already matching, so re-running the seeder is cheap and safe).
            if existing.get('title') == title:
                click.echo('skipped: %s (already up to date)' % name)
            else:
                get_action('group_patch')(dict(context), {
                    'id': existing['id'],
                    'title': title,
                })
                click.echo('updated: %s (title synced)' % name)
        except (ValidationError, NotAuthorized) as exc:
            click.echo('failed:  %s (%s)' % (name, exc))
        except Exception:
            click.echo('failed:  %s (unexpected error)' % name)
