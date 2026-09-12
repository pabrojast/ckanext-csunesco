Local map runtime
=================

MapLibre GL JS 4.7.1 (BSD-3-Clause) and @maplibre/maplibre-gl-leaflet 0.1.4
(ISC), matching the Ofform frontend. License texts are included.
Files are copied without modification from their npm packages. The CSP build
uses a locally served worker instead of creating a blob worker.

Reproduce from the repository root:

  npm install --prefix /tmp/csunesco-map-vendor --ignore-scripts --no-audit --no-fund maplibre-gl@4.7.1 @maplibre/maplibre-gl-leaflet@0.1.4
  python3 scripts/vendor-map.py /tmp/csunesco-map-vendor/node_modules

The map style in ../maps/positron-borderless.json comes from Ofform's Positron
style: national and disputed borders are omitted; regional boundaries, labels,
physical geography and OpenFreeMap/OpenMapTiles/OpenStreetMap attribution remain.
The unused ne2_shaded raster source is omitted so MapLibre can finish loading
the basemap; the visible layers are unchanged from Ofform.
Leaflet continues to be loaded by the map-assets template. Only the basemap
changes: project GeoJSON and observation circle markers are still Leaflet layers.
