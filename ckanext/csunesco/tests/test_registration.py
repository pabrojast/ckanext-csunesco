# encoding: utf-8
"""Citizen Scientist registration redesign: contract and orchestration tests."""
import datetime

import pytest

try:
    from flask import Flask, g
    import ckan.model as model
    import ckan.plugins.toolkit as tk
    from ckanext.csunesco.logic import registration
    from ckanext.csunesco.logic.action import registration as registration_action
    HAVE_CKAN = True
except Exception:  # pragma: no cover - host without CKAN
    HAVE_CKAN = False

pytestmark = pytest.mark.skipif(not HAVE_CKAN, reason='requires CKAN')


@pytest.fixture
def app(monkeypatch):
    app = Flask(__name__)
    app.secret_key = 'registration-test'
    monkeypatch.setattr(tk, '_', lambda message: message)
    return app


def test_optional_profile_normalizes_and_rejects_invalid_values():
    dob, nationality, gender = registration._parse_optional_profile({
        'date_of_birth': '1990-05-17',
        'nationality': 'cl',
        'gender': 'non_binary',
    })
    assert dob == datetime.date(1990, 5, 17)
    assert nationality == 'CL'
    assert gender == 'non_binary'

    # The two intentional-refusal sentinels (2026 mandatory-field rules).
    _dob, nationality, _gender = registration._parse_optional_profile(
        {'nationality': 'PREFER_NOT_TO_SAY'})
    assert nationality == 'PREFER_NOT_TO_SAY'
    assert registration._country_name('PREFER_NOT_TO_SAY') == \
        'Prefer not to say'

    tomorrow = datetime.date.today() + datetime.timedelta(days=1)
    with pytest.raises(tk.ValidationError):
        registration._parse_optional_profile({
            'date_of_birth': tomorrow.isoformat(),
        })
    with pytest.raises(tk.ValidationError):
        registration._parse_optional_profile({'nationality': 'XX'})
    with pytest.raises(tk.ValidationError):
        registration._parse_optional_profile({'gender': 'not-listed'})


def test_project_deep_link_accepts_slug_or_id_only():
    rows = [{'id': 'uuid-1', 'slug': 'river-x', 'title': 'River X'}]
    assert registration._selected_project(rows, 'river-x') == rows[0]
    assert registration._selected_project(rows, 'uuid-1') == rows[0]
    assert registration._selected_project(rows, 'unknown') is None


def test_project_value_resolves_toolbox_ids_and_skips_closed_projects():
    """The QR a Project Manager shares from the app carries the Toolbox id."""
    rows = [
        {'id': 'uuid-1', 'slug': 'river-x', 'title': 'River X', 'app_id': '12'},
        {'id': 'uuid-2', 'slug': '12', 'title': 'Twelve', 'app_id': '40'},
        {'id': 'uuid-3', 'slug': 'closed', 'title': 'Closed', 'app_id': '7',
         'open_participation': False},
    ]
    assert registration._selected_project(rows, '40') == rows[1]
    assert registration._selected_project(rows[:1], '12') == rows[0]
    # This portal's own links carry slugs, so a slug wins over a Toolbox id.
    assert registration._selected_project(rows, '12') == rows[1]
    # Closed participation is never selectable, by slug or by Toolbox id.
    assert registration._selected_project(rows, 'closed') is None
    assert registration._selected_project(rows, '7') is None
    # The scanner is only told about joinable projects.
    assert registration._project_refs(rows) == [
        {'slug': 'river-x', 'title': 'River X', 'app_id': '12'},
        {'slug': '12', 'title': 'Twelve', 'app_id': '40'}]


def test_unlinked_projects_get_their_toolbox_id_from_the_app(app, monkeypatch):
    """A freshly approved project has no recorded Toolbox id: its portal page
    was never published from the app. Its recruitment QR must still resolve."""
    rows = [
        {'id': 'p1', 'slug': 'river-x', 'title': 'River X', 'app_project_id': 12},
        {'id': 'p2', 'slug': 'new-project', 'title': 'New project'},
    ]
    asked = []
    monkeypatch.setattr(tk, 'get_action', lambda name: (
        lambda context, data: {'results': [dict(row) for row in rows],
                               'count': len(rows)}))
    monkeypatch.setattr(registration, '_editor_app_ids', lambda: {})
    monkeypatch.setattr(
        registration, '_toolbox_project_ids',
        lambda: asked.append(1) or {'new-project': '26', 'river-x': '99'})

    with app.test_request_context('/register'):
        g.user = ''
        projects = registration._registration_projects()
        by_slug = dict((row['slug'], row['app_id']) for row in projects)
        # The recorded link wins; the Toolbox is asked once, for the unlinked one.
        assert by_slug == {'river-x': '12', 'new-project': '26'}
        assert len(asked) == 1
        assert registration._selected_project(
            projects, '26')['slug'] == 'new-project'

        # Nothing to look up when every project is already linked.
        rows[1]['app_project_id'] = 26
        del asked[:]
        registration._registration_projects()
        assert asked == []


def test_toolbox_public_project_ids_are_cached_and_fail_soft(monkeypatch):
    from ckanext.csunesco.logic import ofform
    calls = []

    def fetch(path, timeout=None):
        calls.append(path)
        return (b'[{"id": 26, "slug": "local-slug", "ckan_slug": "portal-slug"},'
                b' {"id": 7, "slug": "only-local", "ckan_slug": null}]')

    ofform.cache_clear()
    monkeypatch.setattr(ofform, '_fetch', fetch)
    assert ofform.public_project_ids() == {'portal-slug': '26', 'only-local': '7'}
    assert ofform.public_project_ids() == {'portal-slug': '26', 'only-local': '7'}
    assert calls == ['/public/projects']

    def broken(path, timeout=None):
        raise ofform.OfformError('network error')

    ofform.cache_clear()
    monkeypatch.setattr(ofform, '_fetch', broken)
    assert ofform.public_project_ids() == {}
    ofform.cache_clear()


def test_limiter_counts_successful_consumptions_and_releases(monkeypatch):
    limiter = registration._RegistrationLimiter()
    clock = {'now': 10.0}
    monkeypatch.setattr(registration.time, 'monotonic', lambda: clock['now'])

    assert limiter.consume('ip', 2, 60) is None
    clock['now'] = 11.0
    assert limiter.consume('ip', 2, 60) is None
    clock['now'] = 12.0
    assert limiter.consume('ip', 2, 60) == 58
    clock['now'] = 71.0
    assert limiter.consume('ip', 2, 60) is None


def _post_web_registration(app, monkeypatch, project):
    projects = [{'id': 'p1', 'slug': 'river-x', 'title': 'River X',
                 'app_id': '12'}]
    captured = {'join': None, 'created': None, 'mail': None}

    monkeypatch.setattr(registration, '_registration_retry_after', lambda: None)
    monkeypatch.setattr(registration, '_recaptcha_configured', lambda: False)
    monkeypatch.setattr(registration, '_registration_projects', lambda: projects)
    monkeypatch.setattr(registration, '_render', lambda values: values)
    monkeypatch.setattr(registration.secrets, 'token_urlsafe', lambda size: 'token')
    monkeypatch.setattr(model.User, 'get', staticmethod(lambda value: type(
        'User', (), {'id': 'user-1', 'name': 'maria',
                    'is_anonymous': False, 'sysadmin': False})()))

    def create(context, data, verification_token=None):
        captured['created'] = (data, verification_token)
        return {'id': 'user-1', 'name': 'maria'}

    def get_action(name):
        assert name == 'csunesco_join_request_create'
        return lambda context, data: captured.update(join=data) or {
            'status': 'pending'}

    monkeypatch.setattr(registration, 'create_citizen_scientist', create)
    monkeypatch.setattr(tk, 'get_action', get_action)
    monkeypatch.setattr(
        registration, '_send_verification_email',
        lambda name, email, token, language=None: captured.update(mail=(name, email, token), mail_language=language))

    with app.test_request_context('/register', method='POST', data={
        'email': 'maria@example.org',
        'username': 'Maria',
        'password': 'long-enough',
        'confirm_password': 'long-enough',
        'terms': 'yes',
        'motivation': 'I want to monitor our river with local schools.',
        'fullname': 'Maria Example',
        'language': 'fr',
        'date_of_birth': '1990-05-17',
        'nationality': 'cl',
        'gender': 'female',
        'project': project,
    }):
        g.user = ''
        out = registration.register_citizen()
    return captured, out


def test_web_registration_creates_immediate_join_and_keeps_verification(
        app, monkeypatch):
    captured, out = _post_web_registration(app, monkeypatch, 'river-x')

    data, token = captured['created']
    assert token == 'token'
    assert data['date_of_birth'] == datetime.date(1990, 5, 17)
    assert data['nationality'] == 'CL'
    assert data['terms_accepted'] is True
    assert data['registration_project_slug'] == 'river-x'
    assert captured['join'] == {'project_id': 'p1'}
    assert captured['mail'] == ('Maria Example', 'maria@example.org', 'token')
    assert captured['mail_language'] == 'fr'
    assert out['pending_verification'] is True
    assert out['join_project']['slug'] == 'river-x'


def test_web_registration_stores_the_canonical_slug_for_a_toolbox_id(
        app, monkeypatch):
    captured, out = _post_web_registration(app, monkeypatch, '12')
    assert captured['created'][0]['registration_project_slug'] == 'river-x'
    assert captured['join'] == {'project_id': 'p1'}
    assert out['join_project']['slug'] == 'river-x'


def test_web_registration_drops_an_unknown_project_value(app, monkeypatch):
    captured, out = _post_web_registration(app, monkeypatch, 'no-such-project')
    # The account is still created; nothing forged is stored or joined.
    assert captured['created'][0]['registration_project_slug'] == ''
    assert captured['join'] is None
    assert out['pending_verification'] is True
    assert out['join_project'] is None


def test_rate_limited_post_is_actionable_429(app, monkeypatch):
    monkeypatch.setattr(registration, '_registration_retry_after', lambda: 37)
    monkeypatch.setattr(registration, '_render', lambda values: values)
    with app.test_request_context('/register', method='POST'):
        out = registration.register_citizen()
    body, status, headers = out
    assert status == 429
    assert headers['Retry-After'] == '37'
    assert body['errors']['code'] == 'registration_too_many_attempts'


def test_ofform_legacy_action_payload_remains_valid(monkeypatch):
    captured = {}
    monkeypatch.setattr(tk, 'check_access', lambda *args, **kwargs: True)
    monkeypatch.setattr(model.User, 'get', staticmethod(lambda value: None))

    def create(context, data, **kwargs):
        captured.update(data)
        return {'name': 'maria', 'id': 'user-1'}

    monkeypatch.setattr(
        registration_action, 'create_citizen_scientist', create)
    out = registration_action.csunesco_register_citizen_scientist({}, {
        'email': 'maria@example.org',
        'username': 'maria',
        'password': 'long-enough',
        'motivation': 'I want to monitor our river with local schools.',
        'fullname': 'Maria',
        'country': 'Chile',
    })
    assert out['status'] == 'success'
    assert captured['country'] == 'Chile'
    assert captured['date_of_birth'] is None
    assert captured['terms_accepted'] is False


def test_ofform_action_accepts_optional_profile_fields(monkeypatch):
    captured = {}
    monkeypatch.setattr(tk, 'check_access', lambda *args, **kwargs: True)
    monkeypatch.setattr(model.User, 'get', staticmethod(lambda value: None))
    monkeypatch.setattr(
        registration_action, 'create_citizen_scientist',
        lambda context, data, **kwargs: captured.update(data) or {
            'name': 'maria', 'id': 'user-1'})

    registration_action.csunesco_register_citizen_scientist({}, {
        'email': 'maria@example.org', 'username': 'maria',
        'password': 'long-enough', 'date_of_birth': '1990-05-17',
        'nationality': 'CL', 'gender': 'female', 'terms_accepted': True,
    })
    assert captured['nationality'] == 'CL'
    assert captured['gender'] == 'female'
    assert captured['terms_accepted'] is True


# --------------------------------------------------------------------------- #
# Username auto-generation (spec: optional, generated from the name)          #
# --------------------------------------------------------------------------- #

def test_generate_username_slugifies_and_dedupes(monkeypatch):
    taken = {'maria-perez', 'maria-perez-2'}
    monkeypatch.setattr(
        model.User, 'get',
        staticmethod(lambda name: object() if name in taken else None))
    assert registration._generate_username('José Núñez') == 'jose-nunez'
    assert registration._generate_username('María! Pérez') == 'maria-perez-3'
    # Fallback to the email local part, then to a generic base.
    assert registration._generate_username('', 'sam@example.org') == 'sam'
    assert registration._generate_username('', '') == 'citizen'


def test_create_citizen_scientist_generates_a_username_when_blank(monkeypatch):
    created = {}
    from ckanext.csunesco import db
    monkeypatch.setattr(db, 'get_or_create_citizen_scientist', lambda *a, **k: None)
    monkeypatch.setattr(model.Session, 'commit', lambda: None)
    monkeypatch.setattr(registration, 'check_access', lambda *a, **k: True)
    monkeypatch.setattr(model.User, 'get', staticmethod(lambda name: None))
    monkeypatch.setattr(
        tk, 'get_action',
        lambda name: lambda context, data: created.update(data) or {
            'id': 'user-1', 'name': data['name']})
    out = registration.create_citizen_scientist({}, {
        'email': 'ana@example.org',
        'motivation': 'I want to monitor our river with local schools.',
        'fullname': 'Ana Flores',
        'password': 'long-enough',
    })
    assert created['name'] == 'ana-flores'
    assert out['name'] == 'ana-flores'


# --------------------------------------------------------------------------- #
# Web form required-ness (view-only strictness; the API action stays lenient) #
# --------------------------------------------------------------------------- #

def test_web_registration_requires_the_demographic_block(app, monkeypatch):
    """fullname, DOB, gender AND nationality (2026 reporting rules: the
    Member-State disaggregation made nationality mandatory too)."""
    monkeypatch.setattr(registration, '_registration_retry_after', lambda: None)
    monkeypatch.setattr(registration, '_recaptcha_configured', lambda: False)
    monkeypatch.setattr(registration, '_render', lambda values: values)
    monkeypatch.setattr(
        registration, 'create_citizen_scientist',
        lambda *a, **k: pytest.fail('must not reach account creation'))

    complete = {
        'email': 'maria@example.org',
        'password': 'long-enough',
        'confirm_password': 'long-enough',
        'terms': 'yes',
        'motivation': 'I want to monitor our river with local schools.',
        'fullname': 'Maria Example',
        'date_of_birth': '1990-05-17',
        'gender': 'female',
        'nationality': 'PREFER_NOT_TO_SAY',
    }
    for missing in ('fullname', 'date_of_birth', 'gender', 'nationality'):
        data = dict(complete)
        data[missing] = ''
        with app.test_request_context('/register', method='POST', data=data):
            g.user = ''
            out = registration.register_citizen()
        assert out['errors']['code'] == 'registration_required', missing
        assert missing in out['errors']['fields']


# --------------------------------------------------------------------------- #
# Project Manager registration (spec section 3)                               #
# --------------------------------------------------------------------------- #

_MANAGER_FORM = {
    'email': 'pm@example.org',
    'password': 'long-enough',
    'confirm_password': 'long-enough',
    'motivation': 'I want to monitor our river with local schools.',
    'fullname': 'Paula Manager',
    'date_of_birth': '1985-02-03',
    'gender': 'female',
    'nationality': 'CL',
    'org_type': 'university',
    'org_name': '__new__',
    'new_org_name': 'Hydrology Lab',
    'org_title': 'Research lead',
    'responsibilities': 'yes',
}


def _manager_post(app, monkeypatch, overrides=None):
    captured = {}
    from ckanext.csunesco.logic import onboarding
    monkeypatch.setattr(onboarding, 'exact_org_match', lambda title: False)
    monkeypatch.setattr(registration, '_registration_retry_after', lambda: None)
    monkeypatch.setattr(registration, '_recaptcha_configured', lambda: False)
    monkeypatch.setattr(registration, '_render_manager', lambda values: values)
    monkeypatch.setattr(registration, '_organization_options',
                        lambda: [{'name': 'existing-org',
                                  'title': 'Existing Org'}])
    monkeypatch.setattr(registration.secrets, 'token_urlsafe',
                        lambda size: 'token')
    monkeypatch.setattr(
        registration, '_send_verification_email',
        lambda name, email, token, language=None: captured.update(mail=(email, token), mail_language=language))

    def create(context, data, verification_token=None):
        captured.update(created=data, token=verification_token)
        return {'id': 'user-1', 'name': 'paula'}

    monkeypatch.setattr(registration, 'create_citizen_scientist', create)
    form = dict(_MANAGER_FORM)
    form.update(overrides or {})
    with app.test_request_context('/register-pm', method='POST', data=form):
        g.user = ''
        out = registration.register_manager()
    return out, captured


def test_manager_registration_new_org_derives_admin(app, monkeypatch):
    out, captured = _manager_post(app, monkeypatch, {'language': 'fr'})
    manager = captured['created']['manager']
    assert manager['org_name_requested'] == 'Hydrology Lab'
    assert manager['org_id'] is None
    assert manager['org_role'] == 'admin'
    assert manager['org_type'] == 'university'
    assert captured['token'] == 'token'
    assert captured['mail'] == ('pm@example.org', 'token')
    assert captured['mail_language'] == 'fr'
    assert out['pending_verification'] is True


def test_manager_registration_existing_org_requests_member(app, monkeypatch):
    out, captured = _manager_post(app, monkeypatch, {
        'org_name': 'existing-org', 'new_org_name': ''})
    manager = captured['created']['manager']
    assert manager['org_id'] == 'existing-org'
    assert manager['org_name_requested'] is None
    assert manager['org_role'] == 'member'


def test_manager_registration_requires_the_org_block(app, monkeypatch):
    for missing, value in (('org_type', ''), ('org_name', ''),
                           ('org_title', ''), ('responsibilities', ''),
                           ('new_org_name', ''), ('nationality', '')):
        out, captured = _manager_post(app, monkeypatch, {missing: value})
        expected = {'responsibilities': 'terms_required', 'nationality': 'required'}.get(missing, 'organization_invalid')
        assert out['errors']['code'] == 'registration_' + expected, missing
        assert missing in out['errors']['fields']
        assert 'created' not in captured, missing


def test_manager_registration_rejects_an_unknown_existing_org(app, monkeypatch):
    out, captured = _manager_post(app, monkeypatch, {
        'org_name': 'forged-org', 'new_org_name': ''})
    assert out['errors']['code'] == 'registration_organization_invalid'
    assert 'created' not in captured


# --------------------------------------------------------------------------- #
# The manager double gate at /verify                                          #
# --------------------------------------------------------------------------- #

def _verify_profile(profile_type):
    import datetime as _dt
    return type('Profile', (), {
        'user_id': 'user-1',
        'profile_type': profile_type,
        'token_created': _dt.datetime.utcnow(),
    })()


def test_verify_activates_a_citizen_account(monkeypatch):
    from ckanext.csunesco import db as cs_db
    profile = _verify_profile('citizen')
    activated = {'called': False}
    user = type('User', (), {
        'state': 'pending', 'activate': lambda self: activated.update(called=True)})()
    monkeypatch.setattr(cs_db, 'get_citizen_scientist_by_token',
                        lambda token: profile)
    monkeypatch.setattr(cs_db, 'verify_citizen_scientist', lambda p: p)
    monkeypatch.setattr(model.User, 'get', staticmethod(lambda uid: user))
    monkeypatch.setattr(model.Session, 'commit', lambda: None)
    monkeypatch.setattr(registration, '_render_verify', lambda state, project_slug=None: state)
    assert registration.verify_citizen('tok') == 'ok'
    assert activated['called'] is True


def test_verify_activates_manager_login_without_approving_application(monkeypatch):
    from ckanext.csunesco import db as cs_db
    profile = _verify_profile('manager')
    monkeypatch.setattr(cs_db, 'get_citizen_scientist_by_token',
                        lambda token: profile)
    monkeypatch.setattr(cs_db, 'verify_citizen_scientist', lambda p: p)
    activated = []
    user = type('User', (), {'state': 'pending', 'activate': lambda self: activated.append(True)})()
    monkeypatch.setattr(model.User, 'get', staticmethod(lambda uid: user))
    monkeypatch.setattr(model.Session, 'commit', lambda: None)
    monkeypatch.setattr(registration, '_render_verify', lambda state, project_slug=None: state)
    assert registration.verify_citizen('tok') == 'manager_pending'
    assert activated == [True]
    assert not getattr(profile, 'manager_decision', None)


def test_resend_verification_is_rate_limited(app, monkeypatch):
    """El reenvío era anónimo, ilimitado y rotaba el token de la víctima.

    Un bucle contra una dirección pendiente conocida mandaba correo sin tope y
    dejaba a esa persona sin poder verificarse: cada petición invalidaba el
    enlace que tenía abierto. Ahora consume el MISMO cupo que el alta.
    """
    monkeypatch.setattr(registration, '_registration_retry_after', lambda: 37)
    rendered = []
    monkeypatch.setattr(
        registration.tk, 'render',
        lambda template, extra_vars=None: rendered.append(
            (template, extra_vars)) or 'PAGE')

    with app.test_request_context('/verify/resend', method='POST',
                                  data={'email': 'victim@example.org'}):
        out = registration.resend_verification()

    body, status, headers = out
    assert status == 429
    assert headers['Retry-After'] == '37'
    # Misma página "ya te hemos mandado un enlace": el 429 no puede convertirse
    # en un oráculo de qué direcciones existen.
    assert body == 'PAGE'
    assert rendered[-1][1] == {'sent': True}


def test_resend_verification_does_not_touch_tokens_when_limited(app, monkeypatch):
    """Y sobre todo: estando limitado no llega a rotar ningún token."""
    monkeypatch.setattr(registration, '_registration_retry_after', lambda: 5)
    monkeypatch.setattr(registration.tk, 'render',
                        lambda template, extra_vars=None: 'PAGE')

    def _explode(*args, **kwargs):  # pragma: no cover - debe no llamarse
        raise AssertionError('no se puede consultar usuarios estando limitado')

    monkeypatch.setattr(model.Session, 'query', _explode)
    with app.test_request_context('/verify/resend', method='POST',
                                  data={'email': 'victim@example.org'}):
        _body, status, _headers = registration.resend_verification()
    assert status == 429
