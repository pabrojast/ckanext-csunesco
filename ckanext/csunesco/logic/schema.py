# encoding: utf-8
"""navl schemas for ckanext-csunesco actions.

Kept separate from ``validators.py`` so the action layer imports a ready-to-use
schema dict (field -> [validators]) while the individual validators stay small
and reusable. Core validators are pulled by name via ``tk.get_validator`` so we
inherit CKAN's own coercion (e.g. ``unicode_safe``).
"""
import ckan.plugins.toolkit as tk

from ckanext.csunesco.logic import validators as v


def project_request_schema():
    """Schema for ``csunesco_project_request_create``.

    ``title`` and ``initiative`` are required; everything else is optional. Note
    ``countries`` deliberately omits ``unicode_safe`` so a raw list survives to
    ``csunesco_valid_country_list`` (which accepts a list or a JSON string).
    """
    not_empty = tk.get_validator('not_empty')
    ignore_missing = tk.get_validator('ignore_missing')
    unicode_safe = tk.get_validator('unicode_safe')
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
    }


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
