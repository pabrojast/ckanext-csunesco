# encoding: utf-8
"""Private persistent publication storage, shared by all CKAN web workers.

Files only become accessible through gated routes. A failed refresh keeps the
previous atomic manifest. No background thread inherits a web DB session.
"""
import copy
import csv
import hashlib
import io
import json
import logging
import mimetypes
import os
from pathlib import Path
import re
import secrets
import hmac
import tempfile
import time
import urllib.request
import urllib.error
from urllib.parse import urlsplit, urljoin, unquote

import ckan.plugins.toolkit as tk
from ckanext.csunesco import db

log = logging.getLogger(__name__)
_KEY = re.compile(r'^[a-zA-Z0-9_-]{1,150}$')
MAX_BYTES = 20_000_000


def root():
    storage = tk.config.get('ckanext.csunesco.portal_storage_path') or tk.config.get('ckan.storage_path')
    if not storage:
        raise tk.ValidationError({'storage': ['Persistent CKAN storage is required for project publications']})
    path = Path(storage) / 'csunesco-portal'
    path.mkdir(parents=True, exist_ok=True)
    return path


def path_for(kind, key):
    if not _KEY.fullmatch(str(kind)) or not _KEY.fullmatch(str(key)):
        raise ValueError('Invalid storage key')
    directory = root() / kind
    directory.mkdir(exist_ok=True)
    return directory / (str(key) + '.json')


def write_private(kind, key, data):
    target = path_for(kind, key)
    raw = json.dumps(data, ensure_ascii=False, allow_nan=False).encode('utf-8')
    fd, name = tempfile.mkstemp(dir=str(target.parent))
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, str(target))
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_private(kind, key):
    try:
        with path_for(kind, key).open('r', encoding='utf-8') as stream:
            return json.load(stream)
    except FileNotFoundError:
        return None


def app_request(path, payload=None):
    from ckanext.csunesco.logic.ofform import get_base_url
    base = get_base_url()
    token = tk.config.get('ckanext.csunesco.ofform_callback_token')
    if not base or not token:
        raise ValueError('App callback is not configured')
    request = urllib.request.Request(base + path,
        data=json.dumps(payload).encode('utf-8') if payload is not None else None,
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    with urllib.request.build_opener(_NoRedirect()).open(request, timeout=15) as response:
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('App response too large')
    return json.loads(raw.decode('utf-8'))


def _source(form_id):
    # Only an approved CKAN source enables persisted data. No cached data can
    # bypass the current DB approval after rejection/withdrawal.
    return db.Session.query(db.CsDataSource).filter(
        db.CsDataSource.form_id == int(form_id),
        db.CsDataSource.status == 'approved').first()


def saved_form(form_id):
    # Public form helpers cannot choose a privileged partition. Check the
    # current app policy on every read, including old persisted media URLs.
    from ckanext.csunesco.logic import data_access
    sources = db.Session.query(db.CsDataSource).filter(
        db.CsDataSource.form_id == int(form_id), db.CsDataSource.status == 'approved').all()
    source = next((row for row in sources if data_access.permitted({}, row, 'can_download')), None)
    if not source:
        return None
    record = db.data_source_dictize(source)
    if record.get('partition_id'):
        result = data_access.bundle(record, materialize=False)
    elif tk.config.get('ckanext.csunesco.ofform_callback_token'):
        result = app_request('/internal/ckan/forms/%d/snapshot' % int(form_id))
    else:
        return None
    dashboard, hashes = materialize_observation_media(source, result['dashboard'], result.get('media') or [])
    return dict(result, dashboard=dashboard, csv=csv_from_dashboard(dashboard), media_hashes=hashes)


def csv_from_dashboard(data):
    rows = data.get('rows') or []
    fields = []
    for row in rows:
        for key in (row.get('answers') or {}):
            if key not in fields:
                fields.append(key)
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(['id', 'date', 'lat', 'lng', 'source'] + fields)
    for row in rows:
        values = [row.get(key) for key in ('id', 'date', 'lat', 'lng', 'source')]
        for key in fields:
            value = (row.get('answers') or {}).get(key)
            values.append(json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value)
        writer.writerow(values)
    return stream.getvalue()


def refresh_form(source, force=False, defer_commit=False):
    if source.status != 'approved':
        return False
    from ckanext.csunesco.logic import data_access
    data_access.callback(source)
    record = db.data_source_dictize(source)
    if record.get('partition_id'):
        # Managed partitions use revision-gated live reads; never share the
        # historical per-form cache between privacy levels.
        data_access.bundle(record)
        return True
    key = str(int(source.form_id))
    old = read_private('forms', key)
    if not force and old and time.time() - old.get('created_at', 0) < 300:
        return True
    from ckanext.csunesco.logic import ofform
    # Fetch one immutable upstream snapshot when available; legacy public
    # endpoints use a CSV derived from exactly the same dashboard rows.
    bundle = {}
    if tk.config.get('ckanext.csunesco.ofform_callback_token'):
        try:
            bundle = app_request('/internal/ckan/forms/' + key + '/snapshot')
        except urllib.error.HTTPError as error:
            if error.code == 404:
                withdraw_source(source, defer_commit=defer_commit)
                return False
            raise
        if bundle.get('visibility') != 'public' or bundle.get('status') != 'published':
            withdraw_source(source, defer_commit=defer_commit)
            return False
        from ckanext.csunesco.logic import portal
        expected = portal.metadata(db.get_project(source.project_id)).get('app_project_id')
        actual = bundle.get('app_project_id') or bundle.get('programme_id')
        if expected and actual and int(expected) != int(actual):
            raise ValueError('Observation snapshot belongs to another project')
        data = bundle['dashboard']
        text = bundle['csv']
    else:
        data = json.loads(ofform._fetch('/public/forms/' + key + '/dashboard-data').decode('utf-8'))
        text = csv_from_dashboard(data)
    if not isinstance(data, dict) or not isinstance(data.get('rows'), list) or not isinstance(text, str):
        raise ValueError('Invalid data snapshot')
    data, media_hashes = materialize_observation_media(source, data, bundle.get('media') or [])
    if media_hashes:
        text = csv_from_dashboard(data)
    write_private('forms', key, {'dashboard': data, 'csv': text, 'media_hashes': media_hashes,
        'created_at': time.time(), 'source_id': source.id,
        'checksum': hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()})
    ofform.cache_clear()
    return True


def _safe_media_url(url):
    configured = [tk.config.get(key) or '' for key in (
        'ckanext.csunesco.ofform_base_url', 'ckanext.csunesco.ofform_app_url', 'ckan.site_url')]
    site = tk.config.get('ckan.site_url') or ''
    resolved = urljoin(site + '/', url)
    parsed = urlsplit(resolved)
    origins = {(urlsplit(v).scheme, urlsplit(v).netloc) for v in configured if v}
    if parsed.scheme not in ('https', 'http') or (parsed.scheme, parsed.netloc) not in origins:
        raise tk.ValidationError({'media': ['Upload external media to the app before publication']})
    if parsed.username or parsed.password:
        raise tk.ValidationError({'media': ['Invalid media URL']})
    return resolved


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Media redirects are not permitted')


def _copy_media(project, url, fetch_url=None, endpoint_kind='project-assets'):
    if url.startswith('/citizen-science/portal/media/') or url == '/csunesco/images/preview-unavailable.svg':
        return url
    headers = {}
    if fetch_url:
        from ckanext.csunesco.logic.ofform import get_base_url
        base = get_base_url() or ''
        resolved = urljoin(base + '/', fetch_url)
        parsed = urlsplit(resolved)
        allowed = urlsplit(base)
        if (parsed.scheme, parsed.netloc) != (allowed.scheme, allowed.netloc) or not re.fullmatch(r'/internal/ckan/' + re.escape(endpoint_kind) + r'/[0-9]+', parsed.path):
            raise tk.ValidationError({'media': ['Invalid app asset endpoint']})
        token = tk.config.get('ckanext.csunesco.ofform_callback_token')
        if not token:
            raise tk.ValidationError({'media': ['App asset authorization is not configured']})
        headers['Authorization'] = 'Bearer ' + token
    else:
        resolved = _safe_media_url(url)
    local = None
    path = urlsplit(resolved).path
    if re.fullmatch(r'/citizen-science/portal/intake-media/[A-Za-z0-9_-]+/[a-f0-9]{64}', path):
        ticket, digest = path.rsplit('/', 2)[-2:]
        record = read_private('intake', ticket)
        if not record or record.get('project_id') != project.id or record.get('digest') != digest:
            raise tk.ValidationError({'media': ['Intake media belongs to another project']})
        local = root() / 'assets' / digest
    elif path.startswith('/csunesco/'):
        base = Path(__file__).resolve().parents[1] / 'public' / 'csunesco'
        target = (base / path[len('/csunesco/'):]).resolve()
        if base.resolve() in target.parents and target.is_file():
            local = target
    elif path.startswith('/uploads/csunesco/') and tk.config.get('ckan.storage_path'):
        base = Path(tk.config['ckan.storage_path']) / 'storage' / 'uploads' / 'csunesco'
        target = (base / path[len('/uploads/csunesco/'):]).resolve()
        if base.resolve() in target.parents and target.is_file():
            local = target
    if local is not None:
        with local.open('rb') as stream:
            raw = stream.read(MAX_BYTES + 1)
        content_type = (read_private('asset-types', local.name) or {}).get('type') if local.parent == root() / 'assets' else mimetypes.guess_type(str(local))[0]
    else:
        with urllib.request.build_opener(_NoRedirect()).open(urllib.request.Request(resolved, headers=headers), timeout=15) as response:
            raw = response.read(MAX_BYTES + 1)
            content_type = response.headers.get_content_type()
    if not raw or len(raw) > MAX_BYTES:
        raise tk.ValidationError({'media': ['Invalid media size']})
    # Active SVG/HTML never gains the CKAN origin through uploaded content.
    allowed = {'image/png', 'image/jpeg', 'image/webp', 'image/gif', 'application/pdf',
               'video/mp4', 'video/quicktime', 'video/webm'}
    if endpoint_kind == 'submission-files':
        allowed.update({'audio/mpeg', 'audio/wav', 'audio/ogg', 'video/mp4', 'video/webm',
                        'application/octet-stream', 'text/plain', 'text/csv', 'application/json'})
    if content_type not in allowed:
        raise tk.ValidationError({'media': ['Unsupported public media type']})
    digest = hashlib.sha256(raw).hexdigest()
    directory = root() / 'assets'
    directory.mkdir(exist_ok=True)
    destination = directory / digest
    if not destination.exists():
        fd, name = tempfile.mkstemp(dir=str(directory))
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, str(destination))
        finally:
            if os.path.exists(name):
                os.unlink(name)
    write_private('asset-types', digest, {'type': content_type})
    if path.startswith('/uploads/csunesco/') and local is not None:
        register_legacy_alias(project, unquote(path), digest)
    return '/citizen-science/portal/media/%s/%s' % (project.id, digest)


def materialize_media(project, candidate, endpoint_kind="project-assets", preview_warnings=None):
    candidate = copy.deepcopy(candidate)
    mapping = {}
    legacy_sources = {}
    site_origin = urlsplit(tk.config.get('ckan.site_url') or '')
    for entry in candidate.get('media') or []:
        if isinstance(entry, dict) and entry.get('fetch_url'):
            for key in ('url', 'source_url', 'fetch_url'):
                if entry.get(key):
                    mapping[entry[key]] = entry['fetch_url']
            original = urlsplit(entry.get('source_url') or '')
            if original.path.startswith('/uploads/csunesco/') and (not original.netloc or
                    (original.scheme, original.netloc) == (site_origin.scheme, site_origin.netloc)):
                legacy_sources.setdefault(entry['fetch_url'], []).append(unquote(original.path))
    media_keys = {'image_url', 'logo_url', 'heading_image_url', 'thumbnail_url', 'src',
                  'attachment_url', 'header_image_url'}
    def walk(value, image_items=False, block_id=""):
        if isinstance(value, dict):
            is_image = value.get('type') == 'image'
            block_id = value.get('id', block_id) if value.get('type') else block_id
            for key, item in list(value.items()):
                if (key in media_keys or key == 'url' and image_items) and isinstance(item, str) and item:
                    if value.get('kind', value.get('type')) == 'video' and not mapping.get(item):
                        from ckanext.csunesco.logic.blocks import parse_video
                        if parse_video(item)[0]:
                            continue  # Provider embeds stay links; never download HTML.
                    try:
                        value[key] = _copy_media(project, item, mapping.get(item), endpoint_kind=endpoint_kind)
                    except Exception:
                        if preview_warnings is None:
                            raise
                        value[key] = '/csunesco/images/preview-unavailable.svg'
                        preview_warnings.append({'code': 'portal_media_unavailable', 'block_id': str(block_id or ''), 'field': key})
                    for path in legacy_sources.get(mapping.get(item) or item, []):
                        register_legacy_alias(project, path, value[key].rsplit('/', 1)[-1])
                elif key == 'media' and isinstance(item, str):
                    try:
                        nested = json.loads(item)
                    except ValueError:
                        continue
                    walk(nested, True, block_id)
                    value[key] = json.dumps(nested)
                else:
                    walk(item, image_items or is_image and key == 'items' or key == 'media', block_id)
        elif isinstance(value, list):
            for item in value:
                walk(item, image_items, block_id)
    walk(candidate)
    return candidate


def publish_media_manifest(project, candidate):
    prefix = '/citizen-science/portal/media/' + project.id + '/'
    # Generated URLs are constrained hashes; scan the canonical snapshot to
    # collect the complete allowlist without accepting a requester manifest.
    hashes = re.findall(re.escape(prefix) + r'([a-f0-9]{64})', json.dumps(candidate))
    from ckanext.csunesco.logic import portal
    meta = dict(portal.metadata(project))
    meta['media_hashes'] = sorted(set(hashes))
    portal.set_metadata(project, meta)


def asset_view(project_id, digest):
    from flask import send_file
    from ckanext.csunesco.logic import portal
    project = db.get_project(project_id)
    from ckanext.csunesco.logic.editorial_pages import public_media_allowed
    allowed = public_media_allowed(project_id, digest) if project is None else (project.status == 'approved' and not portal.withdrawn(project) and digest in portal.metadata(project).get('media_hashes', []))
    if not allowed:
        return tk.abort(404, 'Media not found')
    saved_type = read_private('asset-types', digest)
    response = send_file(str(root() / 'assets' / digest), mimetype=saved_type['type'])
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Content-Security-Policy'] = "default-src 'none'; sandbox"
    return response


def withdraw_source(source, defer_commit=False):
    source.status = 'rejected'
    extras = dict(db._load_json(source.extras, {}))
    extras['withdrawn_at'] = time.time()
    source.extras = json.dumps(extras)
    if source.ckan_package_id:
        tk.get_action('package_patch')({'ignore_auth': True, 'defer_commit': defer_commit}, {
            'id': source.ckan_package_id, 'private': True})
    if not defer_commit:
        db.Session.commit()
    from ckanext.csunesco.logic import ofform
    ofform.cache_clear()


def withdraw_project(project):
    sources = db.Session.query(db.CsDataSource).filter(db.CsDataSource.project_id == project.id).all()
    for source in sources:
        withdraw_source(source)
    for content in db.Session.query(db.CsContent).filter(db.CsContent.project_id == project.id).all():
        extras = db._load_json(content.extras, {})
        if extras.get('independent_content') or extras.get('_app_content_review'):
            continue
        if content.source == 'app' or db._load_json(content.extras, {}).get('app_content_id'):
            content.status = 'rejected'
            content.rejection_reason = 'Project publication withdrawn'
    # Media access is revoked atomically by the project's DB status update.
    # All publicly exposed observations routes consult source approval afresh.
    from ckanext.csunesco.logic import ofform
    ofform.cache_clear()


def refresh_all():
    sources = db.Session.query(db.CsDataSource).filter(db.CsDataSource.status == 'approved').all()
    result = {'refreshed': 0, 'failed': 0}
    for source in sources:
        try:
            if refresh_form(source):
                result['refreshed'] += 1
        except Exception:
            result['failed'] += 1
            log.warning('Keeping last data publication for source %s', source.id)
    from ckanext.csunesco.logic import portal
    for project in db.Session.query(db.CsProject).filter(db.CsProject.status == 'approved').all():
        if portal.managed(project) and not portal.withdrawn(project):
            try:
                refresh_project_stats(project)
            except Exception:
                result['failed'] += 1
                log.warning('Keeping last public statistics for project %s', project.id)
    return result


def refresh_project_stats(project, defer_commit=False):
    from ckanext.csunesco.logic import portal
    app_id = portal.metadata(project).get('app_project_id')
    if not app_id:
        return
    values = app_request('/internal/ckan/projects/%s/stats' % int(app_id))
    updates = {}
    for key in ('observations', 'sites_monitored', 'citizen_scientists'):
        value = values.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError('Invalid public project statistics')
        updates[key] = value
    # The public count must match the same CKAN-approved row snapshots used
    # by charts/CSV, even if the app has another form awaiting local approval.
    observation_ids = set()
    sources = db.Session.query(db.CsDataSource).filter(
        db.CsDataSource.project_id == project.id, db.CsDataSource.status == 'approved').all()
    for source in sources:
        saved = saved_form(source.form_id)
        if saved:
            for row in saved.get('dashboard', {}).get('rows', []):
                observation_ids.add((source.form_id, str(row.get('id'))))
    updates['observations'] = len(observation_ids)
    db.stats_set(project.id, **updates)
    if not defer_commit:
        db.Session.commit()


_worker_started = False

def start_worker():
    global _worker_started
    if _worker_started or not tk.asbool(tk.config.get('ckanext.csunesco.portal_sync_enabled', False)):
        return
    import threading
    def run():
        import fcntl
        while True:
            try:
                with (root() / 'refresh.lock').open('a') as lock:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        continue
                    refresh_all()
                    # Repair callbacks and initial requests while retaining
                    # local moderation/publication if the app is unavailable.
                    from ckanext.csunesco.logic import portal
                    projects = db.Session.query(db.CsProject).all()
                    for project in projects:
                        if portal.managed(project):
                            portal.callback(project)
                        elif db._load_json(project.extras, {}).get('_portal_intake'):
                            try:
                                portal.send_initial_request(project)
                            except Exception:
                                pass
            except Exception:
                log.exception('Project portal repair will retry')
            finally:
                db.Session.remove()
                time.sleep(300)
    threading.Thread(target=run, name='csunesco-portal-repair', daemon=True).start()
    _worker_started = True


def prepare_project_sources(project):
    for source in db.Session.query(db.CsDataSource).filter(
            db.CsDataSource.project_id == project.id, db.CsDataSource.status == 'approved').all():
        try:
            refresh_form(source, force=True, defer_commit=True)
        except Exception:
            if not read_private('forms', str(source.form_id)):
                raise tk.ValidationError({'data': ['The first data snapshot is not available yet']})


def materialize_observation_media(source, dashboard, media):
    """Freeze attachments from exactly the selected observation rows."""
    dashboard = copy.deepcopy(dashboard)
    rows = {str(row.get('id')): row for row in dashboard.get('rows', [])}
    project = db.get_project(source.project_id)
    hashes = []
    grouped = {}
    for item in media:
        row = rows.get(str(item.get('submission_id')))
        if row is None or not item.get('fetch_url'):
            raise ValueError('Attachment is outside observation snapshot')
        url = _copy_media(project, item['fetch_url'], item['fetch_url'], endpoint_kind='submission-files')
        digest = url.rsplit('/', 1)[-1]
        if item.get('sha256') and item['sha256'] != digest:
            raise ValueError('Attachment checksum mismatch')
        hashes.append(digest)
        record = db._load_json(getattr(source, "extras", "{}"), {})
        public_url = ('/citizen-science/portal/partition-media/%s/%s' % (source.id, digest)
                      if record.get('partition_id') else '/citizen-science/portal/data-media/%s/%s' % (source.form_id, digest))
        metadata = {key: item.get(key) for key in ('id', 'field_name', 'mime', 'size')}
        metadata['url'] = public_url
        row.setdefault('media', []).append(metadata)
        if item.get('field_name'):
            grouped.setdefault((str(item['submission_id']), item['field_name']), []).append(public_url)
    for (row_id, field), urls in grouped.items():
        rows[row_id].setdefault('answers', {})[field] = urls
    return dashboard, sorted(set(hashes))


def observation_asset_view(form_id, digest):
    from flask import send_file
    bundle = saved_form(form_id)
    if not re.fullmatch(r'[a-f0-9]{64}', digest) or not bundle or digest not in bundle.get('media_hashes', []):
        return tk.abort(404, 'Media not found')
    kind = read_private('asset-types', digest) or {}
    mime = kind.get('type', 'application/octet-stream')
    response = send_file(str(root() / 'assets' / digest), mimetype=mime,
                         as_attachment=not mime.startswith('image/'), download_name=digest)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Content-Security-Policy'] = "default-src 'none'; sandbox"
    return response


def privatize_intake_uploads(data, batch, username):
    """Move only this request's new files out of public FileStore paths."""
    refs = []
    written = {str(Path(item).resolve()) for item in batch._written}
    storage = Path(tk.config.get('ckan.storage_path') or '/') / 'storage' / 'uploads' / 'csunesco'
    for field in ('logo_url', 'heading_image_url'):
        url = data.get(field) or ''
        previous = re.fullmatch(r'/citizen-science/portal/intake-media/([A-Za-z0-9_-]+)/([a-f0-9]{64})', url)
        if previous:
            record = read_private('intake', previous.group(1))
            if record and record.get('actor') == username and not record.get('project_id'):
                refs.append(previous.group(1))
        if not url.startswith('/uploads/csunesco/'):
            continue
        original = (storage / url[len('/uploads/csunesco/'):]).resolve()
        if str(original) not in written:
            continue
        raw = original.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        token = secrets.token_urlsafe(32)
        directory = root() / 'assets'
        directory.mkdir(exist_ok=True)
        os.replace(str(original), str(directory / digest))
        write_private('asset-types', digest, {'type': mimetypes.guess_type(str(original))[0] or 'image/jpeg'})
        write_private('intake', token, {'actor': username, 'project_id': None,
                                      'digest': digest, 'field': field, 'created_at': time.time()})
        data[field] = '/citizen-science/portal/intake-media/' + token + '/' + digest
        refs.append(token)
    return refs


def bind_intake_media(project, refs):
    for token in refs:
        record = read_private('intake', token)
        record['project_id'] = project.id
        write_private('intake', token, record)


def intake_asset_view(ticket, digest):
    from flask import request, send_file
    from ckanext.csunesco.logic import portal, auth
    if not re.fullmatch(r'[A-Za-z0-9_-]{30,100}', ticket) or not re.fullmatch(r'[a-f0-9]{64}', digest):
        return tk.abort(404, 'Media not found')
    record = read_private('intake', ticket)
    if not record or record.get('digest') != digest:
        return tk.abort(404, 'Media not found')
    project = db.get_project(record['project_id']) if record.get('project_id') else None
    token = tk.config.get('ckanext.csunesco.ofform_callback_token') or ''
    service = bool(token and hmac.compare_digest(request.headers.get('Authorization', ''), 'Bearer ' + token))
    username = getattr(tk.g, 'user', None)
    permitted = service or project is None and username == record.get('actor')
    if project is not None:
        if username:
            try:
                portal.actor_context({}, {'actor': {'username': username}}, project)
                permitted = True
            except tk.NotAuthorized:
                pass
        current = getattr(project, record['field'], '') or ''
        public = project.status == 'approved' and not portal.withdrawn(project)
        permitted = permitted or public and current.endswith('/' + ticket + '/' + digest)
    if not permitted:
        return tk.abort(404, 'Media not found')
    kind = read_private('asset-types', digest)
    response = send_file(str(root() / 'assets' / digest), mimetype=kind['type'])
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Content-Security-Policy'] = "default-src 'none'; sandbox"
    return response


def register_legacy_alias(project, path, digest):
    """Keep the old local URL governed by its migrated publication."""
    import fcntl
    key = hashlib.sha256(path.encode()).hexdigest()
    with (root() / 'legacy-alias.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        record = read_private('legacy-aliases', key) or {'path': path, 'owners': []}
        owner = {'project_id': project.id, 'digest': digest}
        if owner not in record['owners']:
            record['owners'].append(owner)
        write_private('legacy-aliases', key, record)


def legacy_alias_public(record):
    from ckanext.csunesco.logic import portal
    path = record['path']
    # A shared published home/initiative or another project keeps its URL.
    for page in db.Session.query(db.CsProjectPage).filter(db.CsProjectPage.published_json.isnot(None)).all():
        if path not in (page.published_json or ''):
            continue
        if db.is_special_page_id(page.project_id):
            return True
        project = db.get_project(page.project_id)
        if project and project.status == 'approved' and not portal.withdrawn(project):
            return True
    for project in db.Session.query(db.CsProject).filter(db.CsProject.status == 'approved').all():
        if portal.withdrawn(project):
            continue
        public_fields = {field: getattr(project, field, None) for field in (
            'image_url', 'logo_url', 'heading_image_url', 'project_document_url', 'landing_content')}
        if path in json.dumps(public_fields):
            return True
        if any(owner['project_id'] == project.id and owner['digest'] in portal.metadata(project).get('media_hashes', [])
               for owner in record['owners']):
            return True
    for content in db.Session.query(db.CsContent).filter(db.CsContent.status == 'approved').all():
        if path not in json.dumps(db.content_dictize(content)):
            continue
        project = db.get_project(content.project_id) if content.project_id else None
        if not content.project_id or project and project.status == 'approved' and not portal.withdrawn(project):
            return True
    return False


def guard_legacy_upload():
    from flask import request, g, abort
    from ckanext.csunesco.logic import portal
    if not request.path.startswith('/uploads/csunesco/'):
        return None
    record = read_private('legacy-aliases', hashlib.sha256(request.path.encode()).hexdigest())
    if not record:
        return None
    g.csunesco_guarded_legacy_asset = True
    if legacy_alias_public(record):
        return None
    token = tk.config.get('ckanext.csunesco.ofform_callback_token') or ''
    if token and hmac.compare_digest(request.headers.get('Authorization', ''), 'Bearer ' + token):
        return None
    username = getattr(tk.g, 'user', None)
    if username:
        for owner in record['owners']:
            project = db.get_project(owner['project_id'])
            if project:
                try:
                    portal.actor_context({}, {'actor': {'username': username}}, project)
                    return None
                except tk.NotAuthorized:
                    pass
    abort(404)


def no_cache_legacy_upload(response):
    from flask import g
    if getattr(g, 'csunesco_guarded_legacy_asset', False):
        response.headers['Cache-Control'] = 'no-store'
    return response


def partition_asset_view(source_id, digest):
    from flask import send_file
    from ckanext.csunesco.logic import data_access, ofform
    source = db.get_data_source(source_id)
    context = {'model': __import__('ckan.model', fromlist=['Session']), 'user': tk.g.user}
    if not source or not re.fullmatch(r'[a-f0-9]{64}', digest) or not data_access.permitted(context, source):
        return tk.abort(404, 'Media not found')
    try:
        bundle = data_access.bundle(source)
    except ofform.OfformError:
        return tk.abort(404, 'Media not available')
    if digest not in bundle.get('media_hashes', []):
        return tk.abort(404, 'Media not found')
    kind = read_private('asset-types', digest) or {}
    mime = kind.get('type', 'application/octet-stream')
    if not mime.startswith('image/') and not data_access.permitted(context, source, 'can_download'):
        return tk.abort(404, 'Media not found')
    response = send_file(str(root() / 'assets' / digest), mimetype=mime,
                         as_attachment=not mime.startswith('image/'), download_name=digest)
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Content-Security-Policy'] = "default-src 'none'; sandbox"
    return response
