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
    { input: document.getElementById("cs-motivation"), hint: hintFor("cs-motivation"),
      valid: function(el) { var n = Array.from(el.value.trim()).length; return n >= 20 && n <= 500; } },
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
    { input: document.getElementById("cs-nationality"),
      hint: hintFor("cs-nationality"), valid: filled },
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
   * Member/Admin). Server validation is authoritative; this is presentation. */
  var orgName = document.getElementById("cs-org-name");
  var newOrgField = document.getElementById("cs-new-org-field");
  var roleNote = document.getElementById("cs-org-role-note");
  if (orgName && newOrgField) {
    var wasCreating = false;
    var syncOrgChoice = function () {
      var creating = orgName.value === "__new__";
      newOrgField.hidden = !creating;
      var extra = document.getElementById("cs-org-extra");
      if (extra) extra.hidden = !creating;
      var role = document.getElementById("cs-org-role");
      if (role) { role.querySelector('[value="member"]').disabled = creating; if (creating) role.value = "admin"; else if (wasCreating) role.value = "member"; }
      wasCreating = creating;
      if (roleNote) {
        var label = roleNote.getAttribute(
          creating ? "data-role-admin" : "data-role-member");
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
      (invalid[0].hidden && invalid[0] === orgName ? document.getElementById("cs-org-search") : invalid[0]).focus();
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

  var serverError = document.getElementById("cs-reg-error");
  if (serverError) {
    var serverFields = {};
    try { serverFields = JSON.parse(serverError.getAttribute("data-fields") || "{}"); } catch (e) { /* no fields */ }
    var firstInvalid = null;
    Object.keys(serverFields).forEach(function (name) {
      var input = form.elements.namedItem(name);
      if (input && input.setAttribute) {
        input.setAttribute("aria-invalid", "true");
        input.setAttribute("aria-describedby", "cs-reg-error");
        if (!firstInvalid) { firstInvalid = input; }
      }
    });
    (firstInvalid && firstInvalid.hidden && firstInvalid === orgName ? document.getElementById("cs-org-search") : (firstInvalid || serverError)).focus();
  }

  var motivation = document.getElementById("cs-motivation");
  if (motivation) motivation.addEventListener("input", function() {
    document.querySelector('[data-motivation-count]').textContent = Array.from(motivation.value).length;
  });
  var fullname = document.getElementById("cs-fullname");
  var manualUsername = Boolean(username && username.value);
  if (username && fullname) {
    username.addEventListener("input", function() { manualUsername = true; });
    fullname.addEventListener("input", function() {
      if (!manualUsername) username.value = fullname.value.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-|-$/g, "").slice(0, 80);
    });
  }
  var picker = document.getElementById("cs-org-picker");
  if (picker && orgName) {
    picker.hidden = false;
    var search = document.getElementById("cs-org-search");
    var results = document.getElementById("cs-org-results");
    var status = document.getElementById("cs-org-status");
    var orgs = [];
    function fold(text) { return String(text || "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase(); }
    function matches(text) { var words=fold(text).split(/\s+/).filter(Boolean); return orgs.filter(function(o) { var hay=fold(o.title+" "+o.name);return words.every(function(w){return hay.indexOf(w)!==-1;}); }).sort(function(a,b){var q=fold(text); var score=function(o){var t=fold(o.title);return t===q?3:t.indexOf(q)===0?2:1;};return score(b)-score(a)||a.title.localeCompare(b.title);}); }
    function render() {
      results.textContent = "";
      var found=matches(search.value); status.textContent=found.length ? "" : status.dataset.empty;
      found.slice(0,10).forEach(function(org) {
        var li=document.createElement("li"), button=document.createElement("button");
        button.type="button"; button.textContent=org.title || org.name;
        button.addEventListener("click", function(){orgName.value=org.name;orgName.dispatchEvent(new Event("change"));search.value=org.title || org.name;results.textContent="";status.textContent=search.value;});
        li.appendChild(button);results.appendChild(li);
      });
    }
    status.textContent=status.dataset.loading;
    fetch(picker.dataset.url, {credentials:"same-origin"}).then(function(res){if(!res.ok)throw new Error();return res.json();}).then(function(data){
      if(!Array.isArray(data.results))throw new Error();orgs=data.results;
      Array.from(orgName.options).forEach(function(opt){if(opt.value && opt.value!=="__new__" && !orgs.some(function(o){return o.name===opt.value;}))orgs.push({name:opt.value,title:opt.text});});
      orgs.forEach(function(o){if(!Array.from(orgName.options).some(function(opt){return opt.value===o.name;})){var opt=new Option(o.title || o.name,o.name);orgName.add(opt);}});
      orgName.hidden=true; if (orgName.value && orgName.value !== "__new__") search.value=orgName.options[orgName.selectedIndex].text; render();
    }).catch(function(){status.textContent=status.dataset.error;orgName.hidden=false;});
    search.addEventListener("input", function(){orgName.value="";orgName.dispatchEvent(new Event("change"));render();});
    document.getElementById("cs-org-new").addEventListener("click",function(){orgName.value="__new__";orgName.dispatchEvent(new Event("change"));document.getElementById("cs-new-org-name").focus();});
    document.getElementById("cs-org-back").addEventListener("click",function(){orgName.value="";orgName.dispatchEvent(new Event("change"));search.focus();render();});
    document.getElementById("cs-new-org-name").addEventListener("input",function(e){var box=document.getElementById("cs-org-similar");var list=e.target.value.trim().length>=2?matches(e.target.value).slice(0,5):[];box.textContent=list.length?box.dataset.similar+" "+list.map(function(o){return o.title;}).join(", "):"";});
  }
  renderStrength();
})();
