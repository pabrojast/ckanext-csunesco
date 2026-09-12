"""Copy the pinned MapLibre runtime and Leaflet adapter from npm packages.

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
            shutil.copyfile(args.node_modules / package / source, target / destination)
    print('Vendored MapLibre 4.7.1 and Leaflet adapter 0.1.4 (including licenses).')


if __name__ == '__main__':
    main()
