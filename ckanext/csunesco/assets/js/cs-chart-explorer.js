/* Temporary visitor controls. No persistence and no data requests. */
(function (root, factory) {
  var api = factory(root.CSChartPresentation || (typeof require === "function" ? require("./cs-chart-presentation.js") : null));
  if (typeof module === "object" && module.exports) module.exports = api;
  root.CSChartExplorer = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (core) {
  "use strict";
  function clone(value) { return JSON.parse(JSON.stringify(value)); }
  function assertPayload(definition, payload) {
    if (definition.version !== 2) return;
    function keys(values, pattern) { return Array.isArray(values) && values.every(function (key) { return typeof key === "string" && pattern.test(key); }) && new Set(values).size === values.length; }
    if (!payload || payload.definition_version !== 2 || payload.mode !== definition.mode || !Array.isArray(payload.labels) || !Array.isArray(payload.category_keys) || !Array.isArray(payload.series) || !keys(payload.series.map(function (s) { return s.key; }), /^s:[0-9a-f]{16}$/)) throw new Error("Unsupported chart response");
    if (definition.mode !== "category" && (payload.category_keys.length || payload.series.some(function (s) { return !Array.isArray(s.xy); }))) throw new Error("Unsupported temporal series");
    if (definition.mode === "category" && (payload.series.length !== 1 || !Array.isArray(payload.labels) || !keys(payload.category_keys, /^c:[0-9a-f]{16}$/) || payload.category_keys.length !== payload.labels.length || payload.series.some(function (s) { return !Array.isArray(s.points) || s.points.length !== payload.labels.length; }))) throw new Error("Unsupported chart categories");
  }
  function inputValue(axes, key) { return axes[key] === undefined ? "" : key[0] === "x" ? new Date(axes[key]).toISOString().slice(0, 10) : String(axes[key]); }
  function createState(definition) {
    var ranges = {}; ["xMin", "xMax", "yMin", "yMax"].forEach(function (key) { ranges[key] = inputValue(definition.axes || {}, key); });
    return { type: definition.chart, palette: "published", ranges: ranges, hidden: {} };
  }
  function limit(text, key) {
    text = String(text || "").trim(); if (!text) return undefined;
    if (key[0] === "x") {
      if (!/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(text) || text.slice(0, 4) === "0000") throw new Error("Invalid date");
      var time = Date.parse(text + "T00:00:00.000Z");
      if (!isFinite(time) || new Date(time).toISOString().slice(0, 10) !== text) throw new Error("Invalid date");
      return time + (key === "xMax" ? 86400000 - 1 : 0);
    }
    if (!/^[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i.test(text) || !isFinite(Number(text))) throw new Error("Invalid number");
    return Number(text);
  }
  function deriveView(definition, payload, state) {
    var allowed = definition.mode === "category" ? ["bar", "pie"] : ["line", "bar"];
    if (allowed.indexOf(state.type) < 0 || ["published", "accessible", "institutional", "mono"].indexOf(state.palette) < 0) throw new Error("Invalid display");
    var view = clone(definition), data = clone(payload), axes = {}, originalAxes = definition.axes || {};
    view.chart = state.type;
    ["xMin", "xMax", "yMin", "yMax"].forEach(function (key) {
      if (view.chart === "pie" || definition.mode === "category" && key[0] === "x") return;
      var value = state.ranges[key] === inputValue(originalAxes, key) ? originalAxes[key] : limit(state.ranges[key], key);
      if (value !== undefined) axes[key] = value;
    });
    ["x", "y"].forEach(function (axis) { if (axes[axis + "Min"] !== undefined && axes[axis + "Max"] !== undefined && axes[axis + "Min"] >= axes[axis + "Max"]) throw new Error("Invalid range"); });
    view.axes = axes;
    if (state.palette !== "published") {
      view.presentation.palette = state.palette;
      Object.keys(view.presentation.series || {}).forEach(function (key) { delete view.presentation.series[key].color; });
      // Automatic line/marker choices also depend on the palette length in
      // the renderer. Freeze their published values: this control changes
      // colors only, including when switching to the one-color palette.
      core.legendEntries(definition, payload).forEach(function (entry) {
        view.presentation.series[entry.key] = Object.assign({}, view.presentation.series[entry.key], {
          lineStyle: entry.lineStyle, pointStyle: entry.pointStyle
        });
      });
    }
    if (definition.mode === "category") {
      var indices = data.category_keys.map(function (_key, i) { return i; }).filter(function (i) { return !state.hidden[data.category_keys[i]]; });
      data.labels = indices.map(function (i) { return payload.labels[i]; });
      data.category_keys = indices.map(function (i) { return payload.category_keys[i]; });
      data.series.forEach(function (series, si) { series.points = indices.map(function (i) { return payload.series[si].points[i]; }); });
    } else data.series = data.series.filter(function (series) { return !state.hidden[series.key]; });
    return { definition: view, payload: data };
  }
  function mount(options) {
    var container = options.container, definition = options.definition, payload = options.payload, frame = options.frame;
    var state = createState(definition), entries = core.legendEntries(definition, payload), inputs = {}, checkboxes = [];
    function label(key, fallback) { return container.getAttribute("data-label-explorer-" + key) || fallback; }
    function node(tag, className, text) { var el = document.createElement(tag); if (className) el.className = className; if (text) el.textContent = text; return el; }
    var details = node("details", "cs-chart-explorer"), summary = node("summary", "", label("title", "Explore chart")); details.appendChild(summary);
    details.appendChild(node("p", "cs-chart-explorer-intro", label("hint", "Temporary display settings. The published chart is unchanged.")));
    var fields = node("div", "cs-chart-explorer-fields"); details.appendChild(fields);
    function field(key, title, type) {
      var wrap = node("label", "cs-chart-explorer-field"); wrap.appendChild(node("span", "", title));
      var input = node(type === "select" ? "select" : "input"); if (type !== "select") input.type = type;
      wrap.appendChild(input); fields.appendChild(wrap); inputs[key] = input; return { input: input, wrap: wrap };
    }
    function option(input, value, title) { var el = node("option", "", title); el.value = value; input.appendChild(el); }
    var type = field("type", label("type", "Chart type"), "select").input;
    (definition.mode === "category" ? ["bar", "pie"] : ["line", "bar"]).forEach(function (value) { option(type, value, label(value, {line:"Lines",bar:"Bars",pie:"Pie"}[value])); });
    var palette = field("palette", label("palette", "Colors"), "select").input;
    option(palette, "published", label("published-colors", "Published colors"));
    ["accessible", "institutional", "mono"].forEach(function (value) { option(palette, value, label("palette-" + value, {accessible:"Accessible",institutional:"Institutional",mono:"Monochrome"}[value])); });
    var rangeFields = {};
    ["xMin", "xMax", "yMin", "yMax"].forEach(function (key) {
      if (key[0] === "x" && definition.mode === "category") return;
      var titles = {xMin:"From date (UTC)",xMax:"Through date (UTC)",yMin:"Minimum value",yMax:"Maximum value"};
      var control = field(key, label(key.replace(/([A-Z])/g, "-$1").toLowerCase(), titles[key]), key[0] === "x" ? "date" : "number");
      if (key[0] === "x") { control.input.min = "0001-01-01"; control.input.max = "9999-12-31"; } else control.input.step = "any";
      rangeFields[key] = control;
      control.input.addEventListener("input", function () { state.ranges[key] = control.input.value; apply(); });
    });
    var error = node("p", "cs-chart-explorer-error", label("invalid-range", "Enter valid limits, with the minimum before the maximum.")); error.setAttribute("role", "alert"); error.hidden = true; details.appendChild(error);
    var picker = node("fieldset", "cs-chart-series-picker"); picker.appendChild(node("legend", "", label("series", "Series and categories"))); details.appendChild(picker);
    var searchWrap = node("label", "cs-chart-explorer-field"), search = node("input", "cs-chart-series-search"); search.type = "search";
    searchWrap.appendChild(node("span", "", label("search", "Find a series or category"))); searchWrap.appendChild(search); picker.appendChild(searchWrap);
    var actions = node("div", "cs-chart-explorer-actions"); picker.appendChild(actions);
    function button(parent, key, fallback, action) { var el = node("button", "", label(key, fallback)); el.type = "button"; el.addEventListener("click", action); parent.appendChild(el); return el; }
    button(actions, "show-all", "Show all", function () { state.hidden = {}; apply(); });
    button(actions, "hide-all", "Hide all", function () { entries.forEach(function (entry) { state.hidden[entry.key] = true; }); apply(); });
    var choices = node("div", "cs-chart-series-choices"); picker.appendChild(choices);
    entries.forEach(function (entry) { var row = node("label"), checkbox = node("input"); checkbox.type = "checkbox"; checkbox.checked = true; row.appendChild(checkbox); row.appendChild(node("span", "", entry.label)); choices.appendChild(row); checkbox.addEventListener("change", function () { state.hidden[entry.key] = !checkbox.checked; apply(); }); checkboxes.push({entry:entry,checkbox:checkbox,row:row}); });
    var noMatches = node("p", "cs-chart-explorer-no-matches", label("no-matches", "No matching series or categories.")); noMatches.hidden = true; picker.appendChild(noMatches);
    search.addEventListener("input", function () { var text = search.value.toLocaleLowerCase().trim(), shown = 0; checkboxes.forEach(function (item) { item.row.hidden = text && item.entry.label.toLocaleLowerCase().indexOf(text) < 0; if (!item.row.hidden) shown++; }); noMatches.hidden = shown > 0; });
    var status = node("p", "cs-chart-explorer-status"); status.setAttribute("role", "status"); details.appendChild(status);
    var empty = node("p", "cs-chart-explorer-empty", label("no-series", "No series selected. Choose a series or restore the published view.")); empty.setAttribute("role", "status"); empty.hidden = true; frame.parentNode.insertBefore(empty, frame.nextSibling);
    button(details, "reset", "Restore published view", function () { state = createState(definition); search.value = ""; noMatches.hidden = true; checkboxes.forEach(function (item) { item.row.hidden = false; }); sync(); apply(); });
    function sync() { type.value = state.type; palette.value = state.palette; Object.keys(rangeFields).forEach(function (key) { inputs[key].value = state.ranges[key]; rangeFields[key].wrap.hidden = state.type === "pie"; inputs[key].disabled = state.type === "pie"; }); checkboxes.forEach(function (item) { item.checkbox.checked = !state.hidden[item.entry.key]; }); }
    function updateStatus() { var visible = entries.filter(function (entry) { return !state.hidden[entry.key]; }).length; status.textContent = label("status", "{visible} of {total} series shown.").replace("{visible}", String(visible)).replace("{total}", String(entries.length)); empty.hidden = visible !== 0; }
    function apply() {
      Object.keys(rangeFields).forEach(function (key) { rangeFields[key].wrap.hidden = state.type === "pie"; inputs[key].disabled = state.type === "pie"; });
      try {
        if (Object.keys(rangeFields).some(function (key) { return state.type !== "pie" && inputs[key].validity && inputs[key].validity.badInput; })) throw new Error("Invalid input");
        var view = deriveView(definition, payload, state); error.hidden = true; sync(); updateStatus(); options.onChange(view.definition, view.payload, clone(state));
      } catch (_error) { error.hidden = false; }
    }
    type.addEventListener("change", function () { state.type = type.value; apply(); }); palette.addEventListener("change", function () { state.palette = palette.value; apply(); });
    sync(); updateStatus(); frame.parentNode.insertBefore(details, frame);
    return { toggle: function (key) { state.hidden[key] = !state.hidden[key]; apply(); }, getState: function () { return clone(state); }, element: details };
  }
  return { assertPayload: assertPayload, createState: createState, deriveView: deriveView, mount: mount };
});
