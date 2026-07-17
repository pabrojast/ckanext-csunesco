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
