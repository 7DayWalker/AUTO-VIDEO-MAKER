/* AUTO VIDEO MAKER — Command Center overview.
   Presentation only: six-state values and run data come from the API. */
window.CC = (function () {
  var C = COMPONENTS;
  var STATES = {
    NOT_STARTED: { badge: "muted", label: "Not started", meaning: "No execution project is bound yet." },
    INCOMPLETE: { badge: "warn", label: "Incomplete", meaning: "A bound project has work remaining; the safe continuation is Resume." },
    IN_FLIGHT: { badge: "info", label: "In flight", meaning: "Execution or a remote job is already in progress; inspect before acting." },
    BLOCKED: { badge: "muted", label: "Blocked", meaning: "A dependency is not completed." },
    FAILED: { badge: "err", label: "Failed", meaning: "Authoritative failure evidence requires inspection." },
    COMPLETED: { badge: "ok", label: "Completed", meaning: "All required execution evidence is complete." }
  };
  var ORDER = ["FAILED", "BLOCKED", "IN_FLIGHT", "INCOMPLETE", "NOT_STARTED", "COMPLETED"];

  function stateInfo(state) {
    return STATES[state] || { badge: "muted", label: state || "Unknown", meaning: "State unavailable." };
  }
  function stateBadge(state) {
    var info = stateInfo(state);
    return '<span class="badge ' + info.badge + '" data-state="' + C.esc(state || "UNKNOWN") + '">' + C.esc(info.label) + '</span>';
  }
  function stateExplanation(state, reason) {
    var info = stateInfo(state);
    return '<p class="state-meaning"><strong>' + C.esc(info.label) + ':</strong> ' + C.esc(reason || info.meaning) + '</p>';
  }
  function countsRow(counts) {
    if (!counts) return '<span class="hint">Progress unavailable; the server could not derive state.</span>';
    return ORDER.map(function (key) {
      var info = STATES[key];
      return '<span class="chip state-chip ' + info.badge + '"><strong>' + C.esc(counts[key] == null ? 0 : counts[key]) + '</strong> ' + C.esc(info.label) + '</span>';
    }).join("");
  }
  function progressBar(counts, total) {
    var done = counts && counts.COMPLETED || 0;
    var denominator = Number(total) || 0;
    var pct = denominator ? Math.round(done / denominator * 100) : 0;
    return '<div class="progress-wrap" aria-label="' + C.esc(done + ' of ' + denominator + ' units completed') + '">' +
      '<div class="statebar"><div class="statebar-fill" style="width:' + pct + '%"></div></div>' +
      '<span class="hint">' + C.esc(done + '/' + denominator + ' units completed (' + pct + '%)') + '</span></div>';
  }
  function runBadge(run) {
    if (run && run.running) return '<span class="badge info">Active run</span>';
    if (run && run.error) return '<span class="badge err">Last run failed</span>';
    return "";
  }
  /* Batch 1G: the first-class Production Route (product-level identity).
     Never a provider/model, and never inferred from the legacy strategy. */
  var ROUTE_LABELS = {
    local_ai_assisted: "Local AI-Assisted",
    full_ai_video: "Full AI Video",
    google_flow: "Google Flow (legacy)"
  };
  function routeChip(route) {
    if (!route) {
      return '<span class="badge muted" title="Created before first-class Production Routes (Batch 1D); route not specified. Route is never inferred from the legacy generation strategy.">Route not specified (legacy)</span>';
    }
    var label = ROUTE_LABELS[route] || route;
    return '<span class="badge muted" title="Production Route (product-level identity; not a provider/model): ' + C.esc(label) + '">' + C.esc(label) + '</span>';
  }
  function actionButtons(p) {
    var counts = p.counts || {};
    var running = p.run && p.run.running;
    var html = "";
    if (running) html = '<button class="btn btn-sm" disabled aria-disabled="true" title="An active command is registered; inspect live progress before acting">Execution active</button>';
    else if (counts.INCOMPLETE > 0 || counts.IN_FLIGHT > 0) html = '<button class="btn btn-sm" data-cc="resume" data-id="' + C.esc(p.production_id) + '" data-total="' + C.esc(p.unit_count) + '">Resume Production</button>';
    else if (counts.NOT_STARTED > 0) html = '<button class="btn btn-sm btn-primary" data-cc="execute" data-id="' + C.esc(p.production_id) + '" data-total="' + C.esc(p.unit_count) + '">Execute Production</button>';
    else if (counts.FAILED > 0) html = '<span class="hint">Inspect failed units; explicit recovery is required.</span>';
    if (!html && counts.BLOCKED > 0) html = '<span class="hint">Complete blocking dependencies first.</span>';
    if (!html && counts.COMPLETED === Number(p.unit_count || 0)) html = '<span class="hint">Completed — no re-execution.</span>';
    return html || '<span class="hint">No safe command available.</span>';
  }
  return { STATES: STATES, ORDER: ORDER, stateInfo: stateInfo, stateBadge: stateBadge,
    stateExplanation: stateExplanation, countsRow: countsRow, progressBar: progressBar,
    runBadge: runBadge, routeChip: routeChip, actionButtons: actionButtons };
})();

CC.confirmDialog = function (opts, onConfirm) {
  COMPONENTS.modal(
    '<h3>' + COMPONENTS.esc(opts.title) + '</h3>' +
    '<p>' + COMPONENTS.esc(opts.description) + '</p>' +
    (opts.scope ? '<p class="hint"><strong>Scope:</strong> ' + COMPONENTS.esc(opts.scope) + '</p>' : '') +
    (opts.firstAction ? '<p class="hint"><strong>First action:</strong> ' + COMPONENTS.esc(opts.firstAction) + '</p>' : '') +
    '<div class="modal-actions"><button class="btn" data-cc-action="cancel">Cancel</button>' +
    '<button class="btn btn-primary" data-cc-action="confirm">' + COMPONENTS.esc(opts.confirmLabel || 'Confirm') + '</button></div>'
  );
  var root = document.getElementById('modal-root');
  root.querySelector('[data-cc-action="cancel"]').addEventListener('click', COMPONENTS.closeModal);
  root.querySelector('[data-cc-action="confirm"]').addEventListener('click', function () {
    COMPONENTS.closeModal();
    onConfirm();
  });
};

window.VIEWS = window.VIEWS || {};
VIEWS.productions = {
  title: "Command Center",
  render: async function () {
    if (!API.live) return '<div class="card cc-state-panel offline-panel">' + COMPONENTS.emptyState("sparkle", "Engine offline", "Live production data is unavailable. Start the local engine with: python -m app.web.server") + '</div>';
    var items;
    try { items = await API.getProductions(); }
    catch (e) { return '<div class="card cc-state-panel error-panel">' + COMPONENTS.emptyState("alert", "Production data unavailable", String(e.message || e)) + '</div>'; }
    if (!items.length) return '<div class="page-head"><div><p class="eyebrow">Personal production control</p><h1 class="page-title">Command Center</h1><p class="page-sub">Production is the primary operational object. No live production intents exist yet.</p></div></div><div class="card cc-state-panel">' + COMPONENTS.emptyState("sparkle", "No productions yet", "Create a durable production intent through produce.py. The Command Center does not create production records.") + '</div>';
    function attentionPriority(p) {
      var counts = p.counts || {};
      if (p.error || !p.counts) return 0;
      if (counts.FAILED) return 1;
      if (counts.BLOCKED) return 2;
      if (counts.IN_FLIGHT) return 3;
      if (counts.INCOMPLETE) return 4;
      if (counts.NOT_STARTED) return 5;
      return 6;
    }
    items.sort(function (left, right) { return attentionPriority(left) - attentionPriority(right); });
    var cards = items.map(function (p) {
      var counts = p.counts || {};
      var attention = p.error || !p.counts ? "Production data requires inspection." : counts.FAILED ? "Failure requires inspection." : counts.BLOCKED ? "Dependencies require attention." : counts.IN_FLIGHT ? "Execution is active or a remote job is in flight." : counts.INCOMPLETE ? "Work can continue with Resume." : counts.NOT_STARTED ? "Ready for first execution." : "No attention required.";
      var tone = p.error || !p.counts || counts.FAILED ? "danger" : counts.BLOCKED || counts.INCOMPLETE ? "warn" : counts.IN_FLIGHT ? "info" : "neutral";
      return '<article class="card production-card attention-' + tone + '">' +
        '<div class="production-card-head"><div><p class="eyebrow">Production</p><h2 class="production-title">' + COMPONENTS.esc(p.title) + '</h2><p class="mono muted-text">' + COMPONENTS.esc(p.production_id) + ' · plan v' + COMPONENTS.esc(p.plan_version) + ' · ' + COMPONENTS.esc(p.unit_count) + ' units</p></div><div>' + CC.routeChip(p.route) + CC.runBadge(p.run) + '</div></div>' +
        '<div class="attention-strip"><span class="attention-dot" aria-hidden="true"></span><span>' + COMPONENTS.esc(attention) + '</span></div>' +
        '<div class="production-card-body"><p class="command-label">Production intent</p><p class="command-text">' + COMPONENTS.esc(p.command_text) + '</p><div class="state-counts" aria-label="Six-state unit summary">' + CC.countsRow(counts) + '</div>' + CC.progressBar(counts, p.unit_count) + (p.error ? '<p class="inline-error">' + COMPONENTS.esc(p.error) + '</p>' : '') + '</div>' +
        '<div class="production-card-foot"><a class="btn btn-sm" href="#/productions/' + encodeURIComponent(p.production_id) + '">Inspect production</a><span class="spacer"></span>' + CC.actionButtons(p) + '</div></article>';
    }).join("");
    return '<div class="page-head"><div><p class="eyebrow">Personal production control</p><h1 class="page-title">Command Center</h1><p class="page-sub">Inspect durable production intent and executor-derived progress. Commands are explicit, confirmed, and never automatic.</p></div></div><div id="production-command-feedback" aria-live="polite"></div><div class="cc-toolbar"><label class="field-inline"><span class="sr-only">Filter productions</span><input class="input" id="production-filter" type="search" placeholder="Filter productions" autocomplete="off"></label><span class="hint">' + COMPONENTS.esc(items.length + ' live production' + (items.length === 1 ? '' : 's')) + '</span></div><div class="grid productions-grid" id="production-cards">' + cards + '</div>';
  },
  mount: async function () {
    var filter = document.getElementById("production-filter");
    if (filter) filter.addEventListener("input", function () {
      var query = filter.value.toLowerCase();
      document.querySelectorAll("#production-cards .production-card").forEach(function (card) { card.hidden = query && card.textContent.toLowerCase().indexOf(query) < 0; });
    });
    document.querySelectorAll("[data-cc]").forEach(function (button) {
      button.addEventListener("click", function () {
        var action = button.getAttribute("data-cc"), id = button.getAttribute("data-id"), total = button.getAttribute("data-total");
        CC.confirmDialog({ title: action === "execute" ? "Execute production?" : "Resume production?", description: (action === "execute" ? "Execute" : "Resume") + " production " + id + "?", scope: total + " units total; only the existing executor decides eligible work.", firstAction: "The Phase 10 ProductionExecutor receives the confirmed command in the background; no completed unit is restarted and no remote job is resubmitted.", confirmLabel: action === "execute" ? "Execute Production" : "Resume Production" }, function () {
          API.productionCommand(id, action).then(function (res) { COMPONENTS.toast(res.message || "Command accepted", "ok"); location.hash = "#/productions/" + encodeURIComponent(id); }).catch(function (err) { var message = err.message || String(err), feedback = document.getElementById("production-command-feedback"); if (feedback) feedback.innerHTML = '<p class="inline-error" role="alert">' + COMPONENTS.esc(message) + '</p>'; COMPONENTS.toast(message, "err"); });
        });
      });
    });
  }
};
