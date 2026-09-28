"""Copy pinned Leaflet, MapLibre and adapter runtimes from npm packages.

Usage: python3 scripts/vendor-map.py /path/to/node_modules
"""
import argparse
import json
import shutil
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('node_modules', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    target = root / 'ckanext/csunesco/public/csunesco/vendor'
    packages = {
        'leaflet': ('1.9.4', {
            'dist/leaflet.js': 'leaflet.js',
            'dist/leaflet.js.map': 'leaflet.js.map',
            'dist/leaflet.css': 'leaflet.css',
            'dist/images/layers.png': 'images/layers.png',
            'dist/images/layers-2x.png': 'images/layers-2x.png',
            'dist/images/marker-icon.png': 'images/marker-icon.png',
            'dist/images/marker-icon-2x.png': 'images/marker-icon-2x.png',
            'dist/images/marker-shadow.png': 'images/marker-shadow.png',
            'LICENSE': 'LEAFLET-LICENSE.txt',
        }),
        'maplibre-gl': ('4.7.1', {
            'dist/maplibre-gl-csp.js': 'maplibre-gl-csp.js',
            'dist/maplibre-gl-csp-worker.js': 'maplibre-gl-csp-worker.js',
            'dist/maplibre-gl.css': 'maplibre-gl.css',
            'dist/LICENSE.txt': 'MAPLIBRE-LICENSE.txt',
        }),
        '@maplibre/maplibre-gl-leaflet': ('0.1.4', {
            'leaflet-maplibre-gl.js': 'leaflet-maplibre-gl.js',
            'LICENSE': 'MAPLIBRE-LEAFLET-LICENSE.txt',
        }),
    }
    for package, (version, files) in packages.items():
        source = args.node_modules / package
        installed = json.loads((source / 'package.json').read_text())['version']
        if installed != version:
            parser.error('%s must be %s, found %s' % (package, version, installed))
        for filename in files:
            if not (source / filename).is_file():
                parser.error('Missing runtime asset: %s' % (source / filename))
    target.mkdir(parents=True, exist_ok=True)
    for package, (_, files) in packages.items():
        for source, destination in files.items():
            (target / destination).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(args.node_modules / package / source, target / destination)
    print('Vendored Leaflet 1.9.4, MapLibre 4.7.1 and adapter 0.1.4 (including licenses).')


if __name__ == '__main__':
    main()
