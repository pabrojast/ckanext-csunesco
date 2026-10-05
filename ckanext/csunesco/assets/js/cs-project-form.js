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

  function visibleField(field) {
    if (!field.hidden) { return field; }
    var host = field.closest('.cs-field');
    return document.getElementById(field.id + '-input')
      || (host && host.querySelector('.cs-rt-area')) || field;
  }

  function setFieldError(field, message) {
    var id = clientErrorId(field);
    var existing = document.getElementById(id);
    var described = (field.getAttribute("aria-describedby") || "")
      .split(/\s+/).filter(function (token) { return token && token !== id; });

    if (!message) {
      if (existing) { existing.parentNode.removeChild(existing); }
      field.removeAttribute("aria-invalid");
      visibleField(field).removeAttribute('aria-invalid');
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
    visibleField(field).setAttribute('aria-invalid', 'true');
    visibleField(field).setAttribute('aria-describedby', described.join(' '));
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
    var checked = {};
    var pending = null;
    var approvedSubmission = false;
    var status = document.getElementById('cs-validation-status');
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
        item.setAttribute("data-state", checked[no] ? "done" : "todo");
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
        if (stepEl.classList.contains("is-active")) { visibleField(firstBad).focus(); }
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

    function busy(value) {
      slice(form.querySelectorAll('.cs-wizard-next, #cs-req-submit')).forEach(function (button) {
        button.disabled = value;
      });
      form.setAttribute('aria-busy', String(value));
    }

    function cancelCheck() {
      if (pending) { pending.abort(); pending = null; }
      busy(false);
      if (status) { status.textContent = ''; }
    }

    async function checkServer(step) {
      if (pending) { return false; }
      var controller = new AbortController();
      pending = controller;
      var body = new FormData();
      new FormData(form).forEach(function (value, key) {
        if (typeof value === 'string') { body.append(key, value); }
      });
      body.set('step', String(step));
      busy(true);
      if (status) { status.textContent = form.dataset.validating; }
      var timeout = setTimeout(function () { controller.abort(); }, 20000);
      try {
        var response = await fetch(form.dataset.validateUrl, {
          method: 'POST', body: body, credentials: 'same-origin',
          headers: { Accept: 'application/json' }, signal: controller.signal
        });
        // A login in another tab rotates the CSRF token. Refresh only that
        // token, preserving every field and selected file in this form.
        if (response.status === 400) {
          var fresh = await fetch(form.action, { credentials: 'same-origin', signal: controller.signal });
          var documentCopy = new DOMParser().parseFromString(await fresh.text(), 'text/html');
          var token = documentCopy.querySelector('#cs-project-form input[name="_csrf_token"]');
          var currentToken = form.querySelector('input[name="_csrf_token"]');
          if (fresh.ok && token && currentToken) {
            currentToken.value = token.value;
            body.set(currentToken.name, token.value);
            response = await fetch(form.dataset.validateUrl, {
              method: 'POST', body: body, credentials: 'same-origin',
              headers: { Accept: 'application/json' }, signal: controller.signal
            });
          }
        }
        if (!response.ok) {
          throw new Error(response.status === 401 || response.status === 403 || response.status === 400
            ? form.dataset.sessionError : form.dataset.validationError);
        }
        var result = await response.json();
        if (pending !== controller) { return false; }
        if (typeof result.valid !== 'boolean' || !result.errors) { throw new Error(); }
        var scope = step === 'all' ? form : activeStep();
        slice(scope.querySelectorAll('.cs-field-error')).forEach(function (el) { el.remove(); });
        slice(scope.querySelectorAll('input, select, textarea')).forEach(function (field) { setFieldError(field, null); });
        var first = null;
        Object.keys(result.errors).forEach(function (name) {
          var field = slice(form.elements).find(function (el) { return el.name === name; });
          if (!field) { return; }
          var messages = result.errors[name];
          setFieldError(field, Array.isArray(messages) ? messages.join(' ') : String(messages));
          if (!first) { first = field; }
          var section = field.closest('.cs-step');
          if (section) { delete checked[stepNumber(section)]; }
        });
        if (status) { status.textContent = ''; }
        if (first) {
          showStep(stepNumber(first.closest('.cs-step')), { focus: false });
          visibleField(first).focus();
        } else if (!result.valid) { throw new Error(); }
        return result.valid;
      } catch (error) {
        if (pending === controller && status) {
          status.textContent = error.message && error.name !== 'AbortError'
            && error.message === form.dataset.sessionError ? error.message : form.dataset.validationError;
          status.focus();
        }
        return false;
      } finally {
        clearTimeout(timeout);
        if (pending === controller) { pending = null; busy(false); }
      }
    }

    async function nextStep() {
      if (!validateStep(activeStep())) { return; }
      var before = current;
      if (await checkServer(current)) {
        checked[before] = true;
        showStep(before + 1);
      }
    }

    form.addEventListener('input', function (event) {
      var section = event.target.closest('.cs-step');
      if (section) { delete checked[stepNumber(section)]; }
      cancelCheck();
    });
    form.addEventListener('change', cancelCheck);

    form.addEventListener("click", function (event) {
      var next = event.target.closest(".cs-wizard-next");
      if (next) {
        nextStep();
        return;
      }
      // Back never validates: being unable to leave a stage you already left
      // would be a trap.
      if (event.target.closest(".cs-wizard-prev")) { cancelCheck(); showStep(current - 1); }
    });

    /* Every nav button is type="button", so Enter in a text field would fall
     * through to the form's default submit -- the real Submit at the end of
     * stage 5, even while it is display:none. Enter should advance instead,
     * until the last stage. */
    form.addEventListener("keydown", function (event) {
      if (event.key !== "Enter" || event.defaultPrevented) { return; }
      var target = event.target;
      var tag = (target.tagName || "").toLowerCase();
      if (target.isContentEditable || tag === "textarea" || tag === "button"
          || target.type === "submit") { return; }
      if (current < steps.length) {
        event.preventDefault();
        nextStep();
      }
    });

    form.addEventListener("submit", async function (event) {
      /* "Save for later" carries formnovalidate: a draft must be savable
       * half-filled, so client validation is skipped and the server applies
       * only its lenient rules. */
      var submitter = event.submitter;
      if (submitter && submitter.hasAttribute("formnovalidate")) { cancelCheck(); return; }
      if (!approvedSubmission) {
        event.preventDefault();
      var badStep = validateAll();
      if (badStep) {
        event.preventDefault();
        showStep(badStep, { focus: false });
        if (lastBadField) { visibleField(lastBadField).focus(); }
        return;
      }
        if (await checkServer('all')) {
          approvedSubmission = true;
          form.requestSubmit(submitter || undefined);
        }
        return;
      }
      if (submit) {
        if (submitter && submitter.name) {
          var action = document.createElement('input');
          action.type = 'hidden'; action.name = submitter.name; action.value = submitter.value;
          form.appendChild(action);
        }
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
      select.dispatchEvent(new Event('change', { bubbles: true }));
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
      if (target) { visibleField(target).focus(); }
    });
  }

  function initDataAccess(form) {
    var root = form.querySelector('[data-cs-da]');
    if (!root) return;
    var reason = root.querySelector('[data-da-justification]');
    var textarea = reason.querySelector('textarea');
    function sync() {
      var selected = root.querySelector('input[name="data_access"]:checked');
      var value = selected ? selected.value : null;
      var needsReason = value === 'private' || value === 'confidential';
      slice(root.querySelectorAll('[data-da-option]')).forEach(function (panel) {
        panel.hidden = panel.getAttribute('data-da-option') !== value;
      });
      reason.hidden = !needsReason;
      textarea.disabled = !needsReason;
      textarea.required = needsReason;
      textarea.setAttribute('aria-required', String(needsReason));
      if (!needsReason) setFieldError(textarea, '');
    }
    slice(root.querySelectorAll('input[name="data_access"]')).forEach(function (radio) {
      radio.addEventListener('change', sync);
    });
    sync();
  }

  function initInitiative(form) {
    var select = form.elements.initiative;
    var input = form.elements.external_initiative_name;
    var host = document.getElementById('cs-external-initiative-field');
    function sync() {
      var external = select.value === '__external__';
      host.hidden = !external;
      input.disabled = !external;
      input.required = external;
      if (!external) { input.value = ''; setFieldError(input, null); }
    }
    select.addEventListener('change', sync);
    sync();
    var slug = form.elements.slug;
    if (slug) {
      function check() {
        slug.setCustomValidity('');
        if (slug.validity.patternMismatch) { slug.setCustomValidity(slug.dataset.formatError); }
      }
      slug.addEventListener('input', check);
      check();
    }
  }

  function initEditors(form) {
    var picker = document.getElementById('cs-editor-picker');
    var input = document.getElementById('cs-editor-search');
    var raw = form.elements.editors;
    var results = document.getElementById('cs-editor-results');
    var selected = document.getElementById('cs-editor-selected');
    var status = document.getElementById('cs-editor-status');
    var labels = {};
    var timer, controller;
    picker.hidden = false;
    function names() { return raw.value.split(/[,\s]+/).filter(Boolean); }
    function render() {
      selected.textContent = '';
      names().forEach(function (name) {
        var li = document.createElement('li');
        li.className = 'cs-chip';
        li.appendChild(document.createTextNode(labels[name] || name));
        var button = document.createElement('button');
        button.type = 'button';
        button.className = 'cs-chip-remove';
        button.textContent = '×';
        button.setAttribute('aria-label', picker.dataset.remove + ' ' + name);
        button.addEventListener('click', function () {
          raw.value = names().filter(function (value) { return value !== name; }).join(', ');
          raw.dispatchEvent(new Event('input', { bubbles: true }));
          input.focus();
        });
        li.appendChild(button);
        selected.appendChild(li);
      });
    }
    raw.addEventListener('input', render);
    input.addEventListener('keydown', function (event) {
      if (event.key === 'Enter') { event.preventDefault(); }
      if (event.key === 'ArrowDown') {
        var first = results.querySelector('button:not(:disabled)');
        if (first) { event.preventDefault(); first.focus(); }
      }
      if (event.key === 'Escape') { results.textContent = ''; }
    });
    results.addEventListener('keydown', function (event) {
      var buttons = slice(results.querySelectorAll('button:not(:disabled)'));
      var index = buttons.indexOf(event.target);
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        var next = index + (event.key === 'ArrowDown' ? 1 : -1);
        if (buttons[next]) { buttons[next].focus(); } else { input.focus(); }
      }
      if (event.key === 'Escape') { results.textContent = ''; input.focus(); }
    });
    input.addEventListener('input', function () {
      clearTimeout(timer);
      if (controller) { controller.abort(); }
      results.textContent = '';
      status.textContent = '';
      var query = input.value.trim();
      if (query.length < 2) { return; }
      timer = setTimeout(async function () {
        var request = new AbortController();
        controller = request;
        status.textContent = picker.dataset.loading;
        var timeout = setTimeout(function () { request.abort(); }, 15000);
        try {
          var url = new URL(form.dataset.editorsUrl, window.location.href);
          url.searchParams.set('q', query);
          if (form.elements.project_id) { url.searchParams.set('project_id', form.elements.project_id.value); }
          var response = await fetch(url, { credentials: 'same-origin', signal: request.signal });
          if (!response.ok) { throw new Error(); }
          var data = await response.json();
          if (controller !== request || input.value.trim() !== query) { return; }
          status.textContent = data.results.length ? '' : picker.dataset.empty;
          data.results.forEach(function (user) {
            var label = user.fullname ? user.fullname + ' (' + user.name + ')' : user.name;
            labels[user.name] = label;
            var li = document.createElement('li');
            var button = document.createElement('button');
            button.type = 'button';
            button.textContent = label;
            button.disabled = names().indexOf(user.name) !== -1 || user.name === form.dataset.owner;
            if (user.name === form.dataset.owner) { button.textContent += ' — ' + picker.dataset.ownerLabel; }
            button.addEventListener('click', function () {
              if (names().indexOf(user.name) === -1) {
                raw.value = names().concat([user.name]).join(', ');
                raw.dispatchEvent(new Event('input', { bubbles: true }));
              }
              results.textContent = ''; input.value = ''; input.focus();
            });
            li.appendChild(button); results.appendChild(li);
          });
        } catch (error) {
          if (controller === request && input.value.trim() === query) { status.textContent = picker.dataset.error; }
        } finally { clearTimeout(timeout); }
      }, 300);
    });
    render();
  }

  function init() {
    var form = document.getElementById("cs-project-form");
    if (!form) { return; }
    try { initDataAccess(form); } catch (error) { /* server validation remains authoritative */ }
    try { initInitiative(form); } catch (error) { /* server validation remains authoritative */ }
    try { initEditors(form); } catch (error) { /* exact username input remains available */ }
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
