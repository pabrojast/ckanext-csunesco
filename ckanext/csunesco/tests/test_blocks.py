# encoding: utf-8
"""Unit tests for ``logic/blocks.py`` -- pure, NO CKAN, NO database.

The contract under test is that :func:`normalize_blocks` is **total**: it is the
only gate between an untrusted payload (a POST body, or JSON that has been
sitting in the database since an older release) and a public page rendered with
``| safe``. It must never raise, never let an unknown type through and never let
a value out of range.

Allowlist assertions need ``bleach``; without it the sanitizer fails closed by
stripping every tag, which is asserted separately. The container harness always
has bleach, so the full set runs there.
"""
import json

import pytest

from ckanext.csunesco.logic import blocks as b

try:
    import bleach  # noqa: F401
    HAVE_BLEACH = True
except ImportError:  # pragma: no cover - exercised on hosts without bleach
    HAVE_BLEACH = False

needs_bleach = pytest.mark.skipif(not HAVE_BLEACH, reason='requires bleach')


# --------------------------------------------------------------------------- #
# The registry itself                                                         #
# --------------------------------------------------------------------------- #

def test_registry_is_self_consistent():
    for key, block_type in b.BLOCK_TYPES.items():
        assert block_type.key == key
        assert block_type.max_instances >= 1
        assert block_type.snippet == 'csunesco/blocks/%s.html' % key
    # Built-ins are toggled with `hidden`, never added from the palette.
    for key in b.BUILTIN_TYPES:
        assert not b.BLOCK_TYPES[key].addable
    assert set(b.DEFAULT_BLOCK_TYPES) <= set(b.BUILTIN_TYPES)


def test_default_blocks_match_the_pre_block_landing_order():
    types = [block['type'] for block in b.default_blocks()]
    assert types == list(b.DEFAULT_BLOCK_TYPES)


def test_default_blocks_survive_a_second_normalize_unchanged():
    """Guards against the registry and the default list drifting apart."""
    blocks = b.default_blocks()
    assert b.normalize_blocks(blocks) == blocks


def test_default_blocks_are_not_shared_mutable_state():
    first = b.default_blocks()
    first[0]['title'] = 'mutated'
    assert b.default_blocks()[0]['title'] == ''


# --------------------------------------------------------------------------- #
# normalize: the envelope                                                     #
# --------------------------------------------------------------------------- #

def test_unknown_type_is_dropped_without_raising():
    out = b.normalize_blocks([
        {'type': 'rich_text', 'html': 'a'},
        {'type': 'from_a_future_release'},
        {'type': 'callout'},
    ])
    assert [block['type'] for block in out] == ['rich_text', 'callout']


def test_non_dict_entries_and_non_list_input_are_survivable():
    assert b.normalize_blocks(['nope', 42, None, {'type': 'callout'}]) != []
    assert b.normalize_blocks('not a list') == []
    assert b.normalize_blocks(None) == []
    assert b.normalize_block('nope') is None


def test_unknown_keys_are_stripped():
    out = b.normalize_block({'type': 'callout', 'evil': '<script>',
                             'onclick': 'x'})
    assert 'evil' not in out and 'onclick' not in out
    assert set(out) == {'id', 'type', 'title', 'hidden', 'width',
                        'tone', 'html', 'cta_label', 'cta_url'}


def test_id_is_generated_once_and_then_stable():
    first = b.normalize_block({'type': 'callout'})
    assert b._ID_RE.match(first['id'])
    assert b.normalize_block(first)['id'] == first['id']
    # A forged id that is not our format is replaced, not trusted.
    forged = b.normalize_block({'type': 'callout', 'id': '../../etc/passwd'})
    assert b._ID_RE.match(forged['id'])


def test_title_is_plain_text_and_truncated():
    out = b.normalize_block({'type': 'callout',
                             'title': '<b>Hi</b>\n\n  there'})
    assert out['title'] == 'Hi there'
    long_title = b.normalize_block({'type': 'callout', 'title': 'x' * 500})
    assert len(long_title['title']) == b.MAX_TITLE


def test_hidden_accepts_html_checkbox_values():
    assert b.normalize_block({'type': 'callout', 'hidden': 'on'})['hidden']
    assert b.normalize_block({'type': 'callout', 'hidden': 'true'})['hidden']
    assert not b.normalize_block({'type': 'callout'})['hidden']
    assert not b.normalize_block({'type': 'callout', 'hidden': ''})['hidden']


def test_width_falls_back_to_full():
    assert b.normalize_block({'type': 'callout', 'width': 'narrow'})['width'] \
        == 'narrow'
    assert b.normalize_block({'type': 'callout', 'width': 'huge'})['width'] \
        == 'full'


# --------------------------------------------------------------------------- #
# normalize: caps                                                             #
# --------------------------------------------------------------------------- #

def test_max_instances_drops_the_extras_in_document_order():
    raw = [{'type': 'chart', 'caption': 'c%d' % i} for i in range(11)]
    out = b.normalize_blocks(raw)
    assert len(out) == b.BLOCK_TYPES['chart'].max_instances == 6
    assert [block['caption'] for block in out] == ['c%d' % i for i in range(6)]


def test_max_blocks_caps_the_page():
    raw = ([{'type': 'rich_text'}] * 20 + [{'type': 'callout'}] * 6
           + [{'type': 'image'}] * 8 + [{'type': 'video'}] * 4
           + [{'type': 'chart'}] * 6 + [{'type': 'stats'}] * 3
           + [{'type': 'content_list'}] * 4
           + [{'type': 'observation_map'}] * 4)
    out = b.normalize_blocks(raw, max_blocks=10)
    assert len(out) == 10


def test_blocks_from_json_rejects_an_oversized_payload_before_parsing():
    huge = json.dumps([{'type': 'rich_text', 'html': 'x' * b.MAX_JSON_BYTES}])
    assert len(huge.encode('utf-8')) > b.MAX_JSON_BYTES
    assert b.blocks_from_json(huge) == []
    assert b.blocks_from_json(huge, default=['sentinel']) == ['sentinel']


def test_blocks_from_json_is_fail_soft():
    assert b.blocks_from_json('{not json') == []
    assert b.blocks_from_json('{"a": 1}') == []       # an object, not a list
    assert b.blocks_from_json('') == []
    assert b.blocks_from_json(None, default=['d']) == ['d']
    assert b.blocks_from_json(b'[{"type":"callout"}]')[0]['type'] == 'callout'


def test_blocks_json_round_trip():
    blocks = b.normalize_blocks([{'type': 'rich_text', 'html': '<p>hi</p>'}])
    assert b.blocks_from_json(b.blocks_to_json(blocks)) == blocks


def test_oversized_reports_the_storage_cap():
    assert not b.oversized(b.default_blocks())
    assert b.oversized([{'type': 'rich_text', 'html': 'x' * b.MAX_JSON_BYTES}])


# --------------------------------------------------------------------------- #
# normalize: rich text                                                        #
# --------------------------------------------------------------------------- #

@needs_bleach
def test_rich_text_strips_every_dangerous_construct():
    dirty = ('<p>ok</p><script>alert(1)</script>'
             '<iframe src="https://evil.test"></iframe>'
             '<img src="https://evil.test/pixel.gif">'
             '<a href="javascript:alert(1)">x</a>'
             '<b onerror="alert(1)">bold</b>'
             '<p class="x" style="color:red">styled</p>')
    html = b.normalize_block({'type': 'rich_text', 'html': dirty})['html']
    assert '<script' not in html and 'alert(1)' not in html
    assert '<iframe' not in html
    # No <img>: the image block is the ONE validated image ingress.
    assert '<img' not in html
    assert 'javascript:' not in html
    assert 'onerror' not in html
    assert 'class=' not in html and 'style=' not in html
    assert '<p>ok</p>' in html and '<b>bold</b>' in html


@needs_bleach
def test_rich_text_keeps_headings_tables_and_scope():
    dirty = ('<h2>too big</h2><h3>ok</h3><h4>ok</h4>'
             '<table><caption>c</caption><thead><tr>'
             '<th scope="col">Site</th></tr></thead>'
             '<tbody><tr><td>A</td></tr></tbody></table><hr>')
    html = b.normalize_block({'type': 'rich_text', 'html': dirty})['html']
    # h2 is reserved for the block title, so the heading outline stays sane.
    assert '<h2>' not in html and 'too big' in html
    assert '<h3>ok</h3>' in html
    assert '<table>' in html and 'scope="col"' in html
    assert '<hr>' in html or '<hr/>' in html


def test_rich_text_is_truncated_after_sanitizing():
    out = b.normalize_block({'type': 'rich_text', 'html': 'a' * 60_000})
    assert len(out['html']) == b.MAX_RICH_TEXT


def test_rich_text_without_bleach_fails_closed():
    if HAVE_BLEACH:
        pytest.skip('bleach installed: the fail-closed path cannot be reached')
    html = b.normalize_block({'type': 'rich_text',
                              'html': '<p>text</p>'})['html']
    assert '<' not in html


# --------------------------------------------------------------------------- #
# normalize: URLs and video                                                   #
# --------------------------------------------------------------------------- #

def test_image_items_must_be_https_and_bad_ones_are_dropped():
    out = b.normalize_block({'type': 'image', 'items': [
        {'url': 'https://ok.test/a.jpg', 'alt': 'A'},
        {'url': 'http://insecure.test/b.jpg'},
        {'url': 'javascript:alert(1)'},
        {'url': 'data:image/png;base64,AAAA'},
        {'url': ''},
    ]})
    assert [item['url'] for item in out['items']] == ['https://ok.test/a.jpg']


def test_image_items_are_capped():
    items = [{'url': 'https://ok.test/%d.jpg' % i} for i in range(20)]
    assert len(b.normalize_block({'type': 'image', 'items': items})['items']) == 12


def test_callout_cta_accepts_internal_paths_but_not_protocol_relative():
    def cta(value):
        return b.normalize_block({'type': 'callout', 'cta_url': value})['cta_url']

    assert cta('/citizen-science/projects') == '/citizen-science/projects'
    assert cta('https://unesco.org') == 'https://unesco.org'
    assert cta('http://unesco.org') == 'http://unesco.org'
    # "//evil.test" is protocol-relative: a naive "starts with /" check would
    # send visitors off-site.
    assert cta('//evil.test/x') == ''
    assert cta('javascript:alert(1)') == ''
    assert cta('mailto:a@b.test') == ''


@pytest.mark.parametrize('url', [
    'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
    'https://youtu.be/dQw4w9WgXcQ',
    'https://www.youtube.com/embed/dQw4w9WgXcQ',
    'https://www.youtube.com/shorts/dQw4w9WgXcQ',
    'https://m.youtube.com/watch?v=dQw4w9WgXcQ&t=30s',
])
def test_every_youtube_url_form_yields_the_same_id(url):
    assert b.parse_video(url) == ('youtube', 'dQw4w9WgXcQ')


@pytest.mark.parametrize('url', [
    'https://evil.test/x?v=dQw4w9WgXcQ',
    'https://youtube.com.evil.test/watch?v=dQw4w9WgXcQ',
    'http://www.youtube.com/watch?v=dQw4w9WgXcQ',   # not https
    'https://www.youtube.com/watch?v=',
    'javascript:alert(1)',
    '',
])
def test_a_non_video_url_yields_no_embed(url):
    assert b.parse_video(url) == ('', '')


def test_vimeo_and_direct_files_are_recognised():
    assert b.parse_video('https://vimeo.com/123456789') == ('vimeo', '123456789')
    assert b.parse_video('https://player.vimeo.com/video/123456789') \
        == ('vimeo', '123456789')
    assert b.parse_video('https://cdn.test/clip.mp4') == ('file', '')


def test_video_block_keeps_a_bad_url_but_exposes_no_id():
    """The block survives so the PM can fix it; nothing embeddable is stored."""
    out = b.normalize_block({'type': 'video', 'url': 'https://evil.test/x?v=a'})
    assert out['provider'] == '' and out['video_id'] == ''


# --------------------------------------------------------------------------- #
# normalize: enums and numbers                                                #
# --------------------------------------------------------------------------- #

def test_chart_values_are_clamped_to_their_domains():
    out = b.normalize_block({'type': 'chart', 'chart': 'pyramid',
                             'mode': 'sql', 'agg': 'drop table',
                             'field': 'ph; DROP', 'height': 9999,
                             'range': 'forever', 'bucket': 'century',
                             'data_source_id': 'a b/c'})
    assert out['chart'] == 'line' and out['mode'] == 'count'
    assert out['agg'] == 'mean' and out['field'] == ''
    assert out['height'] == 600
    assert out['range'] == 'all' and out['bucket'] == 'auto'
    assert out['data_source_id'] == ''


def test_chart_keeps_valid_values():
    out = b.normalize_block({
        'type': 'chart', 'chart': 'bar', 'mode': 'numeric', 'field': 'ph',
        'agg': 'max', 'group_by': 'site', 'bucket': 'month', 'range': '1y',
        'height': 280, 'data_source_id': 'abc-123'})
    assert out['field'] == 'ph' and out['group_by'] == 'site'
    assert out['height'] == 280 and out['agg'] == 'max'


def test_chart_height_floor_applies():
    assert b.normalize_block({'type': 'chart', 'height': 10})['height'] == 200
    assert b.normalize_block({'type': 'chart', 'height': 'tall'})['height'] == 320


def test_stats_live_sources_discard_the_stored_value():
    out = b.normalize_block({'type': 'stats', 'items': [
        {'source': 'observations', 'value': '9999', 'label': 'Obs'},
        {'source': 'manual', 'value': '42', 'label': 'Schools'},
        {'source': 'made_up', 'value': '7', 'label': 'X'},
    ]})
    # A live counter must never be served from a stale number in the JSON.
    assert out['items'][0] == {'source': 'observations', 'value': '',
                               'label': 'Obs'}
    assert out['items'][1]['value'] == '42'
    assert out['items'][2]['source'] == 'manual'


def test_stats_items_are_capped_at_four():
    items = [{'source': 'manual', 'value': str(i), 'label': 'L'}
             for i in range(9)]
    assert len(b.normalize_block({'type': 'stats', 'items': items})['items']) == 4


def test_datasets_list_parses_references_from_a_textarea():
    out = b.normalize_block({'type': 'datasets_list', 'source': 'ids',
                             'ids': 'cs-data-a-1\ncs-data-b-2, bad name!'})
    # "bad name!" is one invalid entry, not a valid "bad" plus junk: splitting
    # on any whitespace would turn a typo into a wrong dataset reference.
    assert out['ids'] == ['cs-data-a-1', 'cs-data-b-2']


def test_datasets_list_caps_the_reference_count():
    out = b.normalize_block({'type': 'datasets_list',
                             'ids': '\n'.join('ds-%d' % i for i in range(30))})
    assert len(out['ids']) == 10


def test_content_list_limit_is_bounded():
    assert b.normalize_block({'type': 'content_list',
                              'limit': 99})['limit'] == 12
    assert b.normalize_block({'type': 'content_list',
                              'limit': 0})['limit'] == 1


# --------------------------------------------------------------------------- #
# Form parsing                                                                #
# --------------------------------------------------------------------------- #

def test_blocks_from_form_handles_non_contiguous_indices():
    """A client-side delete leaves gaps; indices only ever order, never count."""
    pairs = [
        ('blocks[0][type]', 'rich_text'), ('blocks[0][title]', 'First'),
        ('blocks[2][type]', 'callout'), ('blocks[2][title]', 'Second'),
        ('blocks[7][type]', 'rich_text'), ('blocks[7][title]', 'Third'),
    ]
    out = b.blocks_from_form(pairs)
    assert [block['title'] for block in out] == ['First', 'Second', 'Third']


def test_blocks_from_form_orders_numerically_not_lexically():
    pairs = [('blocks[10][type]', 'callout'), ('blocks[10][title]', 'ten'),
             ('blocks[9][type]', 'callout'), ('blocks[9][title]', 'nine')]
    assert [block['title'] for block in b.blocks_from_form(pairs)] \
        == ['nine', 'ten']


def test_blocks_from_form_reads_nested_repeatable_items():
    pairs = [
        ('blocks[0][type]', 'image'),
        ('blocks[0][items][1][url]', 'https://ok.test/b.jpg'),
        ('blocks[0][items][1][alt]', 'B'),
        ('blocks[0][items][0][url]', 'https://ok.test/a.jpg'),
        ('blocks[0][items][0][alt]', 'A'),
    ]
    items = b.blocks_from_form(pairs)[0]['items']
    assert [item['alt'] for item in items] == ['A', 'B']


def test_blocks_from_form_ignores_foreign_keys():
    pairs = [('csrf_token', 'x'), ('op', 'save'), ('blocks', 'junk'),
             ('blocks[0][type]', 'callout'), ('blocks[x][type]', 'callout')]
    assert len(b.blocks_from_form(pairs)) == 1


def test_blocks_from_form_tolerates_empty_input():
    assert b.blocks_from_form([]) == []
    assert b.blocks_from_form(None) == []


# --------------------------------------------------------------------------- #
# Editor operations                                                           #
# --------------------------------------------------------------------------- #

def _three():
    return b.normalize_blocks([{'type': 'rich_text', 'title': 'A'},
                               {'type': 'callout', 'title': 'B'},
                               {'type': 'chart', 'title': 'C'}])


def _titles(blocks):
    return [block['title'] for block in blocks]


def test_parse_op_defaults_to_save():
    assert b.parse_op('') == ('save', '')
    assert b.parse_op(None) == ('save', '')
    assert b.parse_op('drop_table:1') == ('save', '')
    assert b.parse_op('move_up:3') == ('move_up', '3')
    assert b.parse_op('add:rich_text') == ('add', 'rich_text')


def test_move_up_and_down_swap_neighbours():
    blocks, anchor = b.apply_op(_three(), 'move_down:0')
    assert _titles(blocks) == ['B', 'A', 'C']
    assert anchor == blocks[1]['id']          # anchors on the MOVED block
    blocks, _ = b.apply_op(blocks, 'move_up:2')
    assert _titles(blocks) == ['B', 'C', 'A']


def test_moves_at_the_edges_are_no_ops():
    assert _titles(b.apply_op(_three(), 'move_up:0')[0]) == ['A', 'B', 'C']
    assert _titles(b.apply_op(_three(), 'move_down:2')[0]) == ['A', 'B', 'C']


def test_out_of_range_and_garbage_indices_are_ignored():
    for op in ('move_up:99', 'delete:-1', 'move_down:abc', 'delete:'):
        assert _titles(b.apply_op(_three(), op)[0]) == ['A', 'B', 'C']


def test_delete_removes_the_block_and_anchors_on_a_neighbour():
    blocks, anchor = b.apply_op(_three(), 'delete:1')
    assert _titles(blocks) == ['A', 'C']
    assert anchor == blocks[1]['id']


def test_delete_refuses_to_remove_a_builtin():
    """Built-ins are hidden, never deleted -- otherwise they are unrecoverable."""
    blocks = b.default_blocks()
    out, _ = b.apply_op(blocks, 'delete:0')
    assert len(out) == len(blocks)


def test_add_appends_a_new_block_and_returns_its_anchor():
    blocks, anchor = b.apply_op(_three(), 'add:rich_text')
    assert len(blocks) == 4 and blocks[-1]['type'] == 'rich_text'
    assert anchor == blocks[-1]['id']


def test_add_respects_max_instances_and_addability():
    blocks = b.normalize_blocks([{'type': 'chart'}] * 6)
    assert len(b.apply_op(blocks, 'add:chart')[0]) == 6
    assert len(b.apply_op(_three(), 'add:builtin_join')[0]) == 3
    assert len(b.apply_op(_three(), 'add:no_such_type')[0]) == 3


def test_save_and_submit_leave_the_list_alone():
    for op in ('save', 'submit'):
        blocks, anchor = b.apply_op(_three(), op)
        assert _titles(blocks) == ['A', 'B', 'C'] and anchor is None


def test_ensure_builtins_restores_missing_sections_hidden():
    blocks = b.normalize_blocks([{'type': 'rich_text'}])
    out = b.ensure_builtins(blocks)
    assert [block['type'] for block in out][0] == 'rich_text'
    restored = {block['type']: block for block in out[1:]}
    assert set(restored) == set(b.DEFAULT_BLOCK_TYPES)
    assert all(block['hidden'] for block in restored.values())


def test_ensure_builtins_is_idempotent():
    once = b.ensure_builtins(b.default_blocks())
    assert b.ensure_builtins(once) == once


# --------------------------------------------------------------------------- #
# Review policy                                                               #
# --------------------------------------------------------------------------- #

def test_blocks_requiring_review_lists_only_external_embeds():
    blocks = b.normalize_blocks([
        {'type': 'rich_text'}, {'type': 'chart'}, {'type': 'callout'},
        {'type': 'video'}, {'type': 'terria_map'},
    ])
    assert b.blocks_requiring_review(blocks) == ['terria_map', 'video']


def test_a_hidden_external_block_does_not_force_review():
    blocks = b.normalize_blocks([{'type': 'video', 'hidden': 'on'}])
    assert b.blocks_requiring_review(blocks) == []


def test_sysadmin_pages_publish_immediately():
    blocks = b.normalize_blocks([{'type': 'video'}])
    assert b.page_initial_status(True, False, blocks) == 'approved'


def test_a_trusted_project_publishes_text_only_pages():
    blocks = b.normalize_blocks([{'type': 'rich_text'}, {'type': 'chart'}])
    assert b.page_initial_status(False, True, blocks) == 'approved'


def test_trust_is_not_a_blanket_bypass_for_external_embeds():
    """One video defeats auto-publish -- the same rule content.py applies to
    publications and maps."""
    for kind in ('video', 'image', 'terria_map'):
        blocks = b.normalize_blocks([{'type': 'rich_text'}, {'type': kind}])
        assert b.page_initial_status(False, True, blocks) == 'pending', kind


def test_an_untrusted_project_always_queues():
    blocks = b.normalize_blocks([{'type': 'rich_text'}])
    assert b.page_initial_status(False, False, blocks) == 'pending'


# --------------------------------------------------------------------------- #
# The op carriers                                                             #
# --------------------------------------------------------------------------- #

class _Form(object):
    """A Werkzeug-MultiDict stand-in: ``.get()`` returns the FIRST value."""

    def __init__(self, pairs):
        self.pairs = list(pairs)

    def get(self, key, default=None):
        for name, value in self.pairs:
            if name == key:
                return value
        return default

    def items(self, multi=True):
        return list(self.pairs)


def test_choose_op_prefers_the_pressed_button():
    assert b.choose_op('add:callout', '') == 'add:callout'
    assert b.choose_op('save', 'delete:2') == 'save'
    # The disable-on-submit path: the button contributed nothing.
    assert b.choose_op('', 'delete:2') == 'delete:2'
    assert b.choose_op(None, None) == ''
    assert b.parse_op(b.choose_op(None, None))[0] == 'save'


def test_a_browser_post_applies_the_pressed_op_not_the_hidden_field():
    """The regression that shipped: a hidden field sharing the buttons' name
    made MultiDict.get return "save" for every button, so with JavaScript off
    nothing moved, nothing was added and nothing ever published."""
    form = _Form([
        ('op_js', ''),                                   # hidden, DOM-first
        ('blocks[0][type]', 'rich_text'), ('blocks[0][id]', 'aaaaaaaa'),
        ('op', 'add:callout'),                           # the pressed button
    ])
    blocks = b.ensure_builtins(b.blocks_from_form(form.items(multi=True)))
    raw_op = b.choose_op(form.get('op'), form.get('op_js'))
    before = len(blocks)
    blocks, anchor = b.apply_op(blocks, raw_op)
    assert b.parse_op(raw_op)[0] == 'add'
    assert len(blocks) == before + 1
    assert anchor and any(block['id'] == anchor for block in blocks)


def test_a_new_block_lands_above_the_standard_sections():
    """Appending would drop every new section below "Join this project" and
    cost one full page reload per position to move it up."""
    blocks = b.default_blocks()
    blocks, _anchor = b.apply_op(blocks, 'add:rich_text')
    assert blocks[0]['type'] == 'rich_text'
    # With no built-ins present it simply appends.
    plain = b.normalize_blocks([{'type': 'callout'}])
    plain, _ = b.apply_op(plain, 'add:rich_text')
    assert [block['type'] for block in plain] == ['callout', 'rich_text']


def test_preview_is_a_no_op_on_the_block_list():
    blocks = b.default_blocks()
    out, anchor = b.apply_op(blocks, 'preview')
    assert [x['type'] for x in out] == [x['type'] for x in blocks]
    assert anchor is None


def test_convert_moves_the_legacy_description_into_a_text_block():
    blocks = b.default_blocks()
    index = [x['type'] for x in blocks].index('builtin_about')
    out, anchor = b.apply_op(blocks, 'convert:%d' % index,
                             convert_html='<p>Legacy text</p>')
    types = [x['type'] for x in out]
    assert types[index] == 'rich_text'
    assert types[index + 1] == 'builtin_about'
    # The original is hidden so the same prose is not published twice.
    assert out[index + 1]['hidden'] is True
    assert anchor == out[index]['id']


def test_convert_ignores_blocks_that_are_not_the_legacy_about():
    blocks = b.default_blocks()
    out, _anchor = b.apply_op(blocks, 'convert:0', convert_html='<p>x</p>')
    assert [x['type'] for x in out] == [x['type'] for x in blocks]


def test_duplicate_block_ids_are_replaced():
    """Ids round-trip through a visible hidden field, so a hand-edited POST can
    repeat one -- which would collapse every aria-labelledby onto the first."""
    out = b.normalize_blocks([{'type': 'rich_text', 'id': 'aaaaaaaa'},
                              {'type': 'callout', 'id': 'aaaaaaaa'}])
    assert len({block['id'] for block in out}) == 2
    assert out[0]['id'] == 'aaaaaaaa'


# --------------------------------------------------------------------------- #
# The drop report                                                             #
# --------------------------------------------------------------------------- #

def _report_for(raw):
    report = b.DropReport()
    blocks = b.normalize_blocks(raw, report=report)
    return blocks, report.drops


def test_the_report_never_changes_what_is_stored():
    """THE invariant. The report is a parallel record; if instrumenting the
    normalizers ever altered their output, stored pages would silently change
    and draft_hash would move underneath the reviewer."""
    samples = [
        b.default_blocks(),
        [{'type': 'image', 'items': [{'url': 'http://insecure.test/a.jpg'}]}],
        [{'type': 'chart', 'field': 'my field'}],
        [{'type': 'datasets_list', 'ids': 'a b\nvalid-name'}],
        [{'type': 'callout', 'cta_url': 'example.com'}],
        [{'type': 'chart'}] * 11,
        ['nope', 42, None, {'type': 'callout'}],
    ]
    for raw in samples:
        with_report = b.normalize_blocks(raw, report=b.DropReport())
        without = b.normalize_blocks(raw)
        # Ids are random for blocks that arrive without one; compare everything
        # else, which is what actually gets stored and hashed.
        strip = lambda bl: [{k: v for k, v in x.items() if k != 'id'} for x in bl]
        assert strip(with_report) == strip(without)


def test_stored_json_never_gains_report_keys():
    blocks, _drops = _report_for(
        [{'type': 'image', 'items': [{'url': 'http://insecure.test/a.jpg'}]}])
    payload = b.blocks_to_json(blocks)
    assert '_dropped' not in payload and 'reason' not in payload


def test_collecting_is_opt_in():
    """The read path -- every render of stored JSON -- must not pay for a
    feature only the editor uses."""
    import inspect
    for function in (b.normalize_blocks, b.normalize_block, b.blocks_from_form):
        assert inspect.signature(function).parameters['report'].default is None
    # And a normalize with no report still discards the same value.
    assert b.normalize_block({'type': 'chart', 'field': 'bad name'})['field'] == ''


def test_an_http_image_url_is_reported_against_its_block_and_slot():
    blocks, drops = _report_for([{'type': 'image', 'items': [
        {'url': 'https://ok.test/a.jpg'},
        {'url': 'http://insecure.test/b.jpg'},
    ]}])
    assert len(drops) == 1
    drop = drops[0]
    assert drop['field'] == 'url' and drop['reason'] == 'not_https'
    assert drop['item'] == 1
    # The recorded id must be the one the template renders, or the editor
    # cannot open or anchor the right block.
    assert drop['block'] == blocks[0]['id']
    assert 'insecure.test' in drop['value']


def test_each_discard_reports_its_own_code():
    _blocks, drops = _report_for([{'type': 'chart', 'field': 'my field'}])
    assert [d['reason'] for d in drops] == ['bad_field_name']

    _blocks, drops = _report_for([{'type': 'callout', 'cta_url': 'example.com'}])
    assert [d['reason'] for d in drops] == ['bad_link']

    _blocks, drops = _report_for([{'type': 'datasets_list',
                                   'ids': 'good-name\nnot a name'}])
    assert [(d['reason'], d['item']) for d in drops] == [('bad_ref', 1)]

    _blocks, drops = _report_for([{'type': 'terria_map',
                                   'url': 'http://insecure.test/terria'}])
    assert [d['reason'] for d in drops] == ['not_https']


def test_a_video_url_we_cannot_parse_is_not_reported():
    """video.html already warns from STORED state, which re-warns on every
    visit; a one-shot report would be strictly worse. Only a lost URL counts."""
    _blocks, drops = _report_for(
        [{'type': 'video', 'url': 'https://evil.test/x?v=abc'}])
    assert drops == []
    _blocks, drops = _report_for(
        [{'type': 'video', 'url': 'http://www.youtube.com/watch?v=dQw4w9WgXcQ'}])
    assert [d['reason'] for d in drops] == ['not_https']


def test_too_many_blocks_is_reported():
    _blocks, drops = _report_for([{'type': 'chart'}] * 11)
    assert [d['reason'] for d in drops] == ['too_many'] * 5


def test_the_report_is_capped_and_total():
    report = b.DropReport()
    b.normalize_blocks([{'type': 'datasets_list',
                         'ids': '\n'.join('not a name %d' % i
                                          for i in range(40))}],
                       report=report)
    assert len(report.drops) <= b.MAX_DROPS
    # Garbage in, no exception out.
    b.normalize_blocks('nope', report=b.DropReport())
    b.normalize_blocks([None, 42], report=b.DropReport())


def test_a_pasted_dataset_url_is_accepted():
    """Pasting the link out of the address bar is the obvious thing to do and
    used to vanish, leaving "No datasets to show yet"."""
    out = b.normalize_block({'type': 'datasets_list', 'ids':
                             'https://ihp-wins.unesco.org/dataset/river-ph-2024\n'
                             'https://data.dev-wins.com/en/dataset/cs-data-x-3\n'
                             'plain-name'})
    assert out['ids'] == ['river-ph-2024', 'cs-data-x-3', 'plain-name']
