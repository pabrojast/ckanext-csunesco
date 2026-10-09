"""Reviewable defaults from Colab for an existing CKAN identity."""
import datetime
from functools import lru_cache
import unicodedata

from babel import Locale
import ckan.plugins as plugins
from ckanext.csunesco import constants

FIELDS = ('fullname', 'date_of_birth', 'nationality', 'gender')


def _normalized(value):
    return ''.join(char for char in unicodedata.normalize('NFKD', value.casefold())
                   if char.isalnum())


@lru_cache(maxsize=1)
def _country_names():
    names = {}
    for lang in ('en', 'es', 'fr', 'pt', 'uk', 'ar'):
        for code, title in Locale.parse(lang).territories.items():
            if code in constants.ISO_3166_ALPHA2:
                names.setdefault(_normalized(title), set()).add(code)
    # Never resolve an ambiguous name to an arbitrary country.
    return {key: next(iter(codes)) for key, codes in names.items() if len(codes) == 1}


def prefill(user, profile):
    """Fill empty display values only. Completeness still describes saved data."""
    result = dict(profile, colab_prefilled_fields=[], colab_unmapped_fields={})
    if not any(not profile.get(key) for key in FIELDS) or not plugins.plugin_loaded('colab'):
        return result
    from ckanext.colab.lib.registration_details import registration_details
    source = registration_details(user)
    for key in FIELDS:
        raw = source.get(key)
        if profile.get(key) or not raw:
            continue
        value = None
        if key == 'fullname':
            value = str(raw).strip()
            if len(value) > 200:
                value = None
        elif key == 'date_of_birth':
            try:
                day = datetime.date.fromisoformat(str(raw))
                if day <= datetime.date.today():
                    value = day.isoformat()
            except ValueError:
                pass
        elif key == 'gender':
            value = {'woman': 'female', 'female': 'female', 'man': 'male',
                     'male': 'male', 'nonbinary': 'non_binary',
                     'prefernottosay': 'prefer_not_to_say'}.get(_normalized(str(raw)))
        elif key == 'nationality':
            code = str(raw).strip().upper()
            if code in constants.ISO_3166_ALPHA2 or code in ('OTHER', 'PREFER_NOT_TO_SAY'):
                value = code
            else:
                value = _country_names().get(_normalized(str(raw)))
        if value:
            result[key] = value
            result['colab_prefilled_fields'].append(key)
        elif key in ('nationality', 'gender'):
            # Colab permits free text. Keep it visible for the user to reconcile
            # with CS options instead of guessing their nationality or gender.
            result['colab_unmapped_fields'][key] = str(raw).strip()[:200]
    return result
