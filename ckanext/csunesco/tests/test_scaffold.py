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
    assert rendered == {str(step['step']) for step in steps}

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


def test_qrcode_is_a_declared_dependency():
    """The join block's QR must not silently vanish again.

    csunesco_qr_data_uri returns None when `qrcode` is missing and the
    template then renders no <img> at all -- a soft failure by design. Because
    the package was never declared, that soft failure was the ONLY behaviour
    any deployment ever had, portal included: the QR existed in the template
    and nowhere on screen.
    """
    setup_py = os.path.join(REPO_ROOT, 'setup.py')
    with open(setup_py, 'r') as fh:
        source = fh.read()
    assert 'qrcode' in source, 'qrcode missing from install_requires'


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
    # And the landing actually includes the snippet outside the block loop.
    landing = os.path.join(
        PKG_DIR, 'templates', 'csunesco', 'project_landing.html')
    with open(landing, 'r') as handle:
        assert 'snippets/project_structure.html' in handle.read()
