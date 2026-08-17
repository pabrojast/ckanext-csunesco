# encoding: utf-8
"""navl schemas for ckanext-csunesco actions.

Kept separate from ``validators.py`` so the action layer imports a ready-to-use
schema dict (field -> [validators]) while the individual validators stay small
and reusable. Core validators are pulled by name via ``tk.get_validator`` so we
inherit CKAN's own coercion (e.g. ``unicode_safe``).
"""
import ckan.plugins.toolkit as tk

from ckanext.csunesco import constants
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
    # --- spec phase-1 additions (sections A-F) -----------------------------
    'keywords',
    'water_type',
    'water_data_type',
    'geographic_extent',
    'locality',
    'point_lat',
    'point_lng',
    'point_radius_km',
    'participation_mode',
    'allowed_participants',
    'languages',
    'stakeholders',
    'activity_status',
    'lead_partner_type',
    'lead_organisation',
    'other_organisations',
    'editors',
    'funding_body',
    'funding_programme',
    'international_frameworks',
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

    ``title`` is required and ``initiative`` is optional; everything else is optional. Note
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
        # OPTIONAL, not required. The CS Toolbox app offers "Choose a
        # programme (optional)" and posts `initiative: null` when the author
        # skips it -- and because the key is present-but-null, `not_empty`
        # fired and every programme-less project the app created was rejected.
        # The web form exposes the empty value as "Not part of an initiative".
        'initiative': [ignore_missing, unicode_safe,
                       v.csunesco_valid_initiative],
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

        'logo_url': [
            ignore_missing, unicode_safe, v.csunesco_valid_image_url],
        'heading_image_url': [
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

        # --- spec phase-1 additions (sections A-F), all lenient here -------
        'keywords': [ignore_missing, v.csunesco_valid_string_list,
                     v.csunesco_require_list(0, 3)],
        'water_type': [
            ignore_missing, v.csunesco_valid_string_list,
            v.csunesco_choice_list(constants.WATER_TYPES, allow_other=True)],
        'water_data_type': [
            ignore_missing, v.csunesco_valid_string_list,
            v.csunesco_choice_list(constants.WATER_DATA_TYPES,
                                   allow_other=True)],
        'geographic_extent': [
            ignore_missing, unicode_safe,
            v.csunesco_choice(constants.GEOGRAPHIC_EXTENTS)],
        'locality': [ignore_missing, unicode_safe],
        'point_lat': [ignore_missing, v.csunesco_valid_latitude],
        'point_lng': [ignore_missing, v.csunesco_valid_longitude],
        'point_radius_km': [ignore_missing, v.csunesco_valid_radius_km],
        'participation_mode': [
            ignore_missing, unicode_safe,
            v.csunesco_choice(constants.PARTICIPATION_MODES)],
        'allowed_participants': [
            ignore_missing, v.csunesco_valid_string_list],
        'languages': [ignore_missing, v.csunesco_valid_string_list],
        'stakeholders': [
            ignore_missing, v.csunesco_valid_string_list,
            v.csunesco_choice_list(constants.STAKEHOLDER_GROUPS,
                                   allow_other=True)],
        'activity_status': [
            ignore_missing, v.csunesco_valid_string_list,
            v.csunesco_choice_list(constants.ACTIVITY_STATUSES)],
        'lead_partner_type': [
            ignore_missing, unicode_safe,
            v.csunesco_choice(constants.LEAD_PARTNER_TYPES)],
        'lead_organisation': [ignore_missing, unicode_safe],
        'other_organisations': [
            ignore_missing, v.csunesco_valid_string_list],
        'editors': [ignore_missing, v.csunesco_valid_string_list],
        'funding_body': [
            ignore_missing, v.csunesco_valid_string_list,
            v.csunesco_choice_list(constants.FUNDING_BODIES,
                                   allow_other=True)],
        'funding_programme': [ignore_missing, unicode_safe],
        'international_frameworks': [
            ignore_missing, v.csunesco_valid_string_list,
            v.csunesco_choice_list(constants.INTL_FRAMEWORKS)],
    }


def project_request_form_schema():
    """The STRICT variant validated by the WEB form views only.

    The per-caller split (see ``project_request_schema``'s docstring): the
    action keeps its lenient schema frozen for the CS Toolbox outbox, while
    the portal's own form enforces the spec's starred fields BEFORE calling
    the action. Requirements live here exclusively -- never tighten the base.
    """
    not_empty = tk.get_validator('not_empty')
    schema = project_request_schema()
    require = {
        'short_description': [not_empty] + schema['short_description'],
        # Spec B: 2-3 keywords.
        'keywords': [not_empty, v.csunesco_valid_string_list,
                     v.csunesco_require_list(2, 3)],
        'water_type': [not_empty] + schema['water_type'][1:] + [
            v.csunesco_require_list(1)],
        'water_data_type': [not_empty] + schema['water_data_type'][1:] + [
            v.csunesco_require_list(1)],
        'geographic_extent': [not_empty] + schema['geographic_extent'][1:],
        'countries': [not_empty] + schema['countries'][1:],
        'participation_mode': [not_empty] + schema['participation_mode'][1:],
        'activity_status': [not_empty] + schema['activity_status'][1:] + [
            v.csunesco_require_list(1)],
        'lead_partner_type': [not_empty] + schema['lead_partner_type'][1:],
        'lead_organisation': [not_empty] + schema['lead_organisation'][1:],
    }
    schema.update(require)
    return schema


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
        # An event needs a start. The END is optional and MAY equal the start:
        # both rules used to be strict, and both rejected ordinary events the
        # CS Toolbox app creates -- an open-ended one (it posts `end_date:
        # null`) and a single-day one (it posts the same date twice, since it
        # sends date-only ISO strings). `allow_equal` reuses the factory added
        # for exactly this on projects.
        schema['publish_date'] = [not_empty, v.csunesco_valid_iso_date]
        schema['end_date'] = [
            ignore_missing, v.csunesco_valid_iso_date,
            v.csunesco_end_after('publish_date', allow_equal=True)]
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
