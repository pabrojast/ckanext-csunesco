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


def test_staged_form_carries_the_checkbox_marker():
    """An unchecked box submits nothing, which the server cannot tell from
    "the form did not carry this field" -- so an edit could never turn
    open_participation back off."""
    assert 'open_participation_present' in _project_form_source()


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

    # And every field named by the step map has an input in the template.
    for step in steps:
        for field in step['fields']:
            assert 'name="%s"' % field in source, \
                'step %s names %r but the template has no such input' % (
                    step['step'], field)
