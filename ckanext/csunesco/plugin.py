# encoding: utf-8
"""Main plugin for ckanext-csunesco.

Increment 1 = scaffold only. This registers the plugin with CKAN and wires up
the interfaces (config, blueprint, actions, auth, helpers, CLI) with lazy
imports so there are no import-time cycles. The real Citizen Science domain
logic (registration, project request/approval, landing, content) lands in
later increments -- see .mix/plan.md.
"""
import logging

import ckan.plugins as p
import ckan.plugins.toolkit as tk

from ckanext.csunesco import __version__

log = logging.getLogger(__name__)

# Module-level guard so the (idempotent) table bootstrap effectively runs once
# per process even if ``configure`` is invoked more than once.
_tables_ensured = False


class CsunescoPlugin(p.SingletonPlugin):
    """Citizen Science (UNESCO/IHP-WINS) plugin."""

    p.implements(p.IConfigurer)
    p.implements(p.IConfigurable, inherit=True)
    p.implements(p.IBlueprint)
    p.implements(p.IActions)
    p.implements(p.IAuthFunctions)
    p.implements(p.IValidators)
    p.implements(p.ITemplateHelpers)
    p.implements(p.IClick)

    # IConfigurer

    def update_config(self, config):
        tk.add_template_directory(config, 'templates')
        tk.add_resource('assets', 'csunesco')
        tk.add_public_directory(config, 'public')

    # IConfigurable

    def configure(self, config):
        """Called when the plugin is loaded; bootstrap the DB tables once.

        Wrapped in a broad try/except that logs a *generic* error only, so we
        never leak database internals into the logs and never break CKAN
        startup if the bootstrap cannot run.
        """
        global _tables_ensured
        if _tables_ensured:
            return
        try:
            from ckanext.csunesco import db
            db.ensure_tables()
            _tables_ensured = True
        except Exception:
            log.error("ckanext-csunesco: could not initialize database tables")

    # IBlueprint

    def get_blueprint(self):
        from ckanext.csunesco import blueprint
        return blueprint.get_blueprints()

    # IActions

    def get_actions(self):
        from ckanext.csunesco.logic import actions
        return actions.get_actions()

    # IAuthFunctions

    def get_auth_functions(self):
        from ckanext.csunesco.logic import auth
        return auth.get_auth_functions()

    # IValidators

    def get_validators(self):
        from ckanext.csunesco.logic import validators
        return validators.get_validators()

    # ITemplateHelpers

    def get_helpers(self):
        # Presentation-layer helpers live in logic/helpers.py; import lazily so
        # the plugin has no import-time dependency on CKAN internals.
        from ckanext.csunesco.logic import helpers
        return {
            'csunesco_version': lambda: __version__,
            # Public reCAPTCHA v3 site key (read lazily from config). Returns
            # None when reCAPTCHA is not configured, so templates can render the
            # widget conditionally.
            'csunesco_recaptcha_publickey':
                lambda: tk.config.get('ckan.recaptcha.publickey'),
            # Increment 4: public Citizen Science presentation helpers.
            'csunesco_initiatives': helpers.csunesco_initiatives,
            'csunesco_aggregate_stats': helpers.csunesco_aggregate_stats,
            'csunesco_recent_news': helpers.csunesco_recent_news,
            'csunesco_project_url': helpers.csunesco_project_url,
            'csunesco_join_url': helpers.csunesco_join_url,
            'csunesco_qr_data_uri': helpers.csunesco_qr_data_uri,
            'csunesco_member_state_title': helpers.csunesco_member_state_title,
            # Increment 5: admin approval panel + per-project news/events.
            'csunesco_pending_count': helpers.csunesco_pending_count,
            'csunesco_can_manage_project':
                helpers.csunesco_can_manage_project,
            # Content management: allowlisted Terria embed for cs-map pages.
            'csunesco_terria_embed_url': helpers.csunesco_terria_embed_url,
            # Data pipeline: entry point into Data Stories (None when disabled).
            'csunesco_data_stories_new_url':
                helpers.csunesco_data_stories_new_url,
        }

    # IClick

    def get_commands(self):
        from ckanext.csunesco import cli
        return [cli.csunesco]
