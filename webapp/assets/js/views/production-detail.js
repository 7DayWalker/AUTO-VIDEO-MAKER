/* AUTO VIDEO MAKER — production detail.
   Identity → attention → progress/action → units → supporting evidence. */
window.VIEWS = window.VIEWS || {};

VIEWS.productionDetail = {
  title: "Production",

  render: async function (params) {
    if (!API.live) return '<div class="card cc-state-panel offline-panel">' + COMPONENTS.emptyState("sparkle", "Engine offline", "Live production state is unavailable. Start the engine on this machine, then refresh.") + '</div>';
    var p;
    try { p = await API.getProduction(params.id); }
    catch (e) { return '<div class="card cc-state-panel error-panel">' + COMPONENTS.emptyState("alert", "Production data unavailable", String(e.message || e)) + '</div>'; }

    var counts = p.counts || {};
    var firstRunnable = (p.units || []).find(function (u) {
      return u.execution && (u.execution.state === "NOT_STARTED" || u.execution.state === "INCOMPLETE" || u.execution.state === "IN_FLIGHT");
    });
    var attention = counts.FAILED ? "Failed units require inspection; automatic recovery is not available." :
      counts.BLOCKED ? "Some units are waiting for dependencies to complete." :
      counts.IN_FLIGHT ? "Execution is active or a remote job is in flight; inspect before issuing another command." :
      counts.INCOMPLETE ? "The production has resumable work." :
      counts.NOT_STARTED ? "The production is ready for its first execution." : "All units are completed; no re-execution is offered.";
    var tone = counts.FAILED ? "danger" : counts.BLOCKED || counts.INCOMPLETE ? "warn" : counts.IN_FLIGHT ? "info" : "neutral";
    var strategyLabel = {
      local: "Local (default)",
      alibaba_image_local_video: "Alibaba Image → Local Video",
      alibaba_video: "Alibaba Video"
    }[p.generation_strategy] || "Local (default)";
    /* Batch 1G: the first-class Production Route is a PRODUCT-level identity
       (Batch 1D) — never a provider/model, and never inferred from the legacy
       strategy. Legacy productions have no persisted route. */
    var routeLabels = {
      local_ai_assisted: "Local AI-Assisted",
      full_ai_video: "Full AI Video",
      google_flow: "Google Flow (legacy private)"
    };
    var routeLabel = p.route
      ? (routeLabels[p.route] || p.route)
      : "Not specified (legacy)";

    var rows = (p.units || []).map(function (u) {
      var intent = u.intent || {}, exec = u.execution || {}, state = exec.state;
      var unitUrl = '#/productions/' + encodeURIComponent(p.production_id) + '/units/' + encodeURIComponent(intent.unit_id);
      var blockers = (u.blocking_units || []).map(function (id) {
        return '<a href="#/productions/' + encodeURIComponent(p.production_id) + '/units/' + encodeURIComponent(id) + '">' + COMPONENTS.esc(id) + '</a>';
      }).join(", ");
      var blockerLabel = u.blocking_units && u.blocking_units.length
        ? COMPONENTS.esc(u.blocking_units.join(", ")) : "";
      var project = exec.project_id ? '<a href="#/projects/' + encodeURIComponent(exec.project_id) + '">' + COMPONENTS.esc(exec.project_id) + '</a>' : '<span class="muted-text">Unbound</span>';
      var action;
      if (state === "NOT_STARTED") action = '<button class="btn btn-sm btn-primary" data-cc-unit="execute" data-unit-id="' + COMPONENTS.esc(intent.unit_id) + '">Execute Unit</button>';
      else if (state === "INCOMPLETE") action = '<button class="btn btn-sm btn-primary" data-cc-unit="resume" data-unit-id="' + COMPONENTS.esc(intent.unit_id) + '">Resume Unit</button>';
      else if (state === "IN_FLIGHT") action = p.run && p.run.running
        ? '<button class="btn btn-sm" disabled title="An active command is registered; inspect live progress">Inspecting active run</button>'
        : '<button class="btn btn-sm" data-cc-unit="resume" data-unit-id="' + COMPONENTS.esc(intent.unit_id) + '">Resume Unit</button>';
      else if (state === "BLOCKED") action = '<span class="hint">View blockers below</span>';
      else if (exec.state === "FAILED") action = '<span class="hint">Inspect failure; explicit recovery required</span>';
      else action = '<a class="btn btn-sm" href="' + unitUrl + '">View unit</a>';
      return '<tr><td>' + COMPONENTS.esc(intent.ordinal) + '</td><td><a class="unit-link" href="' + unitUrl + '">' + COMPONENTS.esc(intent.unit_id) + '</a><div class="hint">' + COMPONENTS.esc(intent.title) + '</div></td><td>' + CC.stateBadge(state) + '<div class="state-reason">' + COMPONENTS.esc(exec.reason || ((u.blocking_units || []).length ? "Waiting for dependency evidence" : "")) + '</div></td><td title="' + blockerLabel + '">' + (blockers || '<span class="muted-text">None</span>') + '</td><td>' + project + '</td><td>' + action + '</td></tr>';
    }).join("");

    /* Track 3 Increment 4: for a COMPLETED single-video production the
       user's primary question is "Where is my video?" — surface the real
       final artifact prominently, reusing the existing media endpoint and
       the same player conventions as the project output page. Media
       absence or failure simply omits this section; it never alters the
       truthful production-state presentation. */
    var videoHero = "";
    if (API.live && p.unit_count === 1 && counts.COMPLETED === 1 && !(p.run && p.run.running)) {
      var soleUnit = (p.units || [])[0] || {};
      var soleProjectId = (soleUnit.execution || {}).project_id;
      if (soleProjectId) {
        var soleProject = await API.getProject(soleProjectId).catch(function () { return null; });
        if (soleProject && !soleProject.sample && soleProject.final_video_available) {
          videoHero = '<section class="card media-hero" aria-labelledby="video-hero-heading"><div class="section-heading"><div><p class="eyebrow">Your video</p><h2 id="video-hero-heading">Completed output</h2></div><a class="btn btn-sm" href="#/projects/' + encodeURIComponent(soleProjectId) + '">Open output page</a></div><div class="media-player"><video controls preload="metadata" src="' + COMPONENTS.esc(API.mediaUrl(soleProjectId)) + '"></video></div></section>';
        }
      }
    }
    var runnable = firstRunnable ? firstRunnable.intent.unit_id : "";
    var productionAction = "";
    if (p.run && p.run.running) productionAction = '<button class="btn" disabled title="An active command is registered">Execution active</button>';
    else if (counts.INCOMPLETE > 0 || counts.IN_FLIGHT > 0) productionAction = '<button class="btn btn-primary" data-cc-prod="resume" data-total="' + COMPONENTS.esc(p.unit_count) + '" data-first="' + COMPONENTS.esc(runnable) + '">Resume Production</button>';
    else if (counts.NOT_STARTED > 0) productionAction = '<button class="btn btn-primary" data-cc-prod="execute" data-total="' + COMPONENTS.esc(p.unit_count) + '" data-first="' + COMPONENTS.esc(runnable) + '">Execute Production</button>';
    else if (counts.FAILED > 0) productionAction = '<span class="hint">Inspect failed units; no automatic recovery.</span>';
    else productionAction = '<span class="hint">Completed — no re-execution.</span>';

    var times = CC.timeMeta(p.created_at, p.revised_at);
    var timesHtml = times ? '<p class="cc-times">' + times + '</p>' : "";
    return '<div class="page-head"><div><p class="eyebrow">Production control</p><h1 class="page-title">' + COMPONENTS.esc(p.title) + '</h1><p class="page-sub mono">' + COMPONENTS.esc(p.production_id) + ' · plan v' + COMPONENTS.esc(p.plan_version) + ' · ' + COMPONENTS.esc(p.unit_count) + ' units</p>' + timesHtml + '</div><div class="head-actions"><button class="btn" data-cc-refresh>Refresh</button></div></div>' + videoHero +
      '<section class="card production-hero attention-' + tone + '" aria-labelledby="production-heading"><div class="production-hero-top"><div><p class="eyebrow">Production identity</p><h2 id="production-heading">' + COMPONENTS.esc(p.title) + '</h2><p class="command-text">' + COMPONENTS.esc((p.command || {}).text || "No command text recorded") + '</p></div><div class="hero-status"><span class="attention-dot" aria-hidden="true"></span><strong>Attention</strong><p>' + COMPONENTS.esc(attention) + '</p>' + CC.runBadge(p.run) + '</div></div><div class="state-counts" aria-label="Unit state counts">' + CC.countsRow(counts) + '</div>' + CC.stateBar(counts, p.unit_count) + '</section>' +
      '<section class="card action-panel" aria-labelledby="production-actions-heading"><div class="section-heading"><div><p class="eyebrow">Next action</p><h2 id="production-actions-heading">Next step</h2></div><div class="action-group">' + productionAction + '</div></div><p class="hint">State is re-verified on the server when you confirm. Refreshing never starts work — only the buttons above do.</p><div id="production-action-feedback" aria-live="polite"></div></section>' +
      '<section class="card" aria-labelledby="units-heading"><div class="section-heading"><div><p class="eyebrow">Production → Units</p><h2 id="units-heading">Unit workload</h2></div><span class="hint">' + COMPONENTS.esc(p.unit_count) + ' units · live state derived from disk</span></div><div class="table-wrap"><table class="data unit-table"><thead><tr><th>#</th><th>Unit</th><th>State / reason</th><th>Blockers</th><th>Bound project</th><th>Action</th></tr></thead><tbody>' + (rows || '<tr><td colspan="6"><div class="empty"><h4>No units recorded</h4></div></td></tr>') + '</tbody></table></div></section>' +
      '<section class="card evidence-panel" aria-labelledby="intent-heading"><div class="section-heading"><div><p class="eyebrow">Supporting evidence</p><h2 id="intent-heading">Production intent</h2></div></div><dl class="detail-list"><div><dt>Command</dt><dd>' + COMPONENTS.esc((p.command || {}).text || "—") + '</dd></div><div><dt>Mode</dt><dd>' + COMPONENTS.esc((p.command || {}).workload_shape || "—") + '</dd></div><div><dt>Intended units</dt><dd>' + COMPONENTS.esc((p.command || {}).intended_unit_count || p.unit_count) + '</dd></div><div><dt>Route</dt><dd>' + COMPONENTS.esc(routeLabel) + '</dd></div><div><dt>Legacy strategy (compatibility)</dt><dd>' + COMPONENTS.esc(strategyLabel) + '</dd></div><div><dt>Plan version</dt><dd>' + COMPONENTS.esc(p.plan_version) + '</dd></div></dl><p class="hint">Plan artifacts and project directories are evidence only. Binding authority remains ProjectManifest.production_binding.</p></section>';
  },

  mount: async function (params) {
    var id = params.id;
    function command(action, unitId) {
      API.productionCommand(id, action, unitId || undefined).then(function (res) { COMPONENTS.toast(res.message || "Command accepted", "ok"); VIEWS.productionDetail.startPolling(id); }).catch(function (err) { var message = err.message || String(err), feedback = document.getElementById("production-action-feedback"); if (feedback) feedback.innerHTML = '<p class="inline-error" role="alert">' + COMPONENTS.esc(message) + '</p>'; COMPONENTS.toast(message, "err"); });
    }
    document.querySelectorAll("[data-cc-prod]").forEach(function (button) {
      button.addEventListener("click", function () {
        var action = button.getAttribute("data-cc-prod"), total = button.getAttribute("data-total"), first = button.getAttribute("data-first");
        CC.confirmDialog({ title: action === "execute" ? "Execute production?" : "Resume production?", description: (action === "execute" ? "Execute" : "Resume") + " production " + id + "?", scope: total + " units total; only the AVM engine decides eligible work.", firstAction: first ? "First action: AVM starts with the current eligible unit " + first + " in the background." : "First action: no eligible unit is currently available; failed units require explicit recovery.", confirmLabel: action === "execute" ? "Execute Production" : "Resume Production" }, function () { command(action); });
      });
    });
    document.querySelectorAll("[data-cc-unit]").forEach(function (button) {
      button.addEventListener("click", function () {
        var action = button.getAttribute("data-cc-unit"), unitId = button.getAttribute("data-unit-id");
        CC.confirmDialog({ title: action === "execute" ? "Execute unit?" : "Resume unit?", description: (action === "execute" ? "Execute" : "Resume") + " unit " + unitId + "?", scope: "Scope: 1 unit (" + unitId + "). Dependencies and remote-job protection are handled automatically.", firstAction: "The confirmed command starts production of this unit in the background.", confirmLabel: action === "execute" ? "Execute Unit" : "Resume Unit" }, function () { command(action, unitId); });
      });
    });
    var refresh = document.querySelector("[data-cc-refresh]");
    if (refresh) refresh.addEventListener("click", function () { App.refresh(); });
    VIEWS.productionDetail.startPolling(id);
  },

  startPolling: function (id) {
    if (VIEWS.productionDetail._timer) clearInterval(VIEWS.productionDetail._timer);
    var hash = location.hash;
    VIEWS.productionDetail._timer = setInterval(function () {
      if (location.hash !== hash || !API.live) { clearInterval(VIEWS.productionDetail._timer); VIEWS.productionDetail._timer = null; return; }
      API.getProductionProgress(id).then(function (progress) { if (!progress.run || !progress.run.running) { clearInterval(VIEWS.productionDetail._timer); VIEWS.productionDetail._timer = null; App.refresh(); } }).catch(function () { /* refresh remains explicit */ });
    }, 3000);
  }
};
