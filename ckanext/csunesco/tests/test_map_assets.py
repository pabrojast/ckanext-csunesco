"""Public and preview maps must work with production's same-origin CSP."""
import base64
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / 'public/csunesco/vendor'


def test_leaflet_is_same_origin_and_integrity_matches_vendored_release():
    template = (ROOT / 'templates/csunesco/snippets/map_assets.html').read_text()
    assert 'unpkg.com' not in template
    for name in ('leaflet.js', 'leaflet.css'):
        assert "h.url_for_static('/csunesco/vendor/%s')" % name in template
        digest = base64.b64encode(hashlib.sha256((VENDOR / name).read_bytes()).digest()).decode()
        assert 'sha256-' + digest in template
    assert template.index('vendor/leaflet.js') < template.index('vendor/leaflet-maplibre-gl.js')


def test_leaflet_supporting_assets_and_license_are_shipped():
    for name in ('layers.png', 'layers-2x.png', 'marker-icon.png', 'marker-icon-2x.png', 'marker-shadow.png'):
        assert (VENDOR / 'images' / name).read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
    assert (VENDOR / 'leaflet.js.map').is_file()
    assert 'Copyright' in (VENDOR / 'LEAFLET-LICENSE.txt').read_text()
