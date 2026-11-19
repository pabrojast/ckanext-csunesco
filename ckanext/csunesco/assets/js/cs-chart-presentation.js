/* Shared saved-chart v2 presentation. Keep byte-identical in app and portal. */
(function (root, factory) {
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.CSChartPresentation = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  var palettes = {
    accessible: ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#667085", "#167C9B", "#B96500", "#7851A9"],
    institutional: ["#0066A1", "#007FA8", "#17456B", "#407D93", "#073B4C", "#567CBA", "#005F73", "#668FA3"],
    mono: ["#334155"]
  };
  function hash(key) {
    var text = String(key || ""), match = text.match(/^[sc]:[0-9a-f]{8}([0-9a-f]{8})$/);
    if (match) return parseInt(match[1], 16) >>> 0;
    var n = 2166136261;
    for (var i = 0; i < text.length; i++) n = Math.imul(n ^ text.charCodeAt(i), 16777619);
    return n >>> 0;
  }
  function resolveStyle(definition, key, fallbackLabel, index) {
    var p = definition.presentation || {}, palette = palettes[p.palette] || palettes.accessible;
    var n = hash(key || fallbackLabel || String(index || 0)), custom = (p.series || {})[key] || {};
    return {
      color: /^#[0-9a-f]{6}$/i.test(custom.color || "") ? custom.color : palette[n % palette.length],
      label: custom.label || fallbackLabel || definition.title || "",
      lineStyle: custom.lineStyle || ["solid", "dashed", "dotted"][Math.floor(n / palette.length) % 3],
      pointStyle: custom.pointStyle || ["circle", "rect", "triangle"][Math.floor(n / (palette.length * 3)) % 3]
    };
  }
  function lineDash(style) { return style === "dashed" ? [7, 4] : style === "dotted" ? [2, 3] : []; }
  function formatNumber(value, definition, locale) {
    if (typeof value !== "number" || !isFinite(value)) return "—";
    var f = (definition.presentation || {}).numberFormat || {}, options = { useGrouping: f.useGrouping !== false };
    if (typeof f.decimals === "number") options.minimumFractionDigits = options.maximumFractionDigits = f.decimals;
    else options.maximumFractionDigits = 6;
    return new Intl.NumberFormat(locale || undefined, options).format(value);
  }
  function legendEntries(definition, payload) {
    if (payload.mode === "category") return (payload.labels || []).map(function (name, i) {
      var key = (payload.category_keys || [])[i] || "category:" + name;
      return Object.assign({ key: key, index: i, category: true }, resolveStyle(definition, key, name, i));
    });
    return (payload.series || []).map(function (s, i) {
      var key = s.key || "series:" + s.name;
      return Object.assign({ key: key, index: i, category: false }, resolveStyle(definition, key, s.name || definition.title, i));
    });
  }
  function showLegend(definition, payload) {
    var choice = ((definition.presentation || {}).legend || {}).display || "auto";
    return choice === "show" || (choice === "auto" && (definition.chart === "pie" || payload.mode !== "category" && (payload.series || []).length > 1));
  }
  function outOfRangeCount(definition, payload) {
    if (definition.chart === "pie") return 0;
    var axes = definition.axes || {}, count = 0;
    (payload.series || []).forEach(function (series) {
      (series.xy || series.points || []).forEach(function (point, i) {
        if (point === null || point === undefined) return;
        var y = typeof point === "object" ? point.y : point;
        var x = typeof point === "object" ? Date.parse(point.x) : i;
        if (typeof y !== "number" || !isFinite(y)) return;
        if (axes.yMin !== undefined && y < axes.yMin || axes.yMax !== undefined && y > axes.yMax ||
            payload.mode !== "category" && (axes.xMin !== undefined && x < axes.xMin || axes.xMax !== undefined && x > axes.xMax)) count++;
      });
    });
    return count;
  }
  var referenceBandsPlugin = {
    id: "csReferenceBands",
    beforeDatasetsDraw: function (chart, _args, options) {
      if (!chart.scales.y || !chart.chartArea) return;
      var ctx = chart.ctx, area = chart.chartArea, bands = options.bands || [];
      ctx.save(); ctx.beginPath(); ctx.rect(area.left, area.top, area.right - area.left, area.bottom - area.top); ctx.clip();
      bands.forEach(function (band) {
        var a = chart.scales.y.getPixelForValue(band.min), b = chart.scales.y.getPixelForValue(band.max);
        ctx.strokeStyle = band.color; ctx.fillStyle = band.color + "22"; ctx.lineWidth = 1.5;
        if (band.min === band.max) { ctx.setLineDash([6, 3]); ctx.beginPath(); ctx.moveTo(area.left, a); ctx.lineTo(area.right, a); ctx.stroke(); }
        else { ctx.setLineDash([]); ctx.fillRect(area.left, Math.min(a, b), area.right - area.left, Math.abs(a - b)); }
        if (band.label) { ctx.font = "12px sans-serif"; ctx.fillStyle = options.textColor || "#334155"; ctx.textBaseline = "top"; ctx.fillText(band.label, area.left + 5, Math.max(area.top + 3, Math.min(a, b) + 3), area.right - area.left - 10); }
      });
      ctx.restore();
    }
  };
  function buildConfig(definition, payload, locale, theme) {
    var ink = theme === "dark" ? {text:"#e6eaef", grid:"#3d4854", axis:"#7c8997"} : {text:"#334155", grid:"#e2e8f0", axis:"#94a3b8"};
    var entries = legendEntries(definition, payload), category = payload.mode === "category", pie = definition.chart === "pie";
    var p = definition.presentation || {}, axes = definition.axes || {}, titles = p.axisTitles || {};
    var labels = category ? entries.map(function (e) { return e.label; }) : payload.labels || [];
    // Chart.js otherwise lets a manual Y window shrink auto X by dropping
    // dates whose values lie outside that Y window. Keep the full timeline.
    var times = [], bands = p.referenceBands || [];
    (payload.series || []).forEach(function (series) { (series.xy || []).forEach(function (point) { var time = Date.parse(point.x); if (isFinite(time)) times.push(time); }); });
    var autoXMin, autoXMax;
    if (times.length && axes.xMin === undefined && axes.xMax === undefined) {
      autoXMin = times.reduce(function (a, b) { return Math.min(a, b); });
      autoXMax = times.reduce(function (a, b) { return Math.max(a, b); });
      if (autoXMin === autoXMax) { autoXMin -= 43200000; autoXMax += 43200000; }
    }
    var bandMin = bands.length ? bands.reduce(function (a, b) { return Math.min(a, b.min); }, Infinity) : undefined;
    var bandMax = bands.length ? bands.reduce(function (a, b) { return Math.max(a, b.max); }, -Infinity) : undefined;
    var datasets = (payload.series || []).map(function (s, i) {
      var style = category ? resolveStyle(definition, s.key, s.name || definition.title, i) : entries[i];
      return {
        label: style.label,
        data: s.xy ? s.xy.map(function (v) { return { x: Date.parse(v.x), y: v.y }; }) : (s.points || []).slice(),
        borderColor: pie ? "#ffffff" : category ? entries.map(function (e) { return e.color; }) : style.color,
        backgroundColor: category ? entries.map(function (e) { return e.color; }) : style.color,
        borderDash: lineDash(style.lineStyle), pointStyle: style.pointStyle,
        spanGaps: false, fill: false, tension: 0, borderWidth: 2, pointRadius: 2, pointHoverRadius: 4
      };
    });
    var config = {
      type: definition.chart,
      data: { labels: labels, datasets: datasets }, plugins: [referenceBandsPlugin],
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: {
          legend: { display: false }, csReferenceBands: { bands: p.referenceBands || [], textColor: ink.text },
          tooltip: { callbacks: {
            title: function (items) { var first = items[0]; return first ? category ? first.label : new Date(first.parsed.x).toLocaleDateString(locale || undefined, { timeZone: "UTC" }) : ""; },
            label: function (item) { var value = typeof item.parsed === "number" ? item.parsed : item.parsed.y; return (category ? item.label : item.dataset.label) + ": " + formatNumber(value, definition, locale); }
          } }
        }
      }
    };
    if (!pie) config.options.scales = {
      x: {
        type: category ? "category" : "linear", min: category ? undefined : axes.xMin === undefined ? autoXMin : axes.xMin, max: category ? undefined : axes.xMax === undefined ? autoXMax : axes.xMax,
        title: { display: !!titles.x, text: titles.x, color: ink.text }, grid: { display: false }, border: {color: ink.axis},
        ticks: { color: ink.text, maxTicksLimit: 6, ...(category ? {} : { callback: function (v) { return new Date(Number(v)).toLocaleDateString(locale || undefined, { timeZone: "UTC" }); } }) }
      },
      y: {
        min: axes.yMin, max: axes.yMax,
        suggestedMin: axes.yMin === undefined ? bandMin : undefined,
        suggestedMax: axes.yMax === undefined ? bandMax : undefined,
        beginAtZero: definition.chart === "bar" || definition.mode === "count",
        title: { display: !!titles.y, text: titles.y, color: ink.text }, grid: {color: ink.grid}, border: {color: ink.axis},
        ticks: { color: ink.text, ...(category || payload.mode === "count" ? { precision: 0 } : {}), callback: function (v) { return formatNumber(Number(v), definition, locale); } }
      }
    };
    return config;
  }
  return { palettes: palettes, resolveStyle: resolveStyle, lineDash: lineDash, formatNumber: formatNumber,
    legendEntries: legendEntries, showLegend: showLegend, outOfRangeCount: outOfRangeCount,
    referenceBandsPlugin: referenceBandsPlugin, buildConfig: buildConfig };
});
