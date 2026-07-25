/*
 * ckanext-csunesco -- chart blocks on a project page.
 *
 * Each .cs-chart element describes ONE chart in data- attributes and points at
 * /citizen-science/data/<id>/series. The server has already done the work:
 * filtered the time window, bucketed by period, capped the series and rounded
 * the values, so what arrives here is a few KB of dense arrays rather than the
 * ~1.6 MB of raw observations a real form holds.
 *
 * Because the labels are ready-made period keys, no date adapter is needed --
 * the x axis is a plain category axis.
 *
 * Progressive enhancement throughout: every chart container already contains a
 * CSV download link, so a reader without JavaScript (or with a failing upstream)
 * still gets the data instead of an empty box.
 *
 * Vanilla DOM, no jQuery, no CKAN JS modules -- matching the rest of this
 * extension.
 */
(function () {
  "use strict";

  // Categorical palette validated for colour-vision deficiency against a white
  // surface (worst adjacent CVD deltaE 24.2). Slots are handed out per series
  // in a fixed order and NEVER cycled: a ninth series would repeat a colour and
  // silently claim two different sites are the same one. The server caps the
  // series count to match this length.
  var PALETTE = [
    "#2a78d6", // blue
    "#1baf7a", // aqua
    "#eda100", // yellow
    "#008300", // green
    "#4a3aa7", // violet
    "#e34948", // red
    "#e87ba4", // magenta
    "#eb6834"  // orange
  ];

  var INK = {
    text: "#212529",
    muted: "#6c757d",
    grid: "#e9ecef",
    axis: "#ced4da"
  };

  var TOOLTIP = {
    backgroundColor: "#212529",
    titleColor: "#ffffff",
    bodyColor: "#e9ecef",
    cornerRadius: 4,
    padding: 8
  };

  // Charts render one at a time as they scroll into view. Six charts firing
  // six upstream fetches at once would make the page fight itself for
  // connections and hammer the proxy cache on a cold start.
  var queue = [];
  var running = false;

  function seriesColor(index) {
    return PALETTE[Math.min(index, PALETTE.length - 1)];
  }

  function hexToRgba(hex, alpha) {
    var value = parseInt(hex.slice(1), 16);
    return "rgba(" + ((value >> 16) & 255) + "," + ((value >> 8) & 255) + "," +
      (value & 255) + "," + alpha + ")";
  }

  function message(container, text) {
    var node = container.querySelector(".cs-chart-message");
    if (!node) {
      node = document.createElement("p");
      node.className = "cs-chart-message";
      container.insertBefore(node, container.firstChild);
    }
    node.textContent = text;
  }

  function clearMessage(container) {
    var node = container.querySelector(".cs-chart-message");
    if (node) { node.parentNode.removeChild(node); }
  }

  /** Build the series URL from the block's configuration. */
  function seriesUrl(container) {
    var base = container.getAttribute("data-series-url");
    var params = [];
    function add(key, attr) {
      var value = container.getAttribute(attr);
      if (value) { params.push(key + "=" + encodeURIComponent(value)); }
    }
    add("mode", "data-mode");
    add("field", "data-field");
    add("agg", "data-agg");
    add("group_by", "data-group-by");
    add("bucket", "data-bucket");
    add("range", "data-range");
    return params.length ? base + "?" + params.join("&") : base;
  }

  function buildConfig(container, payload) {
    var type = container.getAttribute("data-type") || "line";
    var labels = payload.labels || [];
    var series = payload.series || [];

    if (type === "pie") {
      // A pie shows ONE series: its slices are the labels, not the entities.
      var first = series[0] || { points: [] };
      return {
        type: "pie",
        data: {
          labels: labels,
          datasets: [{
            data: first.points,
            backgroundColor: labels.map(function (_label, i) {
              return seriesColor(i % PALETTE.length);
            }),
            borderColor: "#ffffff",
            borderWidth: 2
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: {
              display: true,
              position: "right",
              labels: {
                color: INK.text, usePointStyle: true, pointStyle: "circle",
                boxWidth: 8, boxHeight: 8
              }
            },
            tooltip: TOOLTIP
          }
        }
      };
    }

    var datasets = series.map(function (entry, index) {
      var color = seriesColor(index);
      return {
        label: entry.name || payload.field_label || "",
        data: entry.points,
        borderColor: color,
        backgroundColor: type === "bar" ? color : hexToRgba(color, 0.15),
        // A gap is a real hole in the record: never bridge it with a straight
        // line that implies a reading nobody took.
        spanGaps: false,
        fill: false,
        tension: 0.25,
        borderWidth: 2,
        borderRadius: type === "bar" ? 4 : 0,
        pointRadius: labels.length > 60 ? 0 : 2.5,
        pointHoverRadius: 4
      };
    });

    return {
      type: type === "bar" ? "bar" : "line",
      data: { labels: labels, datasets: datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "nearest", axis: "x", intersect: false },
        plugins: {
          legend: {
            // One unnamed series needs no legend -- the block title says it.
            display: datasets.length > 1,
            position: "bottom",
            labels: {
              color: INK.text, usePointStyle: true, pointStyle: "circle",
              boxWidth: 8, boxHeight: 8
            }
          },
          tooltip: TOOLTIP
        },
        scales: {
          x: {
            grid: { display: false },
            border: { color: INK.axis },
            ticks: { color: INK.muted, maxTicksLimit: 8, autoSkip: true,
                     maxRotation: 0 }
          },
          y: {
            title: payload.field_label
              ? { display: true, text: payload.field_label, color: INK.muted }
              : undefined,
            grid: { color: INK.grid },
            border: { display: false },
            ticks: { color: INK.muted, maxTicksLimit: 6 },
            // Present only when the data actually crossed a Tukey fence, so a
            // single bad reading cannot flatten the real signal.
            min: payload.y_min,
            max: payload.y_max,
            beginAtZero: payload.mode === "count"
          }
        }
      }
    };
  }

  function hasData(payload) {
    return (payload.series || []).some(function (entry) {
      return (entry.points || []).some(function (point) {
        return point !== null && point !== undefined;
      });
    });
  }

  function render(container, done) {
    var url = seriesUrl(container);
    message(container, container.getAttribute("data-label-loading") || "");

    fetch(url, { credentials: "same-origin" })
      .then(function (response) {
        if (!response.ok) { throw new Error("HTTP " + response.status); }
        return response.json();
      })
      .then(function (payload) {
        if (!hasData(payload)) {
          message(container, container.getAttribute("data-label-empty") || "");
          return;
        }
        clearMessage(container);
        var canvas = document.createElement("canvas");
        canvas.setAttribute("role", "img");
        canvas.setAttribute(
          "aria-label",
          (payload.field_label || "") + " " +
            (payload.bucket ? "(" + payload.bucket + ")" : ""));
        container.insertBefore(canvas, container.firstChild);
        new window.Chart(canvas, buildConfig(container, payload));
        container.classList.add("is-rendered");
      })
      .catch(function () {
        // The CSV link is already in the container, so the reader still has a
        // way to the data.
        message(container, container.getAttribute("data-label-error") || "");
      })
      .then(done, done);
  }

  function pump() {
    if (running) { return; }
    var next = queue.shift();
    if (!next) { return; }
    running = true;
    render(next, function () {
      running = false;
      pump();
    });
  }

  function enqueue(container) {
    if (container.dataset.csQueued) { return; }
    container.dataset.csQueued = "1";
    queue.push(container);
    pump();
  }

  function init() {
    var containers = document.querySelectorAll(".cs-chart[data-series-url]");
    if (!containers.length) { return; }
    if (typeof window.Chart === "undefined" || !window.fetch) {
      // Leave the CSV fallback exactly as the server rendered it.
      return;
    }

    if (!window.IntersectionObserver) {
      Array.prototype.forEach.call(containers, enqueue);
      return;
    }
    var observer = new window.IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          observer.unobserve(entry.target);
          enqueue(entry.target);
        }
      });
    }, { rootMargin: "200px" });
    Array.prototype.forEach.call(containers, function (container) {
      observer.observe(container);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
