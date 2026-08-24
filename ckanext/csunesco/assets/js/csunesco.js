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
    B: 1, I: 1, EM: 1, STRONG: 1, U: 1, A: 1, P: 1, UL: 1, OL: 1, LI: 1,
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

  // Shared image/focal picker used by project details and every page editor.
  function initImagePickers() {
    Array.prototype.forEach.call(
      document.querySelectorAll("[data-image-picker]"), function (picker) {
        var image = picker.querySelector("[data-image-preview]");
        var empty = picker.querySelector("[data-image-empty]");
        var url = picker.querySelector("[data-image-url]");
        var file = picker.querySelector("[data-image-file]");
        var clear = picker.querySelector("[data-image-clear]");
        var fallback = picker.getAttribute("data-image-fallback") || "";
        function show(src) {
          if (!image || !empty) { return; }
          if (src) {
            image.src = src; image.hidden = false; empty.hidden = true;
          } else {
            image.removeAttribute("src"); image.hidden = true; empty.hidden = false;
          }
        }
        if (url) { url.addEventListener("input", function () { show(url.value || fallback); }); }
        if (file) {
          file.addEventListener("change", function () {
            if (file.files && file.files[0]) { show(URL.createObjectURL(file.files[0])); }
          });
        }
        if (clear) { clear.addEventListener("change", function () {
          if (clear.checked) { show(fallback); }
        }); }
      });

    Array.prototype.forEach.call(
      document.querySelectorAll("[data-focal-picker]"), function (picker) {
        var preview = picker.querySelector("[data-focal-preview]");
        var marker = picker.querySelector("[data-focal-marker]");
        var x = picker.querySelector("[data-focal-x]");
        var y = picker.querySelector("[data-focal-y]");
        var url = document.getElementById(picker.getAttribute("data-image-url-id"));
        var file = document.getElementById(picker.getAttribute("data-image-file-id"));
        if (!preview || !marker || !x || !y) { return; }
        function sync() {
          marker.style.left = x.value + "%";
          marker.style.top = y.value + "%";
          preview.style.backgroundPosition = x.value + "% " + y.value + "%";
        }
        function position(clientX, clientY) {
          var box = preview.getBoundingClientRect();
          x.value = Math.max(0, Math.min(100, Math.round((clientX - box.left) * 100 / box.width)));
          y.value = Math.max(0, Math.min(100, Math.round((clientY - box.top) * 100 / box.height)));
          sync();
        }
        x.addEventListener("input", sync); y.addEventListener("input", sync);
        preview.addEventListener("pointerdown", function (event) {
          position(event.clientX, event.clientY);
          preview.setPointerCapture(event.pointerId);
        });
        preview.addEventListener("pointermove", function (event) {
          if (preview.hasPointerCapture(event.pointerId)) { position(event.clientX, event.clientY); }
        });
        preview.addEventListener("keydown", function (event) {
          var changed = true;
          if (event.key === "ArrowLeft") { x.value = Math.max(0, Number(x.value) - 1); }
          else if (event.key === "ArrowRight") { x.value = Math.min(100, Number(x.value) + 1); }
          else if (event.key === "ArrowUp") { y.value = Math.max(0, Number(y.value) - 1); }
          else if (event.key === "ArrowDown") { y.value = Math.min(100, Number(y.value) + 1); }
          else { changed = false; }
          if (changed) { event.preventDefault(); sync(); }
        });
        if (url) { url.addEventListener("input", function () {
          preview.style.backgroundImage = url.value ? "url(\"" + url.value.replace(/\"/g, "") + "\")" : "";
        }); }
        if (file) { file.addEventListener("change", function () {
          if (file.files && file.files[0]) {
            preview.style.backgroundImage = "url(\"" + URL.createObjectURL(file.files[0]) + "\")";
          }
        }); }
        sync();
      });
  }

  function initRichEditors() {
    var tools = [
      ["B", "bold", null, "Bold"], ["I", "italic", null, "Italic"],
      ["U", "underline", null, "Underline"],
      ["¶", "formatBlock", "p", "Paragraph"],
      ["H3", "formatBlock", "h3", "Heading"],
      ["• List", "insertUnorderedList", null, "Bulleted list"],
      ["1. List", "insertOrderedList", null, "Numbered list"],
      ["Quote", "formatBlock", "blockquote", "Quote"],
      ["Link", "createLink", null, "Add link"],
      ["Unlink", "unlink", null, "Remove link"],
      ["Undo", "undo", null, "Undo"], ["Redo", "redo", null, "Redo"]
    ];
    Array.prototype.forEach.call(
      document.querySelectorAll("textarea[data-rich-editor]"), function (source) {
        if (source.getAttribute("data-rt-ready")) { return; }
        source.setAttribute("data-rt-ready", "1");
        var area = document.createElement("div");
        area.className = "cs-rt-area";
        area.contentEditable = "true";
        area.setAttribute("role", "textbox");
        area.setAttribute("aria-multiline", "true");
        area.innerHTML = source.value;
        var toolbar = document.createElement("div");
        toolbar.className = "cs-rt-toolbar";
        toolbar.setAttribute("role", "toolbar");
        function sync() {
          var scratch = document.createElement("div");
          scratch.innerHTML = area.innerHTML;
          stripToAllowed(scratch);
          source.value = scratch.innerHTML;
          source.dispatchEvent(new Event("input", {bubbles: true}));
        }
        tools.forEach(function (tool) {
          var button = document.createElement("button");
          button.type = "button"; button.className = "cs-rt-btn";
          button.textContent = tool[0]; button.setAttribute("aria-label", tool[3]);
          button.title = tool[3];
          button.addEventListener("click", function () {
            area.focus(); var argument = tool[2];
            if (tool[1] === "createLink") {
              argument = window.prompt("https://", "https://");
              if (!argument) { return; }
            }
            try { document.execCommand(tool[1], false, argument); } catch (error) {}
            if (tool[1] === "formatBlock") {
              try { document.execCommand(tool[1], false, "<" + argument + ">"); } catch (error) {}
            }
            sync();
          });
          toolbar.appendChild(button);
        });
        area.addEventListener("paste", function (event) {
          event.preventDefault();
          var text = (event.clipboardData || window.clipboardData)
            .getData("text/plain");
          try { document.execCommand("insertText", false, text); } catch (error) {}
          sync();
        });
        area.addEventListener("input", sync);
        // Accesibilidad: el label y el hint del form apuntan al textarea, que
        // queda OCULTO — sin esto el contenteditable (role=textbox) no tiene
        // nombre ni descripción accesibles.
        if (source.id) {
          var srcLabel = document.querySelector('label[for="' + source.id + '"]');
          if (srcLabel) {
            if (!srcLabel.id) { srcLabel.id = source.id + "-label"; }
            area.setAttribute("aria-labelledby", srcLabel.id);
          }
        }
        var describedBy = source.getAttribute("aria-describedby");
        if (describedBy) { area.setAttribute("aria-describedby", describedBy); }
        source.hidden = true;
        source.parentNode.insertBefore(toolbar, source);
        source.parentNode.insertBefore(area, source);
        if (source.form) { source.form.addEventListener("submit", sync); }
      });
  }

  // -------------------------------------------------------------------------
  // Content editor: end-date toggle, add-media, disable-on-submit. (El rich
  // editor ES la vista previa: la caja "Preview" aparte se eliminó.)
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
    var newsFields = document.getElementById("cs-news-fields");
    var mediaLabel = document.getElementById("cs-media-label");
    var mediaHint = document.getElementById("cs-media-hint");
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
      if (newsFields) { newsFields.hidden = type !== "cs-news"; }
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
    initImagePickers();
    initRichEditors();
    initEditor();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
