"""Atomic, service-only receipts for retryable app creations."""
import hashlib
import json
import re

import sqlalchemy as sa
import ckan.model as model
import ckan.plugins.toolkit as tk
from ckanext.csunesco import db


def begin(context, data, action):
    key = (data or {}).get('delivery_id')
    if key is None:
        return None, None
    from ckanext.csunesco.logic.portal import require_service
    require_service(context)
    if not isinstance(key, str) or not re.fullmatch(r'[a-f0-9]{64}', key):
        raise tk.ValidationError({'delivery_id': ['Invalid bridge delivery key']})
    checksum = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':'),
                                        ensure_ascii=False).encode()).hexdigest()
    session = model.Session
    # Serialize same-key deliveries across CKAN workers. The lock is released
    # by the SAME commit/rollback as the creation and its receipt.
    if session.get_bind().dialect.name == 'postgresql':
        lock_id = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], 'big', signed=True)
        session.execute(sa.text('SELECT pg_advisory_xact_lock(:key)'), {'key': lock_id})
    table = db.cs_delivery_table
    previous = session.execute(sa.select(table).where(table.c.delivery_id == key)).mappings().first()
    if previous:
        if previous['action'] != action or previous['checksum'] != checksum:
            raise tk.ValidationError({'delivery_id': ['Delivery payload does not match its receipt']})
        return None, json.loads(previous['result_json'])
    return {'delivery_id': key, 'action': action, 'checksum': checksum}, None


def complete(receipt, result):
    if receipt:
        model.Session.execute(db.cs_delivery_table.insert().values(
            **receipt, result_json=json.dumps(result, default=str)))
