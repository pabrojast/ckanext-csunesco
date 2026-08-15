# encoding: utf-8
"""navl schemas for ckanext-csunesco actions.

Kept separate from ``validators.py`` so the action layer imports a ready-to-use
schema dict (field -> [validators]) while the individual validators stay small
and reusable. Core validators are pulled by name via ``tk.get_validator`` so we
inherit CKAN's own coercion (e.g. ``unicode_safe``).
"""
import ckan.plugins.toolkit as tk

from ckanext.csunesco.logic import validators as v


# Fields that are NOT columns on ``cs_project`` and are stored in its ``extras``
# JSON blob instead. Kept here, next to the schema that defines them, so the
# action has one authoritative list to iterate rather than a second hand-written
# copy that drifts. ``project_dictize`` merges extras back in on read.
PROJECT_EXTRA_FIELDS = (
    'how_to_participate',
    'start_date',
    'end_date',
    'open_participation',
    'target_group',
    'contact_person',
    'contact_email',
)

# The subset of the above that is free text typed by a user, and therefore has
# to be sanitized before storage (same treatment as ``short_description``).
PROJECT_EXTRA_HTML_FIELDS = ('how_to_participate', 'target_group')

# The subset that ``csunesco_valid_iso_date`` turns into ``datetime`` objects.
# They live in a JSON column, so the action must isoformat() them -- json.dumps
# cannot serialize a datetime.
PROJECT_EXTRA_DATE_FIELDS = ('start_date', 'end_date')


def project_request_schema():
    """Schema for ``csunesco_project_request_create`` / ``_update``.

    ``title`` and ``initiative`` are required; everything else is optional. Note
    ``countries`` deliberately omits ``unicode_safe`` so a raw list survives to
    ``csunesco_valid_country_list`` (which accepts a list or a JSON string).

    Every field added for the staged form is optional ON PURPOSE: the CS Toolbox
    app (ofform) posts to this same action through its outbox with a fixed
    payload, and a new required field would break every project it creates.
    """
    not_empty = tk.get_validator('not_empty')
    ignore_missing = tk.get_validator('ignore_missing')
    unicode_safe = tk.get_validator('unicode_safe')
    boolean_validator = tk.get_validator('boolean_validator')
    email_validator = tk.get_validator('email_validator')
    return {
        'title': [not_empty, unicode_safe],
        'initiative': [not_empty, unicode_safe, v.csunesco_valid_initiative],
        'countries': [ignore_missing, v.csunesco_valid_country_list],
        'slug': [ignore_missing, unicode_safe, v.csunesco_valid_slug],
        'biosphere_reserve': [ignore_missing, unicode_safe],
        'region_geojson': [
            ignore_missing, unicode_safe, v.csunesco_valid_geojson],
        'short_description': [ignore_missing, unicode_safe],
        'project_document_url': [
            ignore_missing, unicode_safe, v.csunesco_valid_document_url],
        'image_url': [
            ignore_missing, unicode_safe, v.csunesco_valid_image_url],

        # --- stored in ``extras`` ------------------------------------------
        'how_to_participate': [ignore_missing, unicode_safe],
        'start_date': [ignore_missing, v.csunesco_valid_iso_date],
        # allow_equal: a project that runs for a single day is legitimate.
        'end_date': [
            ignore_missing, v.csunesco_valid_iso_date,
            v.csunesco_end_after('start_date', allow_equal=True)],
        'open_participation': [ignore_missing, boolean_validator],
        'target_group': [ignore_missing, unicode_safe],
        'contact_person': [ignore_missing, unicode_safe],
        'contact_email': [ignore_missing, unicode_safe, email_validator],
    }


def project_update_schema(present_keys=None):
    """``project_request_schema`` for an EDIT.

    Two differences. ``slug`` is dropped: the project's URL is permanent, and
    letting an edit move it would break every link, QR code and ofform mirror
    already pointing at it.

    And when ``present_keys`` is given the schema is narrowed to the keys
    actually being written, so a PARTIAL API update is not rejected for failing
    to resend ``title``. A key that IS sent still faces its whole rule list --
    ``title=''`` still trips ``not_empty`` -- so this loosens which fields are
    required, never how they are checked.
    """
    schema = project_request_schema()
    schema.pop('slug', None)
    if present_keys is None:
        return schema
    present = set(present_keys)
    return {key: rules for key, rules in schema.items() if key in present}


def content_schema(content_type):
    """Schema for ``csunesco_content_create`` / ``_update``.

    ``title`` and ``content_type`` are always required. ``body`` stays raw HTML
    here (it is SANITIZED in the action before storage, not by navl). Dates are
    coerced to ``datetime`` by ``csunesco_valid_iso_date``. For ``cs-event`` both
    a start (``publish_date``) and an end (``end_date``) are required and the end
    must be strictly later than the start. ``cs-publication`` requires at least
    one document link in ``media``; ``cs-map`` requires an allowlisted Terria
    share URL in ``terria_url``.
    """
    not_empty = tk.get_validator('not_empty')
    ignore_missing = tk.get_validator('ignore_missing')
    unicode_safe = tk.get_validator('unicode_safe')
    boolean_validator = tk.get_validator('boolean_validator')

    schema = {
        'title': [not_empty, unicode_safe],
        'content_type': [
            not_empty, unicode_safe, v.csunesco_valid_content_type],
        # Left raw here on purpose -- the action sanitizes it (single allowlist).
        'body': [ignore_missing, unicode_safe],
        'media': [ignore_missing, v.csunesco_valid_media_list],
        'publish_date': [ignore_missing, v.csunesco_valid_iso_date],
        'end_date': [ignore_missing, v.csunesco_valid_iso_date],
        'featured': [ignore_missing, boolean_validator],
        'visibility': [ignore_missing, v.csunesco_valid_visibility],
        'terria_url': [
            ignore_missing, unicode_safe, v.csunesco_valid_terria_url],
        'doi': [ignore_missing, unicode_safe],
        'authors': [ignore_missing, unicode_safe],
    }
    if content_type == 'cs-event':
        # Events need a start + an end, with end strictly after start.
        schema['publish_date'] = [not_empty, v.csunesco_valid_iso_date]
        schema['end_date'] = [
            not_empty, v.csunesco_valid_iso_date, v.csunesco_end_after_start]
    elif content_type == 'cs-publication':
        # Publications must link at least one document (the '[]' JSON string is
        # truthy, hence the extra nonempty check after list validation).
        schema['media'] = [
            not_empty, v.csunesco_valid_media_list,
            v.csunesco_nonempty_media_list]
    elif content_type == 'cs-map':
        schema['terria_url'] = [
            not_empty, unicode_safe, v.csunesco_valid_terria_url]
    return schema
