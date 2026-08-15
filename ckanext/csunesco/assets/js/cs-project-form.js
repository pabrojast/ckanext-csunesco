/*
 * Progressive enhancement for the staged project form
 * (/citizen-science/project/new and /project/<slug>/edit).
 *
 * Vanilla DOM, no jQuery, no CKAN JS modules -- matching the rest of this
 * extension. Nothing here is required for the form to work: without this file
 * the page is one long form with every stage visible, which submits in a
 * single POST. The server re-validates everything regardless.
 *
 * Two independent enhancements, each wrapped so that one throwing cannot take
 * the other down with it:
 *
 *   1. the stage wizard (show one stage at a time, progress, per-stage checks)
 *   2. the country picker (removable chips + a searchable listbox driving the
 *      real <select multiple>, which stays the only named control on the page)
 */
(function () {
  "use strict";

  function slice(nodes) {
    return Array.prototype.slice.call(nodes);
  }

  /* Accent-folded lowercase, so typing "aland" finds "Åland Islands" and
   * "cote" finds "Côte d'Ivoire". normalize() is available everywhere we
   * support; the guard is for very old engines, where search simply becomes
   * accent-sensitive rather than broken. */
  function fold(text) {
    var value = String(text || "").toLowerCase();
    if (!String.prototype.normalize) { return value; }
    // \u0300-\u036f = combining diacritical marks, written escaped because
    // the literal characters are invisible in a source file.
    return value.normalize("NFKD").replace(/[\u0300-\u036f]/g, "");
  }

  function prefersReducedMotion() {
    return !!(window.matchMedia
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }

  // -------------------------------------------------------------------------
  // Client-side field errors.
  //
  // Rendered into the SAME .cs-field-error slot the server uses, so a user
  // never has to learn two visual languages for "this field is wrong". Server
  // errors keep their own element; ours is marked so we only ever remove ours.
  // -------------------------------------------------------------------------
  function clientErrorId(field) {
    return (field.id || field.name) + "-error-js";
  }

  function setFieldError(field, message) {
    var id = clientErrorId(field);
    var existing = document.getElementById(id);
    var described = (field.getAttribute("aria-describedby") || "")
      .split(/\s+/).filter(function (token) { return token && token !== id; });

    if (!message) {
      if (existing) { existing.parentNode.removeChild(existing); }
      field.removeAttribute("aria-invalid");
      if (described.length) {
        field.setAttribute("aria-describedby", described.join(" "));
      } else {
        field.removeAttribute("aria-describedby");
      }
      return;
    }

    if (!existing) {
      existing = document.createElement("p");
      existing.id = id;
      existing.className = "cs-field-error cs-field-error-js";
      existing.setAttribute("role", "alert");
      var host = field.closest(".cs-field") || field.parentNode;
      host.appendChild(existing);
    }
    existing.textContent = message;
    field.setAttribute("aria-invalid", "true");
    described.push(id);
    field.setAttribute("aria-describedby", described.join(" "));
  }

  // -------------------------------------------------------------------------
  // The stage wizard.
  // -------------------------------------------------------------------------
  function initWizard(form) {
    var steps = slice(form.querySelectorAll(".cs-step[data-step]"));
    if (steps.length < 2) { return; }

    var progress = document.getElementById("cs-wizard-progress");
    var bar = document.getElementById("cs-wizard-bar");
    var fill = document.getElementById("cs-wizard-bar-fill");
    var live = document.getElementById("cs-wizard-live");
    var indicators = slice(
      document.querySelectorAll("#cs-wizard-steps li[data-step]"));
    var submit = document.getElementById("cs-req-submit");

    // The CSS gate. Only now do the stages start hiding each other -- before
    // this class exists the page is a plain long form.
    form.classList.add("is-enhanced");
    if (progress) { progress.hidden = false; }
    slice(form.querySelectorAll(".cs-wizard-nav")).forEach(function (nav) {
      nav.hidden = false;
    });

    function stepNumber(el) {
      return parseInt(el.getAttribute("data-step"), 10) || 1;
    }

    // Read the starting stage FROM THE DOM. The server already painted the
    // stage holding the first error, so there is no flash of stage 1 and no
    // second source of truth to drift.
    var current = 1;
    // The field validateAll() stopped on, so the submit handler can focus it
    // AFTER revealing its stage -- focusing it before the stage is painted is
    // a no-op, which left the user on a freshly revealed stage with focus
    // stranded on the disabled submit button.
    var lastBadField = null;
    steps.forEach(function (el) {
      if (el.classList.contains("is-active")) { current = stepNumber(el); }
    });

    function activeStep() {
      return steps[current - 1];
    }

    /* The ONLY writer of `current`. Keeping the counter and the painting in
     * one place is deliberate: the platform this wizard is modelled on updates
     * the DOM in showStep but leaves its own counter untouched, so after an
     * error jumps you to stage 3, Back takes you to stage 4. */
    function showStep(n, options) {
      var opts = options || {};
      current = Math.min(Math.max(n, 1), steps.length);

      steps.forEach(function (el) {
        el.classList.toggle("is-active", stepNumber(el) === current);
      });
      // Stage 1 reads 1/5, not 0%: an empty bar on first paint looks broken.
      if (fill) { fill.style.width = ((current / steps.length) * 100) + "%"; }
      if (bar) { bar.setAttribute("aria-valuenow", String(current)); }
      indicators.forEach(function (item) {
        var no = parseInt(item.getAttribute("data-step"), 10);
        if (no === current) {
          item.setAttribute("aria-current", "step");
        } else {
          item.removeAttribute("aria-current");
        }
        item.setAttribute("data-state", no < current ? "done" : "todo");
      });

      if (opts.focus !== false) {
        var heading = activeStep().querySelector(".cs-step-title");
        if (heading) {
          heading.focus({ preventScroll: true });
          if (live) { live.textContent = heading.textContent; }
        }
        // ONE argument. Passing two selects the scrollTo(x, y) overload, which
        // coerces the options object with Number({...}) -> NaN -> 0, so the
        // smooth branch silently did nothing and both paths jumped.
        if (prefersReducedMotion()) {
          window.scrollTo(0, 0);
        } else {
          window.scrollTo({ top: 0, behavior: "smooth" });
        }
      }
    }

    /* end_date >= start_date, expressed through the native constraint API so
     * it lands in the same slot and the same styling as every other rule. The
     * message comes off the element, so it is translated with the page. */
    function crossFieldChecks(scope) {
      var start = form.querySelector("[name='start_date']");
      var end = form.querySelector("[name='end_date']");
      if (!start || !end || !scope.contains(end)) { return; }
      end.setCustomValidity("");
      if (start.value && end.value && end.value < start.value) {
        end.setCustomValidity(
          end.getAttribute("data-cs-msg-end-before-start") || "Invalid date");
      }
    }

    /* Validate ONE stage before letting Next through. The reference wizard has
     * no per-stage checks at all, so you can reach the last stage with an
     * empty form and only then find out. Uses the native constraint API rather
     * than a hand-rolled ruleset: `required`, type=email/url and maxlength are
     * already declared in the markup, so the rules live in one place. The form
     * keeps `novalidate` so the browser does not ALSO pop its own bubbles --
     * checkValidity() still works, we just do the reporting ourselves. */
    function validateStep(stepEl) {
      var firstBad = null;
      crossFieldChecks(stepEl);
      slice(stepEl.querySelectorAll("input, select, textarea"))
        .forEach(function (field) {
          if (field.disabled || field.type === "hidden") { return; }
          // The native country <select> is hidden by the picker but still
          // submits, and it carries no constraints -- skip rather than report.
          if (field.hidden) { return; }
          var ok = typeof field.checkValidity !== "function"
            || field.checkValidity();
          setFieldError(field, ok ? null : field.validationMessage);
          if (!ok && !firstBad) { firstBad = field; }
        });
      // Only focus when the stage is actually on screen. Calling focus() on a
      // field inside a display:none stage is a silent no-op, which is how a
      // failed submit used to reveal a stage and leave focus behind on the
      // disabled submit button, announcing nothing.
      if (firstBad) {
        lastBadField = firstBad;
        if (stepEl.classList.contains("is-active")) { firstBad.focus(); }
      }
      return !firstBad;
    }

    function validateAll() {
      lastBadField = null;
      for (var i = 0; i < steps.length; i++) {
        if (!validateStep(steps[i])) { return i + 1; }
      }
      return 0;
    }

    form.addEventListener("click", function (event) {
      var next = event.target.closest(".cs-wizard-next");
      if (next) {
        if (validateStep(activeStep())) { showStep(current + 1); }
        return;
      }
      // Back never validates: being unable to leave a stage you already left
      // would be a trap.
      if (event.target.closest(".cs-wizard-prev")) { showStep(current - 1); }
    });

    /* Every nav button is type="button", so Enter in a text field would fall
     * through to the form's default submit -- the real Submit at the end of
     * stage 5, even while it is display:none. Enter should advance instead,
     * until the last stage. */
    form.addEventListener("keydown", function (event) {
      if (event.key !== "Enter" || event.defaultPrevented) { return; }
      var target = event.target;
      var tag = (target.tagName || "").toLowerCase();
      if (tag === "textarea" || tag === "button"
          || target.type === "submit") { return; }
      if (current < steps.length) {
        event.preventDefault();
        if (validateStep(activeStep())) { showStep(current + 1); }
      }
    });

    form.addEventListener("submit", function (event) {
      var badStep = validateAll();
      if (badStep) {
        event.preventDefault();
        showStep(badStep, { focus: false });
        if (lastBadField) { lastBadField.focus(); }
        return;
      }
      if (submit) {
        submit.disabled = true;
        submit.classList.add("is-loading");
        var label = submit.querySelector(".cs-btn-label");
        var spinner = submit.querySelector(".cs-btn-spinner");
        if (label) { label.textContent = label.getAttribute("data-sending")
          || "Sending…"; }
        if (spinner) { spinner.hidden = false; }
      }
    });

    // Clear a stale cross-field error as soon as either date changes.
    slice(form.querySelectorAll("[name='start_date'], [name='end_date']"))
      .forEach(function (field) {
        field.addEventListener("input", function () {
          var end = form.querySelector("[name='end_date']");
          if (end) { end.setCustomValidity(""); setFieldError(end, null); }
        });
      });

    showStep(current, { focus: false });
    return showStep;
  }

  // -------------------------------------------------------------------------
  // The country picker.
  //
  // ~210 options. The native <select multiple> stays in the DOM as the only
  // named control -- we hide it (NEVER disable it: a disabled control is
  // dropped from the POST, which on an edit would silently clear every
  // country) and drive it by flipping option.selected. Chips show what is
  // currently chosen, which the bare listbox never did.
  // -------------------------------------------------------------------------
  function initCountryPicker(form) {
    var wrap = form.querySelector("[data-countrypicker]");
    if (!wrap) { return; }
    var select = wrap.querySelector("select[multiple]");
    if (!select || !select.options.length) { return; }

    var baseId = select.id;
    var hint = document.getElementById(baseId + "-hint");
    var label = wrap.querySelector("label[for='" + baseId + "']");

    /* Every user-facing string this control builds comes off the element, so
     * it goes through the page's translation catalogue instead of being
     * hardcoded English in a JS bundle. Same idiom as content_form.html's
     * data-label-* attributes. The fallbacks only fire if the template and
     * this file ever get out of step. */
    function text(name, fallback) {
      return wrap.getAttribute("data-label-" + name) || fallback;
    }
    var LABEL_REMOVE = text("remove", "Remove");
    var LABEL_ADDED = text("added", "{name} added, {count} selected");
    var LABEL_REMOVED = text("removed", "{name} removed, {count} selected");
    var LABEL_MATCHES = text("matches", "{count} countries match");
    var LABEL_SEARCH = text("search", "Type to search…");

    var chips = document.createElement("ul");
    chips.className = "cs-chips";
    chips.id = baseId + "-chips";

    var empty = document.createElement("p");
    empty.className = "cs-chips-empty";
    empty.textContent = text("empty", "No countries selected yet.");

    var combo = document.createElement("div");
    combo.className = "cs-combobox";

    var input = document.createElement("input");
    input.type = "text";
    input.id = baseId + "-input";
    input.className = "cs-combobox-input";
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-controls", baseId + "-listbox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("autocomplete", "off");
    input.setAttribute("placeholder", LABEL_SEARCH);
    if (hint) {
      input.setAttribute("aria-describedby", hint.id);
      var enhanced = hint.getAttribute("data-hint-enhanced");
      if (enhanced) { hint.textContent = enhanced; }
    }

    var listbox = document.createElement("ul");
    listbox.id = baseId + "-listbox";
    listbox.className = "cs-listbox";
    listbox.setAttribute("role", "listbox");
    listbox.setAttribute("aria-multiselectable", "true");
    listbox.hidden = true;

    var live = document.createElement("p");
    live.className = "cs-visually-hidden";
    live.setAttribute("aria-live", "polite");

    // Build every option row ONCE. Filtering then toggles `hidden` on ~210
    // nodes, which is far cheaper than rebuilding the list on each keystroke
    // and keeps the ids stable for aria-activedescendant.
    var rows = [];
    var fragment = document.createDocumentFragment();
    slice(select.options).forEach(function (option, index) {
      var li = document.createElement("li");
      li.id = baseId + "-o" + index;
      li.className = "cs-option";
      li.setAttribute("role", "option");
      li.setAttribute("data-value", option.value);
      li.setAttribute("aria-selected", option.selected ? "true" : "false");
      li.textContent = option.text;
      fragment.appendChild(li);
      rows.push({ el: li, option: option, haystack: fold(option.text) });
    });
    listbox.appendChild(fragment);

    combo.appendChild(input);
    combo.appendChild(listbox);
    select.hidden = true;
    select.setAttribute("tabindex", "-1");
    select.parentNode.insertBefore(chips, select.nextSibling);
    chips.parentNode.insertBefore(empty, chips.nextSibling);
    empty.parentNode.insertBefore(combo, empty.nextSibling);
    if (hint && hint.parentNode) {
      hint.parentNode.insertBefore(live, hint.nextSibling);
    }
    if (label) { label.setAttribute("for", input.id); }

    var active = -1;

    function visibleRows() {
      return rows.filter(function (row) { return !row.el.hidden; });
    }

    function announce(message) {
      live.textContent = message;
    }

    function renderChips() {
      var selected = rows.filter(function (row) {
        return row.option.selected;
      });
      chips.textContent = "";
      selected.forEach(function (row) {
        var li = document.createElement("li");
        li.className = "cs-chip";
        var text = document.createElement("span");
        text.textContent = row.option.text;
        var remove = document.createElement("button");
        remove.type = "button";
        remove.className = "cs-chip-remove";
        remove.setAttribute("data-value", row.option.value);
        // The visible glyph is decorative; the accessible name carries the
        // country, so a screen reader hears "Remove Chile", not "Remove".
        remove.setAttribute("aria-label",
          LABEL_REMOVE + " " + row.option.text);
        remove.textContent = "×";
        li.appendChild(text);
        li.appendChild(remove);
        chips.appendChild(li);
      });
      empty.hidden = selected.length > 0;
      return selected.length;
    }

    function setSelected(row, selected) {
      row.option.selected = selected;
      row.el.setAttribute("aria-selected", selected ? "true" : "false");
      var count = renderChips();
      announce((selected ? LABEL_ADDED : LABEL_REMOVED)
        .replace("{name}", row.option.text)
        .replace("{count}", String(count)));
    }

    function setActive(index) {
      var visible = visibleRows();
      rows.forEach(function (row) { row.el.classList.remove("is-active"); });
      if (index < 0 || index >= visible.length) {
        active = -1;
        input.removeAttribute("aria-activedescendant");
        return;
      }
      active = index;
      var row = visible[index];
      row.el.classList.add("is-active");
      input.setAttribute("aria-activedescendant", row.el.id);
      // Keep the roving option inside the scrollport without moving the page.
      var top = row.el.offsetTop;
      var bottom = top + row.el.offsetHeight;
      if (top < listbox.scrollTop) { listbox.scrollTop = top; }
      else if (bottom > listbox.scrollTop + listbox.clientHeight) {
        listbox.scrollTop = bottom - listbox.clientHeight;
      }
    }

    function open() {
      if (!listbox.hidden) { return; }
      listbox.hidden = false;
      input.setAttribute("aria-expanded", "true");
    }

    function close() {
      listbox.hidden = true;
      input.setAttribute("aria-expanded", "false");
      setActive(-1);
    }

    function filter() {
      var term = fold(input.value.trim());
      var shown = 0;
      rows.forEach(function (row) {
        var match = !term || row.haystack.indexOf(term) !== -1;
        row.el.hidden = !match;
        if (match) { shown += 1; }
      });
      setActive(shown ? 0 : -1);
      return shown;
    }

    input.addEventListener("focus", function () { open(); filter(); });

    input.addEventListener("input", function () {
      open();
      announce(LABEL_MATCHES.replace("{count}", String(filter())));
    });

    input.addEventListener("keydown", function (event) {
      var visible = visibleRows();
      switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        open();
        setActive(active + 1 >= visible.length ? 0 : active + 1);
        break;
      case "ArrowUp":
        event.preventDefault();
        open();
        setActive(active - 1 < 0 ? visible.length - 1 : active - 1);
        break;
      case "Home":
        if (!listbox.hidden) { event.preventDefault(); setActive(0); }
        break;
      case "End":
        if (!listbox.hidden) {
          event.preventDefault();
          setActive(visible.length - 1);
        }
        break;
      case "Enter":
        // Swallow Enter whenever the list is open, EVEN WITH NO MATCHES.
        // Guarding on `active >= 0` let a misspelled search fall through to
        // the form-level handler, which advanced the stage -- so a typo
        // navigated you off the page you were filling in.
        if (!listbox.hidden) {
          event.preventDefault();
          if (active >= 0) {
            setSelected(visible[active], !visible[active].option.selected);
          }
        }
        break;
      case "Escape":
        if (!listbox.hidden) { event.preventDefault(); close(); }
        break;
      case "Backspace":
        if (!input.value) {
          var selected = rows.filter(function (r) { return r.option.selected; });
          if (selected.length) {
            event.preventDefault();
            setSelected(selected[selected.length - 1], false);
          }
        }
        break;
      default:
        break;
      }
    });

    listbox.addEventListener("mousedown", function (event) {
      // mousedown, not click: the input must not lose focus first.
      var li = event.target.closest(".cs-option");
      if (!li) { return; }
      event.preventDefault();
      var row = rows.filter(function (r) { return r.el === li; })[0];
      if (row) { setSelected(row, !row.option.selected); }
    });

    chips.addEventListener("click", function (event) {
      var button = event.target.closest(".cs-chip-remove");
      if (!button) { return; }
      var value = button.getAttribute("data-value");
      var row = rows.filter(function (r) {
        return r.option.value === value;
      })[0];
      if (!row) { return; }
      // Focus must not fall to <body> when the chip disappears from under it.
      var buttons = slice(chips.querySelectorAll(".cs-chip-remove"));
      var next = buttons[buttons.indexOf(button) + 1];
      setSelected(row, false);
      if (next && next.parentNode) { next.focus(); } else { input.focus(); }
    });

    document.addEventListener("click", function (event) {
      if (!combo.contains(event.target) && event.target !== input) { close(); }
    });

    renderChips();
  }

  // -------------------------------------------------------------------------
  // The server-rendered error summary: focus it, and make its links jump to
  // the stage that actually holds the offending field.
  // -------------------------------------------------------------------------
  function initErrorSummary(showStep) {
    var summary = document.getElementById("cs-form-errors");
    if (!summary) { return; }
    summary.focus();
    summary.addEventListener("click", function (event) {
      var link = event.target.closest("a[data-step]");
      if (!link) { return; }
      event.preventDefault();
      var target = document.querySelector(link.getAttribute("href"));
      if (showStep) {
        showStep(parseInt(link.getAttribute("data-step"), 10),
                 { focus: false });
      }
      if (target) { target.focus(); }
    });
  }

  function init() {
    var form = document.getElementById("cs-project-form");
    if (!form) { return; }
    var showStep = null;
    try { showStep = initWizard(form); } catch (error) { /* long form */ }
    try { initCountryPicker(form); } catch (error) { /* native select */ }
    try { initErrorSummary(showStep); } catch (error) { /* summary is static */ }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
