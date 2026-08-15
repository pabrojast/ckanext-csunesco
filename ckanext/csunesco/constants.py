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

# The stages of the project form (/citizen-science/project/new and .../edit).
#
# ONE definition drives the step indicator, the server-side "which stage holds
# the first error" jump, and the scaffold test that asserts the template's
# ``data-step`` blocks still agree with this list. Titles and hints are English
# source strings; the template runs them through ``_()``.
#
# ``fields`` must stay in sync with ``logic.schema.project_request_schema()``
# minus ``slug`` (which is derived from the title and never shown) -- that is
# what the scaffold test checks, so a field added to the schema and forgotten
# here fails loudly instead of silently never rendering.
PROJECT_FORM_STEPS = [
    {'step': 1, 'key': 'essentials', 'title': u'The essentials',
     'hint': u'What the project is called, and which initiative it belongs '
             u'to.',
     'fields': ('title', 'initiative')},
    {'step': 2, 'key': 'where', 'title': u'Where it happens',
     'hint': u'The countries involved, any biosphere reserve, and the region '
             u'shown on the map.',
     'fields': ('countries', 'biosphere_reserve', 'region_geojson')},
    {'step': 3, 'key': 'about', 'title': u'What it is about',
     'hint': u'A short summary, and how volunteers take part.',
     'fields': ('short_description', 'how_to_participate')},
    {'step': 4, 'key': 'participation', 'title': u'Taking part',
     'hint': u'When the project runs and who it is for.',
     'fields': ('start_date', 'end_date', 'open_participation',
                'target_group')},
    {'step': 5, 'key': 'contact', 'title': u'Contact and materials',
     'hint': u'Who to reach, plus a reference document and a cover image.',
     'fields': ('contact_person', 'contact_email', 'project_document_url',
                'image_url')},
]

# The parent CKAN group whose ACTIVE child groups are the valid member states
# (water-family pattern). It lived as a private copy in four modules -- db,
# validators, helpers and views -- each carrying a "keep in sync with the
# others" comment, which is the shape of a literal that eventually drifts.
MEMBER_STATES_GROUP = 'member-states'

# The regional projects of the retired CS Toolbox site
# (cstoolbox.quartex.co.za), seeded as regular APPROVED ``cs_project`` rows by
# ``ckan csunesco seed-legacy-projects``. Banner images were downloaded from
# that site once and shipped in ``public/csunesco/images/`` -- the site is
# being decommissioned, so nothing may hotlink it. ``countries`` entries must
# be member-state group names (children of the ``member-states`` group); the
# seeder drops unknown ones with a warning instead of failing.
LEGACY_PROJECTS = [
    {
        'slug': 'cape-winelands',
        'title': 'Cape Winelands Biosphere Reserve',
        'initiative_group': 'be-resilient',
        'countries': ['south-africa'],
        'biosphere_reserve': 'Cape Winelands Biosphere Reserve',
        'short_description': (
            'Cape Winelands Biosphere Reserve is an active participant in '
            'the Be Resilient programme, working to monitor and protect '
            'vital ecosystems through collaboration with local communities, '
            'scientists and policymakers.'),
        'image_url': '/csunesco/images/project-cape-winelands.jpg',
    },
    {
        'slug': 'kruger-to-canyons',
        'title': 'Kruger2Canyons Biosphere Reserve',
        'initiative_group': 'be-resilient',
        'countries': ['south-africa'],
        'biosphere_reserve': 'Kruger to Canyons Biosphere Region',
        'short_description': (
            'The Freshwater Monitoring Citizen Science Project assesses and '
            'enhances the health of freshwater ecosystems in the Kruger to '
            'Canyons Biosphere Region. Launched in 2022 under the '
            'Flemish-funded UNESCO Be Resilient project, it involves '
            'community-based citizen scientists in water monitoring across '
            'several important river systems.'),
        'image_url': '/csunesco/images/project-kruger-to-canyons.jpg',
    },
    {
        'slug': 'marico',
        'title': 'Marico Biosphere Reserve',
        'initiative_group': 'be-resilient',
        'countries': ['south-africa'],
        'biosphere_reserve': 'Marico Biosphere Reserve',
        'short_description': (
            'Marico Biosphere Reserve is an active participant in the '
            'Be Resilient programme, working to monitor and protect vital '
            'ecosystems through collaboration with local communities, '
            'scientists and policymakers.'),
        'image_url': '/csunesco/images/project-marico.jpg',
    },
    {
        'slug': 'vhembe',
        'title': 'Vhembe Biosphere Reserve',
        'initiative_group': 'be-resilient',
        'countries': ['south-africa'],
        'biosphere_reserve': 'Vhembe Biosphere Reserve',
        'short_description': (
            'Vhembe Biosphere Reserve is an active participant in the '
            'Be Resilient programme, working to monitor and protect vital '
            'ecosystems through collaboration with local communities, '
            'scientists and policymakers.'),
        'image_url': '/csunesco/images/project-vhembe.jpg',
    },
    {
        'slug': 'cuba',
        'title': 'Cuba',
        'initiative_group': 'islandwatch',
        'countries': ['cuba'],
        'biosphere_reserve': '',
        'short_description': (
            'As part of the IslandWatch initiative and the Flemish-funded '
            'Be Resilient SIDS project, this Citizen Science initiative '
            'improves water resource management with a climate-focused '
            'approach and strengthens response capacity to hydroclimatic '
            "risks in Cuba's Biosphere Reserves."),
        'image_url': '/csunesco/images/project-cuba.jpg',
    },
    {
        'slug': 'seychelles',
        'title': 'Seychelles',
        'initiative_group': 'islandwatch',
        'countries': ['seychelles'],
        'biosphere_reserve': '',
        'short_description': (
            'The NGO Gaea, in coordination with UNESCO, leads a Citizen '
            'Science initiative under the IslandWatch programme, engaging '
            'governmental institutions, schools and citizen scientists to '
            'collect critical water-quality indicators across the '
            'Seychelles.'),
        'image_url': '/csunesco/images/project-seychelles.jpg',
    },
    {
        'slug': 'ghana',
        'title': 'Ghana',
        'initiative_group': 'riverwatch',
        'countries': ['ghana'],
        'biosphere_reserve': '',
        'short_description': (
            "A UNESCO project to strengthen Ghana's resilience against "
            'floods and droughts, engaging youth in disaster risk reduction '
            'and climate adaptation with the support of the Government of '
            'Japan.'),
        'image_url': '/csunesco/images/project-ghana.jpg',
    },
]

# Google reCAPTCHA v3 server-side verification endpoint. reCAPTCHA is OPTIONAL:
# it is only enforced when BOTH ``ckan.recaptcha.publickey`` and
# ``ckan.recaptcha.privatekey`` are configured (see logic/registration.py).
RECAPTCHA_SITEVERIFY_URL = 'https://www.google.com/recaptcha/api/siteverify'

# Email-verification window for web self-registration. A Citizen Scientist
# account created through the web form is left in CKAN ``pending`` state (cannot
# log in) until the emailed link is opened within this many hours; after that the
# link is expired and a fresh one must be requested via the resend form.
VERIFICATION_TOKEN_TTL_HOURS = 48
