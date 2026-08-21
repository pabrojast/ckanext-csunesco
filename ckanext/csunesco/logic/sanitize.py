# encoding: utf-8
"""THE HTML sanitizers for ckanext-csunesco -- two allowlists, no more.

Every piece of user-supplied HTML that gets stored and later rendered with
``| safe`` passes through one of exactly two functions here, so every render
path shares an audited allowlist and none of them can drift:

* :func:`sanitize_html` -- the RESTRICTIVE list, for news/event bodies and the
  rejection ``reason`` shown back to authors.
* :func:`sanitize_page_html` -- the WIDER list, for project-page text blocks,
  where a PM legitimately needs headings and tables.

They are deliberately two functions with two frozen constant sets rather than
one function taking an ``allowlist=`` argument: a parameter is an open
invitation for some future caller to hand the wide list to the news path.

``bleach`` is imported LAZILY so byte-compilation and the CKAN-free verification
never require it. When bleach is not installed both functions FAIL CLOSED: every
tag is stripped so no markup at all reaches the database (and thus never the
``| safe`` render path).
"""
import re

# RESTRICTIVE allowlist: inline emphasis, links, lists, small headings and
# blockquotes only. Deliberately NO images, tables, styles, iframes or scripts.
ALLOWED_TAGS = [
    'b', 'i', 'em', 'strong', 'u', 'a', 'p', 'ul', 'ol', 'li', 'br',
    'h3', 'h4', 'blockquote',
]
# Only anchors keep attributes, and only these three.
ALLOWED_ATTRS = {'a': ['href', 'title', 'rel']}
# Link protocols we trust; anything else (javascript:, data:, ...) is dropped.
ALLOWED_PROTOCOLS = ['http', 'https', 'mailto']

# Fallback tag stripper used only when bleach is unavailable.
_TAG_RE = re.compile(r'<[^>]*>')

# Elements whose CONTENT must go with them. bleach's ``strip=True`` removes a
# disallowed tag but KEEPS its text, so ``<script>alert(1)</script>`` survives
# as the literal text ``alert(1)``. That is inert -- the tag is gone -- but it
# dumps script/style source into the page as visible copy. These are the only
# elements whose body is never prose, so we drop them whole beforehand.
# Anything this misses is still neutralised by bleach afterwards.
_DROP_ELEMENT_RE = re.compile(
    r'<\s*(script|style|template|noscript)\b[^>]*>.*?<\s*/\s*\1\s*>',
    re.IGNORECASE | re.DOTALL)


# --------------------------------------------------------------------------- #
# Wider allowlist: project-page rich-text blocks.                             #
# --------------------------------------------------------------------------- #
# A PM writing their project page needs sub-headings and the occasional table.
# What it still refuses, and why:
#   * NO <img>  -- there is a dedicated image block whose URL is validated and
#                  rendered with our own loading/referrer attributes. A free
#                  <img src> would be a second, unvalidated image ingress with
#                  no host control: a tracking-pixel vector on a public page.
#   * NO <iframe>/<script>/<style>/<object> -- Terria and video are dedicated
#                  blocks that build their own src from a validated id.
#   * NO <h1>/<h2> -- the block title already renders as the section <h2>;
#                  author-level h2 would break the heading outline.
#   * NO class/style/id attributes -- the page CSS owns presentation.
PAGE_ALLOWED_TAGS = [
    'b', 'i', 'em', 'strong', 'u', 's', 'sub', 'sup', 'code', 'a',
    'p', 'ul', 'ol', 'li', 'br', 'hr',
    'h3', 'h4', 'h5', 'blockquote', 'pre',
    'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption',
]
# ``scope`` is the one attribute that carries meaning we cannot re-derive: it
# is what makes a data table readable to a screen reader.
PAGE_ALLOWED_ATTRS = {'a': ['href', 'title', 'rel'], 'th': ['scope']}
PAGE_ALLOWED_PROTOCOLS = ['http', 'https', 'mailto']


def sanitize_html(html):
    """Strip ``html`` down to the RESTRICTIVE allowlist BEFORE it is stored.

    Returns the cleaned string. Falsy input is returned unchanged. When bleach is
    not installed we fail closed by removing every tag with a plain regex so no
    markup survives to storage.
    """
    return _clean(html, ALLOWED_TAGS, ALLOWED_ATTRS, ALLOWED_PROTOCOLS)


def sanitize_page_html(html):
    """Strip ``html`` down to the WIDER project-page allowlist before storage.

    Same fail-closed contract as :func:`sanitize_html`. Only project-page text
    and callout blocks may use this -- never news, events or rejection reasons.
    """
    return _clean(html, PAGE_ALLOWED_TAGS, PAGE_ALLOWED_ATTRS,
                  PAGE_ALLOWED_PROTOCOLS)


def _clean(html, tags, attributes, protocols):
    if not html:
        return html
    # Drop script/style bodies BEFORE bleach so their source never survives as
    # visible text (see _DROP_ELEMENT_RE), then let bleach do the real work.
    html = _DROP_ELEMENT_RE.sub('', html)
    try:
        import bleach
    except ImportError:
        return _TAG_RE.sub('', html)
    return bleach.clean(
        html,
        tags=tags,
        attributes=attributes,
        protocols=protocols,
        strip=True,
    )
