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
 * Progressive enhancement throughout: the figure's caption already carries a
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
      // A live region: these messages arrive after the page has been read, so
      // without it "loading", "no data" and "failed" are all silent.
      node.setAttribute("role", "status");
      container.insertBefore(node, container.firstChild);
    }
    node.textContent = text;
    // The server-rendered placeholder is the NO-JS state. Once we have anything
    // to say, it must go: leaving it up printed "The chart could not be
    // loaded.The chart appears here." side by side in the same card.
    container.classList.add("has-message");
  }

  function clearMessage(container) {
    var node = container.querySelector(".cs-chart-message");
    if (node) { node.parentNode.removeChild(node); }
    container.classList.remove("has-message");
  }

  // The proxy tags an upstream outage explicitly (views_data.UPSTREAM_
  // UNAVAILABLE) so a reader is told the SOURCE is away and will be back,
  // instead of a message that reads like a bug on this portal. Compared
  // literally -- this string is a machine channel and is never translated.
  var UPSTREAM_UNAVAILABLE = "upstream_unavailable";

  /** Which data-label-* answers a failed load: outage vs "our problem". */
  function errorMessage(container, reason) {
    if (reason === UPSTREAM_UNAVAILABLE) {
      var label = container.getAttribute("data-label-unavailable");
      if (label) { return label; }
    }
    return container.getAttribute("data-label-error") || "";
  }

  /**
   * Reject, carrying the proxy's reason code when it sent one.
   *
   * `response.ok` stays the ONLY gate for "did it work": the body is read here
   * purely to CHOOSE the wording, so a garbled or hostile response can never
   * make a failure look like data.
   */
  function failure(response) {
    return response.json().then(
      function (body) { return body; },
      function () { return {}; }
    ).then(function (body) {
      var error = new Error("HTTP " + response.status);
      error.csReason = body && body.reason;
      throw error;
    });
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
        data: entry.xy ? entry.xy.map(function (p) { return { x: Date.parse(p.x), y: p.y }; }) : entry.points,
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

    var temporal = series.some(function (s) { return !!s.xy; });
    var axes = payload.axes || {};
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
          tooltip: temporal ? Object.assign({}, TOOLTIP, { callbacks: {
            title: function(items) { return items.length ? new Date(items[0].parsed.x).toLocaleDateString(undefined, {timeZone: "UTC"}) : ""; }
          } }) : TOOLTIP
        },
        scales: {
          x: {
            type: temporal ? "linear" : "category",
            min: axes.xMin, max: axes.xMax,
            grid: { display: false },
            border: { color: INK.axis },
            ticks: { color: INK.muted, maxTicksLimit: 8, autoSkip: true,
                     maxRotation: 0, callback: temporal ? function(v) { return new Date(v).toLocaleDateString(undefined, {timeZone: "UTC"}); } : undefined }
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
            min: axes.yMin !== undefined ? axes.yMin : payload.y_min,
            max: axes.yMax !== undefined ? axes.yMax : payload.y_max,
            beginAtZero: payload.mode === "count"
          }
        }
      }
    };
  }

  function paintStudio(container, payload, definition) {
    var core = window.CSChartPresentation, explorer = window.CSChartExplorer;
    if (!core || !explorer) throw new Error("Chart presentation unavailable");
    var locale = document.documentElement.lang || undefined;
    var frame = document.createElement("div"); frame.className = "cs-chart-studio";
    var legendOptions = definition.presentation.legend;
    frame.classList.add("cs-chart-studio-" + legendOptions.position);
    container.parentNode.insertBefore(frame, container); frame.appendChild(container);
    clearMessage(container);
    var canvas = document.createElement("canvas");
    canvas.setAttribute("role", "img"); canvas.setAttribute("aria-label", describe(container, payload));
    container.insertBefore(canvas, container.firstChild);
    var instance, legend, controller;
    var warning = document.createElement("p"); warning.className = "cs-chart-range-warning"; warning.setAttribute("role", "status"); warning.hidden = true;
    frame.parentNode.insertBefore(warning, frame.nextSibling);
    function label(key, fallback) { return container.getAttribute("data-label-" + key) || fallback; }
    function updateView(viewDefinition, viewPayload, state) {
      var focusedKey = document.activeElement && document.activeElement.getAttribute("data-chart-series-key");
      canvas.setAttribute("aria-label", describe(container, viewPayload, viewDefinition.chart));
      if (instance) instance.destroy();
      instance = new window.Chart(canvas, core.buildConfig(viewDefinition, viewPayload, locale));
      if (legend) { legend.parentNode.removeChild(legend); legend = null; }
      if (core.showLegend(viewDefinition, payload)) {
        legend = document.createElement("div"); legend.className = "cs-chart-studio-legend";
        legend.setAttribute("role", "group"); legend.setAttribute("aria-label", label("legend", "Chart legend"));
        if (legendOptions.title) { var title = document.createElement("p"); title.className = "cs-chart-studio-legend-title"; title.textContent = legendOptions.title; legend.appendChild(title); }
        core.legendEntries(viewDefinition, payload).forEach(function (entry) {
          var button = document.createElement("button"); button.type = "button";
          button.setAttribute("aria-pressed", String(!state.hidden[entry.key]));
          button.setAttribute("data-chart-series-key", entry.key);
          var swatch = document.createElement("span"); swatch.className = "cs-chart-studio-swatch";
          swatch.style.color = entry.color; swatch.style.borderBottomStyle = entry.lineStyle === "solid" ? "solid" : entry.lineStyle;
          swatch.textContent = entry.pointStyle === "rect" ? "■" : entry.pointStyle === "triangle" ? "▲" : "●";
          swatch.setAttribute("aria-hidden", "true"); button.appendChild(swatch);
          var name = document.createElement("span"); name.textContent = entry.label; button.appendChild(name);
          button.addEventListener("click", function () { controller.toggle(entry.key); });
          legend.appendChild(button);
        });
        frame.appendChild(legend);
        if (focusedKey) Array.prototype.forEach.call(legend.children, function (button) { if (button.getAttribute("data-chart-series-key") === focusedKey && button.focus) button.focus(); });
      }
      var clipped = core.outOfRangeCount(viewDefinition, viewPayload);
      warning.hidden = !clipped;
      warning.textContent = label("clipped", "{count} plotted values are outside the chosen axis limits. They remain in the data table.").replace("{count}", String(clipped));
    }
    updateView(definition, payload, explorer.createState(definition));
    controller = explorer.mount({container:container, frame:frame, definition:definition, payload:payload, onChange:updateView});
    var bands = definition.presentation.referenceBands || [];
    if (bands.length) {
      var references = document.createElement("ul"); references.className = "cs-chart-references";
      bands.forEach(function (band) { var li = document.createElement("li"); li.textContent = band.label + ": " + core.formatNumber(band.min, definition, locale) + (band.min === band.max ? "" : " – " + core.formatNumber(band.max, definition, locale)); references.appendChild(li); });
      frame.parentNode.insertBefore(references, frame.nextSibling);
    }
    var details = document.createElement("details"); details.className = "cs-chart-data-table";
    var summary = document.createElement("summary"); summary.textContent = label("table", "View published values"); details.appendChild(summary);
    var tableHint = document.createElement("p"); tableHint.className = "cs-chart-table-hint"; tableHint.textContent = label("table-hint", "All published values are included, including hidden series and values outside the displayed range."); details.appendChild(tableHint);
    var built = false, entries = core.legendEntries(definition, payload);
    details.addEventListener("toggle", function () {
      if (!details.open || built) return; built = true;
      var scroll = document.createElement("div"); scroll.className = "cs-chart-table-scroll"; scroll.tabIndex = 0;
      var table = document.createElement("table"), head = document.createElement("thead"), heading = document.createElement("tr");
      [label("series", "Series"), payload.mode === "category" ? label("category", "Category") : label("date", "Date (UTC)"), label("value", "Value")].forEach(function (text) { var th = document.createElement("th"); th.scope = "col"; th.textContent = text; heading.appendChild(th); });
      head.appendChild(heading); table.appendChild(head); var body = document.createElement("tbody"); table.appendChild(body); scroll.appendChild(table); details.appendChild(scroll);
      var rows = [];
      (payload.series || []).forEach(function (series, si) { (series.xy || series.points || []).forEach(function (point, pi) { var item = entries[payload.mode === "category" ? pi : si]; rows.push([payload.mode === "category" ? definition.title : item.label, series.xy ? core.formatDate(point.x, definition, locale) : item.label, core.formatNumber(series.xy ? point.y : point, definition, locale)]); }); });
      var shown = 0, more = document.createElement("button"); more.type = "button"; more.textContent = label("more", "Show more values"); details.appendChild(more);
      function appendRows() { var end = Math.min(shown + 200, rows.length); for (; shown < end; shown++) { var tr = document.createElement("tr"); rows[shown].forEach(function (text) { var td = document.createElement("td"); td.textContent = text; tr.appendChild(td); }); body.appendChild(tr); } more.hidden = shown >= rows.length; }
      more.addEventListener("click", appendRows); appendRows();
    });
    frame.parentNode.insertBefore(details, frame.nextSibling);
    container.classList.add("is-rendered");
    return true;
  }

  function hasData(payload) {
    return (payload.series || []).some(function (entry) {
      return (entry.xy || entry.points || []).some(function (point) {
        return point !== null && point !== undefined;
      });
    });
  }

  /** A sentence a screen reader can use in place of the picture. */
  function describe(container, payload, currentType) {
    var parts = [];
    var title = container.getAttribute("data-label-chart");
    var type = currentType || container.getAttribute("data-type") || "line";
    parts.push(type + " chart");
    if (title) { parts.push(title); }
    if (payload.field_label) { parts.push(payload.field_label); }
    var series = (payload.series || []).length;
    if (series > 1) { parts.push(series + " series"); }
    var labels = (payload.labels || []).length;
    if (labels) {
      parts.push(labels + " points from " + payload.labels[0] +
                 " to " + payload.labels[labels - 1]);
    }
    return parts.join(", ");
  }

  /**
   * Draw ``payload`` into ``container``.
   *
   * Returns false when the payload holds no drawable point, so the caller can
   * show its own empty state rather than an axis with nothing on it.
   */
  function paint(container, payload) {
    var definition = JSON.parse(container.getAttribute("data-definition") || "null");
    if (definition && definition.version === 2) {
      if (!window.CSChartExplorer) throw new Error("Chart explorer unavailable");
      window.CSChartExplorer.assertPayload(definition, payload);
    }
    if (!hasData(payload)) { return false; }
    if (definition && definition.version === 2) return paintStudio(container, payload, definition);
    clearMessage(container);
    var canvas = document.createElement("canvas");
    canvas.setAttribute("role", "img");
    // Describe the CHART, not just its y axis. For mode=count there is no
    // field_label at all, which left the label as a single space -- i.e.
    // an unnamed image.
    canvas.setAttribute("aria-label", describe(container, payload));
    container.insertBefore(canvas, container.firstChild);
    new window.Chart(canvas, buildConfig(container, payload));
    container.classList.add("is-rendered");
    return true;
  }

  function render(container, done) {
    var serverResolved = container.getAttribute("data-server-resolved") === "true";
    var url = serverResolved ? container.getAttribute("data-series-url") : seriesUrl(container);
    if (serverResolved && !url) {
      message(container, errorMessage(container));
      container.setAttribute("aria-busy", "false");
      done();
      return;
    }
    container.setAttribute("aria-busy", "true");
    message(container, container.getAttribute("data-label-loading") || "");

    var definition = container.getAttribute("data-definition");
    var options = serverResolved ? { method: "GET", credentials: "omit" } : { credentials: "same-origin" };
    if (definition && !serverResolved) {
      url = container.getAttribute("data-series-url");
      options.method = "POST";
      options.headers = { "Content-Type": "application/json" };
      // The series endpoint only calculates values. Presentation stays in the
      // published definition on the element and is applied locally by paint.
      var calculationDefinition = JSON.parse(definition);
      if (calculationDefinition.version === 2) {
        delete calculationDefinition.presentation;
        delete calculationDefinition.template;
        delete calculationDefinition.title;
        delete calculationDefinition.caption;
      }
      options.body = JSON.stringify({ definition: calculationDefinition,
        history_sources: JSON.parse(container.getAttribute("data-history-sources") || "[]") });
    }
    fetch(url, options)
      .then(function (response) {
        return response.ok ? response.json() : failure(response);
      })
      .then(function (payload) {
        if (payload.truncated) {
          var warning = document.createElement("p");
          warning.className = "cs-empty";
          warning.setAttribute("role", "status");
          warning.textContent = container.getAttribute("data-label-truncated");
          container.parentNode.appendChild(warning);
        }
        if (!paint(container, payload)) {
          message(container, container.getAttribute("data-label-empty") || "");
        }
      })
      .catch(function (error) {
        message(container, errorMessage(container, error && error.csReason));
      })
      .then(function () {
        container.setAttribute("aria-busy", "false");
        done();
      }, function () {
        container.setAttribute("aria-busy", "false");
        done();
      });
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

  // The data-chat block draws the same kind of chart, from an answer it is
  // already holding rather than from a URL. Exposing the painter keeps the
  // palette, the gap handling and the aria-label sentence in ONE place instead
  // of growing a second charting implementation that slowly diverges.
  window.csunescoCharts = { paint: paint };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
