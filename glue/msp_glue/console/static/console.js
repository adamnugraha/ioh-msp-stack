// MSP Console: table filters, confirmations, and a quiet refresh of the live parts of the page.
(function () {
  "use strict";

  var every = parseInt(document.body.getAttribute("data-refresh") || "0", 10);

  // ---------------------------------------------------------------- filters
  function applyFilters() {
    var inputs = document.querySelectorAll("input[data-filter]");
    Array.prototype.forEach.call(inputs, function (input) {
      var target = document.getElementById(input.getAttribute("data-filter"));
      if (!target) { return; }
      var words = input.value.toLowerCase().trim().split(/\s+/).filter(Boolean);
      Array.prototype.forEach.call(target.querySelectorAll("tr"), function (row) {
        if (row.classList.contains("empty")) { return; }
        var text = row.textContent.toLowerCase();
        row.hidden = words.length > 0 && !words.every(function (w) { return text.indexOf(w) !== -1; });
      });
    });
  }
  document.addEventListener("input", function (event) {
    if (event.target.matches && event.target.matches("input[data-filter]")) { applyFilters(); }
  });

  // ---------------------------------------------------- forms that change things
  document.addEventListener("submit", function (event) {
    var form = event.target;
    var question = form.getAttribute("data-confirm");
    if (question && !window.confirm(question)) {
      event.preventDefault();
      return;
    }
    // One click, one request: a second click while the first is on its way does nothing.
    window.setTimeout(function () {
      Array.prototype.forEach.call(form.querySelectorAll("button"), function (b) { b.disabled = true; });
      form.classList.add("busy");
    }, 0);
  });

  // Close an open acknowledge box with Escape.
  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") { return; }
    Array.prototype.forEach.call(document.querySelectorAll("details[open]"), function (d) { d.open = false; });
  });

  // ---------------------------------------------------------------- refresh
  function inUse(region) {
    if (region.querySelector("details[open]")) { return true; }
    var active = document.activeElement;
    return !!(active && region.contains(active) && active.matches("input, select, textarea, button"));
  }

  function refresh() {
    if (document.visibilityState !== "visible") { return; }
    fetch(window.location.pathname + window.location.search, {
      headers: { "X-Console-Refresh": "1" }, credentials: "same-origin", cache: "no-store"
    }).then(function (response) {
      if (response.status === 401) { window.location.reload(); return null; }
      if (!response.ok) { throw new Error("HTTP " + response.status); }
      return response.text();
    }).then(function (html) {
      if (html === null) { return; }
      var fresh = new DOMParser().parseFromString(html, "text/html");
      Array.prototype.forEach.call(document.querySelectorAll("[data-live]"), function (region) {
        var replacement = region.id && fresh.getElementById(region.id);
        if (replacement && !inUse(region)) { region.replaceWith(document.adoptNode(replacement)); }
      });
      applyFilters();
      document.body.classList.remove("stale");
    }).catch(function () {
      document.body.classList.add("stale");
    });
  }

  if (every > 0) {
    window.setInterval(refresh, every * 1000);
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible") { refresh(); }
    });
  }
  applyFilters();
})();
