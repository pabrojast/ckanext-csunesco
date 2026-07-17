# encoding: utf-8
"""CKAN-free scaffold tests for ckanext-csunesco.

These tests deliberately use only the standard library (``os`` + ``ast``) so
they run in an environment where CKAN is NOT installed. They assert the
package structure, the plugin entry point, and that the plugin class is
defined -- without importing any runtime module that pulls in ``ckan``.
"""
import ast
import os

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
