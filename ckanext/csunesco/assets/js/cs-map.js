/*
 * ckanext-csunesco -- Citizen Science (UNESCO / IHP-WINS)
 * Project region map (increment 4).
 *
 * Progressive enhancement only: initialises ONLY when #cs-map is present, reads
 * the GeoJSON URL from data-geojson-url and fetches it asynchronously. On
 * success it renders a Leaflet layer and fits the bounds; on an empty response
 * (204 / no features), a fetch/parse failure, or a missing Leaflet/fetch, it
 * reveals the accessible text fallback baked into the markup. Popups are built
 * with textContent only -- never innerHTML -- so feature properties can never
 * inject markup.
 */
(function () {
  "use strict";

  var FALLBACK_UNAVAILABLE = "The interactive map could not be loaded.";

  function hide(el) { if (el) { el.hidden = true; } }
  function show(el) { if (el) { el.hidden = false; } }

  function showFallback(container, message) {
    hide(container.querySelector(".cs-map-skeleton"));
    var fallback = container.querySelector(".cs-map-fallback");
    if (fallback) {
      if (message) { fallback.textContent = message; }
      show(fallback);
    }
  }

  function buildPopup(feature) {
    var props = (feature && feature.properties) || {};
    var label = props.name || props.title || props.NAME || props.label;
    if (!label) { return null; }
    var node = document.createElement("div");
    node.className = "cs-map-popup";
    // textContent (never innerHTML): feature properties are untrusted.
    node.textContent = String(label);
    return node;
  }

  function hasFeatures(geojson) {
    if (!geojson || typeof geojson !== "object") { return false; }
    if (geojson.type === "FeatureCollection") {
      return Array.isArray(geojson.features) && geojson.features.length > 0;
    }
    return true;
  }

  function renderMap(container, geojson) {
    // Remove our placeholder children before handing the node to Leaflet.
    var skeleton = container.querySelector(".cs-map-skeleton");
    if (skeleton && skeleton.parentNode) {
      skeleton.parentNode.removeChild(skeleton);
    }
    hide(container.querySelector(".cs-map-fallback"));

    var map = L.map(container, { scrollWheelZoom: false });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap contributors",
      maxZoom: 18
    }).addTo(map);

    var layer = L.geoJSON(geojson, {
      onEachFeature: function (feature, featureLayer) {
        var popup = buildPopup(feature);
        if (popup) { featureLayer.bindPopup(popup); }
      }
    }).addTo(map);

    try {
      var bounds = layer.getBounds();
      if (bounds && bounds.isValid()) {
        map.fitBounds(bounds, { padding: [20, 20] });
      } else {
        map.setView([0, 0], 2);
      }
    } catch (err) {
      map.setView([0, 0], 2);
    }
  }

  function initMap() {
    var container = document.getElementById("cs-map");
    if (!container) { return; }

    var url = container.getAttribute("data-geojson-url");
    if (!url) {
      // No region defined server-side -> the fallback text is already visible.
      return;
    }
    if (typeof L === "undefined" || !window.fetch) {
      showFallback(container, FALLBACK_UNAVAILABLE);
      return;
    }

    window.fetch(url, { headers: { "Accept": "application/json" } })
      .then(function (response) {
        if (response.status === 204 || !response.ok) { return null; }
        return response.json();
      })
      .then(function (geojson) {
        if (!hasFeatures(geojson)) {
          // Keep the baked-in "No region defined" fallback text.
          showFallback(container, null);
          return;
        }
        renderMap(container, geojson);
      })
      .catch(function () {
        showFallback(container, FALLBACK_UNAVAILABLE);
      });
  }

  // Run on window load so the CDN Leaflet script has finished executing,
  // regardless of asset ordering.
  if (window.addEventListener) {
    window.addEventListener("load", initMap);
  }
})();
