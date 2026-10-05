/* Citizen Scientist registration: the optional "Scan QR code" of step 3.
 *
 * Progressive enhancement only. The form posts a hidden `project` field; this
 * script fills it from the QR code a Project Manager shares. Without JS, a
 * camera or permission the step stays empty: joining is optional and can be
 * done later from the project page. The server re-validates the value. */
(function () {
  "use strict";

  var box = document.getElementById("cs-qr");
  var input = document.getElementById("cs-project");
  if (!box || !input) { return; }

  var badge = document.getElementById("cs-join-badge");
  var badgeText = document.getElementById("cs-join-badge-text");
  var remove = document.getElementById("cs-join-remove");
  var scan = document.getElementById("cs-qr-scan");
  var panel = document.getElementById("cs-qr-panel");
  var video = document.getElementById("cs-qr-video");
  var cancel = document.getElementById("cs-qr-cancel");
  var status = document.getElementById("cs-qr-status");
  var form = document.getElementById("cs-register-form");

  var projects = [];
  try {
    projects = JSON.parse(
      document.getElementById("cs-join-projects").textContent) || [];
  } catch (e) { /* no joinable projects published */ }

  var SLUG_RE = /^[a-z0-9][a-z0-9_-]{0,98}$/;
  var NUM_RE = /^\d+$/;

  /* Same payloads the Toolbox scanner accepts (ofform routes/joinQr.ts):
   *   .../register-citizen?project=<id|slug>   the QR a Project Manager shares
   *   .../explorer/projects/<id>               older Toolbox QR codes
   *   .../citizen-science/project/<slug>       a portal project page
   *   <id> or <slug> on its own
   * A number is always a Toolbox project id; anything else is a portal slug. */
  function parseRef(text) {
    var raw = String(text || "").trim();
    if (!raw) { return null; }
    if (NUM_RE.test(raw)) { return {app: true, value: raw}; }
    var url;
    try { url = new URL(raw, "https://qr.invalid"); } catch (e) { return null; }
    var param = url.searchParams.get("project");
    if (param && NUM_RE.test(param)) { return {app: true, value: param}; }
    if (param && SLUG_RE.test(param)) { return {app: false, value: param}; }
    var match = url.pathname.match(/\/explorer\/projects\/(\d+)(?:\/|$)/);
    if (match) { return {app: true, value: match[1]}; }
    match = url.pathname.match(/\/citizen-science\/project\/([^/]+)\/?$/);
    if (match) {
      var slug;
      try { slug = decodeURIComponent(match[1]); } catch (e) { return null; }
      return SLUG_RE.test(slug) ? {app: false, value: slug} : null;
    }
    return SLUG_RE.test(raw) ? {app: false, value: raw} : null;
  }

  function findProject(ref) {
    for (var i = 0; i < projects.length; i++) {
      var project = projects[i];
      if (ref.app ? String(project.app_id || "") === ref.value
                  : project.slug === ref.value) {
        return project;
      }
    }
    return null;
  }

  function setProject(project) {
    input.value = project ? project.slug : "";
    if (!badge) { return; }
    badge.hidden = !project;
    if (project && badgeText) {
      badgeText.textContent = (badge.getAttribute("data-template") || "{project}")
        .replace("{project}", project.title || project.slug);
    }
  }

  function say(key) {
    status.textContent = key ? (box.getAttribute("data-" + key) || "") : "";
  }

  var stream = null;
  var frameId = 0;
  var active = false;

  function stop() {
    active = false;
    if (frameId) { cancelAnimationFrame(frameId); frameId = 0; }
    if (stream) {
      stream.getTracks().forEach(function (track) { track.stop(); });
      stream = null;
    }
    video.srcObject = null;
    panel.hidden = true;
    scan.hidden = false;
  }

  function loadDecoder(url) {
    return new Promise(function (resolve, reject) {
      if (typeof window.jsQR === "function") { resolve(); return; }
      var tag = document.createElement("script");
      tag.src = url;
      tag.onload = function () {
        if (typeof window.jsQR === "function") { resolve(); } else { reject(new Error("jsQR")); }
      };
      tag.onerror = function () { reject(new Error("jsQR")); };
      document.head.appendChild(tag);
    });
  }

  /* Native detection where the browser really reads QR codes (Chromium,
   * Android); otherwise the vendored jsQR, downloaded only in that case. */
  function pickDecoder() {
    var Detector = window.BarcodeDetector;
    var supported = Detector && Detector.getSupportedFormats
      ? Detector.getSupportedFormats().then(
          function (formats) { return formats.indexOf("qr_code") !== -1; },
          function () { return false; })
      : Promise.resolve(false);
    return supported.then(function (native) {
      if (native) {
        var detector = new Detector({formats: ["qr_code"]});
        return function (frame) {
          return detector.detect(frame).then(
            function (codes) { return (codes[0] && codes[0].rawValue) || null; },
            function () { return null; });
        };
      }
      return loadDecoder(box.getAttribute("data-jsqr-url")).then(function () {
        var canvas = document.createElement("canvas");
        var context = canvas.getContext("2d", {willReadFrequently: true});
        return function (frame) {
          canvas.width = frame.videoWidth;
          canvas.height = frame.videoHeight;
          context.drawImage(frame, 0, 0);
          var image = context.getImageData(0, 0, canvas.width, canvas.height);
          var code = window.jsQR(image.data, image.width, image.height,
                                 {inversionAttempts: "dontInvert"});
          return Promise.resolve((code && code.data) || null);
        };
      });
    });
  }

  function onDecoded(text) {
    stop();
    var ref = parseRef(text);
    var project = ref ? findProject(ref) : null;
    if (!project) {
      say("not-found");
      scan.focus();
      return;
    }
    setProject(project);
    say("");
    (remove || scan).focus();
  }

  function start() {
    if (active) { return; }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      say("unavailable");
      return;
    }
    active = true;
    say("starting");
    scan.hidden = true;
    panel.hidden = false;
    navigator.mediaDevices.getUserMedia({video: {facingMode: "environment"}})
      .then(function (media) {
        if (!active) {
          // Cancelled while the permission prompt was open.
          media.getTracks().forEach(function (track) { track.stop(); });
          return null;
        }
        stream = media;
        video.srcObject = media;
        return Promise.all([video.play(), pickDecoder()]);
      })
      .then(function (ready) {
        if (!ready || !active) { return; }
        var decode = ready[1];
        var busy = false;
        say("");
        (function tick() {
          if (!active) { return; }
          if (!busy && video.readyState >= 2 && video.videoWidth) {
            busy = true;
            decode(video).then(function (text) {
              busy = false;
              if (text && active) { onDecoded(text); }
            }, function () { busy = false; });
          }
          frameId = requestAnimationFrame(tick);
        })();
      })
      .catch(function () {
        // No camera, permission denied, insecure context or no decoder.
        stop();
        say("unavailable");
      });
  }

  scan.addEventListener("click", start);
  cancel.addEventListener("click", function () {
    stop();
    say("");
    scan.focus();
  });
  if (remove) {
    remove.addEventListener("click", function (event) {
      event.preventDefault();
      setProject(null);
      scan.focus();
    });
  }
  if (form) { form.addEventListener("submit", stop); }
  window.addEventListener("pagehide", stop);
  document.addEventListener("visibilitychange", function () {
    if (document.hidden && active) { stop(); say(""); }
  });

  box.hidden = false;
})();
