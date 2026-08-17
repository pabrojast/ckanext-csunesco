# encoding: utf-8
"""Image-upload orchestration for the page builder.

The block model stores URLs only. This module is the narrow CKAN-dependent
bridge that turns multipart file controls into same-origin FileStore URLs
*before* block normalization and page-action validation run.

Uploads are immutable: replacing an image only changes the draft reference.
The old file may still be used by the published page (or pasted elsewhere), so
automatic deletion would make draft editing capable of breaking live pages.
"""
import os
from io import BytesIO
from urllib.parse import urlsplit

import ckan.lib.uploader as ckan_uploader
import ckan.plugins.toolkit as tk
from PIL import Image, ImageOps, UnidentifiedImageError


UPLOAD_TO = 'csunesco'
PUBLIC_PREFIX = '/uploads/{0}/'.format(UPLOAD_TO)
MAX_UPLOADS_PER_REQUEST = 12

# Spec section 6.A: normalized sizes for the three project images. Uploads are
# center-cropped to the target aspect and resized server-side.
PROJECT_IMAGE_SIZES = {
    'image_url': (600, 400),          # cover / thumbnail
    'logo_url': (600, 400),           # project logo
    'heading_image_url': (1100, 400),  # profile heading image
}


class PageImageUploadError(Exception):
    """A bounded list of editor-addressable upload failures."""

    def __init__(self, problems):
        super().__init__('Page image upload failed')
        self.problems = list(problems or [])


def uploads_enabled():
    """Whether the configured uploader can accept a web upload."""
    configured = tk.config.get('ckan.uploads_enabled')
    if configured is not None and not tk.asbool(configured):
        return False
    if tk.config.get('ckan.storage_path'):
        return True

    # External upload plugins (for example ckanext-asset-storage) replace
    # CKAN's uploader and do not require a local ``ckan.storage_path``.
    try:
        uploader = ckan_uploader.get_uploader(UPLOAD_TO)
    except Exception:
        return False
    return bool(
        getattr(uploader, '_storage', None)
        or uploader.__class__.__module__ != ckan_uploader.__name__)


def max_image_size():
    """Configured per-image limit in MiB (CKAN's default is 2)."""
    try:
        return int(tk.config.get('ckan.max_image_size') or 2)
    except (TypeError, ValueError):
        return 2


def _truthy(value):
    return value is True or (
        isinstance(value, str)
        and value.strip().lower() in ('1', 'true', 'on', 'yes'))


def _has_file(value):
    return bool(value is not None and getattr(value, 'filename', None))


def _problem(block, field, reason, item=None):
    return {
        'block': (block or {}).get('id'),
        'field': field,
        'item': item,
        'reason': reason,
        'value': u'',
    }


def _reason_for(error):
    messages = []
    for values in getattr(error, 'error_dict', {}).values():
        if isinstance(values, (list, tuple)):
            messages.extend(str(value) for value in values)
        elif values:
            messages.append(str(values))
    text = ' '.join(messages).lower()
    return 'upload_too_large' if 'too large' in text else 'upload_bad_type'


def _validate_image(upload):
    """Inspect actual bytes instead of trusting a filename or MIME header."""
    stream = getattr(upload, 'stream', upload)
    if not all(hasattr(stream, method) for method in ('read', 'seek', 'tell')):
        raise UnidentifiedImageError('The upload has no readable stream')
    position = stream.tell()
    try:
        stream.seek(0)
        with Image.open(stream) as image:
            image.verify()
            if image.format not in ('JPEG', 'PNG', 'WEBP'):
                raise UnidentifiedImageError('Unsupported image format')
    finally:
        stream.seek(position)


def _resized_upload(upload, size):
    """A new in-memory upload holding the image fitted to ``size``.

    ``ImageOps.fit`` center-crops to the target aspect then resizes, which is
    the spec's "resized automatically". The original format survives (JPEG
    stays JPEG); JPEG cannot carry alpha, so it is flattened to RGB. Runs
    AFTER ``_validate_image``, so the bytes are known-good.
    """
    from werkzeug.datastructures import FileStorage

    stream = getattr(upload, 'stream', upload)
    stream.seek(0)
    with Image.open(stream) as image:
        fmt = image.format
        if image.size == size:
            stream.seek(0)
            return upload
        mode = 'RGB' if fmt == 'JPEG' else 'RGBA'
        fitted = ImageOps.fit(image.convert(mode), size,
                              method=Image.LANCZOS)
    buffer = BytesIO()
    fitted.save(buffer, format=fmt)
    buffer.seek(0)
    return FileStorage(
        stream=buffer,
        filename=getattr(upload, 'filename', None),
        content_type=getattr(upload, 'content_type', None))


def _stored_url(value):
    """Keep backend URLs intact; expand only CKAN's bare local filename."""
    value = str(value or u'').strip()
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or '/' in value or '\\' in value:
        return value
    return PUBLIC_PREFIX + value


def _asset_reference(uploader):
    """Return a plugin-storage cleanup tuple when the uploader exposes one."""
    storage = getattr(uploader, '_storage', None)
    filename = getattr(uploader, '_filename', None)
    object_type = getattr(uploader, '_object_type', None)
    if storage is None or not filename or not hasattr(storage, 'delete'):
        return None
    reference = '/'.join(
        part.strip('/') for part in (object_type, filename) if part)
    return storage, reference


class UploadBatch(object):
    """Prepared uploads plus rollback information for one page POST."""

    def __init__(self):
        self._jobs = []
        self._written = []
        self._stored_assets = []
        self._project_holders = {}
        self.project_image_url = None
        self.project_image_urls = {}

    def add_picker(self, target, url_field, upload_field, clear_field,
                   block=None, item=None, error_field='image_upload',
                   resize_to=None):
        """Apply clear semantics or stage one non-empty file input."""
        upload = target.pop(upload_field, None)
        clear = target.pop(clear_field, None)
        if _has_file(upload):
            self._jobs.append({
                'target': target,
                'url_field': url_field,
                'upload': upload,
                'block': block,
                'item': item,
                'error_field': error_field,
                'resize_to': resize_to,
            })
        elif _truthy(clear):
            target[url_field] = u''

    def prepare_blocks(self, raw_blocks):
        for block in raw_blocks or []:
            block_type = block.get('type')
            if block_type in ('site_hero', 'site_about', 'initiative_hero',
                              'initiative_about', 'media_text'):
                self.add_picker(block, 'image_url', 'image_upload',
                                'image_clear', block=block)
            elif block_type in ('image', 'site_initiatives'):
                items = block.get('items') or {}
                if isinstance(items, dict):
                    ordered = [items[key] for key in sorted(items)]
                elif isinstance(items, list):
                    ordered = items
                else:
                    ordered = []
                url_field = ('url' if block_type == 'image'
                             else 'image_url')
                for index, item in enumerate(ordered):
                    if isinstance(item, dict):
                        self.add_picker(item, url_field, 'upload', 'clear',
                                        block=block, item=index)
        return raw_blocks

    def prepare_project_image(self, field, url, upload=None, clear=None):
        """Stage one of the project's images (cover / logo / heading).

        ``field`` keys :data:`PROJECT_IMAGE_SIZES`, whose entry drives the
        server-side fit-and-resize.
        """
        holder = {'url': url or u'', 'upload': upload, 'clear': clear}
        self.add_picker(holder, 'url', 'upload', 'clear', block=None,
                        error_field='%s_upload' % field,
                        resize_to=PROJECT_IMAGE_SIZES.get(field))
        self._project_holders[field] = holder

    def prepare_project_cover(self, url, upload=None, clear=None):
        # Back-compat spelling of prepare_project_image('image_url', ...).
        self.prepare_project_image('image_url', url, upload, clear)

    def write(self):
        if len(self._jobs) > MAX_UPLOADS_PER_REQUEST:
            raise PageImageUploadError([
                _problem(job.get('block'), job.get('error_field'),
                         'too_many_uploads', job.get('item'))
                for job in self._jobs[:1]
            ])
        if self._jobs and not uploads_enabled():
            raise PageImageUploadError([
                _problem(job.get('block'), job.get('error_field'),
                         'uploads_disabled', job.get('item'))
                for job in self._jobs[:1]
            ])

        prepared = []
        try:
            # CKAN validates declared and content-detected MIME types here.
            # Prepare every job before writing the first file so a bad second
            # picker cannot leave the first one orphaned.
            for job in self._jobs:
                _validate_image(job['upload'])
                if job.get('resize_to'):
                    job['upload'] = _resized_upload(job['upload'],
                                                    job['resize_to'])
                data = {'url': u'', 'upload': job['upload']}
                upload = ckan_uploader.get_uploader(UPLOAD_TO)
                upload.update_data_dict(data, 'url', 'upload', 'clear')
                if not data.get('url'):
                    raise tk.ValidationError(
                        {'upload': [tk._('The image could not be uploaded.')]})
                prepared.append((job, upload, data['url']))

            for job, upload, stored_value in prepared:
                upload.upload(max_image_size())
                filepath = getattr(upload, 'filepath', None)
                if filepath:
                    self._written.append(filepath)
                asset = _asset_reference(upload)
                if asset:
                    self._stored_assets.append(asset)
                job['target'][job['url_field']] = _stored_url(stored_value)
        except (UnidentifiedImageError, OSError, ValueError):
            self.rollback()
            job = job if 'job' in locals() else (self._jobs[0] if self._jobs
                                                  else {})
            raise PageImageUploadError([
                _problem(job.get('block'), job.get('error_field'),
                         'upload_bad_type', job.get('item'))
            ])
        except tk.ValidationError as error:
            self.rollback()
            job = job if 'job' in locals() else (self._jobs[0] if self._jobs
                                                  else {})
            raise PageImageUploadError([
                _problem(job.get('block'), job.get('error_field'),
                         _reason_for(error), job.get('item'))
            ])
        except Exception:
            self.rollback()
            raise PageImageUploadError([
                _problem((self._jobs[0] if self._jobs else {}).get('block'),
                         (self._jobs[0] if self._jobs else {}).get(
                             'error_field', 'image_upload'), 'upload_failed')
            ])

        self.project_image_urls = {
            field: holder['url']
            for field, holder in self._project_holders.items()
        }
        # Back-compat: the cover keeps its historical attribute.
        if 'image_url' in self.project_image_urls:
            self.project_image_url = self.project_image_urls['image_url']
        return self

    def rollback(self):
        """Delete only files created by this request, never older assets."""
        storage = tk.config.get('ckan.storage_path')
        expected = (os.path.realpath(os.path.join(
            storage, 'storage', 'uploads', UPLOAD_TO)) if storage else None)
        for filepath in self._written:
            try:
                real = os.path.realpath(filepath)
                if expected and os.path.commonpath((expected, real)) == expected:
                    os.remove(real)
            except (OSError, ValueError):
                pass
        self._written = []
        for storage_backend, reference in self._stored_assets:
            try:
                storage_backend.delete(reference)
            except Exception:
                pass
        self._stored_assets = []


def process_page_images(raw_blocks, project_cover=None, project_images=None):
    """Resolve all multipart controls and return the rollback-capable batch.

    ``project_images`` maps a project image field (a
    :data:`PROJECT_IMAGE_SIZES` key) to its picker dict
    (``{'url', 'upload', 'clear'}``) -- the three-image form of the spec's
    section 6.A. ``project_cover`` remains the single-cover spelling.
    """
    batch = UploadBatch()
    batch.prepare_blocks(raw_blocks)
    if project_cover is not None:
        batch.prepare_project_cover(
            project_cover.get('url'), project_cover.get('upload'),
            project_cover.get('clear'))
    for field, picker in (project_images or {}).items():
        batch.prepare_project_image(
            field, picker.get('url'), picker.get('upload'),
            picker.get('clear'))
    return batch.write()
