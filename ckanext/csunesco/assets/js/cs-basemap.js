/* Shared vector basemap for project regions and observation maps. */
(function () {
  "use strict";

  var ATTRIBUTION = '<a href="https://openfreemap.org/">OpenFreeMap</a> &middot; ' +
    '&copy; <a href="https://openmaptiles.org/">OpenMapTiles</a> &middot; ' +
    'Data from <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>';

  window.csunescoAddBasemap = function (map) {
    try {
      var settings = document.getElementById("cs-map-config");
      if (!settings || !window.maplibregl || !L.maplibreGL) {
        throw new Error("The vector basemap is unavailable.");
      }
      var config = JSON.parse(settings.textContent);
      maplibregl.setWorkerUrl(config.workerUrl);
      // A local style URL lets each MapLibre instance load its own style.
      return L.maplibreGL({
        style: config.styleUrl,
        interactive: false,
        attributionControl: { customAttribution: ATTRIBUTION }
      }).addTo(map);
    } catch (error) {
      // Leave the existing accessible fallback usable if WebGL is unavailable.
      map.remove();
      throw error;
    }
  };
})();
