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
  /* Track 3 Increment 1: multi-state DISTRIBUTION bar. Segment widths come
     only from the durable six-state counts (never invented progress); the
     state-counts chips above it remain the textual, non-color channel.
     Zero-count states render no segment; NOT_STARTED units are the unlit
     remainder of the track. */
  var BAR_STATES = [
    { key: "COMPLETED", tone: "ok" },
    { key: "IN_FLIGHT", tone: "info" },
    { key: "INCOMPLETE", tone: "warn" },
    { key: "FAILED", tone: "err" },
    { key: "BLOCKED", tone: "muted" }
  ];
  function stateSummary(counts) {
    counts = counts || {};
    return BAR_STATES.filter(function (seg) { return (counts[seg.key] || 0) > 0; })
      .map(function (seg) {
        return (counts[seg.key] || 0) + " " + STATES[seg.key].label.toLowerCase();
      })
      .join(" · ");
  }
  function stateBar(counts, total) {
    counts = counts || {};
    var denominator = Number(total) || 0;
    var summary = stateSummary(counts);
    if (!denominator || !summary) return "";
    var segments = BAR_STATES.filter(function (seg) { return (counts[seg.key] || 0) > 0; })
      .map(function (seg) {
        var count = counts[seg.key] || 0;
        var pct = Math.round(count / denominator * 100);
        var label = STATES[seg.key].label;
        return '<span class="statebar-seg ' + seg.tone + '" aria-hidden="true" style="width:' + pct + '%" title="' +
          C.esc(count + " " + label.toLowerCase()) + '"></span>';
      }).join("");
    return '<div class="statebar-multi" role="img" aria-label="' + C.esc(summary) + '">' + segments + '</div>';
  }
  /* Temporal context from SERVER-provided timestamps only. Missing/invalid
     timestamps render nothing at all (never "just now" without a real
     timestamp); the exact time stays in title + <time datetime>. */
  function relTime(iso) {
    if (!iso) return null;
    var then = new Date(iso);
    if (isNaN(then.getTime())) return null;
    var seconds = Math.max(0, Math.floor((Date.now() - then.getTime()) / 1000));
    if (seconds < 60) return "just now";
    var minutes = Math.floor(seconds / 60);
    if (minutes < 60) return minutes + "m ago";
    var hours = Math.floor(minutes / 60);
    if (hours < 24) return hours + "h ago";
    var days = Math.floor(hours / 24);
    if (days < 7) return days + "d ago";
    return then.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  }
  function timeMeta(created_at, revised_at) {
    var created = relTime(created_at);
    var revised = relTime(revised_at);
    var parts = [];
    if (created) parts.push('<span>Created <time datetime="' + C.esc(String(created_at)) +
      '" title="' + C.esc(String(created_at)) + '">' + C.esc(created) + '</time></span>');
    if (revised && String(revised_at) !== String(created_at)) {
      parts.push('<span>Updated <time datetime="' + C.esc(String(revised_at)) +
        '" title="' + C.esc(String(revised_at)) + '">' + C.esc(revised) + '</time></span>');
    }
    return parts.join("");
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
    stateExplanation: stateExplanation, countsRow: countsRow, stateBar: stateBar,
    stateSummary: stateSummary, timeMeta: timeMeta, relTime: relTime,
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
    if (!API.live) return '<div class="card cc-state-panel offline-panel">' + COMPONENTS.emptyState("sparkle", "Engine offline", "The local AVM engine isn't running — live production data is never replaced by demo productions. Start the engine on this machine, then refresh.") + '</div>';
    var items;
    try { items = await API.getProductions(); }
    catch (e) { return '<div class="card cc-state-panel error-panel">' + COMPONENTS.emptyState("alert", "Production data unavailable", String(e.message || e) + " — reload the page to try again. Nothing was invented to fill this page.") + '</div>'; }
    if (!items.length) return '<div class="page-head"><div><p class="eyebrow">Studio control</p><h1 class="page-title">Command Center</h1><p class="page-sub">Your videos and their production status will live here.</p></div><div class="head-actions"><a class="btn btn-primary" href="#/create">Create Video</a></div></div><div class="card cc-state-panel">' + COMPONENTS.emptyState("sparkle", "No productions yet", "Create your first video — AVM will plan, produce, and render it, and it will appear here with live status.", '<a class="btn btn-primary" href="#/create">Create Video</a>') + '</div>';
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
    /* Real media only: the projects index carries each manifest's own
       binding_production_id (structurally validated server-side). A media
       strip renders only when a bound project's manifest reports an
       available final video — nothing is generated or inferred here. */
    var finishedByProduction = {};
    if (API.live) {
      try {
        (await API.getProjectsWithFallback()).forEach(function (pr) {
          if (pr && !pr.error && !pr.sample && pr.final_video_available &&
              pr.binding_production_id && !finishedByProduction[pr.binding_production_id]) {
            finishedByProduction[pr.binding_production_id] = pr;
          }
        });
      } catch (e) { /* no cross-reference: cards render without media */ }
    }
    var cards = items.map(function (p) {
      var counts = p.counts || {};
      var attention = p.error || !p.counts ? "Production data requires inspection." : counts.FAILED ? "Failure requires inspection." : counts.BLOCKED ? "Dependencies require attention." : counts.IN_FLIGHT ? "Execution is active or a remote job is in flight." : counts.INCOMPLETE ? "Work can continue with Resume." : counts.NOT_STARTED ? "Ready for first execution." : "No attention required.";
      var times = CC.timeMeta(p.created_at, p.revised_at);
      var timesHtml = times ? '<p class="cc-times">' + times + '</p>' : "";
      var tone = p.error || !p.counts || counts.FAILED ? "danger" : counts.BLOCKED || counts.INCOMPLETE ? "warn" : counts.IN_FLIGHT ? "info" : "neutral";
      var finished = finishedByProduction[p.production_id] || null;
      var mediaStrip = finished
        ? '<a class="cc-media-strip" href="#/projects/' + encodeURIComponent(finished.project_id) + '" title="Open the finished video project">' +
          '<video class="cc-media hv-video" preload="none" muted loop playsinline tabindex="-1" aria-hidden="true" src="' +
          COMPONENTS.esc(API.mediaUrl(finished.project_id)) + '" onerror="this.closest(\'.cc-media-strip\').remove()"></video>' +
          '<span class="cc-media-label">' + ICONS.film + '<span>Finished video ready — hover to preview, click to open</span></span></a>'
        : "";
      return '<article class="card production-card attention-' + tone + '">' + mediaStrip +
        '<div class="production-card-head"><div><h2 class="production-title">' + COMPONENTS.esc(p.title) + '</h2><p class="mono muted-text">' + COMPONENTS.esc(p.production_id) + ' · plan v' + COMPONENTS.esc(p.plan_version) + ' · ' + COMPONENTS.esc(p.unit_count) + (p.unit_count === 1 ? ' video' : ' videos') + '</p>' + timesHtml + '</div><div>' + CC.routeChip(p.route) + CC.runBadge(p.run) + '</div></div>' +
        '<div class="attention-strip"><span class="attention-dot" aria-hidden="true"></span><span>' + COMPONENTS.esc(attention) + '</span></div>' +
        '<div class="production-card-body"><p class="command-label">Brief</p><p class="command-text">' + COMPONENTS.esc(p.command_text) + '</p><div class="state-counts" aria-label="Six-state unit summary">' + CC.countsRow(counts) + '</div>' + CC.stateBar(counts, p.unit_count) + (p.error ? '<p class="inline-error">' + COMPONENTS.esc(p.error) + '</p>' : '') + '</div>' +
        '<div class="production-card-foot"><a class="btn btn-sm" href="#/productions/' + encodeURIComponent(p.production_id) + '">Inspect production</a><span class="spacer"></span>' + CC.actionButtons(p) + '</div></article>';
    }).join("");
    return '<div class="page-head"><div><p class="eyebrow">Studio control</p><h1 class="page-title">Command Center</h1><p class="page-sub">Every production and its live status. Commands are explicit and confirmed — nothing runs without your approval.</p></div><div class="head-actions"><a class="btn btn-primary" href="#/create">Create Video</a></div></div>' +
      '<section class="card cc-hero" aria-label="Create a video"><div class="cc-hero-text"><p class="eyebrow">Create</p><h2>What are we making today?</h2><p class="page-sub">Describe your idea — AVM plans, produces, and renders the video. You approve every command.</p></div><div class="cc-hero-actions"><a class="btn btn-primary btn-lg" href="#/create">Create Video</a><a class="btn" href="#/projects">View Projects</a></div></section>' +
      '<div id="production-command-feedback" aria-live="polite"></div><div class="cc-toolbar"><label class="field-inline"><span class="sr-only">Filter productions</span><input class="input" id="production-filter" type="search" placeholder="Filter productions" autocomplete="off"></label><span class="hint">' + COMPONENTS.esc(items.length + ' production' + (items.length === 1 ? '' : 's')) + '</span></div><div class="grid productions-grid" id="production-cards">' + cards + '</div>';
  },
  mount: async function () {
    var filter = document.getElementById("production-filter");
    if (filter) filter.addEventListener("input", function () {
      var query = filter.value.toLowerCase();
      document.querySelectorAll("#production-cards .production-card").forEach(function (card) { card.hidden = query && card.textContent.toLowerCase().indexOf(query) < 0; });
    });
    /* Real-media hover preview (manifest-backed finals only). */
    document.querySelectorAll("#production-cards .hv-video").forEach(function (v) {
      var strip = v.closest(".cc-media-strip");
      if (!strip) return;
      strip.addEventListener("mouseenter", function () {
        v.preload = "metadata";
        var pr = v.play(); if (pr && pr.catch) pr.catch(function () { /* first frame stays */ });
      });
      strip.addEventListener("mouseleave", function () { try { v.pause(); v.currentTime = 0; } catch (e) { /* ignore */ } });
    });
    document.querySelectorAll("[data-cc]").forEach(function (button) {
      button.addEventListener("click", function () {
        var action = button.getAttribute("data-cc"), id = button.getAttribute("data-id"), total = button.getAttribute("data-total");
        CC.confirmDialog({ title: action === "execute" ? "Execute production?" : "Resume production?", description: (action === "execute" ? "Execute" : "Resume") + " production " + id + "?", scope: total + " units total; only the AVM engine decides eligible work.", firstAction: "AVM receives the confirmed command in the background; no completed unit is restarted and no remote job is resubmitted.", confirmLabel: action === "execute" ? "Execute Production" : "Resume Production" }, function () {
          API.productionCommand(id, action).then(function (res) { COMPONENTS.toast(res.message || "Command accepted", "ok"); location.hash = "#/productions/" + encodeURIComponent(id); }).catch(function (err) { var message = err.message || String(err), feedback = document.getElementById("production-command-feedback"); if (feedback) feedback.innerHTML = '<p class="inline-error" role="alert">' + COMPONENTS.esc(message) + '</p>'; COMPONENTS.toast(message, "err"); });
        });
      });
    });
  }
};
