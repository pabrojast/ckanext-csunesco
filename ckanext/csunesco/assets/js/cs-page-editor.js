/*
 * ckanext-csunesco -- project-page editor enhancements.
 *
 * Everything here is OPTIONAL. The editor is built so the JavaScript-free path
 * is the primary one: add / move / delete / hide / save / publish are all real
 * submit buttons posting to the same endpoint. This file only makes that nicer.
 *
 * Four enhancements:
 *   1. A small formatting toolbar over a contenteditable mirror of each
 *      textarea. The TEXTAREA remains the field that submits -- a
 *      contenteditable posts nothing, so it can never be the source of truth.
 *   2. A field picker for chart blocks, populated from the chosen data source's
 *      own field list. Without it the field is still a plain text input.
 *   3. Confirmation before a destructive op, an unsaved-changes guard, and the
 *      pressed op mirrored into `op_js` -- a `disabled` button contributes
 *      neither name nor value, so disable-on-submit would otherwise swallow the
 *      operation. `op_js` is a SEPARATE name from the buttons' `op` on purpose;
 *      sharing one made the server's answer depend on DOM order.
 *   4. Immediate local previews for image uploads and pasted image URLs.
 *
 * The server re-parses, re-validates and re-sanitizes everything regardless.
 */
(function () {
  "use strict";

  // Mirrors sanitize.PAGE_ALLOWED_TAGS. This only keeps the author's own
  // preview tidy; the SERVER decides what is actually stored.
  var ALLOWED = {
    B: 1, I: 1, EM: 1, STRONG: 1, U: 1, S: 1, SUB: 1, SUP: 1, CODE: 1, A: 1,
    P: 1, UL: 1, OL: 1, LI: 1, BR: 1, HR: 1, H3: 1, H4: 1, H5: 1,
    BLOCKQUOTE: 1, PRE: 1, TABLE: 1, THEAD: 1, TBODY: 1, TFOOT: 1, TR: 1,
    TH: 1, TD: 1, CAPTION: 1
  };

  function stripToAllowed(node) {
    Array.prototype.slice.call(node.childNodes).forEach(function (child) {
      if (child.nodeType !== 1) { return; }
      if (!ALLOWED[child.tagName]) {
        child.parentNode.replaceChild(
          document.createTextNode(child.textContent || ""), child);
        return;
      }
      Array.prototype.slice.call(child.attributes).forEach(function (attr) {
        var keep = (child.tagName === "A" &&
              (attr.name === "href" || attr.name === "title" ||
               attr.name === "rel")) ||
          (child.tagName === "TH" && attr.name === "scope");
        if (!keep) { child.removeAttribute(attr.name); }
      });
      stripToAllowed(child);
    });
  }

  // label, command, argument. execCommand is deprecated but is the only
  // universally supported way to do this without pulling in an editor library.
  var TOOLS = [
    ["B", "bold", null, "Bold"],
    ["I", "italic", null, "Italic"],
    ["H3", "formatBlock", "<h3>", "Heading"],
    ["¶", "formatBlock", "<p>", "Paragraph"],
    ["• List", "insertUnorderedList", null, "Bulleted list"],
    ["1. List", "insertOrderedList", null, "Numbered list"],
    ["Link", "createLink", null, "Add a link"],
    ["Clear", "removeFormat", null, "Remove formatting"]
  ];

  function buildToolbar(area, textarea) {
    var bar = document.createElement("div");
    bar.className = "cs-rt-toolbar";
    bar.setAttribute("role", "toolbar");

    TOOLS.forEach(function (tool) {
      var button = document.createElement("button");
      button.type = "button";               // never submits the form
      button.className = "cs-rt-btn";
      button.textContent = tool[0];
      button.title = tool[3];
      button.setAttribute("aria-label", tool[3]);
      button.addEventListener("click", function () {
        area.focus();
        var argument = tool[2];
        if (tool[1] === "createLink") {
          var url = window.prompt(tool[3], "https://");
          if (!url) { return; }
          argument = url;
        }
        try {
          document.execCommand(tool[1], false, argument);
        } catch (error) { /* older browser: leave the text as typed */ }
        sync(area, textarea);
      });
      bar.appendChild(button);
    });
    return bar;
  }

  function sync(area, textarea) {
    var scratch = document.createElement("div");
    scratch.innerHTML = area.innerHTML;
    stripToAllowed(scratch);
    textarea.value = scratch.innerHTML;
  }

  function initRichText(form) {
    var fields = form.querySelectorAll("textarea.cs-pb-richtext");
    Array.prototype.forEach.call(fields, function (textarea) {
      var area = document.createElement("div");
      area.className = "cs-rt-area";
      area.contentEditable = "true";
      area.innerHTML = textarea.value;
      area.setAttribute("role", "textbox");
      area.setAttribute("aria-multiline", "true");
      var label = form.querySelector('label[for="' + textarea.id + '"]');
      if (label) { area.setAttribute("aria-label", label.textContent.trim()); }

      // Paste as PLAIN text: pasting from a word processor otherwise drags in
      // a mountain of markup the server would strip anyway, leaving the author
      // looking at a preview that does not match what gets saved.
      area.addEventListener("paste", function (event) {
        event.preventDefault();
        var text = (event.clipboardData || window.clipboardData)
          .getData("text/plain");
        document.execCommand("insertText", false, text);
      });
      area.addEventListener("input", function () { sync(area, textarea); });
      area.addEventListener("blur", function () { sync(area, textarea); });

      textarea.hidden = true;
      textarea.parentNode.insertBefore(buildToolbar(area, textarea), textarea);
      textarea.parentNode.insertBefore(area, textarea);
      // One last sync at submit time, in case a toolbar command left the
      // textarea a keystroke behind.
      form.addEventListener("submit", function () { sync(area, textarea); });
    });
  }

  /**
   * Populate a chart block's field picker from its data source.
   * Falls back silently: the input is a plain text field either way.
   */
  function initChartPickers(form) {
    if (!window.fetch) { return; }
    var editors = form.querySelectorAll("[data-chart-editor]");
    Array.prototype.forEach.call(editors, function (editor) {
      var index = editor.getAttribute("data-index");
      var select = form.querySelector(
        '[name="blocks[' + index + '][data_source_id]"]');
      var list = document.getElementById("b" + index + "-chart-fieldlist");
      var hint = document.getElementById("b" + index + "-chart-field-hint");
      var mode = document.getElementById("b" + index + "-chart-mode");
      if (!select || !list) { return; }

      function load() {
        var id = select.value;
        list.innerHTML = "";
        if (!id) { return; }
        fetch("/citizen-science/data/" + encodeURIComponent(id) + "/fields",
              { credentials: "same-origin" })
          .then(function (response) {
            if (!response.ok) { throw new Error("HTTP " + response.status); }
            return response.json();
          })
          .then(function (payload) {
            var wanted = (mode && mode.value === "category")
              ? payload.categorical
              : payload.numeric;
            (wanted || []).forEach(function (field) {
              var option = document.createElement("option");
              option.value = field.name;
              option.label = field.label || field.name;
              list.appendChild(option);
            });
            if (hint && payload.total) {
              hint.textContent = payload.total + " observations, " +
                (payload.first_date || "?") + " to " + (payload.last_date || "?");
            }
          })
          .catch(function () { /* keep the plain text input */ });
      }

      select.addEventListener("change", load);
      if (mode) { mode.addEventListener("change", load); }
      load();
    });
  }

  /**
   * Confirm destructive ops, and mirror the pressed op into `op_js`.
   *
   * A disabled button submits neither its name nor its value, so a future
   * disable-on-submit needs a second carrier. It has its OWN name: sharing one
   * name with the buttons made the server's answer depend on DOM order, and it
   * silently turned every button into "save" for anyone without JavaScript.
   */
  function initOps(form) {
    var hidden = document.getElementById("cs-pb-op");
    var buttons = form.querySelectorAll('button[name="op"]');
    Array.prototype.forEach.call(buttons, function (button) {
      button.addEventListener("click", function (event) {
        // aria-disabled keeps the control focusable and announced, so the
        // reason for it being unavailable is reachable -- but it must not act.
        if (button.getAttribute("aria-disabled") === "true") {
          event.preventDefault();
          return;
        }
        var confirmMessage = button.getAttribute("data-confirm-op");
        if (confirmMessage && !window.confirm(confirmMessage)) {
          event.preventDefault();
          // Never leave a stale op behind: it would ride along on a later
          // submission that carries no button of its own.
          if (hidden) { hidden.value = ""; }
          return;
        }
        if (hidden) { hidden.value = button.value; }
      });
    });
  }

  /**
   * Warn before leaving with unsaved edits.
   *
   * The editor is a long form; losing it to a stray click on the site nav is
   * the most expensive mistake available here. Submitting the form is not
   * "leaving" -- the flag is cleared first.
   */
  function initUnsavedGuard(form) {
    var dirty = false;
    form.addEventListener("input", function () { dirty = true; });
    form.addEventListener("change", function () { dirty = true; });
    form.addEventListener("submit", function () { dirty = false; });
    window.addEventListener("beforeunload", function (event) {
      if (!dirty) { return undefined; }
      // Browsers show their own wording; returning a string is what arms it.
      event.preventDefault();
      event.returnValue = "";
      return "";
    });
  }

  /**
   * One button to open or close every section at once.
   *
   * Injected here rather than in the template because without JavaScript it
   * could do nothing -- a dead control is worse than none. type="button": it
   * must never submit the form.
   */
  function initToggleAll(form) {
    var list = form.querySelector(".cs-pb-list");
    var all = form.querySelectorAll(".cs-pb-details");
    if (!list || all.length < 2) { return; }

    var button = document.createElement("button");
    button.type = "button";
    button.className = "cs-btn cs-btn-secondary cs-btn-sm cs-pb-toggle-all";

    function anyClosed() {
      return Array.prototype.some.call(all, function (details) {
        return !details.open;
      });
    }
    function relabel() {
      button.textContent = anyClosed()
        ? button.getAttribute("data-open-label")
        : button.getAttribute("data-close-label");
    }
    // Labels ride on data attributes so a future i18n pass has one place to
    // inject translations; English is the fallback.
    button.setAttribute("data-open-label", "Open all sections");
    button.setAttribute("data-close-label", "Close all sections");

    button.addEventListener("click", function () {
      var open = anyClosed();
      Array.prototype.forEach.call(all, function (details) {
        details.open = open;
      });
      relabel();
    });
    Array.prototype.forEach.call(all, function (details) {
      details.addEventListener("toggle", relabel);
    });
    relabel();
    list.parentNode.insertBefore(button, list);
  }

  /**
   * Highlight in the spine the section currently on screen.
   *
   * Pure orientation -- the anchors already work without it. Skipped silently
   * where IntersectionObserver is missing.
   */
  function initSpineSpy() {
    if (!window.IntersectionObserver) { return; }
    var links = document.querySelectorAll(".cs-pb-jump-list a[href^='#']");
    if (!links.length) { return; }
    var byId = {};
    Array.prototype.forEach.call(links, function (link) {
      byId[link.getAttribute("href").slice(1)] = link;
    });

    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        var link = byId[entry.target.id];
        if (!link) { return; }
        if (entry.isIntersecting) {
          Array.prototype.forEach.call(links, function (other) {
            other.classList.remove("is-current");
          });
          link.classList.add("is-current");
        }
      });
    }, { rootMargin: "-20% 0px -70% 0px" });

    Object.keys(byId).forEach(function (id) {
      var target = document.getElementById(id);
      if (target) { observer.observe(target); }
    });
  }

  function initGalleryEditors(form) {
    Array.prototype.forEach.call(form.querySelectorAll("[data-gallery-items]"),
      function (container) {
        function reindex() {
          var items = Array.prototype.slice.call(
            container.querySelectorAll("[data-gallery-item]"));
          items.forEach(function (item, index) {
            var legend = item.querySelector("legend");
            if (legend) { legend.textContent = "Image " + (index + 1); }
            Array.prototype.forEach.call(item.querySelectorAll("[name]"), function (field) {
              field.name = field.name.replace(/(\[items\]\[)\d+(\])/, "$1" + index + "$2");
            });
            var up = item.querySelector("[data-gallery-up]");
            var down = item.querySelector("[data-gallery-down]");
            if (up) { up.disabled = index === 0; }
            if (down) { down.disabled = index === items.length - 1; }
          });
        }
        container.addEventListener("click", function (event) {
          var button = event.target.closest("[data-gallery-up], [data-gallery-down]");
          if (!button) { return; }
          var item = button.closest("[data-gallery-item]");
          if (button.hasAttribute("data-gallery-up") && item.previousElementSibling) {
            container.insertBefore(item, item.previousElementSibling);
          } else if (button.hasAttribute("data-gallery-down") && item.nextElementSibling) {
            container.insertBefore(item.nextElementSibling, item);
          }
          reindex();
        });
        reindex();
      });

    Array.prototype.forEach.call(form.querySelectorAll("[data-gallery-batch]"),
      function (batch) {
        var input = batch.querySelector("[data-gallery-batch-input]");
        var preview = batch.querySelector("[data-gallery-batch-preview]");
        if (!input || !preview) { return; }
        input.addEventListener("change", function () {
          preview.innerHTML = "";
          Array.prototype.forEach.call(input.files || [], function (file, index) {
            var figure = document.createElement("figure");
            figure.className = "cs-gallery-batch-item";
            var image = document.createElement("img");
            image.src = URL.createObjectURL(file);
            image.alt = "";
            var caption = document.createElement("figcaption");
            caption.textContent = (index + 1) + ". " + file.name;
            figure.appendChild(image); figure.appendChild(caption);
            preview.appendChild(figure);
          });
        });
      });
  }

  function init() {
    var form = document.getElementById("cs-page-form");
    if (!form) { return; }
    initRichText(form);
    initChartPickers(form);
    initOps(form);
    initUnsavedGuard(form);
    initToggleAll(form);
    initSpineSpy();
    initGalleryEditors(form);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
