/* Citizen Scientist registration: accessible client validation + password UX.
 * Progressive enhancement only; the server repeats every rule. */
(function () {
  "use strict";

  var form = document.getElementById("cs-register-form");
  if (!form) { return; }

  var email = document.getElementById("cs-email");
  var username = document.getElementById("cs-username");
  var password = document.getElementById("cs-password");
  var confirm = document.getElementById("cs-confirm-password");
  var terms = document.getElementById("cs-terms");
  var submit = document.getElementById("cs-submit");
  var announce = document.getElementById("cs-reg-announcement");
  var toggle = form.querySelector(".cs-password-toggle");
  var meter = form.querySelectorAll(".cs-password-meter span");

  var fields = [
    { input: email, hint: document.getElementById("cs-email-hint") },
    { input: username, hint: document.getElementById("cs-username-hint") },
    { input: password, hint: document.getElementById("cs-password-hint") },
    { input: confirm, hint: document.getElementById("cs-confirm-password-hint") },
    { input: terms, hint: document.getElementById("cs-terms-hint") }
  ];

  function setInvalid(item, invalid) {
    if (!item.input) { return; }
    item.input.setAttribute("aria-invalid", invalid ? "true" : "false");
    if (item.hint) {
      var text = item.hint.getAttribute(invalid ? "data-error" : "data-default");
      item.hint.textContent = text || "";
      item.hint.classList.toggle("is-error", invalid);
    }
  }

  function invalidFields() {
    var invalid = [];
    var emailOk = /^[^@\s]+@[^@\s]+$/.test((email.value || "").trim());
    var checks = [
      emailOk,
      Boolean((username.value || "").trim()),
      (password.value || "").length >= 8,
      confirm.value === password.value,
      terms.checked
    ];
    fields.forEach(function (item, index) {
      setInvalid(item, !checks[index]);
      if (!checks[index]) { invalid.push(item.input); }
    });
    return invalid;
  }

  function passwordStrength(value) {
    if (!value) { return 0; }
    if (value.length < 8) { return 1; }
    var score = 1;
    if (value.length >= 12) { score += 1; }
    if (/[a-z]/.test(value) && /[A-Z]/.test(value) && /\d/.test(value)) {
      score += 1;
    }
    if (/[^A-Za-z0-9]/.test(value)) { score += 1; }
    return score;
  }

  function renderStrength() {
    var score = passwordStrength(password.value || "");
    Array.prototype.forEach.call(meter, function (segment, index) {
      segment.classList.toggle("is-on", index < score);
    });
    var hint = document.getElementById("cs-password-hint");
    if (!hint || password.getAttribute("aria-invalid") === "true") { return; }
    if (!password.value) {
      hint.textContent = hint.getAttribute("data-default") || "";
      return;
    }
    var labels = (hint.getAttribute("data-strengths") || "").split("|");
    hint.textContent = (hint.getAttribute("data-strength-label") || "") +
      ": " + (labels[Math.max(0, score - 1)] || labels[0] || "");
  }

  if (toggle) {
    toggle.addEventListener("click", function () {
      var visible = password.type === "text";
      password.type = visible ? "password" : "text";
      confirm.type = visible ? "password" : "text";
      toggle.setAttribute("aria-pressed", visible ? "false" : "true");
      toggle.setAttribute(
        "aria-label",
        toggle.getAttribute(visible ? "data-show-label" : "data-hide-label")
      );
      toggle.classList.toggle("is-visible", !visible);
    });
  }

  password.addEventListener("input", renderStrength);
  username.addEventListener("blur", function () {
    username.value = (username.value || "").trim().toLowerCase();
  });

  function lockSubmit() {
    submit.disabled = true;
    submit.classList.add("is-loading");
    submit.textContent = form.getAttribute("data-sending-label") || "Sending…";
    if (announce) { announce.textContent = submit.textContent; }
  }

  form.addEventListener("submit", function (event) {
    if (submit.disabled) {
      event.preventDefault();
      return;
    }
    var invalid = invalidFields();
    renderStrength();
    if (invalid.length) {
      event.preventDefault();
      invalid[0].focus();
      return;
    }

    var siteKey = form.getAttribute("data-recaptcha-key");
    if (!siteKey) {
      lockSubmit();
      return;
    }

    event.preventDefault();
    if (!window.grecaptcha) {
      if (announce) {
        announce.textContent = form.getAttribute("data-recaptcha-loading") || "";
      }
      return;
    }
    lockSubmit();
    window.grecaptcha.ready(function () {
      window.grecaptcha.execute(siteKey, { action: "register" }).then(
        function (token) {
          var field = document.getElementById("cs-recaptcha-response");
          if (field) { field.value = token; }
          HTMLFormElement.prototype.submit.call(form);
        },
        function () {
          submit.disabled = false;
          submit.classList.remove("is-loading");
          submit.textContent = form.getAttribute("data-submit-label") || "";
          if (announce) {
            announce.textContent = form.getAttribute("data-recaptcha-error") || "";
          }
        }
      );
    });
  });

  renderStrength();
})();
