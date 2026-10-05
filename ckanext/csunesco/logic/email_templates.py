"""Citizen Science transactional mail, matching the Toolbox email layout.

Rendering is independent of the web page templates and the Toolbox service.
All arguments are plain text; Jinja escapes them, including URLs, by default.
"""
from functools import lru_cache

from jinja2 import Environment, PackageLoader, select_autoescape
import ckan.plugins.toolkit as tk


LOGO_PATH = '/csunesco/images/unesco-logo-email.png'
SUPPORT_EMAIL = 'support-citizenscience@unesco.org'


@lru_cache(maxsize=1)
def _template():
    environment = Environment(
        loader=PackageLoader('ckanext.csunesco', 'templates'),
        autoescape=select_autoescape(['html']),
    )
    return environment.get_template('csunesco/emails/notification.html')


def render_notification(*, subject, message, cta_label, cta_url, footer_note):
    """Render a branded HTML alternative using CKAN's current language."""
    lang = (tk.h.lang() or 'en').replace('_', '-').lower()
    direction = 'rtl' if lang.split('-')[0] == 'ar' else 'ltr'
    return _template().render(
        lang=lang, direction=direction,
        align='right' if direction == 'rtl' else 'left',
        subject=subject, paragraphs=message.split('\n\n'),
        cta_label=cta_label, cta_url=cta_url, footer_note=footer_note,
        brand_name=tk._('Citizen Science'),
        support_label=tk._('Questions? Reach us at'),
        support_email=SUPPORT_EMAIL,
        logo_url=tk.h.url_for_static(LOGO_PATH, qualified=True),
    )
