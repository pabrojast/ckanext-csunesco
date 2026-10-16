"""Private mail capture for exact QA recipients on the DEV portal only."""
import json
import os
import uuid
from urllib.parse import urlparse

import ckan.plugins.toolkit as tk


def capture(recipient, subject, text, html=None):
    directory = os.environ.get('CSUNESCO_QA_EMAIL_CAPTURE_DIR', '')
    allowed = {v.strip().lower() for v in os.environ.get('CSUNESCO_QA_EMAIL_CAPTURE_RECIPIENTS', '').split(',') if v.strip()}
    if (not directory or recipient.lower() not in allowed
            or urlparse(tk.config.get('ckan.site_url', '')).hostname != 'data.dev-wins.com'):
        return False
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fd = os.open(os.path.join(directory, uuid.uuid4().hex + '.json'),
                 os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump({'to': recipient, 'subject': subject, 'text': text, 'html': html}, stream)
    return True
