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
    # ``name`` must stay 'c4water': it is the seeded CKAN group slug and the
    # alias the app's initiative routing normalizes to. Only the human label
    # follows the spec's "Citizens4Water".
    {'name': 'c4water', 'title': 'Citizens4Water'},
]

# CKAN group type used for the initiative groups above.
CS_INITIATIVE_GROUP_TYPE = 'group'

# The stages of the project form (/citizen-science/project/new and .../edit),
# following the spec's phase-1 sections A-F ("Public Portal Structure +
# Project Forms", section 6).
#
# ONE definition drives the step indicator, the server-side "which stage holds
# the first error" jump, and the scaffold test that asserts the template's
# ``data-step`` blocks still agree with this list. Titles and hints are English
# source strings; the template runs them through ``_()``.
#
# ``fields`` must stay in sync with the form template's inputs -- the scaffold
# test checks every named field has an input, so a field added to the schema
# and forgotten here fails loudly instead of silently never rendering.
PROJECT_FORM_STEPS = [
    {'step': 1, 'key': 'identity', 'title': u'Basic identity',
     'hint': u'What the project is called, how it introduces itself, and its '
             u'images.',
     'fields': ('title', 'slug', 'short_description', 'image_url',
                'image_focal_x', 'image_focal_y', 'logo_url',
                'heading_image_url', 'heading_focal_x', 'heading_focal_y')},
    {'step': 2, 'key': 'classification', 'title': u'Classification',
     'hint': u'Keywords, the water bodies and data involved, and the '
             u'initiative if it belongs to one.',
     'fields': ('keywords', 'initiative', 'water_type', 'water_data_type')},
    {'step': 3, 'key': 'location', 'title': u'Location',
     'hint': u'Where the project happens: extent, countries and the region '
             u'shown on the map.',
     'fields': ('geographic_extent', 'countries', 'locality',
                'biosphere_reserve', 'region_geojson', 'point_lat',
                'point_lng', 'point_radius_km')},
    {'step': 4, 'key': 'participation', 'title': u'Participation',
     'hint': u'Who can take part, when the project runs and who benefits.',
     'fields': ('participation_mode', 'allowed_participants', 'languages',
                'stakeholders', 'activity_status', 'start_date', 'end_date',
                'how_to_participate', 'target_group')},
    {'step': 5, 'key': 'leadership', 'title': u'Leadership and contact',
     'hint': u'The institutions behind the project and who to reach.',
     'fields': ('lead_partner_type', 'lead_organisation',
                'organization_id', 'other_organisations', 'editors', 'contact_person',
                'contact_email')},
    {'step': 6, 'key': 'funding', 'title': u'Funding and references',
     'hint': u'Who funds the project and where to read more.',
     'fields': ('funding_body', 'funding_programme', 'project_document_url',
                'international_frameworks')},
]

# --------------------------------------------------------------------------- #
# Phase-1 option lists (spec section 6). Plain data, mirrored to the CS
# Toolbox app through the ``csunesco_option_lists`` action -- never hard-code
# these in a second place.
# --------------------------------------------------------------------------- #

WATER_TYPES = (
    'Lake Surface water', 'River', 'Stream', 'Pond', 'Wetland', 'Soil water',
    'Groundwater', 'Estuarine/Coastal', 'Snow & ice', 'Other',
)

WATER_DATA_TYPES = (
    'Water quantity', 'Physical water quality', 'Chemical water quality',
    'Biological water quality', 'Water related hazard', 'Hydro-meteorological',
    'Other',
)

GEOGRAPHIC_EXTENTS = (
    'Global', 'Macro-regional', 'National', 'Transnational / transboundary',
    'Sub-national', 'Regional',
    'UNESCO Site: Biosphere Reserve, Geopark, Natural Heritage Site, '
    'Ecohydrology Demosite',
    'City', 'Neighbourhood',
)

STAKEHOLDER_GROUPS = (
    'Citizens', 'Researchers', 'School teachers', 'School children',
    'Policy makers', 'Authorities', 'Businesses',
)

ACTIVITY_STATUSES = (
    'Not yet started', 'Active', 'Periodically active', 'On hold',
)

LEAD_PARTNER_TYPES = (
    'UNESCO IHP Secretariat', 'UNESCO Field Office', 'IHP National Committee',
    'UNESCO Category I Center', 'UNESCO Category II Center',
    'UNESCO Chair of UNITWIN', 'Non-Governmental Organization',
    'Governmental Organizations', 'University', 'Research Institute',
    'Private Partner', 'Citizen Movement', 'Other',
)

FUNDING_BODIES = (
    'Flanders', 'Japan', 'Austria', 'Swiss National Science Foundation', 'EU',
    'Kurt Eberhard Bode Foundation', 'University of Amsterdam', 'VLIR UOS',
    'AXA Reseach Fund', 'European Union',
    'Italian Ministry of University and Research', 'UKRI',
    'Ministry of Research, Innovation and Digitization',
    'Federal Minstry of Education and Research (01BF 2108)',
    'National Monitoring Center for Biodiversity (NMZB) and Federal Agency '
    'for Nature Conservation (BfN)',
    'Federal Ministry of Education and Research (BMBF)',
    'Deutsche Bundesstiftung Umwelt (DBU)', 'IHE delft',
    'European Commission',
)

INTL_FRAMEWORKS = (
    'SDG 1: No Poverty', 'SDG 2: Zero Hunger',
    'SDG 3: Good Health and Well-being', 'SDG 4: Quality Education',
    'SDG 5: Gender Equality', 'SDG 6: Clean Water and Sanitation',
    'SDG 7: Affordable and Clean Energy',
    'SDG 8: Decent Work and Economic Growth',
    'SDG 9: Industry, Innovation and Infrastructure',
    'SDG 10: Reduced Inequalities',
    'SDG 11: Sustainable Cities and Communities',
    'SDG 12: Responsible Consumption and Production', 'SDG 13: Climate Action',
    'SDG 14: Life Below Water', 'SDG 15: Life on Land',
    'SDG 16: Peace, Justice and Strong Institutions',
    'SDG 17: Partnerships for the Goals',
    'Decade of Action on Cryospheric Sciences',
    'Sendai Framework for Disaster Risk Reduction',
    'Global Goals for Adaptation', 'Other',
)

# Participation modes (spec D: open to any participant, with a QR on the
# landing page, or limited to a selected group).
PARTICIPATION_MODES = ('open', 'limited')

# Static field -> audience map for project fields (spec section 5). Fields
# absent here are PUBLIC. Values: 'logged-in' (any authenticated portal user)
# or 'participants' (active members of that project only). Rendering goes
# through ``h.csunesco_field_audience_ok``.
FIELD_AUDIENCE = {
    'contact_email': 'logged-in',
    'contact_person': 'logged-in',
    'editors': 'logged-in',
    'allowed_participants': 'participants',
    # Phase-2 mirror (spec section 7): section D and the two C flags are
    # participants-only; the rest of the structure is Public + Participants.
    'local_govt_engagement': 'participants',
    'indigenous_knowledge': 'participants',
    'indigenous_knowledge_notes': 'participants',
    'timeframe_start': 'participants',
    'timeframe_end': 'participants',
    'duration_of_involvement': 'participants',
    'workplan': 'participants',
}

# The parent CKAN group whose ACTIVE child groups are the valid member states
# (water-family pattern). It lived as a private copy in four modules -- db,
# validators, helpers and views -- each carrying a "keep in sync with the
# others" comment, which is the shape of a literal that eventually drifts.
MEMBER_STATES_GROUP = 'member-states'

# Institution types offered by the Project Manager registration form (spec
# "Public Portal Structure + Project Forms", section 3.C). ``name`` is the
# stored value; ``title`` the human label -- same shape as CS_INITIATIVES.
ORG_TYPES = [
    {'name': 'unesco-ihp-secretariat', 'title': 'UNESCO IHP Secretariat'},
    {'name': 'unesco-field-office', 'title': 'UNESCO Field Office'},
    {'name': 'ihp-national-committee', 'title': 'IHP National Committee'},
    {'name': 'unesco-category-1-center', 'title': 'UNESCO Category I Center'},
    {'name': 'unesco-category-2-center', 'title': 'UNESCO Category II Center'},
    {'name': 'unesco-chair-unitwin', 'title': 'UNESCO Chair or UNITWIN'},
    {'name': 'ngo', 'title': 'Non-Governmental Organization'},
    {'name': 'governmental', 'title': 'Governmental Organization'},
    {'name': 'university', 'title': 'University'},
    {'name': 'private-partner', 'title': 'Private Partner'},
    {'name': 'research-institution', 'title': 'Research Institution'},
    {'name': 'other', 'title': 'Other'},
]

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

# Officially assigned ISO-3166-1 alpha-2 codes.  The Citizen Scientist
# registration form uses these stable values for nationality; human labels are
# localized at render time with Babel, so they never become persisted data.
ISO_3166_ALPHA2 = tuple(u'''
AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI
BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN
CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK
FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM
HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN
KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK
ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP
NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW
SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF
TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI
VN VU WF WS YE YT ZA ZM ZW
'''.split())
