# encoding: utf-8
"""Shared constants for ckanext-csunesco (Citizen Science / UNESCO).

Kept CKAN-free (plain data only) so it is safe to import from anywhere -- the
CLI seeder, the registration view and future domain logic all read from here
rather than hard-coding the same literals in several places.
"""

# The four Citizen Science initiatives are modelled as CKAN ``group`` objects
# (water-family pattern -- NOT children of member-states). ``seed-initiatives``
# creates/syncs one group per entry. ``name`` is the URL slug; ``title`` is the
# human-facing label.
CS_INITIATIVES = [
    {'name': 'be-resilient', 'title': 'Be Resilient'},
    {'name': 'islandwatch', 'title': 'Island Watch'},
    {'name': 'riverwatch', 'title': 'River Watch'},
    {'name': 'c4water', 'title': 'C4Water'},
]

# CKAN group type used for the initiative groups above.
CS_INITIATIVE_GROUP_TYPE = 'group'

# Google reCAPTCHA v3 server-side verification endpoint. reCAPTCHA is OPTIONAL:
# it is only enforced when BOTH ``ckan.recaptcha.publickey`` and
# ``ckan.recaptcha.privatekey`` are configured (see logic/registration.py).
RECAPTCHA_SITEVERIFY_URL = 'https://www.google.com/recaptcha/api/siteverify'
