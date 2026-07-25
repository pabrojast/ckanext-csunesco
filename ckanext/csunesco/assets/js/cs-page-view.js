/*
 * ckanext-csunesco -- public project-page behaviour (gallery lightbox).
 *
 * Purely additive: without JavaScript the gallery is still a grid of images
 * (and links, when the author set one), so nothing here is required to see the
 * content. Loaded only on pages that actually have a lightbox gallery.
 */
(function () {
  "use strict";

  var overlay = null;
  var lastFocused = null;

  function close() {
    if (!overlay) { return; }
    document.removeEventListener("keydown", onKeydown);
    overlay.parentNode.removeChild(overlay);
    overlay = null;
    if (lastFocused) { lastFocused.focus(); }
  }

  function onKeydown(event) {
    if (event.key === "Escape") { close(); }
  }

  function open(src, alt) {
    close();
    lastFocused = document.activeElement;

    overlay = document.createElement("div");
    overlay.className = "cs-lightbox";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.setAttribute("aria-label", alt || "");

    var image = document.createElement("img");
    image.src = src;
    image.alt = alt || "";

    var button = document.createElement("button");
    button.type = "button";
    button.className = "cs-lightbox-close";
    button.setAttribute("aria-label", "Close");
    button.textContent = "×";
    button.addEventListener("click", close);

    // Clicking the backdrop closes; clicking the image itself must not.
    overlay.addEventListener("click", function (event) {
      if (event.target === overlay) { close(); }
    });

    overlay.appendChild(image);
    overlay.appendChild(button);
    document.body.appendChild(overlay);
    button.focus();
    document.addEventListener("keydown", onKeydown);
  }

  function init() {
    var galleries = document.querySelectorAll(".cs-gallery[data-lightbox]");
    Array.prototype.forEach.call(galleries, function (gallery) {
      var images = gallery.querySelectorAll("img[data-full]");
      Array.prototype.forEach.call(images, function (image) {
        // An author-supplied link wins: they meant it to go somewhere.
        if (image.closest("a")) { return; }
        image.style.cursor = "zoom-in";
        image.tabIndex = 0;
        image.setAttribute("role", "button");
        image.addEventListener("click", function () {
          open(image.getAttribute("data-full"), image.alt);
        });
        image.addEventListener("keydown", function (event) {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            open(image.getAttribute("data-full"), image.alt);
          }
        });
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
