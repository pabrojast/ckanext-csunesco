/*
 * ckanext-csunesco -- the "ask the data" block on a project page.
 *
 * The server answers with a COMPUTED result plus a sentence about it, so this
 * file's job is to show the result first and the sentence second. It never
 * formats a figure the server did not send, and it never renders model output
 * as HTML -- every string that came back over the wire goes in via textContent.
 *
 * Charts are drawn by cs-charts.js (window.csunescoCharts.paint), so the
 * palette, the gap handling and the aria-label sentence live in one place.
 *
 * The conversation lives in localStorage: the server is stateless, and the
 * history is posted back each turn (clamped and re-validated server-side,
 * because nothing that round-trips through a browser is trusted).
 *
 * Vanilla DOM, no jQuery, no CKAN JS modules -- matching the rest of this
 * extension. Every user-facing string arrives from the template.
 */
(function () {
  "use strict";

  var MAX_STORED_TURNS = 8;

  function parseJson(text, fallback) {
    try {
      var value = JSON.parse(text);
      return value === null || value === undefined ? fallback : value;
    } catch (error) {
      return fallback;
    }
  }

  /** Substitute {name} placeholders. The template owns the wording; this only
   *  fills the holes, so a translation that reorders them still works. */
  function fill(template, values) {
    return String(template || "").replace(/\{(\w+)\}/g, function (match, key) {
      return Object.prototype.hasOwnProperty.call(values, key)
        ? String(values[key]) : match;
    });
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text !== undefined && text !== null) { node.textContent = String(text); }
    return node;
  }

  function csrfToken(form) {
    // Found by TYPE, not by name: CKAN renders the field as
    // `name="{{ g.csrf_field_name }}"`, which is configurable, so hard-coding
    // "csrf_token" would turn a portal setting into a silent 403. The template
    // puts exactly one hidden input in this form, and it is that one.
    var input = form.querySelector('input[type="hidden"]');
    return input ? input.value : "";
  }

  function numberText(value) {
    // The server already rounded. Re-formatting here would let the browser's
    // locale disagree with the figures printed on the chart.
    return value === null || value === undefined ? "—" : String(value);
  }

  // ------------------------------------------------------------------ //
  // One panel                                                          //
  // ------------------------------------------------------------------ //

  function ChatPanel(root) {
    this.root = root;
    this.strings = parseJson(
      (root.querySelector(".cs-chat-strings") || {}).textContent, {});
    this.log = root.querySelector(".cs-chat-log");
    this.chips = root.querySelector(".cs-chat-chips");
    this.form = root.querySelector(".cs-chat-form");
    this.input = root.querySelector(".cs-chat-input");
    this.send = root.querySelector(".cs-chat-send");
    this.chatUrl = root.getAttribute("data-chat-url");
    this.fieldsUrl = root.getAttribute("data-fields-url");
    this.storeKey = root.getAttribute("data-store-key") || "";
    this.language = root.getAttribute("data-language") || "en";
    this.busy = false;
    this.history = this.load();
  }

  ChatPanel.prototype.load = function () {
    if (!this.storeKey || !window.localStorage) { return []; }
    var stored = parseJson(window.localStorage.getItem(this.storeKey), []);
    return Array.isArray(stored) ? stored.slice(-MAX_STORED_TURNS) : [];
  };

  ChatPanel.prototype.save = function () {
    if (!this.storeKey || !window.localStorage) { return; }
    try {
      window.localStorage.setItem(
        this.storeKey, JSON.stringify(this.history.slice(-MAX_STORED_TURNS)));
    } catch (error) {
      // A full or disabled store must not break the conversation in progress.
    }
  };

  ChatPanel.prototype.start = function () {
    var self = this;
    this.root.classList.add("is-ready");
    this.form.addEventListener("submit", function (event) {
      event.preventDefault();
      self.ask(self.input.value);
    });
    this.input.addEventListener("keydown", function (event) {
      // Enter sends; Shift+Enter is a newline. A two-line box that only
      // submits from a button gets typed into and then abandoned.
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        self.ask(self.input.value);
      }
    });
    this.replay();
    this.loadSuggestions();
  };

  /** Re-draw stored turns as plain text. Cards are NOT stored: they contain
   *  figures, and a figure restored from a browser is a figure nobody can
   *  vouch for. Re-asking recomputes it. */
  ChatPanel.prototype.replay = function () {
    var self = this;
    this.history.forEach(function (turn) {
      self.bubble(turn.role, turn.content);
    });
    this.renderClear();
  };

  ChatPanel.prototype.bubble = function (role, text) {
    var wrap = el("div", "cs-chat-turn cs-chat-turn-" + role);
    wrap.appendChild(el("span", "cs-chat-who",
                        role === "user" ? this.strings.you
                                        : this.strings.answer));
    if (text) { wrap.appendChild(el("p", "cs-chat-text", text)); }
    this.log.appendChild(wrap);
    this.scroll();
    return wrap;
  };

  ChatPanel.prototype.scroll = function () {
    this.log.scrollTop = this.log.scrollHeight;
  };

  ChatPanel.prototype.renderClear = function () {
    var existing = this.root.querySelector(".cs-chat-clear");
    if (existing) { existing.parentNode.removeChild(existing); }
    if (!this.history.length) { return; }
    var self = this;
    var button = el("button", "cs-chat-clear", this.strings.clear);
    button.type = "button";
    button.addEventListener("click", function () {
      self.history = [];
      self.save();
      self.log.innerHTML = "";
      self.bubble("assistant", self.strings.cleared);
      self.renderClear();
    });
    this.log.parentNode.insertBefore(button, this.log);
  };

  // ------------------------------------------------------------------ //
  // Starter chips                                                      //
  // ------------------------------------------------------------------ //

  ChatPanel.prototype.loadSuggestions = function () {
    var self = this;
    if (!this.fieldsUrl || !window.fetch) { return; }
    fetch(this.fieldsUrl, { credentials: "same-origin" })
      .then(function (response) {
        if (!response.ok) { throw new Error("HTTP " + response.status); }
        return response.json();
      })
      .then(function (payload) { self.showChips(self.buildChips(payload)); })
      .catch(function () {
        // No chips is a smaller failure than wrong chips: a reader who types a
        // suggested question and is told the column does not exist stops
        // trusting the box entirely.
      });
  };

  /** Mirrors chat.suggestions_from_profile: busiest numeric field first, then
   *  per-site, then a second measurement, then counts, then a breakdown. Only
   *  names fields the server said actually hold data. */
  ChatPanel.prototype.buildChips = function (payload) {
    var strings = this.strings;
    var numeric = (payload && payload.numeric) || [];
    var categorical = (payload && payload.categorical) || [];
    var siteLabel = (payload && payload.site_label) || "";
    var chips = [];

    var busiest = null;
    numeric.forEach(function (field) {
      if (!busiest || (field.rows || 0) > (busiest.rows || 0)) { busiest = field; }
    });
    if (busiest) {
      chips.push(fill(strings.suggest_average, { field: busiest.label }));
      if (siteLabel) {
        chips.push(fill(strings.suggest_by_site,
                        { field: busiest.label, site: siteLabel }));
      }
      var other = null;
      numeric.forEach(function (field) {
        if (!other && field.name !== busiest.name) { other = field; }
      });
      if (other) {
        chips.push(fill(strings.suggest_trend, { field: other.label }));
      }
    }
    chips.push(strings.suggest_count_over_time);
    if (categorical.length) {
      chips.push(fill(strings.suggest_breakdown,
                      { field: categorical[0].label }));
    }
    return chips.slice(0, 4);
  };

  ChatPanel.prototype.showChips = function (questions) {
    var self = this;
    if (!questions.length) { return; }
    this.chips.innerHTML = "";
    questions.forEach(function (question) {
      var chip = el("button", "cs-chat-chip", question);
      chip.type = "button";
      chip.addEventListener("click", function () { self.ask(question); });
      self.chips.appendChild(chip);
    });
    this.chips.hidden = false;
  };

  // ------------------------------------------------------------------ //
  // Asking                                                             //
  // ------------------------------------------------------------------ //

  ChatPanel.prototype.ask = function (rawQuestion) {
    var self = this;
    var question = String(rawQuestion || "").trim();
    if (!question || this.busy) { return; }

    this.busy = true;
    this.send.disabled = true;
    this.input.value = "";
    this.chips.hidden = true;
    this.bubble("user", question);
    this.history.push({ role: "user", content: question });
    this.save();

    var pending = this.bubble("assistant", this.strings.thinking);
    pending.classList.add("is-pending");

    fetch(this.chatUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken(this.form)
      },
      body: JSON.stringify({
        question: question,
        language: this.language,
        history: this.history.slice(0, -1)
      })
    })
      .then(function (response) { return response.json(); })
      .then(function (payload) { self.render(pending, payload || {}); })
      .catch(function () {
        self.replace(pending, self.strings.error);
      })
      .then(function () {
        self.busy = false;
        self.send.disabled = false;
        self.renderClear();
        self.scroll();
      }, function () {
        self.busy = false;
        self.send.disabled = false;
      });
  };

  ChatPanel.prototype.replace = function (bubble, text) {
    bubble.classList.remove("is-pending");
    var paragraph = bubble.querySelector(".cs-chat-text");
    if (!paragraph) {
      paragraph = el("p", "cs-chat-text");
      bubble.appendChild(paragraph);
    }
    paragraph.textContent = text || "";
  };

  ChatPanel.prototype.render = function (bubble, payload) {
    var status = payload.status || "unavailable";
    var card = payload.card;

    if (status !== "ok" && status !== "refused" && status !== "empty") {
      this.replace(bubble, this.statusText(status));
      return;
    }

    // The computed result FIRST. The prose annotates the figure; it never
    // stands in for it.
    this.replace(bubble, "");
    if (card) { this.renderCard(bubble, card); }
    if (status === "empty") {
      bubble.appendChild(el("p", "cs-chat-text", this.strings.status_empty));
    } else if (payload.reply) {
      bubble.appendChild(el("p", "cs-chat-text", payload.reply));
      this.history.push({ role: "assistant", content: payload.reply });
      this.save();
    }
  };

  ChatPanel.prototype.statusText = function (status) {
    var map = {
      unconfigured: this.strings.status_unconfigured,
      no_data: this.strings.status_no_data,
      empty: this.strings.status_empty,
      quota_reached: this.strings.status_quota,
      unauthenticated: this.strings.status_unauthenticated,
      bad_request: this.strings.status_bad_request,
      not_found: this.strings.status_unavailable
    };
    return map[status] || this.strings.status_unavailable;
  };

  // ------------------------------------------------------------------ //
  // The answer card                                                    //
  // ------------------------------------------------------------------ //

  ChatPanel.prototype.renderCard = function (bubble, card) {
    if (card.kind === "refusal") {
      bubble.appendChild(el("p", "cs-chat-text",
                            this.strings["reason_" + card.reason] ||
                            this.strings.reason_unclear));
      if (card.suggestion) {
        var hint = el("p", "cs-chat-hint", this.strings.try_instead + " ");
        hint.appendChild(el("em", null, card.suggestion));
        bubble.appendChild(hint);
      }
      return;
    }

    var heading = this.queryText(card.query || {});
    if (heading) { bubble.appendChild(el("p", "cs-chat-query", heading)); }

    if (card.kind === "stat") {
      bubble.appendChild(this.statTable(card));
    } else if (card.kind === "series") {
      this.chart(bubble, card);
    }
    bubble.appendChild(this.basis(card));
  };

  /** The question restated in the reader's terms -- "Average pH (per Site)".
   *  This is what replaces "look at the generated query": nobody validates
   *  code, but everyone can check whether the question was understood. */
  ChatPanel.prototype.queryText = function (query) {
    var parts = [];
    if (query.field_label) { parts.push(query.field_label); }
    if (query.unit) { parts.push("(" + query.unit + ")"); }
    if (query.group_by_label) { parts.push("· " + query.group_by_label); }
    return parts.join(" ");
  };

  ChatPanel.prototype.statTable = function (card) {
    var table = el("table", "cs-chat-table");
    var head = el("thead");
    var headRow = el("tr");
    headRow.appendChild(el("th", null, this.strings.group_header));
    headRow.appendChild(el("th", null, this.strings.value_header));
    headRow.appendChild(el("th", null, this.strings.count_header));
    head.appendChild(headRow);
    table.appendChild(head);

    var body = el("tbody");
    var overall = el("tr", "cs-chat-row-overall");
    overall.appendChild(el("th", null, this.strings.overall));
    overall.appendChild(el("td", null, numberText(card.overall)));
    overall.appendChild(el("td", null,
                           numberText((card.basis || {}).used_rows)));
    body.appendChild(overall);

    (card.groups || []).forEach(function (group) {
      var row = el("tr");
      row.appendChild(el("th", null, group.name));
      row.appendChild(el("td", null, numberText(group.value)));
      row.appendChild(el("td", null, numberText(group.count)));
      body.appendChild(row);
    });
    table.appendChild(body);
    return table;
  };

  ChatPanel.prototype.chart = function (bubble, card) {
    var painter = window.csunescoCharts;
    var query = card.query || {};
    var holder = el("div", "cs-chart cs-chat-chart");
    holder.style.height = "260px";
    // A category breakdown is a bar chart; anything with a time axis is a line.
    holder.setAttribute(
      "data-type",
      (query.tool === "top_categories" || query.mode === "category")
        ? "bar" : "line");
    holder.setAttribute("data-label-chart", this.queryText(query));
    bubble.appendChild(holder);

    var drawn = painter && painter.paint
      ? painter.paint(holder, {
          labels: card.labels || [],
          series: card.series || [],
          field_label: query.field_label || ""
        })
      : false;
    if (!drawn) {
      bubble.removeChild(holder);
    }
  };

  /** How many observations the figure rests on, and over what span. This is
   *  the line that makes an answer checkable rather than a claim. */
  ChatPanel.prototype.basis = function (card) {
    var info = card.basis || {};
    var text;
    if (info.first_date && info.last_date && info.total_rows) {
      text = fill(this.strings.basis, {
        used: info.used_rows, total: info.total_rows,
        first: info.first_date, last: info.last_date
      });
    } else {
      text = fill(this.strings.basis_short, { used: info.used_rows || 0 });
    }
    if (info.omitted_groups) {
      text += " " + fill(this.strings.omitted, { count: info.omitted_groups });
    }
    if (info.truncated) { text += " " + this.strings.truncated; }
    return el("p", "cs-chat-basis", text);
  };

  // ------------------------------------------------------------------ //

  function init() {
    var panels = document.querySelectorAll(".cs-chat[data-chat-url]");
    if (!panels.length || !window.fetch) { return; }
    Array.prototype.forEach.call(panels, function (root) {
      new ChatPanel(root).start();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
