# encoding: utf-8
"""Page-image FileStore bridge tests (real CKAN import, no web app)."""
import base64
import io
import os

import pytest

try:
    import ckan.plugins.toolkit as tk
    from werkzeug.datastructures import FileStorage
    from ckanext.csunesco.logic import uploads
    HAVE_CKAN = True
except Exception:  # pragma: no cover - local hosts without CKAN
    HAVE_CKAN = False

pytestmark = pytest.mark.skipif(not HAVE_CKAN, reason='requires CKAN')


ONE_PIXEL_PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+'
    'A8AAQUBAScY42YAAAAASUVORK5CYII=')


class DummyFile(object):
    def __init__(self, filename, data=ONE_PIXEL_PNG):
        self.filename = filename
        self.stream = io.BytesIO(data)


class FakeUploader(object):
    def __init__(self, filename, storage=None, failure=None):
        self.filename = filename
        self.filepath = (os.path.join(storage, 'storage', 'uploads',
                                      uploads.UPLOAD_TO, filename)
                         if storage else None)
        self.failure = failure

    def update_data_dict(self, data, url_field, file_field, clear_field):
        data[url_field] = self.filename

    def upload(self, max_size):
        if self.failure:
            raise self.failure
        if self.filepath:
            os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
            with open(self.filepath, 'wb') as stream:
                stream.write(b'image')


class FakeAssetStorage(object):
    def __init__(self):
        self.deleted = []

    def delete(self, reference):
        self.deleted.append(reference)


class FakeAssetUploader(FakeUploader):
    def __init__(self, url, storage, failure=None):
        super().__init__(url, failure=failure)
        self._storage = storage
        self._object_type = 'csunesco'
        self._filename = 'generated.webp'


def _factory(monkeypatch, uploaders):
    queue = list(uploaders)
    monkeypatch.setattr(uploads, 'uploads_enabled', lambda: True)
    monkeypatch.setattr(
        uploads.ckan_uploader, 'get_uploader', lambda _kind: queue.pop(0))


def test_uploaded_file_wins_and_becomes_an_internal_url(monkeypatch):
    _factory(monkeypatch, [FakeUploader('generated.webp')])
    raw = [{'type': 'site_hero', 'id': 'abcdef01',
            'image_url': 'https://example.test/old.jpg',
            'image_upload': DummyFile('new.webp'), 'image_clear': 'on'}]
    batch = uploads.process_page_images(raw)
    assert raw[0]['image_url'] == '/uploads/csunesco/generated.webp'
    assert batch.project_image_url is None


def test_external_uploader_url_is_preserved_and_can_be_rolled_back(
        monkeypatch):
    storage = FakeAssetStorage()
    public_url = 'https://assets.example.test/csunesco/generated.webp'
    _factory(monkeypatch, [FakeAssetUploader(public_url, storage)])
    raw = [{'type': 'site_hero', 'id': 'abcdef01',
            'image_upload': DummyFile('new.webp')}]

    batch = uploads.process_page_images(raw)

    assert raw[0]['image_url'] == public_url
    batch.rollback()
    assert storage.deleted == ['csunesco/generated.webp']


def test_external_uploader_enables_uploads_without_local_storage(monkeypatch):
    storage = FakeAssetStorage()
    uploader = FakeAssetUploader('https://assets.test/image.png', storage)
    monkeypatch.setitem(tk.config, 'ckan.storage_path', '')
    monkeypatch.setitem(tk.config, 'ckan.uploads_enabled', True)
    monkeypatch.setattr(
        uploads.ckan_uploader, 'get_uploader', lambda _kind: uploader)
    assert uploads.uploads_enabled() is True


def test_declared_image_with_invalid_bytes_is_refused_before_write(
        monkeypatch):
    uploader = FakeUploader('bad.png')
    _factory(monkeypatch, [uploader])
    raw = [{'type': 'site_hero', 'id': 'abcdef01',
            'image_upload': DummyFile('bad.png', b'not an image')}]

    with pytest.raises(uploads.PageImageUploadError) as caught:
        uploads.process_page_images(raw)

    assert caught.value.problems[0]['reason'] == 'upload_bad_type'


def test_real_ckan_uploader_writes_and_batch_can_roll_it_back(monkeypatch,
                                                               tmp_path):
    monkeypatch.setitem(tk.config, 'ckan.storage_path', str(tmp_path))
    monkeypatch.setitem(tk.config, 'ckan.uploads_enabled', True)
    monkeypatch.setitem(tk.config, 'ckan.upload.csunesco.types', ['image'])
    monkeypatch.setitem(
        tk.config, 'ckan.upload.csunesco.mimetypes',
        ['image/jpeg', 'image/png', 'image/webp'])
    image = FileStorage(
        stream=io.BytesIO(ONE_PIXEL_PNG), filename='pixel.png',
        content_type='image/png')
    raw = [{'type': 'site_hero', 'id': 'abcdef01',
            'image_upload': image}]

    batch = uploads.process_page_images(raw)

    assert raw[0]['image_url'].startswith('/uploads/csunesco/')
    filepath = os.path.join(
        str(tmp_path), 'storage', raw[0]['image_url'].lstrip('/'))
    assert os.path.isfile(filepath)
    with open(filepath, 'rb') as stored:
        assert stored.read() == ONE_PIXEL_PNG

    batch.rollback()
    assert not os.path.exists(filepath)


def test_clear_without_a_file_removes_the_reference(monkeypatch):
    monkeypatch.setattr(uploads, 'uploads_enabled', lambda: False)
    raw = [{'type': 'media_text', 'id': 'abcdef01',
            'image_url': 'https://example.test/old.jpg',
            'image_clear': 'on'}]
    uploads.process_page_images(raw)
    assert raw[0]['image_url'] == ''


def test_project_cover_uses_the_same_upload_contract(monkeypatch):
    _factory(monkeypatch, [FakeUploader('cover.jpg')])
    batch = uploads.process_page_images([], project_cover={
        'url': 'https://example.test/old.jpg',
        'upload': DummyFile('cover.jpg'),
        'clear': None,
    })
    assert batch.project_image_url == '/uploads/csunesco/cover.jpg'


def test_more_than_twelve_files_are_refused_before_writing(monkeypatch):
    monkeypatch.setattr(uploads, 'uploads_enabled', lambda: True)
    raw = [{'type': 'image', 'id': 'abcdef01', 'items': {
        index: {'upload': DummyFile('%s.jpg' % index)}
        for index in range(13)
    }}]
    with pytest.raises(uploads.PageImageUploadError) as caught:
        uploads.process_page_images(raw)
    assert caught.value.problems[0]['reason'] == 'too_many_uploads'


def test_a_file_post_is_refused_when_filestore_is_disabled(monkeypatch):
    monkeypatch.setattr(uploads, 'uploads_enabled', lambda: False)
    raw = [{'type': 'site_about', 'id': 'abcdef01',
            'image_upload': DummyFile('about.jpg')}]
    with pytest.raises(uploads.PageImageUploadError) as caught:
        uploads.process_page_images(raw)
    assert caught.value.problems[0]['reason'] == 'uploads_disabled'


def test_later_failure_rolls_back_files_written_by_the_batch(monkeypatch,
                                                              tmp_path):
    monkeypatch.setitem(tk.config, 'ckan.storage_path', str(tmp_path))
    first = FakeUploader('first.jpg', str(tmp_path))
    second = FakeUploader(
        'second.jpg', str(tmp_path),
        tk.ValidationError({'upload': ['File upload too large']}))
    _factory(monkeypatch, [first, second])
    raw = [{'type': 'image', 'id': 'abcdef01', 'items': {
        0: {'upload': DummyFile('first.jpg')},
        1: {'upload': DummyFile('second.jpg')},
    }}]
    with pytest.raises(uploads.PageImageUploadError) as caught:
        uploads.process_page_images(raw)
    assert caught.value.problems[0]['reason'] == 'upload_too_large'
    assert not os.path.exists(first.filepath)
