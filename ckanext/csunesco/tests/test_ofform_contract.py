# encoding: utf-8
"""The CS Toolbox (ofform) → CKAN contract, payload by literal payload.

THIS FILE EXISTS BECAUSE ITS ABSENCE SHIPPED FOUR BROKEN FEATURES.

ofform's own suite mocks the portal, so it pins what ofform *sends*; CKAN's
suite hand-writes its inputs, so it pins what CKAN *accepts*. Nothing compared
the two, and four payloads that CKAN rejected outright reached production:

* ``csunesco_join_request_create`` — ofform sends ``project_slug``, the action
  read only ``project_id``/``id``: every app join failed;
* ``csunesco_join_approve`` — ofform sends ``project_slug`` + ``username``, the
  action demanded ``project_id`` + ``user_id``: zero key overlap;
* ``csunesco_project_request_create`` — ``initiative: null`` tripped
  ``not_empty``: every programme-less project failed;
* ``csunesco_content_create`` for ``cs-event`` — ``end_date: null`` tripped
  ``not_empty``, and a single-day event tripped a strict end > start.

Each failure was silent to the user: the row is created locally in ofform and
only the CKAN mirror dies, landing in the outbox as ``failed`` after five
retries.

So every payload below is copied VERBATIM from ofform's source, with the
producing file:line named. If ofform changes its payload, or CKAN narrows what
it reads, one of these must fail.
"""
import pytest

try:
    import sqlalchemy as sa
    import ckan.plugins.toolkit as tk
    import ckan  # noqa: F401
    from ckanext.csunesco import db
    from ckanext.csunesco.logic import auth as cs_auth
    from ckanext.csunesco.logic import schema as cs_schema
    from ckanext.csunesco.logic.action import members as members_action
    from ckanext.csunesco.logic.action import projects as projects_action
    HAVE_CKAN = True
except Exception:  # pragma: no cover - environment without CKAN
    HAVE_CKAN = False

pytestmark = pytest.mark.skipif(
    not HAVE_CKAN, reason="requires CKAN (ckan.model + sqlalchemy)")


class _User(object):
    is_anonymous = False

    def __init__(self, user_id):
        self.id = user_id


def _service_ctx():
    """The outbox calls with ONE sysadmin service token, for everybody."""
    return {'user': 'cs-toolbox-service',
            'auth_user_obj': _User('cs-toolbox-service')}


@pytest.fixture
def session():
    engine = sa.create_engine('sqlite://')
    db.ensure_mappers()
    from ckan.model.group import group_table, member_table
    db.metadata.create_all(
        bind=engine, tables=list(db._ALL_TABLES) + [group_table, member_table])
    db.Session.remove()
    db.Session.configure(bind=engine)
    try:
        yield db.Session
    finally:
        db.Session.remove()
        engine.dispose()


@pytest.fixture
def service(session, monkeypatch):
    """A sysadmin service token, which is what the outbox actually holds."""
    monkeypatch.setattr(tk, 'check_access', lambda *a, **k: True)
    monkeypatch.setattr(cs_auth, '_is_sysadmin', lambda context: True)
    monkeypatch.setattr(cs_auth, 'can_manage_project',
                        lambda context, project_id: True)


@pytest.fixture
def project(session):
    row = db.CsProject()
    row.slug = 'douro-basin'
    row.title = 'Douro Basin'
    row.status = 'approved'
    session.add(row)
    session.commit()
    return row


# --------------------------------------------------------------------------- #
# csunesco_project_request_create                                              #
# ofform/backend/app/routers/cs_projects.py:272-287                            #
# --------------------------------------------------------------------------- #

def _project_payload(**overrides):
    payload = {
        'programme_id': 42,
        'slug': 'douro-basin-freshwater',
        'title': 'Douro Basin Freshwater',
        'short_description': None,
        'initiative': None,
        'countries': [],
        'biosphere_reserve': None,
        'region_geojson': None,
        'project_document_url': None,
        'requested_by': 'ana',
    }
    payload.update(overrides)
    return payload


def test_project_request_accepts_the_literal_payload(service, session):
    """Every optional key is sent as JSON null, not omitted -- that is what
    made `initiative` trip `not_empty` even though it is 'optional'."""
    out = projects_action.csunesco_project_request_create(
        _service_ctx(), _project_payload())
    assert out['status'] == 'pending'
    assert out['title'] == 'Douro Basin Freshwater'


def test_project_request_survives_a_programme_less_project(service, session):
    """The app's programme picker is explicitly optional."""
    out = projects_action.csunesco_project_request_create(
        _service_ctx(), _project_payload(initiative=None))
    assert not out.get('initiative_group')


def test_project_request_normalizes_the_apps_hyphenated_initiative(service,
                                                                   session):
    out = projects_action.csunesco_project_request_create(
        _service_ctx(), _project_payload(initiative='river-watch'))
    assert out['initiative_group'] == 'riverwatch'


def test_project_request_ignores_the_apps_routing_keys(service, session):
    """`programme_id` and `requested_by` are ofform's own bookkeeping and must
    be dropped without an 'unexpected field' error."""
    out = projects_action.csunesco_project_request_create(
        _service_ctx(), _project_payload())
    assert 'programme_id' not in out
    assert 'requested_by' not in out


def test_the_outbox_reads_back_a_slug_it_can_store(service, session):
    """cs_sync.py:167-170 does `result.get('slug') or result.get('name')` to
    learn the slug CKAN actually assigned after de-duplication."""
    out = projects_action.csunesco_project_request_create(
        _service_ctx(), _project_payload())
    assert out.get('slug')


# --------------------------------------------------------------------------- #
# csunesco_join_request_create                                                 #
# ofform/backend/app/routers/cs_projects.py:973-982                            #
# --------------------------------------------------------------------------- #

def _join_payload(**overrides):
    payload = {
        'programme_id': 42,
        'project_slug': 'douro-basin',
        'username': 'maria',
        'note': 'I teach nearby and my class can sample weekly.',
    }
    payload.update(overrides)
    return payload


def _ckan_user(session, name='maria'):
    """A stand-in for a CKAN account.

    CKAN's `user` table cannot be created on SQLite (JSONB `plugin_extras`), so
    `model.User.get` is patched per test rather than seeded.
    """
    return _User('user-%s' % name)


def test_join_request_accepts_project_slug(service, session, project,
                                           monkeypatch):
    """THE blocker: the app names the project by slug, and only by slug."""
    import ckan.model as model
    monkeypatch.setattr(model.User, 'get',
                        staticmethod(lambda name: _ckan_user(session, name)))
    out = members_action.csunesco_join_request_create(
        _service_ctx(), _join_payload())
    assert out['status'] == 'pending'
    assert out['project_id'] == project.id


def test_join_request_is_filed_for_the_named_user_not_the_token(
        service, session, project, monkeypatch):
    """The whole point of the trusted proxy.

    One service token joins on behalf of whoever tapped the button; without
    this the membership belonged to the service account and the reviewer saw
    its name and email on every single request.
    """
    import ckan.model as model
    monkeypatch.setattr(model.User, 'get',
                        staticmethod(lambda name: _ckan_user(session, name)))
    out = members_action.csunesco_join_request_create(
        _service_ctx(), _join_payload())
    assert out['user_id'] == 'user-maria'
    assert out['user_id'] != 'cs-toolbox-service'


def test_join_request_carries_the_apps_note(service, session, project,
                                            monkeypatch):
    import ckan.model as model
    monkeypatch.setattr(model.User, 'get',
                        staticmethod(lambda name: _ckan_user(session, name)))
    out = members_action.csunesco_join_request_create(
        _service_ctx(), _join_payload())
    assert out['note'].startswith('I teach nearby')


def test_an_app_join_is_marked_as_coming_from_the_app(service, session,
                                                      project, monkeypatch):
    """Provenance is derived from the impersonation, not from the payload --
    the app never sends a `source` key, and a payload-supplied one would be
    attacker-controlled while carrying weight in a human decision."""
    import ckan.model as model
    monkeypatch.setattr(model.User, 'get',
                        staticmethod(lambda name: _ckan_user(session, name)))
    out = members_action.csunesco_join_request_create(
        _service_ctx(), _join_payload())
    assert out['source'] == 'app'


def test_an_unknown_username_is_a_clear_error_not_a_silent_misfiling(
        service, session, project, monkeypatch):
    import ckan.model as model
    monkeypatch.setattr(model.User, 'get', staticmethod(lambda name: None))
    with pytest.raises(tk.ValidationError):
        members_action.csunesco_join_request_create(
            _service_ctx(), _join_payload())


def test_a_non_sysadmin_cannot_impersonate(session, project, monkeypatch):
    """Only the trusted service token may act for someone else. An ordinary
    caller's `username` is ignored, never honoured and never rejected -- so it
    cannot be used to probe which accounts exist."""
    monkeypatch.setattr(tk, 'check_access', lambda *a, **k: True)
    monkeypatch.setattr(cs_auth, '_is_sysadmin', lambda context: False)
    ctx = {'user': 'mallory', 'auth_user_obj': _User('mallory')}
    out = members_action.csunesco_join_request_create(ctx, _join_payload())
    assert out['user_id'] == 'mallory'
    assert out['source'] == 'ckan'


# --------------------------------------------------------------------------- #
# csunesco_join_approve                                                        #
# ofform/backend/app/routers/cs_projects.py:1094-1103                          #
# --------------------------------------------------------------------------- #

def test_join_approve_accepts_slug_and_username(service, session, project,
                                                monkeypatch):
    """The app has neither the project UUID nor the CKAN user id, and
    db.project_member matches raw columns -- so both had to be resolved."""
    import ckan.model as model
    monkeypatch.setattr(model.User, 'get',
                        staticmethod(lambda name: _ckan_user(session, name)))
    members_action.csunesco_join_request_create(
        _service_ctx(), _join_payload())

    out = members_action.csunesco_join_approve(_service_ctx(), {
        'programme_id': 42,
        'project_slug': 'douro-basin',
        'username': 'maria',
        'approved_by': 7,          # ofform's LOCAL integer id: meaningless here
    })
    assert out['membership']['status'] == 'active'
    assert out['citizen_scientists'] == 1


def test_join_approve_records_the_caller_not_the_apps_approved_by(
        service, session, project, monkeypatch):
    """`approved_by` is an ofform-local integer. The reviewer of record must be
    the authenticated caller, never a number from the payload."""
    import ckan.model as model
    monkeypatch.setattr(model.User, 'get',
                        staticmethod(lambda name: _ckan_user(session, name)))
    members_action.csunesco_join_request_create(
        _service_ctx(), _join_payload())
    out = members_action.csunesco_join_approve(_service_ctx(), {
        'project_slug': 'douro-basin', 'username': 'maria', 'approved_by': 7,
    })
    assert out['membership']['reviewed_by'] == 'cs-toolbox-service'


# --------------------------------------------------------------------------- #
# csunesco_content_create — the cs-event date rules                            #
# ofform/backend/app/routers/cs_content.py:99-120                              #
# ofform/backend/app/routers/project_space.py:437-450                          #
# --------------------------------------------------------------------------- #

def _navl_event(**overrides):
    payload = {
        'programme_id': 42,
        'project_slug': 'douro-basin',
        'content_type': 'cs-event',
        'title': 'Sampling day',
        'body': 'Bring boots.',
        'publish_date': '2026-07-16',
        'end_date': None,
        'source': 'app',
        'author': 'maria',
    }
    payload.update(overrides)
    s = cs_schema.content_schema('cs-event')
    incoming = {k: payload[k] for k in s if k in payload}
    return tk.navl_validate(incoming, s, {'model': None, 'session': None})


def test_an_open_ended_app_event_validates():
    """`end_date: null` is what BOTH ofform content producers send when the
    event has no end."""
    _data, errors = _navl_event()
    assert not errors, errors


def test_a_single_day_app_event_validates():
    """project_space.py sends date-only ISO strings, so a one-day event has
    end == start. Strict end > start rejected the commonest event there is."""
    _data, errors = _navl_event(end_date='2026-07-16')
    assert not errors, errors


def test_a_backwards_app_event_is_still_rejected():
    _data, errors = _navl_event(end_date='2026-07-15')
    assert 'end_date' in errors


# --------------------------------------------------------------------------- #
# csunesco_register_citizen_scientist                                          #
# ofform/backend/app/services/cs_registration.py:136-144                       #
# --------------------------------------------------------------------------- #

def test_registration_accepts_the_literal_payload(service, session,
                                                  monkeypatch):
    """The app's CKAN-first registration payload, verbatim.

    Fase 1 made the WEB form stricter (fullname/DOB/gender required, username
    optional+generated). None of that may leak into this action: the payload
    below carries a username and no demographics, and must keep working
    byte-for-byte.
    """
    import ckan.model as model
    from ckanext.csunesco.logic import registration as reg_module
    from ckanext.csunesco.logic.action import registration as reg_action

    monkeypatch.setattr(model.User, 'get', staticmethod(lambda key: None))
    monkeypatch.setattr(reg_module, 'check_access', lambda *a, **k: True)

    def get_action(name):
        assert name == 'user_create'
        return lambda context, data: {'id': 'user-77', 'name': data['name']}

    monkeypatch.setattr(tk, 'get_action', get_action)

    out = reg_action.csunesco_register_citizen_scientist(_service_ctx(), {
        'username': 'koen',
        'email': 'koen@example.org',
        'password': 'water-quality-2026',
        'fullname': 'Koen Verbruggen',
        'country': 'Belgium',
    })

    assert out['status'] == 'success'
    # The app-chosen username is honoured verbatim -- never regenerated.
    assert out['username'] == 'koen'
    profile = (session.query(db.CsCitizenScientist)
               .filter(db.CsCitizenScientist.user_id == 'user-77').one())
    assert profile.country == 'Belgium'
    # Trusted server-to-server path: already verified, no token.
    assert bool(profile.email_verified) is True
    assert profile.profile_type in (None, 'citizen')


# --------------------------------------------------------------------------- #
# csunesco_project_structure_upsert                                            #
# ofform/backend/app/routers/cs_structure.py:build_structure_payload           #
# --------------------------------------------------------------------------- #

def test_project_structure_accepts_the_literal_payload(service, session,
                                                       project):
    """The app's phase-2 snapshot push, verbatim (Fase 4 lockstep contract).

    Shape produced by ``build_structure_payload``: empty fields OMITTED,
    dates as ISO strings, workplan steps carrying position and optional
    dates. The portal must accept it byte-for-byte -- its ``test_cs_sync``
    twin on the app side asserts this same shape is what gets enqueued.
    """
    from ckanext.csunesco.logic.action import structure as structure_action

    payload = {
        'programme_id': 7,
        'project_slug': 'douro-basin',
        'structure': {
            'aim': 'Push me to the portal.',
            'focus_areas': ['Community Engagement and Awareness'],
            'engagement_level': 'Participatory',
            'engagement_activities': ['Jointly generating knowledge'],
            'training_level': 'Brief training needed',
            'target_groups': ['Youth', 'Students'],
            'water_parameters': {
                'Physical water quality': ['pH', 'Temperature'],
            },
            'how_to_participate': 'Join a sampling day.',
            'incentives': ['Recognition (e.g. certificate)'],
            'local_govt_engagement': True,
            'indigenous_knowledge': True,
            'indigenous_knowledge_notes': 'Elders guide site selection.',
            'timeframe_start': '2026-09-01',
            'timeframe_end': '2027-03-01',
            'duration_of_involvement': 'Monthly',
        },
        'workplan': [
            {'title': '1. Understand the Context and Challenges',
             'kind': 'milestone', 'status': 'upcoming', 'position': 0,
             'description': "Define the water problem, who's affected, and "
                            "what's already been tried."},
            {'title': 'Sampling season', 'kind': 'step',
             'status': 'in_progress', 'position': 1,
             'starts_on': '2026-09-01', 'ends_on': '2026-11-30'},
        ],
    }
    out = structure_action.csunesco_project_structure_upsert(
        _service_ctx(), payload)
    assert out['slug'] == 'douro-basin'
    assert out['structure']['duration_of_involvement'] == 'Monthly'
    assert len(out['workplan']) == 2
    assert out['workplan'][1]['starts_on'] == '2026-09-01'


# --------------------------------------------------------------------------- #
# csunesco_project_update                                                      #
# ofform/backend/app/services/cs_details.py:build_update_payload               #
# --------------------------------------------------------------------------- #

def _project_update_payload(**overrides):
    """Literal ofform outbox body for a ficha PATCH (kind ``project_update``).

    ``programme_id`` and ``updated_by`` are ofform bookkeeping and must be
    dropped, never rejected. ``id`` is the CKAN slug (or the local slug before
    reconcile). ``slug`` is deliberately absent -- the URL is permanent.
    """
    payload = {
        'programme_id': 42,
        'id': 'douro-basin',
        'title': 'Douro Basin Freshwater',
        'short_description': 'Citizen monitoring along the Douro.',
        'initiative': 'riverwatch',
        # Empty on purpose: member-state names are validated against the
        # seeded ``member-states`` group, which this sqlite harness does not
        # populate. ofform still sends real slugs in production.
        'countries': [],
        'keywords': ['rivers', 'citizens'],
        'water_type': ['River'],
        'water_data_type': ['Physical water quality'],
        'geographic_extent': 'National',
        'locality': 'Douro valley',
        'biosphere_reserve': None,
        'participation_mode': 'open',
        'activity_status': ['Active'],
        'lead_partner_type': 'University',
        'lead_organisation': 'University of Porto',
        'contact_person': 'Ana Silva',
        'contact_email': 'ana@example.org',
        'start_date': '2026-03-01',
        'end_date': '2027-03-01',
        'updated_by': 'ana',
    }
    payload.update(overrides)
    return payload


def test_project_update_accepts_the_literal_payload(service, session, project):
    out = projects_action.csunesco_project_update(
        _service_ctx(), _project_update_payload())
    assert out['slug'] == 'douro-basin'
    assert out['title'] == 'Douro Basin Freshwater'
    assert out['initiative_group'] == 'riverwatch'
    assert out['contact_email'] == 'ana@example.org'
    assert out['keywords'] == ['rivers', 'citizens']
    assert 'programme_id' not in out
    assert 'updated_by' not in out
    assert 'slug' not in _project_update_payload()


def test_project_update_ignores_a_slug_key_so_the_url_stays_put(
        service, session, project):
    """Even if a stale client smuggles ``slug``, the action's schema drops it
    and the project's public URL does not move."""
    out = projects_action.csunesco_project_update(
        _service_ctx(), _project_update_payload(slug='renamed-in-error'))
    assert out['slug'] == 'douro-basin'


def test_project_update_partial_contact_only(service, session, project):
    """A PATCH of a single extras field must not demand ``title`` again."""
    out = projects_action.csunesco_project_update(_service_ctx(), {
        'programme_id': 42,
        'id': 'douro-basin',
        'contact_email': 'new@example.org',
        'updated_by': 'ana',
    })
    assert out['contact_email'] == 'new@example.org'
    assert out['title'] == 'Douro Basin'


# --------------------------------------------------------------------------- #
# csunesco_initiative_page_update                                              #
# ofform/backend/app/services/cs_initiatives.py:build_page_payload             #
# --------------------------------------------------------------------------- #

def _initiative_page_payload(**overrides):
    """Literal ofform outbox body after GET-draft + merge of builtins."""
    payload = {
        'initiative': 'riverwatch',
        'blocks': [
            {'type': 'initiative_hero',
             'heading': 'River Watch',
             'tagline': 'Citizen monitoring of rivers',
             'image_url': None, 'focal_x': 50, 'focal_y': 50},
            {'type': 'initiative_glance'},
            {'type': 'initiative_about',
             'html': '<p>We watch rivers together.</p>',
             'image_url': None, 'alt': ''},
            {'type': 'initiative_projects', 'intro': 'Our projects',
             'limit': 6},
            {'type': 'initiative_news', 'intro': '', 'limit': 3},
            {'type': 'initiative_events', 'intro': '', 'limit': 3},
        ],
    }
    payload.update(overrides)
    return payload


def test_initiative_page_update_accepts_the_literal_payload(service, session):
    from ckanext.csunesco.logic.action import page as page_action
    from ckanext.csunesco.logic import blocks as blocks_module

    out = page_action.csunesco_initiative_page_update(
        _service_ctx(), _initiative_page_payload())
    types = {item['type'] for item in out['draft_blocks']}
    assert set(blocks_module.INITIATIVE_DEFAULT_BLOCK_TYPES) <= types
    hero = next(b for b in out['draft_blocks']
                if b['type'] == 'initiative_hero')
    assert hero['heading'] == 'River Watch'
    about = next(b for b in out['draft_blocks']
                 if b['type'] == 'initiative_about')
    assert 'rivers together' in about['html']


def test_initiative_page_update_keeps_an_extra_block(service, session):
    """The action REPLACES the draft. ofform therefore GET-drafts, merges
    builtins, and PUTs the full list -- extras survive only if they ride back.
    This pins that a round-trip of the stored blocks keeps ``rich_text``."""
    from ckanext.csunesco.logic.action import page as page_action

    first = page_action.csunesco_initiative_page_update(
        _service_ctx(), _initiative_page_payload(blocks=[
            {'type': 'initiative_hero', 'heading': 'River Watch'},
            {'type': 'rich_text', 'html': '<p>Keep me</p>'},
        ]))
    merged = list(first['draft_blocks'])
    out = page_action.csunesco_initiative_page_update(
        _service_ctx(), {'initiative': 'riverwatch', 'blocks': merged})
    types = [item['type'] for item in out['draft_blocks']]
    assert 'rich_text' in types


def test_initiative_page_publish_accepts_the_apps_payload(service, session):
    from ckanext.csunesco.logic.action import page as page_action

    page_action.csunesco_initiative_page_update(
        _service_ctx(), _initiative_page_payload())
    out = page_action.csunesco_initiative_page_publish(
        _service_ctx(), {'initiative': 'riverwatch'})
    assert out['status'] == 'approved'
    assert out['published_blocks'] == out['draft_blocks']
