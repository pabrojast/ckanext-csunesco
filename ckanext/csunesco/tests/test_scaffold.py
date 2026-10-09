# encoding: utf-8
"""CKAN-free scaffold tests for ckanext-csunesco.

These tests deliberately use only the standard library (``os`` + ``ast``) so
they run in an environment where CKAN is NOT installed. They assert the
package structure, the plugin entry point, and that the plugin class is
defined -- without importing any runtime module that pulls in ``ckan``.
"""
import ast
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
PKG_DIR = os.path.dirname(HERE)                       # ckanext/csunesco
REPO_ROOT = os.path.dirname(os.path.dirname(PKG_DIR))  # repo root


def test_package_structure_exists():
    expected = [
        os.path.join(PKG_DIR, '__init__.py'),
        os.path.join(PKG_DIR, 'plugin.py'),
        os.path.join(PKG_DIR, 'db.py'),
        os.path.join(PKG_DIR, 'blueprint.py'),
        os.path.join(PKG_DIR, 'cli.py'),
        os.path.join(PKG_DIR, 'logic', '__init__.py'),
        os.path.join(PKG_DIR, 'logic', 'actions.py'),
        os.path.join(PKG_DIR, 'logic', 'auth.py'),
        os.path.join(PKG_DIR, 'logic', 'validators.py'),
        os.path.join(PKG_DIR, 'templates', 'csunesco', 'citizen-science.html'),
        os.path.join(PKG_DIR, 'assets', 'webassets.yml'),
    ]
    for path in expected:
        assert os.path.isfile(path), 'missing expected file: %s' % path


def test_setup_py_declares_entry_point():
    setup_py = os.path.join(REPO_ROOT, 'setup.py')
    with open(setup_py, 'r') as fh:
        source = fh.read()
    assert 'csunesco=ckanext.csunesco.plugin:CsunescoPlugin' in source


def test_plugin_defines_class():
    plugin_py = os.path.join(PKG_DIR, 'plugin.py')
    with open(plugin_py, 'r') as fh:
        tree = ast.parse(fh.read(), filename=plugin_py)
    class_names = [
        node.name for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
    ]
    assert 'CsunescoPlugin' in class_names


# --------------------------------------------------------------------------- #
# The staged project form: structural guards.                                  #
#                                                                              #
# CKAN-free on purpose -- these are the cheapest checks in the suite and they  #
# catch the failure modes that no unit test reaches, because they live in a    #
# template, a YAML file or the gap between two files.                          #
# --------------------------------------------------------------------------- #

PROJECT_FORM = os.path.join(
    PKG_DIR, 'templates', 'csunesco', 'project_request.html')


def _project_form_source():
    with open(PROJECT_FORM, 'r') as handle:
        return handle.read()


def test_staged_form_assets_exist():
    expected = [
        os.path.join(PKG_DIR, 'assets', 'js', 'cs-project-form.js'),
        os.path.join(PKG_DIR, 'templates', 'csunesco', 'snippets',
                     'project_facts.html'),
    ]
    for path in expected:
        assert os.path.isfile(path), 'missing expected file: %s' % path


def test_staged_form_bundle_is_declared():
    """A bundle referenced by a template but absent from webassets.yml is a
    500 at render time, not a missing script."""
    with open(os.path.join(PKG_DIR, 'assets', 'webassets.yml'), 'r') as handle:
        manifest = handle.read()
    assert 'cs-project-form-js:' in manifest
    assert 'cs-project-form-js' in _project_form_source()


def test_staged_form_posts_multipart():
    """Without this the stage-5 file input silently never arrives."""
    assert 'enctype="multipart/form-data"' in _project_form_source()


def test_staged_form_carries_the_participation_choice():
    """The spec's participation field is a REQUIRED two-way choice (open with
    a QR on the landing page vs limited to a selected group), posted as
    ``participation_mode`` radios; the action derives the legacy
    ``open_participation`` boolean from it so the app contract and the
    Fase-0 join gate keep working."""
    source = _project_form_source()
    assert 'name="participation_mode"' in source
    assert 'value="open"' in source
    assert 'value="limited"' in source


def test_blueprint_registers_the_edit_route():
    with open(os.path.join(PKG_DIR, 'blueprint.py'), 'r') as handle:
        source = handle.read()
    assert "'/project/<slug>/edit'" in source


def test_form_stages_match_the_step_map():
    """The template's ``data-step`` blocks and constants.PROJECT_FORM_STEPS
    must not drift.

    constants.py is plain data with no CKAN import, so this stays CKAN-free:
    it is parsed with ``ast``, not imported.
    """
    source = _project_form_source()

    # Which stages the template actually renders.
    rendered = set(re.findall(r'<section class="cs-step[^"]*"\s*\n?\s*'
                              r'data-step="(\d+)"', source))
    if not rendered:                       # tolerate attribute reordering
        rendered = set(re.findall(r'data-step="(\d+)"[^>]*role="group"',
                                  source))
    assert rendered, 'no data-step sections found in the form template'

    constants_py = os.path.join(PKG_DIR, 'constants.py')
    with open(constants_py, 'r') as handle:
        tree = ast.parse(handle.read(), filename=constants_py)
    steps = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if 'PROJECT_FORM_STEPS' in names:
                steps = ast.literal_eval(node.value)
    assert steps, 'PROJECT_FORM_STEPS not found in constants.py'
    # Both creation and editing follow the eight-stage proposal.
    assert rendered == {str(step['step']) for step in steps}
    assert 'Step 8 of 8 — Brand images' in source

    # And every field named by the step map has an input in the template --
    # either a literal control or a multi_select macro invocation (the macro
    # emits `name="{{ name }}"`, so the literal search cannot see it).
    for step in steps:
        for field in step['fields']:
            has_input = ('name="%s"' % field in source
                         or "multi_select('%s'" % field in source)
            assert has_input, \
                'step %s names %r but the template has no such input' % (
                    step['step'], field)


def test_join_block_has_no_qr_or_share_link_and_pillow_stays_declared():
    """The project page offers one way to join per audience: log in/register,
    or the request form. Its QR code and share link were removed (Oct 2026).

    Pillow used to be installed only as an extra of the QR library, while
    logic/uploads.py imports PIL at module load -- so it is declared on its
    own now, or every upload would break on a clean install.
    """
    join_template = os.path.join(
        PKG_DIR, 'templates', 'csunesco', 'blocks', 'builtin_join.html')
    with open(join_template, 'r') as handle:
        source = handle.read()
    for gone in ('csunesco_qr_data_uri', 'cs-join-qr', 'Share this project'):
        assert gone not in source, gone
    assert 'name="note"' in source
    with open(os.path.join(REPO_ROOT, 'setup.py'), 'r') as handle:
        setup_source = handle.read()
    assert "'Pillow'" in setup_source
    assert 'qrcode' not in setup_source


# --------------------------------------------------------------------------- #
# Citizen Scientist registration redesign: cross-file structural guards.      #
# --------------------------------------------------------------------------- #

REGISTER_FORM = os.path.join(
    PKG_DIR, 'templates', 'csunesco', 'register_citizen.html')


def _register_form_source():
    with open(REGISTER_FORM, 'r') as handle:
        return handle.read()


def test_registration_bundle_exists_and_is_declared():
    assert os.path.isfile(os.path.join(
        PKG_DIR, 'assets', 'js', 'cs-register.js'))
    with open(os.path.join(PKG_DIR, 'assets', 'webassets.yml'), 'r') as handle:
        manifest = handle.read()
    assert 'cs-register-js:' in manifest
    assert "{% asset 'csunesco/cs-register-js' %}" in _register_form_source()


def test_registration_form_carries_the_new_contract_without_password_values():
    source = _register_form_source()
    for name in ('project', 'date_of_birth', 'nationality', 'gender', 'terms'):
        assert 'name="%s"' % name in source
    password_tag = re.search(r'<input type="password" id="cs-password"[^>]*>',
                             source, re.S).group(0)
    confirm_tag = re.search(
        r'<input type="password" id="cs-confirm-password"[^>]*>',
        source, re.S).group(0)
    assert 'value=' not in password_tag
    assert 'value=' not in confirm_tag


def test_project_register_link_preserves_the_project_slug():
    join_template = os.path.join(
        PKG_DIR, 'templates', 'csunesco', 'blocks', 'builtin_join.html')
    with open(join_template, 'r') as handle:
        source = handle.read()
    assert "register_citizen', project=ctx.project.slug" in source


def test_country_picker_round_trips_values_outside_the_current_list():
    """A project's stored countries must survive an edit even when the
    member-state list is empty, degraded, or has dropped one of them.

    The select is the ONLY place `countries` round-trips through, and it used
    to be populated exclusively from `member_states`. With that list empty the
    control rendered zero options, the POST carried an empty selection, and the
    update read that as "the user cleared every country" -- so opening the edit
    form during a member-state outage and pressing Save silently wiped them.
    """
    source = _project_form_source()
    # Stored-but-unknown countries are emitted as their own selected options.
    assert 'for name in (data.countries or []) if name not in known' in source
    # And the control announces that it rendered, so an empty selection can be
    # told apart from a control that never drew.
    assert 'name="countries_present"' in source


def test_the_view_only_trusts_an_empty_country_selection_when_marked():
    """The server half of the same guard."""
    views_py = os.path.join(PKG_DIR, 'logic', 'views.py')
    with open(views_py, 'r') as handle:
        source = handle.read()
    assert "if form.get('countries_present'):" in source
    # countries must NOT be set unconditionally in the dict literal any more.
    literal = source.split('def _read_project_form')[1].split('return data')[0]
    unconditional = "'countries': [c for c in form.getlist('countries')" in \
        literal.split("if form.get('countries_present')")[0]
    assert not unconditional, 'countries is still sent unconditionally'


def test_structure_snippet_gates_the_participants_only_fields():
    """The landing's phase-2 section must consult the audience helper for the
    participants-only pieces (spec section 5): the two C flags and the whole
    D timeline/workplan. Without these calls an anonymous visitor would see
    everything the app pushed."""
    snippet = os.path.join(
        PKG_DIR, 'templates', 'csunesco', 'snippets', 'project_structure.html')
    with open(snippet, 'r') as handle:
        source = handle.read()
    assert "csunesco_field_audience_ok('timeframe_start'" in source
    assert "csunesco_field_audience_ok('local_govt_engagement'" in source
    # Public project composition is exercised with actual templates and roles
    # in test_project_page_composition; it no longer inserts this legacy snippet.


# --------------------------------------------------------------------------- #
# "Get on board" role chooser and the simplified joining flow (Oct 2026).     #
# --------------------------------------------------------------------------- #

def _template_source(*parts):
    path = os.path.join(PKG_DIR, 'templates', 'csunesco', *parts)
    with open(path, 'r') as handle:
        return handle.read()


def test_get_on_board_routes_to_both_registration_forms():
    with open(os.path.join(PKG_DIR, 'blueprint.py'), 'r') as handle:
        assert "'/get-on-board', 'get_on_board'" in handle.read()
    chooser = _template_source('get_on_board.html')
    assert "url_for('csunesco.register_citizen')" in chooser
    assert "url_for('csunesco.register_manager')" in chooser
    # The forms go back to the chooser instead of cross-linking each other.
    citizen = _template_source('register_citizen.html')
    manager = _template_source('register_manager.html')
    assert "url_for('csunesco.get_on_board')" in citizen
    assert "url_for('csunesco.get_on_board')" in manager
    assert "url_for('csunesco.register_manager')" not in citizen
    assert "url_for('csunesco.register_citizen')" not in manager


def test_manager_org_picker_lists_nothing_before_a_search():
    """A short pre-rendered list read as the complete catalogue ("it stops at
    B"). The picker only shows matches for what the visitor typed."""
    manager = _template_source('register_manager.html')
    for gone in ('cs-org-more', 'cs-org-count', 'Show more',
                 'shows only some organizations', '/api/colab/organizations'):
        assert gone not in manager, gone
    assert 'Search your organization…' in manager
    assert 'Search by full name or keyword.' in manager
    # The <select> stays: it is the submitted value and the no-JS fallback.
    assert '<select id="cs-org-name" name="org_name"' in manager
    assert '{% for org in organizations %}' in manager
    with open(os.path.join(PKG_DIR, 'assets', 'js', 'cs-register.js'),
              'r') as handle:
        script = handle.read()
    assert 'if (query.length < MIN_ORG_QUERY)' in script
    assert 'fetch(' not in script


def test_citizen_join_step_is_qr_only_and_optional():
    """Joining at sign-up happens by QR code only; there is no project list."""
    source = _register_form_source()
    assert '<select id="cs-project"' not in source
    assert 'type="hidden" id="cs-project" name="project"' in source
    assert 'cs-optional-pill' in source
    assert 'id="cs-qr"' in source and 'id="cs-join-projects"' in source
    assert "{% asset 'csunesco/cs-register-qr-js' %}" in source
    with open(os.path.join(PKG_DIR, 'assets', 'webassets.yml'), 'r') as handle:
        assert 'cs-register-qr-js:' in handle.read()
    with open(os.path.join(PKG_DIR, 'assets', 'js', 'cs-register-qr.js'),
              'r') as handle:
        scanner = handle.read()
    # Native detection first, the camera is released when the page goes away.
    for needle in ('BarcodeDetector', 'getUserMedia', 'pagehide'):
        assert needle in scanner, needle
    # The fallback decoder is vendored (no CDN) together with its license.
    vendor = os.path.join(PKG_DIR, 'public', 'csunesco', 'vendor')
    assert os.path.isfile(os.path.join(vendor, 'jsQR.js'))
    assert os.path.isfile(os.path.join(vendor, 'JSQR-LICENSE.txt'))


def test_confirmation_screens_list_the_real_next_steps():
    citizen = _template_source('register_citizen.html')
    manager = _template_source('register_manager.html')
    assert 'Check your inbox' in citizen
    assert 'cs-confirm-steps' in citizen
    assert "url_for('csunesco.resend_verification')" in citizen
    assert 'Your existing account' in manager
    assert 'Email verified' in manager
    assert 'Publication requires approval of both your PM request and your project.' in manager
