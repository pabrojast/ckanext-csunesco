# encoding: utf-8
"""Server-to-server Citizen Scientist registration action.

Increment 9: a SYSADMIN-only API action that lets the ofform backend register a
Citizen Scientist account CKAN-first and idempotently. It reuses the core
``create_citizen_scientist`` flow (``logic/registration.py``) so the web view and
the API share a single implementation.

Idempotency: if a CKAN user with the requested name already exists AND already
carries a ``cs_citizen_scientist`` profile, a previous (possibly retried)
registration already succeeded -- we return success with ``existed=True`` instead
of raising, so retries are safe. Otherwise we create the account. Every failure
is collapsed into a single generic error (no account enumeration).
"""
import logging

import ckan.plugins.toolkit as tk
import ckan.model as model

from ckanext.csunesco import db
from ckanext.csunesco.logic.registration import (
    create_citizen_scientist,
    GENERIC_ERROR,
)

log = logging.getLogger(__name__)


def csunesco_register_citizen_scientist(context, data_dict):
    """Register a Citizen Scientist account (server-to-server, idempotent)."""
    tk.check_access('csunesco_register_citizen_scientist', context, data_dict)
    data_dict = data_dict or {}

    email = (data_dict.get('email') or '').strip()
    username = (data_dict.get('username') or '').lower().strip()
    fullname = (data_dict.get('fullname') or '').strip()
    password = data_dict.get('password') or ''
    country = (data_dict.get('country') or '').strip()

    # IDEMPOTENT fast-path: an existing CKAN user that already carries a CS
    # profile means a previous registration succeeded. Return success WITHOUT
    # touching anything -- never raise, never re-create the account.
    existing_user = model.User.get(username) if username else None
    if existing_user is not None:
        db.ensure_mappers()
        profile = (
            model.Session.query(db.CsCitizenScientist)
            .filter(db.CsCitizenScientist.user_id == existing_user.id)
            .first()
        )
        if profile is not None:
            return {
                'status': 'success',
                'username': existing_user.name,
                'id': existing_user.id,
                'existed': True,
            }

    try:
        new_user = create_citizen_scientist(context, {
            'email': email,
            'username': username,
            'fullname': fullname,
            'password': password,
            'country': country,
        })
    except tk.ValidationError:
        # Collapse to a single generic error (no account enumeration).
        raise tk.ValidationError({'message': GENERIC_ERROR})

    return {
        'status': 'success',
        'username': new_user['name'],
        'id': new_user['id'],
        'existed': False,
    }


def get_actions():
    return {
        'csunesco_register_citizen_scientist': csunesco_register_citizen_scientist,
    }
