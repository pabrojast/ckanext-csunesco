# encoding: utf-8
"""THE inline-SVG icon set for the Citizen Science surfaces (NO CKAN import).

Hand-drawn 24x24 stroke glyphs, monochrome via ``currentColor``, emitted inline
so a page makes no extra request and every platform renders the same shape --
the emoji they replaced looked different on every OS next to the UNESCO brand.

Why Python and not a Jinja macro: CKAN's template environment does not export
``{% macro %}`` definitions across ``{% from %}`` imports, and a snippet per
icon would be a full template render each of the ~100 times an editor page
draws one. A dict lookup costs nothing, keeps ONE source of truth next to the
block registry that names these slugs, and is unit-testable without a
container.

Templates use the ``csunesco_icon`` helper; the verify script asserts every
``BlockType.icon`` slug exists here, because an unknown name would otherwise
render the neutral fallback dot in silence.
"""
import re

_NAME_RE = re.compile(r'[^a-z0-9-]')

# Inner markup only -- the <svg> wrapper is shared. Paths are drawn for a
# 24x24 box, stroke 1.7, round caps/joins.
ICONS = {
    'text': '<path d="M4 6h16M4 11h16M4 16h10"/>',
    'chart': '<path d="M3.5 20.5h17M6 20v-7M11 20V8M16 20v-4M21 20V11"/>',
    'chat': ('<path d="M4 7a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v7a2 2 0 0 1-2 '
             '2h-8l-4 4v-4H6a2 2 0 0 1-2-2z"/>'),
    'counters': '<path d="M9.5 4 7.5 20M16.5 4l-2 16M4.5 9h16M3.5 15h16"/>',
    'image': ('<rect x="3.5" y="5" width="17" height="14" rx="2"/>'
              '<circle cx="9" cy="10" r="1.6"/>'
              '<path d="M6 16.5 10 12l3.5 3.5 2.5-2.5 2.5 3"/>'),
    'video': ('<rect x="3.5" y="5" width="17" height="14" rx="2"/>'
              '<path d="M10.5 9.5v5l4.4-2.5z"/>'),
    'pin': ('<path d="M12 21s-6.5-5.1-6.5-10a6.5 6.5 0 0 1 13 0c0 '
            '4.9-6.5 10-6.5 10z"/><circle cx="12" cy="10.8" r="2.2"/>'),
    'map': ('<path d="M9 4 4 6v14l5-2 6 2 5-2V4l-5 2z"/>'
            '<path d="M9 4v14M15 6v14"/>'),
    'news': ('<path d="M18 20H5.5A1.5 1.5 0 0 1 4 18.5V4h12v14.5a2 2 0 0 0 '
             '4 0V8h-4"/><path d="M7 8h6M7 12h6M7 16h4"/>'),
    'database': ('<ellipse cx="12" cy="5.8" rx="7" ry="2.6"/>'
                 '<path d="M5 5.8v12.4c0 1.4 3.1 2.6 7 2.6s7-1.2 '
                 '7-2.6V5.8"/>'
                 '<path d="M5 12c0 1.4 3.1 2.6 7 2.6s7-1.2 7-2.6"/>'),
    'bulb': ('<path d="M12 3a6 6 0 0 1 3.6 10.8c-.7.5-1.1 1.3-1.1 '
             '2.2h-5c0-.9-.4-1.7-1.1-2.2A6 6 0 0 1 12 3z"/>'
             '<path d="M9.8 19h4.4M10.6 21.5h2.8"/>'),
    'gauge': ('<path d="M4 17.5a8.5 8.5 0 0 1 16 0"/>'
              '<path d="M12 17.5 16 12"/>'
              '<path d="M4.5 13.5l1.6.7M19.5 13.5l-1.6.7M12 8v1.8"/>'),
    'globe': ('<circle cx="12" cy="12" r="8.5"/>'
              '<ellipse cx="12" cy="12" rx="3.8" ry="8.5"/>'
              '<path d="M3.5 12h17"/>'),
    'doc': ('<path d="M7 3h7l4 4v14H7z"/>'
            '<path d="M14 3v4h4M10 12h5M10 16h5"/>'),
    'layers': ('<path d="m12 3.5 8 4.5-8 4.5L4 8z"/>'
               '<path d="m4 12.5 8 4.5 8-4.5M4 16.5 12 21l8-4.5"/>'),
    'users': ('<circle cx="9" cy="8" r="3.2"/>'
              '<path d="M3.5 19.5c0-3 2.4-5 5.5-5s5.5 2 5.5 5"/>'
              '<path d="M15.5 5.6a3 3 0 0 1 0 5M17.4 14.9c2 .7 3.1 2.4 '
              '3.1 4.6"/>'),
    # UI glyphs
    'up': '<path d="M12 19V5M6 11l6-6 6 6"/>',
    'down': '<path d="M12 5v14M6 13l6 6 6-6"/>',
    'trash': ('<path d="M4.5 7h15M9.5 7V4.5h5V7M7 7l.9 13h8.2L17 '
              '7M10.2 11v5M13.8 11v5"/>'),
    'eye-off': ('<path d="m4 4 16 16"/>'
                '<path d="M10.6 6.3q.7-.13 1.4-.13c5 0 8.4 3.9 9.5 '
                '5.83-.5 1-1.4 2.3-2.7 3.4M6.6 8.4C5 9.6 3.4 11 2.5 12c1.1 '
                '2 4.5 5.8 9.5 5.8 1 0 2-.15 2.9-.44"/>'
                '<path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/>'),
    'alert': ('<path d="M12 4 2.8 19.5h18.4z"/>'
              '<path d="M12 10.5v3.5M12 17v.2"/>'),
    'check': '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    'inbox': ('<path d="M6.5 6h11L20 13v5H4v-5z"/>'
              '<path d="M4 13h4.5l1.5 2.5h4L15.5 13H20"/>'),
    'arrow-right': '<path d="M4 12h15M13 6l6 6-6 6"/>',
}

# Unknown slug: a visible neutral dot, never an empty box. The verify guard
# makes this unreachable for registry icons.
_FALLBACK = '<circle cx="12" cy="12" r="4"/>'

_TEMPLATE = (
    '<svg class="cs-icon cs-icon-{name}" width="{size}" height="{size}" '
    'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" '
    'aria-hidden="true" focusable="false">{body}</svg>')


def svg(name, size=20):
    """The full ``<svg>`` markup for ``name`` -- a plain string.

    Size is int-coerced so a template can never smuggle markup through it;
    everything else in the output is a literal from this module.
    """
    try:
        size = int(size)
    except (TypeError, ValueError):
        size = 20
    clean = _NAME_RE.sub('', str(name))
    return _TEMPLATE.format(name=clean, size=size,
                            body=ICONS.get(name, _FALLBACK))
