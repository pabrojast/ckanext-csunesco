# encoding: utf-8
"""The one outbound call to a chat-completions provider.

Deliberately shaped like :mod:`ckanext.csunesco.logic.ofform`, the module it
most resembles: a config-pinned base URL, a timeout, a response size cap, and
errors that collapse to a type name so nothing about the upstream leaks into a
public page. It uses ``urllib`` for the same reason ``ofform`` does -- the
extension adds no HTTP dependency for a single POST.

Two things this module does that the extensions it is modelled on do not:

* **It logs.** ``ckanext-terriassistant`` swallows every provider failure with
  a bare ``except``, which makes a broken key or a changed response envelope
  undiagnosable in production. Here every failure logs an exception type and,
  where there is one, an HTTP status -- and never the key, the prompt or the
  body, all three of which can carry user content.
* **It is the extension's first outbound credential** (``ofform`` sends no
  ``Authorization`` at all), so the key is read lazily, never cached in a
  module global, and redacted from anything that could be raised.

The wire format is the OpenAI-compatible ``/chat/completions`` shape, which is
what the portal's existing provider speaks. Nothing above this module knows the
provider: :func:`complete` returns a normalized ``{'text', 'tool_call'}``.
"""
import json
import logging
import urllib.error
import urllib.request

import ckan.plugins.toolkit as tk

log = logging.getLogger(__name__)

# Config, read lazily at call time -- the ofform.py convention. Every one of
# these has a working default except the key, whose absence disables the
# feature rather than breaking a page.
API_KEY_OPTION = 'ckanext.csunesco.llm_api_key'
BASE_URL_OPTION = 'ckanext.csunesco.llm_base_url'
MODEL_OPTION = 'ckanext.csunesco.llm_model'
TIMEOUT_OPTION = 'ckanext.csunesco.llm_timeout'

DEFAULT_BASE_URL = 'https://api.deepseek.com'
DEFAULT_MODEL = 'deepseek-chat'
DEFAULT_TIMEOUT = 45

# A chat answer is a few hundred tokens. Anything approaching this is a
# misbehaving upstream, not an answer.
MAX_RESPONSE_BYTES = 1_000_000

# Temperature 0 is load-bearing, not a default. "Same question, different
# answer" is one of the standard complaints about these interfaces, and since
# the model's only job is picking a tool call, sampling variety buys nothing
# and costs reproducibility.
TEMPERATURE = 0


class LlmError(Exception):
    """The provider could not be reached or did not answer usefully."""


class LlmDisabled(LlmError):
    """No API key is configured -- the feature is off, not broken."""


def _option(name, default=''):
    try:
        value = tk.config.get(name, default)
    except Exception:
        return default
    return value if value is not None else default


def api_key():
    return str(_option(API_KEY_OPTION, '')).strip()


def base_url():
    return str(_option(BASE_URL_OPTION, DEFAULT_BASE_URL)).strip().rstrip('/')


def model_name():
    return str(_option(MODEL_OPTION, DEFAULT_MODEL)).strip() or DEFAULT_MODEL


def timeout():
    try:
        return max(5, int(_option(TIMEOUT_OPTION, DEFAULT_TIMEOUT)))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT


def is_configured():
    """Whether the feature can run at all. Callers degrade, never raise."""
    return bool(api_key()) and bool(base_url())


def complete(messages, tools=None, max_tokens=700):
    """One chat-completions round trip.

    Returns ``{'text': str, 'tool_call': {'name', 'args'} or None}``.

    ``tool_choice`` is left at the provider default rather than forced to
    ``"required"``: not every OpenAI-compatible endpoint implements that value,
    and the caller already treats "answered with prose instead of a tool call"
    as a failed attempt worth one repair. Portability is worth more here than
    saving that one retry.
    """
    key = api_key()
    if not key:
        raise LlmDisabled('no API key configured')

    payload = {
        'model': model_name(),
        'messages': messages,
        'temperature': TEMPERATURE,
        'max_tokens': max_tokens,
    }
    if tools:
        payload['tools'] = tools

    body = json.dumps(payload).encode('utf-8')
    request = urllib.request.Request(
        base_url() + '/chat/completions',
        data=body,
        headers={
            'Authorization': 'Bearer ' + key,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'User-Agent': 'ckanext-csunesco (IHP-WINS data chat)',
        },
        method='POST')

    try:
        with urllib.request.urlopen(request, timeout=timeout()) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        # The status is the one genuinely useful signal (401 = bad key,
        # 429 = quota, 5xx = theirs). The body may echo the prompt, so it is
        # never logged.
        log.warning('csunesco: LLM request failed (HTTP %s)', error.code)
        raise LlmError('HTTP %s' % error.code)
    except Exception as error:
        log.warning('csunesco: LLM request failed (%s)', type(error).__name__)
        raise LlmError('network error')

    if len(raw) > MAX_RESPONSE_BYTES:
        log.warning('csunesco: LLM response exceeded the size cap')
        raise LlmError('response too large')

    try:
        envelope = json.loads(raw.decode('utf-8'))
        message = (envelope['choices'][0] or {}).get('message') or {}
    except Exception as error:
        log.warning('csunesco: LLM envelope was unreadable (%s)',
                    type(error).__name__)
        raise LlmError('unexpected response envelope')

    return {'text': _text_of(message), 'tool_call': _tool_call_of(message)}


def _text_of(message):
    content = message.get('content')
    return content.strip() if isinstance(content, str) else ''


def _tool_call_of(message):
    """The first tool call, with its arguments parsed.

    ``arguments`` arrives as a JSON *string*; a provider that mangles it gives
    us ``{}`` rather than an exception, which the validator then rejects with a
    message the repair attempt can act on. That is the right place for it to
    fail -- there is nothing useful this module could say about it.
    """
    calls = message.get('tool_calls')
    if not isinstance(calls, list) or not calls:
        return None
    first = calls[0] if isinstance(calls[0], dict) else {}
    function = first.get('function') if isinstance(first.get('function'),
                                                   dict) else {}
    name = function.get('name')
    if not isinstance(name, str) or not name:
        return None
    try:
        args = json.loads(function.get('arguments') or '{}')
    except (TypeError, ValueError):
        args = {}
    return {'name': name, 'args': args if isinstance(args, dict) else {}}
