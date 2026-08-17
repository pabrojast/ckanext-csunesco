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

  function hintFor(id) { return document.getElementById(id + "-hint"); }

  /* Shared by the citizen AND the manager form: a rule only applies when its
   * element exists in the page. Username is deliberately absent -- it is
   * optional (the server generates one from the name when blank). */
  var fields = [
    { input: email, hint: hintFor("cs-email"),
      valid: function (el) {
        return /^[^@\s]+@[^@\s]+$/.test((el.value || "").trim());
      } },
    { input: document.getElementById("cs-fullname"),
      hint: hintFor("cs-fullname"), valid: filled },
    { input: document.getElementById("cs-date-of-birth"),
      hint: hintFor("cs-date-of-birth"), valid: filled },
    { input: document.getElementById("cs-gender"),
      hint: hintFor("cs-gender"), valid: filled },
    { input: document.getElementById("cs-org-type"),
      hint: hintFor("cs-org-type"), valid: filled },
    { input: document.getElementById("cs-org-name"),
      hint: hintFor("cs-org-name"), valid: filled },
    { input: document.getElementById("cs-org-title"),
      hint: hintFor("cs-org-title"), valid: filled },
    { input: password, hint: hintFor("cs-password"),
      valid: function (el) { return (el.value || "").length >= 8; } },
    { input: confirm, hint: hintFor("cs-confirm-password"),
      valid: function (el) { return el.value === password.value; } },
    { input: terms, hint: hintFor("cs-terms"),
      valid: function (el) { return el.checked; } }
  ].filter(function (item) { return item.input; });

  function filled(el) {
    return Boolean((el.value || "").trim());
  }

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
    fields.forEach(function (item) {
      var ok = item.valid(item.input);
      setInvalid(item, !ok);
      if (!ok) { invalid.push(item.input); }
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
  if (username) {
    username.addEventListener("blur", function () {
      username.value = (username.value || "").trim().toLowerCase();
    });
  }

  /* Manager form only: choosing "create a new organization" reveals the name
   * input and flips the derived role note (new org -> Admin, existing ->
   * Editor). Server-side derivation is authoritative; this is presentation. */
  var orgName = document.getElementById("cs-org-name");
  var newOrgField = document.getElementById("cs-new-org-field");
  var roleNote = document.getElementById("cs-org-role-note");
  if (orgName && newOrgField) {
    var syncOrgChoice = function () {
      var creating = orgName.value === "__new__";
      newOrgField.hidden = !creating;
      if (roleNote) {
        var label = roleNote.getAttribute(
          creating ? "data-role-admin" : "data-role-editor");
        roleNote.textContent = label || "";
      }
    };
    orgName.addEventListener("change", syncOrgChoice);
    syncOrgChoice();
  }

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
