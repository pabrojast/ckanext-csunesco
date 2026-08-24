# encoding: utf-8
"""CKAN-free structural guards for the content editor + detail templates.

Same discipline as test_scaffold: regex over the template SOURCE, stdlib only.
These pin the failure modes that made the content surfaces look broken and
that no unit test reaches because they live in a template or in the gap
between a template and the stylesheet:

* an ``<input>`` without ``type=`` matches none of the attribute-based CSS
  control selectors and renders completely unstyled (the 2026-08 bug: six of
  them, including the naked "Author" box);
* a class used by a template but absent from csunesco.css silently renders
  unstyled (the detail pages shipped with six of those);
* the editor's field ``name=`` set is the contract of ``_read_content_form``
  and of the app outbox -- renaming one silently drops data.
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
PKG_DIR = os.path.dirname(HERE)                       # ckanext/csunesco
TPL_DIR = os.path.join(PKG_DIR, 'templates', 'csunesco')

CONTENT_FORM = os.path.join(TPL_DIR, 'content_form.html')
DETAIL_BASE = os.path.join(TPL_DIR, 'content_detail_base.html')
DETAIL_TEMPLATES = [
    os.path.join(TPL_DIR, name)
    for name in ('cs-news.html', 'cs-events.html',
                 'cs-publications.html', 'cs-maps.html')
]
CSS_FILE = os.path.join(PKG_DIR, 'assets', 'css', 'csunesco.css')
WEBASSETS = os.path.join(PKG_DIR, 'assets', 'webassets.yml')


def _read(path):
    with open(path, 'r') as handle:
        return handle.read()


def test_content_form_inputs_are_typed():
    """Every <input> carries an explicit type= (the CSS selectors are
    attribute-based; a bare <input> renders unstyled)."""
    source = _read(CONTENT_FORM)
    naked = [
        tag for tag in re.findall(r'<input\b[^>]*>', source, re.DOTALL)
        if 'type=' not in tag
    ]
    assert naked == [], 'inputs without type= in content_form.html: %s' % naked


def test_content_form_carries_the_contract_names():
    """The name= set mirrors _read_content_form (views_content.py) and the
    payload the app outbox posts -- renaming one silently drops data."""
    source = _read(CONTENT_FORM)
    for name in ('title', 'content_type', 'body', 'publish_date', 'end_date',
                 'media', 'visibility', 'terria_url', 'doi', 'authors',
                 'excerpt', 'author', 'source_url', 'header_image_alt',
                 'gallery_url', 'gallery_alt', 'gallery_caption',
                 'related_link_url', 'related_link_label',
                 'attachment_url', 'attachment_label',
                 'featured', 'featured_present'):
        assert 'name="%s"' % name in source, 'missing field name=%s' % name
    # These travel as snippet args (the image picker builds the inputs).
    for token in ('header_image_url', 'header_focal_x', 'header_focal_y'):
        assert token in source, 'missing picker field %s' % token


def test_content_form_layout_guards():
    source = _read(CONTENT_FORM)
    assert 'enctype="multipart/form-data"' in source
    assert 'cs-form-card--wide' in source, 'editor must use the wide card'
    assert 'cs-hero' not in source, 'the empty hero band must stay gone'
    assert '_("Cancel")' in source, 'Cancel must exist for every scope'
    assert "{% asset 'csunesco/csunesco-css' %}" in source
    assert "{% asset 'csunesco/csunesco-js' %}" in source
    webassets = _read(WEBASSETS)
    assert 'csunesco-css' in webassets and 'csunesco-js' in webassets


def test_detail_templates_extend_the_shared_base():
    for path in DETAIL_TEMPLATES:
        source = _read(path)
        assert '{% extends "csunesco/content_detail_base.html" %}' in source, \
            '%s must extend the shared article skeleton' % os.path.basename(path)


def test_detail_classes_have_css_rules():
    """Every load-bearing class of the article pages has at least one rule --
    the pages shipped once with six classes that existed nowhere in the CSS."""
    css = _read(CSS_FILE)
    for name in ('cs-article-media', 'cs-content-type-label--news',
                 'cs-content-type-label--event',
                 'cs-content-type-label--publication',
                 'cs-content-type-label--map', 'cs-content-authors',
                 'cs-content-doi', 'cs-map-embed', 'cs-map-open',
                 'cs-media-list', 'cs-rich-text', 'cs-content-meta',
                 'cs-meta-item', 'cs-article-manage'):
        assert re.search(r'\.%s[\s,{:]' % re.escape(name), css), \
            'class .%s has no CSS rule' % name


def test_gallery_pages_load_the_view_bundle():
    """The carousel/lightbox bundle is declared and loaded by the shared base
    (conditionally, only when the item has a gallery)."""
    base = _read(DETAIL_BASE)
    assert "cs-page-view-js" in base
    assert 'cs-page-view' in _read(WEBASSETS)


# --------------------------------------------------------------------------- #
# Dropzones + tarjetas de sección (editor v2, patrón "water family")           #
# --------------------------------------------------------------------------- #

IMAGE_PICKER = os.path.join(
    TPL_DIR, 'blocks', 'edit', '_image_picker.html')
DROPZONE_SNIPPET = os.path.join(TPL_DIR, 'snippets', '_dropzone.html')
JS_FILE = os.path.join(PKG_DIR, 'assets', 'js', 'csunesco.js')


def test_dropzone_snippet_is_progressive_enhancement():
    """El div nace `hidden` (sin JS no existe) y declara su contrato data-*."""
    source = _read(DROPZONE_SNIPPET)
    assert 'data-dropzone' in source
    assert 'hidden' in source
    assert 'data-for=' in source and 'data-preview=' in source
    assert 'cs-dropzone-content' in source


def test_dropzones_target_existing_file_inputs():
    """Cada dropzone apunta (data-for/for_id) a un input file del MISMO fuente
    — un id colgando deja una zona muerta que no abre ningún picker."""
    for path, pairs in (
        (CONTENT_FORM, [("'cs-news-gallery-upload'", 'id="cs-news-gallery-upload"'),
                        ("'cs-news-attachment'", 'id="cs-news-attachment"')]),
        (IMAGE_PICKER, [("input_id ~ '-upload'", '{{ input_id }}-upload')]),
    ):
        source = _read(path)
        for snippet_arg, input_id in pairs:
            assert snippet_arg in source, \
                'missing dropzone for %s in %s' % (input_id, os.path.basename(path))
            assert input_id in source


def test_dropzone_css_contract():
    css = _read(CSS_FILE)
    assert re.search(r'\.cs-dropzone[\s,{:.]', css)
    assert re.search(
        r'\.cs-dropzone-content\s*\{[^}]*pointer-events:\s*none', css), \
        'sin pointer-events:none el dragleave parpadea sobre los hijos'
    for name in ('cs-dz-grid', 'cs-dz-chip', 'cs-dz-input-hidden',
                 'cs-editor-section', 'cs-editor-section-body',
                 'cs-editor-progress', 'cs-progress-card'):
        assert re.search(r'\.%s[\s,{:]' % re.escape(name), css), \
            'class .%s has no CSS rule' % name


def test_dropzone_js_contract():
    js = _read(JS_FILE)
    for token in ('data-dropzone', 'new DataTransfer', 'dispatchEvent',
                  'initDropzones', 'initFormProgress'):
        assert token in js, 'csunesco.js perdió %s' % token


def test_editor_sections_structure():
    """El form del editor está seccionado en tarjetas y trae el aside de
    progreso; las secciones tipadas conservan sus ids/clases del toggle."""
    source = _read(CONTENT_FORM)
    assert source.count('cs-editor-section"') >= 3  # Basics, Body, Links
    assert 'cs-content-editor' in source
    assert 'cs-editor-layout' in source
    assert 'id="cs-editor-progress"' in source
    assert 'id="cs-news-fields" class="cs-content-type-fields cs-editor-section"' in source
    assert 'id="cs-publication-fields" class="cs-content-type-fields cs-editor-section"' in source
