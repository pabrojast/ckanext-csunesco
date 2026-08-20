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
  var closeButton = null;
  // Labels come from the gallery's data- attributes so they go through _().
  var labels = { close: "Close", open: "View image" };

  function close() {
    if (!overlay) { return; }
    document.removeEventListener("keydown", onKeydown);
    closeButton = null;
    overlay.parentNode.removeChild(overlay);
    overlay = null;
    if (lastFocused) { lastFocused.focus(); }
  }

  function onKeydown(event) {
    if (event.key === "Escape") { close(); return; }
    // Trap Tab. The page behind is still in the DOM and focusable, so without
    // this a keyboard user tabs straight out of an aria-modal dialog into
    // content they cannot see.
    if (event.key === "Tab" && closeButton) {
      event.preventDefault();
      closeButton.focus();
    }
  }

  function open(src, alt) {
    close();
    lastFocused = document.activeElement;

    overlay = document.createElement("div");
    overlay.className = "cs-lightbox";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    // An empty aria-label is treated as NO name, so a picture with no alt
    // text produced a dialog that announced nothing at all.
    overlay.setAttribute("aria-label", alt || labels.open);

    var image = document.createElement("img");
    image.src = src;
    image.alt = alt || "";

    var button = document.createElement("button");
    button.type = "button";
    button.className = "cs-lightbox-close";
    button.setAttribute("aria-label", labels.close);
    button.textContent = "×";
    button.addEventListener("click", close);

    // Clicking the backdrop closes; clicking the image itself must not.
    overlay.addEventListener("click", function (event) {
      if (event.target === overlay) { close(); }
    });

    overlay.appendChild(image);
    overlay.appendChild(button);
    document.body.appendChild(overlay);
    closeButton = button;
    button.focus();
    document.addEventListener("keydown", onKeydown);
  }

  function init() {
    var galleries = document.querySelectorAll(".cs-gallery[data-lightbox]");
    Array.prototype.forEach.call(galleries, function (gallery) {
      labels.close = gallery.getAttribute("data-label-close") || labels.close;
      labels.open = gallery.getAttribute("data-label-open") || labels.open;
      var images = gallery.querySelectorAll("img[data-full]");
      Array.prototype.forEach.call(images, function (image) {
        // An author-supplied link wins: they meant it to go somewhere.
        if (image.closest("a")) { return; }
        // A focusable role="button" with no accessible name is a tab stop that
        // announces nothing. Without alt text, leave it a plain image.
        if (!image.alt) { return; }
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

    Array.prototype.forEach.call(
      document.querySelectorAll("[data-carousel]"), function (carousel) {
        var items = Array.prototype.slice.call(
          carousel.querySelectorAll(".cs-gallery-item"));
        if (items.length < 2) { return; }
        var previous = carousel.querySelector("[data-carousel-prev]");
        var next = carousel.querySelector("[data-carousel-next]");
        var status = carousel.querySelector("[data-carousel-status]");
        var index = 0;
        carousel.classList.add("is-enhanced");
        function paint() {
          items.forEach(function (item, itemIndex) {
            item.hidden = itemIndex !== index;
            item.setAttribute("aria-hidden", itemIndex === index ? "false" : "true");
          });
          if (status) { status.textContent = (index + 1) + " / " + items.length; }
        }
        function move(delta) {
          index = (index + delta + items.length) % items.length;
          paint();
        }
        if (previous) { previous.addEventListener("click", function () { move(-1); }); }
        if (next) { next.addEventListener("click", function () { move(1); }); }
        carousel.addEventListener("keydown", function (event) {
          if (event.key === "ArrowLeft") { event.preventDefault(); move(-1); }
          if (event.key === "ArrowRight") { event.preventDefault(); move(1); }
        });
        paint();
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
