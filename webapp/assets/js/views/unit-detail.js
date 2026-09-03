/* AUTO VIDEO MAKER — unit workspace.
   Intent and execution are separate; evidence is read-only and server-owned. */
window.VIEWS = window.VIEWS || {};

VIEWS.unitDetail = {
  title: "Unit",

  render: async function (params) {
    if (!API.live) return '<div class="card cc-state-panel offline-panel">' + COMPONENTS.emptyState("sparkle", "Engine offline", "Live unit state is unavailable. Start the local engine with: python -m app.web.server") + '</div>';
    var u;
    try { u = await API.getUnit(params.productionId, params.unitId); }
    catch (e) { return '<div class="card cc-state-panel error-panel">' + COMPONENTS.emptyState("alert", "Unit data unavailable", String(e.message || e)) + '</div>'; }
    var connections = await API.getConnections().catch(function () { return {}; });
    var intent = u.unit_intent || {}, exec = u.execution || {}, state = exec.state;
    var back = '#/productions/' + encodeURIComponent(u.production_id);
    var project = u.bound_project;
    var finalArtifact = project && project.artifacts && project.artifacts.final_video;
    var finalVideo = finalArtifact && finalArtifact.available && finalArtifact.media_url;
    var stateMeaning = CC.stateInfo(state).meaning;

    var intentRows = [
      ["Unit", COMPONENTS.esc(intent.unit_id + " · ordinal " + intent.ordinal)],
      ["Type", COMPONENTS.esc(intent.unit_type || "—")],
      ["Title", COMPONENTS.esc(intent.title || "—")],
      ["Description", COMPONENTS.esc(intent.description || "—")],
      ["Premise", COMPONENTS.esc(intent.premise || "—")],
      ["Dependencies", COMPONENTS.esc(intent.dependencies.length ? intent.dependencies.join(", ") : "none")],
      ["Provider intent", COMPONENTS.esc(intent.provider_intent || "default")],
      ["Bible refs", COMPONENTS.esc((intent.bible_refs || []).length ? intent.bible_refs.join(", ") : "none")]
    ];
    if (intent.episode) intentRows.push(["Episode", COMPONENTS.esc("S" + (intent.episode.season_number || "?") + "E" + (intent.episode.episode_number || "?") + (intent.episode.episode_title ? " — " + intent.episode.episode_title : ""))]);
    intentRows.push(["Content intent", COMPONENTS.esc(Object.keys(intent.content_intent || {}).length ? JSON.stringify(intent.content_intent) : "—")]);

    var execRows = [
      ["State", CC.stateBadge(state)],
      ["Meaning", COMPONENTS.esc(stateMeaning)],
      ["Bound project", exec.project_id ? '<a href="#/projects/' + encodeURIComponent(exec.project_id) + '">' + COMPONENTS.esc(exec.project_id) + '</a>' : '<span class="muted-text">Unbound — no project has been adopted.</span>'],
      ["Bound plan version", COMPONENTS.esc(exec.bound_plan_version || "—")]
    ];
    if ((u.blocking_units || []).length) execRows.push(["Blocking units", COMPONENTS.esc(u.blocking_units.join(", ")) + " — complete these first; Phase 10 enforces dependencies."]);

    var meta = (u.plan_evidence && u.plan_evidence.meta) || {};
    var planRows = [
      ["Plan artifacts", u.plan_evidence && u.plan_evidence.exists ? "Present — evidence only" : "Not generated"],
      ["Plan identity", COMPONENTS.esc(meta.plan_identity || "—")],
      ["Content hash", COMPONENTS.esc(meta.content_hash || "—")],
      ["Plan versions", COMPONENTS.esc(meta.production_plan_version != null ? "production v" + meta.production_plan_version + " · unit v" + (meta.unit_plan_version || 1) : "—")]
    ];

    var actionFeedback = '<div id="unit-action-feedback" aria-live="polite"></div>';
    var sectionLinks = '<nav class="unit-section-links" aria-label="Unit sections">' +
      (exec.project_id ? '<a href="#/projects/' + encodeURIComponent(exec.project_id) + '">Bound project</a>' : '<span>Unbound project</span>') +
      '<a href="#media-section">Media</a><a href="#evidence-section">Evidence</a><a href="#publishing-section">Publishing</a></nav>';
    var html = '<div class="page-head"><div><p class="eyebrow">Unit workspace</p><h1 class="page-title">' + COMPONENTS.esc(intent.title || intent.unit_id) + '</h1><p class="page-sub"><a href="' + back + '">Back to production</a> · ' + COMPONENTS.esc(u.production_title) + ' · ' + COMPONENTS.esc(intent.unit_id) + '</p>' + actionFeedback + sectionLinks + '</div></div>' +
      '<section class="card unit-hero attention-' + (state === "FAILED" ? "danger" : state === "BLOCKED" || state === "INCOMPLETE" ? "warn" : state === "IN_FLIGHT" ? "info" : "neutral") + '" aria-labelledby="unit-heading"><div class="unit-hero-top"><div><p class="eyebrow">Execution state</p><h2 id="unit-heading">' + CC.stateBadge(state) + '</h2><p class="state-meaning">' + COMPONENTS.esc(stateMeaning) + '</p></div><div class="unit-hero-project">' + (exec.project_id ? '<span class="eyebrow">Bound project</span><a href="#/projects/' + encodeURIComponent(exec.project_id) + '">' + COMPONENTS.esc(exec.project_id) + '</a>' : '<span class="muted-text">Unbound project</span>') + '</div></div>' + VIEWS.unitDetail.actionPanel(u, exec) + '</section>' +
      '<div class="unit-sections">' +
      '<section class="card" aria-labelledby="intent-section"><div class="section-heading"><div><p class="eyebrow">Production → Unit</p><h2 id="intent-section">Intent</h2></div><span class="badge muted">Authoritative production intent</span></div>' + VIEWS.unitDetail.kv(intentRows) + '</section>' +
      '<section class="card" aria-labelledby="execution-section"><div class="section-heading"><div><p class="eyebrow">Phase 10 authority</p><h2 id="execution-section">Execution</h2></div><span class="hint">Derived from ProductionExecutor.derive_progress()</span></div>' + VIEWS.unitDetail.kv(execRows) + VIEWS.unitDetail.stagePanel(project) + '</section>' +
      '<section class="card" aria-labelledby="media-section"><div class="section-heading"><div><p class="eyebrow">Unit → Media</p><h2 id="media-section">Media and storyboard</h2></div><span class="hint">Existing evidence only</span></div>' + VIEWS.unitDetail.mediaPanel(u, finalVideo) + '</section>' +
      '<section class="card" aria-labelledby="evidence-section"><div class="section-heading"><div><p class="eyebrow">Supporting evidence</p><h2 id="evidence-section">Artifacts and plan evidence</h2></div></div>' + VIEWS.unitDetail.artifactPanel(project) + VIEWS.unitDetail.kv(planRows) + '<p class="hint">Plan artifacts and paths are evidence only; binding authority remains ProjectManifest.production_binding.</p></section>' +
      (u.failure_details && u.failure_details.length ? VIEWS.unitDetail.failurePanel(u) : '') +
      '<section class="card" aria-labelledby="reconciliation-section"><div class="section-heading"><div><p class="eyebrow">Read-only check</p><h2 id="reconciliation-section">Reconciliation</h2></div></div><div id="cc-reconciliation"><p class="hint">Run the existing reconciliation oracle on demand. Nothing is persisted or repaired.</p></div><button class="btn" data-cc-reconcile>Run reconciliation</button></section>' +
      '<section class="card" aria-labelledby="publishing-section"><div class="section-heading"><div><p class="eyebrow">Unit ? Publishing</p><h2 id="publishing-section">Publishing</h2></div><span class="hint">Existing records and explicit actions only</span></div>' + VIEWS.unitDetail.publishingPanel(u, project, connections, finalVideo) + '</section>' +
      '</div>';
    return html;
  },

  kv: function (rows) {
    return '<table class="kv-table">' + rows.map(function (row) { return '<tr><th>' + COMPONENTS.esc(row[0]) + '</th><td>' + row[1] + '</td></tr>'; }).join('') + '</table>';
  },

  stagePanel: function (project) {
    if (!project) return '<p class="hint stage-summary">No bound project stage evidence exists.</p>';
    var stages = Object.keys(project.stages || {}).map(function (name) {
      var stage = project.stages[name] || {};
      var status = stage.status || "PENDING";
      var tone = status === "COMPLETED" ? "ok" : status === "FAILED" ? "err" : status === "RUNNING" ? "info" : "muted";
      return '<span class="chip stage-chip"><strong>' + COMPONENTS.esc(name) + '</strong> <span class="badge ' + tone + '">' + COMPONENTS.esc(status) + '</span></span>';
    }).join('');
    return '<div class="stage-summary"><p class="hint">Current stage: <strong>' + COMPONENTS.esc(project.current_stage || "Unavailable") + '</strong></p><div class="stage-list" aria-label="Project stage progression">' + (stages || '<span class="hint">No stage evidence recorded.</span>') + '</div></div>';
  },

  publishingPanel: function (u, project, connections, finalVideo) {
    if (!project) return '<div class="media-empty"><span class="badge muted">UNBOUND</span><p>Publishing requires an authoritative bound project.</p></div>';
    var records = {};
    (project.publications || []).forEach(function (record) { records[record.platform] = record; });
    var state = (u.execution || {}).state;
    return COMPONENTS.PLATFORMS.filter(function (platform) { return platform.id === "youtube" || platform.id === "tiktok"; }).map(function (platform) {
      var connection = connections[platform.id] || {};
      var record = records[platform.id];
      var connected = connection.status === "CONNECTED";
      var detail;
      var action = record ? COMPONENTS.pubBadge(record.state, false) : '<span class="badge muted">Not published</span>';
      if (platform.id === "youtube") {
        detail = (connected ? "Connected" : "Not connected") + " ? this Command Center action is not supported for YouTube.";
        action += ' <span class="badge muted">Unsupported here</span>';
      } else if (record && ["VERIFIED", "VERIFIED_SANDBOX_PRIVATE", "UPLOAD_SUBMITTED", "IDENTITY_PERSISTED", "PROCESSING", "UNKNOWN_RECONCILIATION_REQUIRED", "FAILED"].indexOf(record.state) >= 0) {
        detail = "Existing durable TikTok publication state; inspect it before any new action.";
      } else if (state !== "COMPLETED" || !finalVideo) {
        detail = "TikTok publishing requires a completed unit and safely available final media.";
      } else if (!connected) {
        detail = "Connect TikTok before using the existing per-unit publish action.";
      } else {
        detail = "Connected ? supported by the existing per-unit TikTok bridge.";
        action += ' <button class="btn btn-sm btn-primary" data-cc-publish>Publish Unit</button>';
      }
      return '<div class="destination-row"><div><strong>' + COMPONENTS.esc(platform.name) + '</strong><p class="hint">' + COMPONENTS.esc(detail) + '</p></div><div class="destination-actions">' + action + '</div></div>';
    }).join('');
  },

  actionPanel: function (u, exec) {
    var state = exec.state, buttons = [], note;
    var resumable = state === "INCOMPLETE" || state === "IN_FLIGHT";
    if (state === "NOT_STARTED") buttons.push('<button class="btn btn-primary" data-cc-cmd="execute">Execute Unit</button>');
    else if (resumable && state === "INCOMPLETE") buttons.push('<button class="btn btn-primary" data-cc-cmd="resume">Resume Unit</button>');
    else if (state === "IN_FLIGHT") {
      if (u.run && u.run.running) buttons.push('<button class="btn" disabled title="An active command is registered">Inspecting active run</button>');
      else buttons.push('<button class="btn" data-cc-cmd="resume">Resume Unit</button>');
      note = "Inspect first. Resume never submits another remote job.";
    } else if (state === "BLOCKED") note = "View blockers: " + (u.blocking_units || []).join(', ') + ". Phase 10 must satisfy them first.";
    else if (state === "FAILED") note = "Inspect failure details. FAILED_UNIT_REQUIRES_EXPLICIT_RECOVERY; use the existing project recovery path (python produce.py --resume <project_id>) only after diagnosis. No retry, skip, fallback, or force action is offered.";
    else note = "Completed units are not re-executed.";
    var project = u.bound_project, final = project && project.artifacts && project.artifacts.final_video;
    return '<div class="unit-actions"><div class="action-group">' + buttons.join('') + '</div><p class="hint">' + COMPONENTS.esc(note || 'Commands require explicit confirmation and delegate to the existing engine.') + '</p></div>';
  },

  mediaPanel: function (u, finalVideo) {
    var video = finalVideo ? '<div class="media-player"><video controls preload="metadata" src="' + COMPONENTS.esc(finalVideo) + '"></video></div><p class="hint">Available for preview — browser playback determines playability; this does not claim pipeline finality beyond the authoritative artifact state.</p>' : '<div class="media-empty"><span class="badge muted">No playable final media</span><p>' + COMPONENTS.esc((u.execution || {}).state === "COMPLETED" ? "A completed unit has no safely available final-video artifact." : "Final media is not generated or safely available for this unit.") + '</p></div>';
    var rows = u.scene_checkpoint_rows || [];
    var storyboard = rows.length ? '<ol class="storyboard">' + rows.map(function (row, index) {
      var media = row.video_available && row.media_url;
      var preview = media ? '<video controls preload="none" src="' + COMPONENTS.esc(media) + '"></video>' : '<div class="scene-placeholder"><span>' + COMPONENTS.esc(row.artifact_state || "NOT GENERATED") + '</span></div>';
      return '<li class="scene-card"><div class="scene-number">' + COMPONENTS.esc(index + 1) + '</div><div class="scene-content">' + preview + '<div class="scene-meta"><strong>' + COMPONENTS.esc(row.scene_id) + '</strong><span>' + COMPONENTS.esc(row.artifact_state || row.status || "Evidence unavailable") + '</span><span>Provider: ' + COMPONENTS.esc(row.provider || "Not recorded") + '</span><span>Cost: ' + COMPONENTS.esc(row.generation_cost == null ? "Unavailable" : row.generation_cost + " credits") + '</span><span>' + COMPONENTS.esc(row.updated_at || "Timestamp unavailable") + '</span></div></div></li>';
    }).join('') + '</ol>' : '<div class="media-empty"><p>No scene checkpoint evidence is recorded for this bound project.</p></div>';
    return '<div class="unit-media-grid"><div>' + video + '</div><div><p class="eyebrow">Scene storyboard</p>' + storyboard + '</div></div>';
  },

  artifactPanel: function (project) {
    if (!project) return '<div class="media-empty"><span class="badge muted">UNBOUND</span><p>No project artifacts are associated with this unit. Legacy or unbound projects are not adopted automatically.</p></div>';
    var artifacts = project.artifacts || {};
    return '<div class="artifact-grid">' + Object.keys(artifacts).map(function (key) { var item = artifacts[key] || {}; return '<article class="artifact-card"><div><p class="eyebrow">' + COMPONENTS.esc(item.type || key.replace(/_/g, ' ')) + '</p>' + CC.stateBadge(item.state) + '</div><p>' + COMPONENTS.esc(item.reason || (item.available ? 'Safely addressable evidence.' : 'No safely addressable file.')) + '</p>' + (item.media_url && item.available ? '<a class="btn btn-sm" href="' + COMPONENTS.esc(item.media_url) + '">Open media</a>' : '') + '</article>'; }).join('') + '</div>';
  },

  failurePanel: function (u) {
    return '<section class="card failure-panel" aria-labelledby="failure-heading"><div class="section-heading"><div><p class="eyebrow">Persistent error context</p><h2 id="failure-heading">Failure details</h2></div></div><table class="kv-table">' + u.failure_details.map(function (f) { return '<tr><th>' + COMPONENTS.esc(f.stage) + '</th><td>' + COMPONENTS.esc(f.error || 'No error text recorded') + '</td></tr>'; }).join('') + '</table><p class="hint">What AVM did not do: it did not retry or silently substitute another action. Existing recovery guidance remains authoritative.</p></section>';
  },

  mount: async function (params) {
    var productionId = params.productionId, unitId = params.unitId;
    function command(action) { API.productionCommand(productionId, action, unitId).then(function (res) { COMPONENTS.toast(res.message || "Command accepted", "ok"); App.refresh(); }).catch(function (err) { var message = err.message || String(err), feedback = document.getElementById("unit-action-feedback"); if (feedback) feedback.innerHTML = '<p class="inline-error" role="alert">' + COMPONENTS.esc(message) + '</p>'; COMPONENTS.toast(message, "err"); }); }
    document.querySelectorAll("[data-cc-cmd]").forEach(function (button) { button.addEventListener("click", function () { var action = button.getAttribute("data-cc-cmd"); CC.confirmDialog({ title: action === "execute" ? "Execute unit?" : "Resume unit?", description: (action === "execute" ? "Execute" : "Resume") + " unit " + unitId + "?", scope: "Scope: 1 unit (" + unitId + ") only; Phase 10 owns dependencies and remote-job protection.", firstAction: "First action: ProductionExecutor.execute(unit_id=\"" + unitId + "\") receives the confirmed command.", confirmLabel: action === "execute" ? "Execute Unit" : "Resume Unit" }, function () { command(action); }); }); });
    var publish = document.querySelector("[data-cc-publish]");
    if (publish) publish.addEventListener("click", function () { CC.confirmDialog({ title: "Publish unit?", description: "Publish unit " + unitId + " through the existing TikTok publishing flow?", scope: "1 completed, bound unit; no batch publishing.", firstAction: "The existing publishing adapter receives the confirmed request and owns durable outcomes.", confirmLabel: "Publish Unit" }, function () { API.publishUnit(productionId, unitId).then(function (res) { COMPONENTS.toast(res.message || "Publish result received", res.status === "published" ? "ok" : "err"); App.refresh(); }).catch(function (err) { var message = err.message || String(err), feedback = document.getElementById("unit-action-feedback"); if (feedback) feedback.innerHTML = '<p class="inline-error" role="alert">' + COMPONENTS.esc(message) + '</p>'; COMPONENTS.toast(message, "err"); }); }); });
    var reconcile = document.querySelector("[data-cc-reconcile]");
    if (reconcile) reconcile.addEventListener("click", function () { var target = document.getElementById("cc-reconciliation"); target.setAttribute("aria-busy", "true"); target.innerHTML = '<p class="hint" role="status">Running read-only reconciliation…</p>'; API.getUnitReconciliation(productionId, unitId).then(function (res) { var rec = res.reconciliation || {}; target.setAttribute("aria-busy", "false"); target.innerHTML = '<p><span class="badge ' + (rec.verdict === "PROJECT_COMPLETE" ? "ok" : "warn") + '">' + COMPONENTS.esc(rec.verdict || "Unknown") + '</span></p><ul>' + (rec.reasons || []).map(function (reason) { return '<li>' + COMPONENTS.esc(reason) + '</li>'; }).join('') + '</ul><p class="hint">Content: ' + COMPONENTS.esc((rec.content || {}).status || "—") + ' · Voice: ' + COMPONENTS.esc((rec.voice || {}).status || "—") + ' · Scenes: ' + COMPONENTS.esc((rec.scenes || {}).status || "—") + ' · Assembly: ' + COMPONENTS.esc((rec.assembly || {}).status || "—") + ' · persisted: no</p>'; }).catch(function (err) { target.setAttribute("aria-busy", "false"); target.innerHTML = '<p class="inline-error" role="alert">' + COMPONENTS.esc(err.message || String(err)) + '</p>'; }); });
  }
};
