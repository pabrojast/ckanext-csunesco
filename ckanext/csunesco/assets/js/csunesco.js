/*
 * ckanext-csunesco -- Citizen Science (UNESCO / IHP-WINS)
 *
 * Progressive enhancement for the admin approval panel and the content editor.
 * Everything here is optional: the server re-validates and re-sanitizes every
 * input, and the pages remain usable with JavaScript disabled (all admin panels
 * are visible, forms submit normally). Vanilla DOM only -- no jQuery required.
 */
(function () {
  "use strict";

  // Tags the editor preview is allowed to render (mirrors the SERVER allowlist
  // in logic/sanitize.py). The real sanitization is server-side; this only keeps
  // the author's own preview tidy and free of active markup.
  var ALLOWED_PREVIEW_TAGS = {
    B: 1, I: 1, EM: 1, STRONG: 1, A: 1, P: 1, UL: 1, OL: 1, LI: 1,
    BR: 1, H3: 1, H4: 1, BLOCKQUOTE: 1
  };

  // -------------------------------------------------------------------------
  // Tabs: show one panel at a time, track aria-selected / aria-current, and
  // restore the active tab from the URL fragment (survives the PRG redirect).
  // -------------------------------------------------------------------------
  function initTabs() {
    var tablist = document.querySelector(".cs-tabs[role='tablist']");
    if (!tablist) { return; }
    var tabs = Array.prototype.slice.call(tablist.querySelectorAll(".cs-tab"));
    if (!tabs.length) { return; }

    function activate(name) {
      var matched = false;
      tabs.forEach(function (tab) {
        var isActive = tab.getAttribute("data-tab") === name;
        tab.setAttribute("aria-selected", isActive ? "true" : "false");
        if (isActive) {
          tab.setAttribute("aria-current", "true");
          matched = true;
        } else {
          tab.removeAttribute("aria-current");
        }
        var panel = document.getElementById(tab.getAttribute("aria-controls"));
        if (panel) { panel.hidden = !isActive; }
      });
      return matched;
    }

    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        var name = tab.getAttribute("data-tab");
        activate(name);
        // Reflect the choice in the fragment so a later PRG re-opens it.
        if (window.history && window.history.replaceState) {
          window.history.replaceState(null, "", "#tab-" + name);
        }
      });
    });

    // Initial tab: from the fragment (#tab-xxx) if valid, else the first tab.
    var fromHash = (window.location.hash || "").replace(/^#tab-/, "");
    if (!fromHash || !activate(fromHash)) {
      activate(tabs[0].getAttribute("data-tab"));
    }
  }

  // -------------------------------------------------------------------------
  // Confirm dialogs on any form carrying a data-confirm message.
  // -------------------------------------------------------------------------
  function initConfirms() {
    var forms = document.querySelectorAll("form[data-confirm]");
    Array.prototype.forEach.call(forms, function (form) {
      form.addEventListener("submit", function (event) {
        var message = form.getAttribute("data-confirm");
        if (message && !window.confirm(message)) {
          event.preventDefault();
        }
      });
    });
  }

  // -------------------------------------------------------------------------
  // Content editor: end-date toggle, live preview, add-media, disable-on-submit.
  // -------------------------------------------------------------------------
  function stripToAllowed(node) {
    var children = Array.prototype.slice.call(node.childNodes);
    children.forEach(function (child) {
      if (child.nodeType === 1) {
        if (!ALLOWED_PREVIEW_TAGS[child.tagName]) {
          // Replace a disallowed element with its text content.
          child.parentNode.replaceChild(
            document.createTextNode(child.textContent || ""), child);
          return;
        }
        // Drop every attribute except href/title/rel on anchors.
        Array.prototype.slice.call(child.attributes).forEach(function (attr) {
          var keep = child.tagName === "A" &&
            (attr.name === "href" || attr.name === "title" || attr.name === "rel");
          if (!keep) { child.removeAttribute(attr.name); }
        });
        stripToAllowed(child);
      }
    });
  }

  function initEditor() {
    var form = document.getElementById("cs-content-form");
    if (!form) { return; }

    var typeSelect = document.getElementById("cs-content-type");
    var endField = document.getElementById("cs-enddate-field");
    var terriaField = document.getElementById("cs-terria-field");
    var publicationFields = document.getElementById("cs-publication-fields");
    var mediaLabel = document.getElementById("cs-media-label");
    var mediaHint = document.getElementById("cs-media-hint");
    var body = document.getElementById("cs-content-body");
    var preview = document.getElementById("cs-content-preview");
    var mediaList = document.getElementById("cs-media-list");
    var mediaAdd = document.getElementById("cs-media-add");
    var submit = document.getElementById("cs-content-submit");

    // Show/relabel the type-specific fields (end date for events, Terria link
    // for maps, authors/DOI + required document links for publications).
    function syncTypeFields() {
      if (!typeSelect) { return; }
      var type = typeSelect.value;
      if (endField) { endField.hidden = type !== "cs-event"; }
      if (terriaField) { terriaField.hidden = type !== "cs-map"; }
      if (publicationFields) {
        publicationFields.hidden = type !== "cs-publication";
      }
      var isPublication = type === "cs-publication";
      if (mediaLabel) {
        mediaLabel.textContent = isPublication
          ? mediaLabel.getAttribute("data-label-publication")
          : mediaLabel.getAttribute("data-label-default");
      }
      if (mediaHint) {
        mediaHint.textContent = isPublication
          ? mediaHint.getAttribute("data-hint-publication")
          : mediaHint.getAttribute("data-hint-default");
      }
    }
    if (typeSelect) {
      typeSelect.addEventListener("change", syncTypeFields);
      syncTypeFields();
    }

    // Live, allowlist-filtered preview of the body.
    function renderPreview() {
      if (!body || !preview) { return; }
      var scratch = document.createElement("div");
      scratch.innerHTML = body.value;
      stripToAllowed(scratch);
      preview.innerHTML = scratch.innerHTML;
    }
    if (body && preview) {
      body.addEventListener("input", renderPreview);
      renderPreview();
    }

    // Add another empty media URL input.
    if (mediaAdd && mediaList) {
      mediaAdd.addEventListener("click", function () {
        var input = document.createElement("input");
        input.type = "url";
        input.name = "media";
        input.className = "cs-media-input";
        input.placeholder = "https://example.org/…";
        input.setAttribute("aria-label", "Media URL");
        mediaList.appendChild(input);
        input.focus();
      });
    }

    // Disable the submit button to prevent double submission.
    if (submit) {
      form.addEventListener("submit", function () {
        submit.disabled = true;
        submit.classList.add("is-loading");
        var spinner = submit.querySelector(".cs-btn-spinner");
        if (spinner) { spinner.hidden = false; }
      });
    }
  }

  function init() {
    initTabs();
    initConfirms();
    initEditor();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
