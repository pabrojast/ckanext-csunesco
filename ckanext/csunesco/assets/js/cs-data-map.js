/*
 * ckanext-csunesco -- Citizen Science (UNESCO / IHP-WINS)
 * Observation maps for connected app-data sources.
 *
 * Progressive enhancement only: for EVERY .cs-data-map element it reads the
 * GeoJSON URL from data-observations-url and fetches it asynchronously. On
 * success it renders clustered-free circle markers and fits the bounds; on an
 * empty collection, a fetch failure or missing Leaflet/fetch it reveals the
 * accessible text fallback baked into the markup. Popups are built with
 * textContent only -- never innerHTML -- so observation properties can never
 * inject markup. (Same discipline as cs-map.js.)
 */
(function () {
  "use strict";

  var FALLBACK_UNAVAILABLE = "The observation map could not be loaded.";
  var FALLBACK_EMPTY = "No observations have been recorded yet.";
  var MAX_POPUP_ROWS = 8;

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
    var node = document.createElement("div");
    node.className = "cs-map-popup";
    var rows = 0;
    for (var key in props) {
      if (!Object.prototype.hasOwnProperty.call(props, key)) { continue; }
      var value = props[key];
      if (value === null || value === undefined || value === "") { continue; }
      if (rows >= MAX_POPUP_ROWS) { break; }
      var row = document.createElement("div");
      // textContent (never innerHTML): observation values are untrusted.
      row.textContent = key + ": " + String(value);
      node.appendChild(row);
      rows += 1;
    }
    return rows > 0 ? node : null;
  }

  function renderMap(container, geojson) {
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
      pointToLayer: function (feature, latlng) {
        return L.circleMarker(latlng, {
          radius: 6,
          weight: 1,
          color: "#0067b1",
          fillColor: "#0067b1",
          fillOpacity: 0.55
        });
      },
      onEachFeature: function (feature, featureLayer) {
        var popup = buildPopup(feature);
        if (popup) { featureLayer.bindPopup(popup); }
      }
    }).addTo(map);

    try {
      var bounds = layer.getBounds();
      if (bounds && bounds.isValid()) {
        map.fitBounds(bounds, { padding: [20, 20], maxZoom: 12 });
      } else {
        map.setView([0, 0], 2);
      }
    } catch (err) {
      map.setView([0, 0], 2);
    }
  }

  function initOne(container) {
    var url = container.getAttribute("data-observations-url");
    if (!url) { return; }
    if (typeof L === "undefined" || !window.fetch) {
      showFallback(container, FALLBACK_UNAVAILABLE);
      return;
    }

    window.fetch(url, { headers: { "Accept": "application/json" } })
      .then(function (response) {
        if (!response.ok) { return null; }
        return response.json();
      })
      .then(function (geojson) {
        var features = geojson && geojson.features;
        if (!Array.isArray(features) || features.length === 0) {
          showFallback(container, FALLBACK_EMPTY);
          return;
        }
        renderMap(container, geojson);
      })
      .catch(function () {
        showFallback(container, FALLBACK_UNAVAILABLE);
      });
  }

  function initAll() {
    var containers = document.querySelectorAll(".cs-data-map");
    for (var i = 0; i < containers.length; i += 1) {
      initOne(containers[i]);
    }
  }

  // Run on window load so the CDN Leaflet script has finished executing,
  // regardless of asset ordering.
  if (window.addEventListener) {
    window.addEventListener("load", initAll);
  }
})();
