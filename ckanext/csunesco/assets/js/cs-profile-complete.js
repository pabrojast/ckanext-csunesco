/* Optional hints for an existing account. Native and server validation still
 * work without JavaScript; no account creation or registration hooks run here. */
(function () {
  'use strict';
  var form = document.getElementById('cs-profile-form');
  if (!form) return;

  var motivation = form.elements.motivation;
  var count = form.querySelector('[data-character-count]');
  function updateCount() {
    count.textContent = Array.from(motivation.value).length;
  }
  motivation.addEventListener('input', updateCount);
  updateCount();

  form.querySelectorAll('.cs-profile-missing').forEach(function (badge) {
    var field = document.getElementById(badge.parentElement.htmlFor);
    function updateBadge() {
      badge.hidden = Boolean(field.value.trim()) && field.validity.valid;
    }
    field.addEventListener('input', updateBadge);
    field.addEventListener('change', updateBadge);
  });

  var errors = document.getElementById('profile-errors');
  if (errors) errors.focus();
}());
