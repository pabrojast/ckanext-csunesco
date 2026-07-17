# encoding: utf-8
"""ONE shared HTML sanitizer for ckanext-csunesco.

A single restrictive allowlist is used for EVERY piece of user-supplied HTML that
gets stored and later rendered with ``| safe`` -- news/event bodies and the
rejection ``reason`` shown back to authors. Keeping it in one place means every
render path shares the identical, audited allowlist (no drift between callers).

``bleach`` is imported LAZILY inside :func:`sanitize_html` so byte-compilation and
the CKAN-free verification never require it. When bleach is not installed we FAIL
CLOSED: every tag is stripped so no markup at all reaches the database (and thus
never the ``| safe`` render path).
"""
import re

# RESTRICTIVE allowlist: inline emphasis, links, lists, small headings and
# blockquotes only. Deliberately NO images, tables, styles, iframes or scripts.
ALLOWED_TAGS = [
    'b', 'i', 'em', 'strong', 'a', 'p', 'ul', 'ol', 'li', 'br',
    'h3', 'h4', 'blockquote',
]
# Only anchors keep attributes, and only these three.
ALLOWED_ATTRS = {'a': ['href', 'title', 'rel']}
# Link protocols we trust; anything else (javascript:, data:, ...) is dropped.
ALLOWED_PROTOCOLS = ['http', 'https', 'mailto']

# Fallback tag stripper used only when bleach is unavailable.
_TAG_RE = re.compile(r'<[^>]*>')


def sanitize_html(html):
    """Strip ``html`` down to the safe allowlist BEFORE it is stored.

    Returns the cleaned string. Falsy input is returned unchanged. When bleach is
    not installed we fail closed by removing every tag with a plain regex so no
    markup survives to storage.
    """
    if not html:
        return html
    try:
        import bleach
    except ImportError:
        return _TAG_RE.sub('', html)
    return bleach.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
    )
