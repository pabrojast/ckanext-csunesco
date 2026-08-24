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


  // -------------------------------------------------------------------------
  // Dropzones sin AJAX (patrón "water family" de ckanext-pages, accesible):
  // la zona rellena el <input type="file"> EXISTENTE y dispara `change`, así
  // que el preview del image picker reacciona solo y los ficheros viajan en
  // el submit multipart de siempre. Sin JS la zona queda `hidden` y el input
  // sigue visible. `multiple` ACUMULA entre drops reconstruyendo DataTransfer
  // (Chrome 60+/FF 62+/Safari 14.1+); sin el constructor se degrada a
  // asignación directa (el drop reemplaza) y, si ni eso, la zona delega en el
  // picker nativo.
  // -------------------------------------------------------------------------
  var SUPPORTS_DT = (function () {
    try { return !!new DataTransfer(); } catch (e) { return false; }
  })();

  function formatSize(bytes) {
    if (!bytes && bytes !== 0) { return ""; }
    if (bytes < 1024) { return bytes + " B"; }
    if (bytes < 1048576) { return (bytes / 1024).toFixed(0) + " KB"; }
    return (bytes / 1048576).toFixed(1) + " MB";
  }

  function fileAccepted(input, file) {
    var accept = (input.getAttribute("accept") || "").trim();
    if (!accept) { return true; }
    var name = (file.name || "").toLowerCase();
    var type = (file.type || "").toLowerCase();
    return accept.split(",").some(function (rule) {
      rule = rule.trim().toLowerCase();
      if (!rule) { return false; }
      if (rule.charAt(0) === ".") { return name.slice(-rule.length) === rule; }
      if (rule.slice(-2) === "/*") { return type.indexOf(rule.slice(0, -1)) === 0; }
      return type === rule;
    });
  }

  function initDropzones() {
    Array.prototype.forEach.call(
      document.querySelectorAll("[data-dropzone]"), function (zone) {
        if (zone.getAttribute("data-dz-ready")) { return; }
        var input = document.getElementById(zone.getAttribute("data-for"));
        if (!input || input.type !== "file") { return; }
        zone.setAttribute("data-dz-ready", "1");
        zone.hidden = false;
        // display:none SÍ viaja en el submit (solo `disabled` lo excluiría).
        input.classList.add("cs-dz-input-hidden");
        zone.setAttribute("role", "button");
        zone.setAttribute("tabindex", "0");

        var mode = zone.getAttribute("data-preview") || "chip";
        var removeLabel = zone.getAttribute("data-remove-label") || "×";
        var error = document.createElement("p");
        error.className = "cs-field-error cs-dz-error";
        error.setAttribute("role", "alert");
        error.hidden = true;
        zone.parentNode.insertBefore(error, zone.nextSibling);
        var preview = document.createElement("div");
        preview.className = "cs-dz-preview";
        zone.parentNode.insertBefore(preview, error.nextSibling);
        var thumbs = [];

        function showError(message) {
          error.textContent = message || "";
          error.hidden = !message;
        }

        function setFiles(list) {
          var dt = new DataTransfer();
          list.forEach(function (f) { dt.items.add(f); });
          input.files = dt.files;
          input.dispatchEvent(new Event("change", { bubbles: true }));
        }

        function clearAll() {
          input.value = "";
          input.dispatchEvent(new Event("change", { bubbles: true }));
        }

        function removeAt(index) {
          if (!SUPPORTS_DT) { clearAll(); return; }
          var kept = Array.prototype.slice.call(input.files);
          kept.splice(index, 1);
          setFiles(kept);
        }

        function renderPreview() {
          thumbs.forEach(function (u) { URL.revokeObjectURL(u); });
          thumbs = [];
          preview.textContent = "";
          var files = Array.prototype.slice.call(input.files || []);
          if (!files.length) { return; }
          if (mode === "none") { return; }
          if (mode === "grid") {
            var list = document.createElement("ul");
            list.className = "cs-dz-grid";
            files.forEach(function (file, index) {
              var item = document.createElement("li");
              item.className = "cs-dz-item";
              if (file.type && file.type.indexOf("image/") === 0) {
                var img = document.createElement("img");
                var blobUrl = URL.createObjectURL(file);
                thumbs.push(blobUrl);
                img.src = blobUrl;
                img.alt = "";
                img.className = "cs-dz-thumb";
                item.appendChild(img);
              }
              var meta = document.createElement("span");
              meta.className = "cs-dz-meta";
              meta.textContent = file.name + " · " + formatSize(file.size);
              item.appendChild(meta);
              var remove = document.createElement("button");
              remove.type = "button";
              remove.className = "cs-dz-remove";
              remove.textContent = "×";
              remove.setAttribute("aria-label", removeLabel + " " + file.name);
              remove.addEventListener("click", function () { removeAt(index); });
              item.appendChild(remove);
              list.appendChild(item);
            });
            preview.appendChild(list);
          } else {
            var file = files[0];
            var chip = document.createElement("span");
            chip.className = "cs-dz-chip";
            var label = document.createElement("span");
            label.textContent = file.name + " · " + formatSize(file.size);
            chip.appendChild(label);
            var clear = document.createElement("button");
            clear.type = "button";
            clear.className = "cs-dz-remove";
            clear.textContent = "×";
            clear.setAttribute("aria-label", removeLabel + " " + file.name);
            clear.addEventListener("click", clearAll);
            chip.appendChild(clear);
            preview.appendChild(chip);
          }
        }

        zone.addEventListener("dragover", function (e) {
          e.preventDefault();
          zone.classList.add("is-dragover");
        });
        zone.addEventListener("dragleave", function () {
          zone.classList.remove("is-dragover");
        });
        zone.addEventListener("drop", function (e) {
          e.preventDefault();
          zone.classList.remove("is-dragover");
          var dropped = Array.prototype.slice.call(
            (e.dataTransfer && e.dataTransfer.files) || []);
          if (!dropped.length) { return; }  // drags de texto/URL: ignorar
          var ok = dropped.filter(function (f) { return fileAccepted(input, f); });
          showError(ok.length < dropped.length
            ? zone.getAttribute("data-error-type") : "");
          if (!ok.length) { return; }
          if (!SUPPORTS_DT) {
            try {
              input.files = e.dataTransfer.files;
              input.dispatchEvent(new Event("change", { bubbles: true }));
            } catch (err) { input.click(); }
            return;
          }
          var current = input.multiple
            ? Array.prototype.slice.call(input.files) : [];
          setFiles(input.multiple ? current.concat(ok) : [ok[0]]);
        });
        zone.addEventListener("click", function () { input.click(); });
        zone.addEventListener("keydown", function (e) {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            input.click();
          }
        });

        // Fuente de verdad única: input.files (drop O picker nativo).
        input.addEventListener("change", renderPreview);
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


  // -------------------------------------------------------------------------
  // Panel de progreso del editor (adaptación vanilla del water-form-
  // enhancements de ckanext-pages): una tarjeta-botón por sección con estado
  // pendiente/completo/a-revisar y scroll suave. A diferencia del original,
  // se re-filtran las secciones [hidden] en CADA update — las secciones
  // tipadas (news/publication) aparecen y desaparecen con el select de tipo.
  // Solo corre si la página trae el aside #cs-editor-progress.
  // -------------------------------------------------------------------------
  function initFormProgress() {
    var aside = document.getElementById("cs-editor-progress");
    var form = document.getElementById("cs-content-form");
    if (!aside || !form) { return; }
    var labels = {
      pending: aside.getAttribute("data-label-pending") || "",
      complete: aside.getAttribute("data-label-complete") || "",
      error: aside.getAttribute("data-label-error") || ""
    };
    var reduceMotion = window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    function sections() {
      return Array.prototype.filter.call(
        form.querySelectorAll("fieldset.cs-editor-section"),
        function (s) { return !s.hidden; });
    }

    function sectionState(section) {
      if (section.querySelector(".cs-field-error") ||
          section.querySelector(":invalid")) {
        return "error";
      }
      var filled = Array.prototype.some.call(
        section.querySelectorAll("input, textarea, select"),
        function (field) {
          if (field.type === "hidden" || field.type === "checkbox") {
            return field.type === "checkbox" && field.checked;
          }
          if (field.type === "file") {
            return field.files && field.files.length > 0;
          }
          return (field.value || "").trim() !== "";
        });
      return filled ? "complete" : "pending";
    }

    function render() {
      aside.textContent = "";
      sections().forEach(function (section, index) {
        var legend = section.querySelector("legend");
        var state = sectionState(section);
        var card = document.createElement("button");
        card.type = "button";
        card.className = "cs-progress-card is-" + state;
        var num = document.createElement("span");
        num.className = "cs-progress-num";
        num.textContent = String(index + 1);
        card.appendChild(num);
        var text = document.createElement("span");
        text.className = "cs-progress-text";
        var title = document.createElement("span");
        title.className = "cs-progress-title";
        title.textContent = legend ? legend.textContent.trim() : "";
        text.appendChild(title);
        var status = document.createElement("span");
        status.className = "cs-progress-status";
        status.textContent = labels[state] || state;
        text.appendChild(status);
        card.appendChild(text);
        card.addEventListener("click", function () {
          section.scrollIntoView(
            reduceMotion ? {} : { behavior: "smooth", block: "start" });
          var first = section.querySelector(
            "input:not([type=hidden]), textarea, select, [contenteditable]");
          if (first) { first.focus({ preventScroll: true }); }
        });
        aside.appendChild(card);
      });
      aside.hidden = false;
    }

    var scheduled = false;
    function scheduleRender() {
      if (scheduled) { return; }
      scheduled = true;
      window.requestAnimationFrame(function () {
        scheduled = false;
        render();
      });
    }

    form.addEventListener("input", scheduleRender);
    form.addEventListener("change", scheduleRender);
    new MutationObserver(scheduleRender).observe(form, {
      attributes: true,
      attributeFilter: ["hidden"],
      subtree: true
    });
    render();
  }

  function init() {
    initTabs();
    initConfirms();
    initImagePickers();
    initDropzones();
    initRichEditors();
    initFormProgress();
    initEditor();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
