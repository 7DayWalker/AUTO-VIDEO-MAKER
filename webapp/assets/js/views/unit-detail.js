/* AUTO VIDEO MAKER — Command Center: unit inspection & control
   Unit intent, execution state, plan evidence, bound project manifest,
   scene checkpoints, on-demand reconciliation, and explicitly confirmed
   Execute/Resume/Publish commands (thin delegation to existing flows). */
window.VIEWS = window.VIEWS || {};

VIEWS.unitDetail = {
  title: "Unit",

  render: async function (params) {
    if (!API.live) {
      return '<div class="card">' + COMPONENTS.emptyState("sparkle", "Engine offline",
        "Start the local engine (python -m app.web.server) to inspect units.") + "</div>";
    }

    let u;
    try {
      u = await API.getUnit(params.productionId, params.unitId);
    } catch (e) {
      return '<div class="card">' + COMPONENTS.emptyState("alert", "Unit not found",
        String(e.message || e)) + "</div>";
    }

    var intent = u.unit_intent, exec = u.execution || {};
    var back = '#/productions/' + encodeURIComponent(u.production_id);

    var intentRows = [
      ["Unit", COMPONENTS.esc(intent.unit_id + " · ordinal " + intent.ordinal)],
      ["Type", COMPONENTS.esc(intent.unit_type)],
      ["Title", COMPONENTS.esc(intent.title)],
      ["Description", COMPONENTS.esc(intent.description || "—")],
      ["Premise", COMPONENTS.esc(intent.premise || "—")],
      ["Dependencies", COMPONENTS.esc(intent.dependencies.length ? intent.dependencies.join(", ") : "none")],
      ["Provider intent", COMPONENTS.esc(intent.provider_intent || "default")],
      ["Bible refs", COMPONENTS.esc(intent.bible_refs.length ? intent.bible_refs.join(", ") : "none")]
    ];
    if (intent.episode) {
      intentRows.push(["Episode", COMPONENTS.esc("S" + (intent.episode.season_number || "?") +
        "E" + (intent.episode.episode_number || "?") +
        (intent.episode.episode_title ? " — " + intent.episode.episode_title : ""))]);
    }
    intentRows.push(["Content intent", COMPONENTS.esc(Object.keys(intent.content_intent || {}).length
      ? JSON.stringify(intent.content_intent) : "—")]);

    var execRows = [
      ["State", CC.stateBadge(exec.state)],
      ["Bound project", exec.project_id
        ? '<a href="#/projects/' + encodeURIComponent(exec.project_id) + '">' + COMPONENTS.esc(exec.project_id) + "</a>"
        : "no project (not started)"],
      ["Bound plan version", COMPONENTS.esc(exec.bound_plan_version || "—")]
    ];
    if ((u.blocking_units || []).length) {
      execRows.push(["Blocking units", COMPONENTS.esc(u.blocking_units.join(", ")) +
        " — complete these first (Phase 10 enforces dependencies)."]);
    }

    var meta = (u.plan_evidence && u.plan_evidence.meta) || {};
    var planRows = [
      ["Plan artifacts", u.plan_evidence && u.plan_evidence.exists
        ? "present (evidence only — binding authority is ProjectManifest.production_binding)"
        : "none"],
      ["Plan directory", COMPONENTS.esc((u.plan_evidence && u.plan_evidence.plan_directory) || "—")],
      ["Plan identity", COMPONENTS.esc(meta.plan_identity || "—")],
      ["Content hash", COMPONENTS.esc(meta.content_hash || "—")],
      ["Plan versions", COMPONENTS.esc(meta.production_plan_version != null
        ? "production v" + meta.production_plan_version + " · unit v" + (meta.unit_plan_version || 1) : "—")]
    ];

    var html =
      '<div class="view-head">' +
        '<h2>' + COMPONENTS.esc(intent.title) + "</h2>" +
        '<p class="hint">' + COMPONENTS.esc(u.production_title) + " · " +
        COMPONENTS.esc(intent.unit_id) + '· <a href="' + back + '">back to production</a></p>' +
      "</div>" +
      '<div class="grid unit-grid">' +
        '<div class="card"><h3>Unit Intent</h3>' + VIEWS.unitDetail.kv(intentRows) + "</div>" +
        '<div class="card"><h3>Execution State</h3>' + VIEWS.unitDetail.kv(execRows) + "</div>" +
        '<div class="card"><h3>Plan Evidence</h3>' + VIEWS.unitDetail.kv(planRows) +
          '<p class="hint">Version pinning is automatic; plan artifacts are never mutated here.</p></div>' +
      "</div>";

    html += VIEWS.unitDetail.actionsPanel(u, exec);
    if (u.bound_project) html += VIEWS.unitDetail.projectPanel(u);
    if (u.failure_details && u.failure_details.length) html += VIEWS.unitDetail.failurePanel(u);
    html += '<div class="card"><h3>Reconciliation (on-demand, read-only)</h3>' +
      '<div id="cc-reconciliation"><p class="hint">Run a read-only reconciliation report over the bound project artifacts. Nothing is persisted or repaired.</p></div>' +
      '<button class="btn" data-cc-reconcile>Run Reconciliation</button></div>';

    return html;
  },

  kv: function (rows) {
    return '<table class="kv-table">' + rows.map(function (row) {
      return "<tr><th>" + COMPONENTS.esc(row[0]) + "</th><td>" + row[1] + "</td></tr>";
    }).join("") + "</table>";
  },

  actionsPanel: function (u, exec) {
    var state = exec.state;
    var buttons = [];
    if (state === "NOT_STARTED" || state === "INCOMPLETE") {
      buttons.push('<button class="btn btn-primary" data-cc-cmd="execute">Execute Unit</button>');
    }
    if (state === "INCOMPLETE" || state === "IN_FLIGHT") {
      buttons.push('<button class="btn" data-cc-cmd="resume">Resume Unit</button>');
    }
    if (state === "FAILED") {
      // F-01 (audit): no Resume Unit for FAILED units. The Phase 10 executor
      // never auto-resumes FAILED units, so such a button would be a lie;
      // show the truthful recovery state instead.
      var failedProjectId = exec.project_id;
      return '<div class="card"><h3>Actions</h3>' +
        '<p class="hint" style="color:var(--danger)">Unit is FAILED. The Phase 10 ProductionExecutor ' +
        'never automatically resumes FAILED units, and the Command Center offers no retry, skip, or fallback.</p>' +
        '<p class="hint">Recovery (existing mechanisms only): inspect Failure Details below, then resume the ' +
        'bound project via <code>python produce.py --resume &lt;project_id&gt;</code>' +
        (failedProjectId ? " (bound project: " + COMPONENTS.esc(failedProjectId) + ")" : "") +
        ", or issue a revised Phase 9 intent.</p></div>";
    }
    if (state === "COMPLETED") {
      buttons.push('<button class="btn" data-cc-publish>Publish Unit</button>');
    }
    if (state === "BLOCKED") {
      return '<div class="card"><h3>Actions</h3>' +
        '<p class="hint">Unit is BLOCKED by ' + COMPONENTS.esc((u.blocking_units || []).join(", ")) +
        ". Complete those units first — Phase 10 enforces dependencies.</p></div>";
    }
    return '<div class="card"><h3>Actions</h3><div style="display:flex;gap:8px;flex-wrap:wrap">' +
      buttons.join("") + "</div>" +
      '<p class="hint">Every command requires explicit confirmation and flows exclusively ' +
      "through the Phase 10 ProductionExecutor. There is no retry, skip, or cancel.</p></div>";
  },

  projectPanel: function (u) {
    var p = u.bound_project;
    var stages = Object.keys(p.stages || {}).map(function (name) {
      var s = p.stages[name];
      var cls = s.status === "COMPLETED" ? "ok" : s.status === "FAILED" ? "err" :
                s.status === "RUNNING" ? "info" : "";
      return '<span class="chip">' + COMPONENTS.esc(name) +
        ' <span class="badge ' + cls + '">' + COMPONENTS.esc(s.status) + "</span></span>";
    }).join(" ");

    var checkpoints = (u.scene_checkpoint_rows || []).map(function (row) {
      return "<tr>" +
        "<td>" + COMPONENTS.esc(row.scene_id) + "</td>" +
        "<td>" + COMPONENTS.esc(row.provider || "—") + "</td>" +
        "<td>" + COMPONENTS.esc(row.status || "—") + "</td>" +
        "<td>" + COMPONENTS.esc(row.job_id || "—") + "</td>" +
        "<td>" + COMPONENTS.esc(row.generation_cost != null ? row.generation_cost + " credits" : "—") + "</td>" +
        "<td>" + COMPONENTS.esc(row.updated_at || "—") + "</td>" +
      "</tr>";
    }).join("");

    var artifacts = [
      ["Content plan", p.artifacts.content_plan_path],
      ["Voice audio", p.artifacts.voice_audio_path],
      ["Captions", p.artifacts.captions_path],
      ["Final video", p.artifacts.final_video_path]
    ].filter(function (row) { return row[1]; })
      .map(function (row) {
        return "<tr><th>" + COMPONENTS.esc(row[0]) + "</th><td><code>" + COMPONENTS.esc(row[1]) + "</code></td></tr>";
      }).join("");

    var pubs = (p.publications || []).map(function (pub) {
      return '<span class="chip">' + COMPONENTS.esc(pub.platform) +
        " · " + COMPONENTS.esc(pub.state) + "</span>";
    }).join(" ") || '<span class="hint">not published</span>';

    var binding = p.production_binding;
    return '<div class="card"><h3>Bound Project — ' + COMPONENTS.esc(p.project_id) + "</h3>" +
      '<p class="hint">' + COMPONENTS.esc(p.topic) + " · current stage: " +
      COMPONENTS.esc(p.current_stage) + " · updated " + COMPONENTS.esc(p.updated_at) + "</p>" +
      '<div style="margin:10px 0">' + stages + "</div>" +
      (checkpoints ? '<div class="table-wrap"><table class="data"><thead><tr><th>Scene</th><th>Provider</th><th>Status</th><th>Job</th><th>Cost</th><th>Updated</th></tr></thead><tbody>' + checkpoints + "</tbody></table></div>" : "") +
      (artifacts ? '<table class="kv-table">' + artifacts + "</table>" : "") +
      '<div style="margin-top:10px"><span class="hint">Publications: </span>' + pubs + "</div>" +
      (binding ? '<p class="hint">Binding (authority): unit ' + COMPONENTS.esc(binding.unit_id) +
        " · production plan v" + COMPONENTS.esc(binding.production_plan_version) +
        " · hash " + COMPONENTS.esc(String(binding.unit_plan_content_hash).slice(0, 12)) + "…</p>" : "") +
      "</div>";
  },

  failurePanel: function (u) {
    var rows = u.failure_details.map(function (f) {
      return "<tr><th>" + COMPONENTS.esc(f.stage) + "</th><td>" +
        COMPONENTS.esc(f.error || "no error text") + "</td></tr>";
    }).join("");
    return '<div class="card"><h3 style="color:var(--danger)">Failure Details</h3>' +
      '<table class="kv-table">' + rows + "</table>" +
      '<p class="hint">Inspect the stage error above. FAILED units are never auto-resumed; see ' +
      "Actions for the existing recovery path (in-flight remote jobs are protected, there is " +
      "no automatic retry).</p></div>";
  },

  mount: async function (params) {
    var productionId = params.productionId;
    var unitId = params.unitId;

    function command(action) {
      API.productionCommand(productionId, action, unitId)
        .then(function (res) {
          COMPONENTS.toast(res.message || "Command accepted", "ok");
          App.refresh();
        })
        .catch(function (err) { COMPONENTS.toast(String(err.message || err), "err"); });
    }

    document.querySelectorAll("[data-cc-cmd]").forEach(function (button) {
      button.addEventListener("click", function () {
        var action = button.getAttribute("data-cc-cmd");
        CC.confirmDialog({
          title: action === "execute" ? "Execute unit?" : "Resume unit?",
          description: (action === "execute" ? "Execute" : "Resume") + " unit " + unitId + " of production " + productionId + "?",
          scope: "Scope: 1 unit (" + unitId + "). COMPLETED units are never restarted; in-flight remote jobs are never re-submitted (Phase 10 semantics).",
          firstAction: "First action: ProductionExecutor.execute(unit_id=\"" + unitId + "\") runs in a background thread through the Phase 10 engine.",
          confirmLabel: action === "execute" ? "Execute Unit" : "Resume Unit"
        }, function () { command(action); });
      });
    });

    var publish = document.querySelector("[data-cc-publish]");
    if (publish) publish.addEventListener("click", function () {
      CC.confirmDialog({
        title: "Publish unit?",
        description: "Publish unit " + unitId + " through the existing publishing flow (TikTok)?",
        scope: "Scope: 1 unit (" + unitId + ") — the bound project's final video.",
        firstAction: "First action: delegate to the existing TikTok publishing bridge; the durable publication state machine owns all outcomes and duplicate protection.",
        confirmLabel: "Publish Unit"
      }, function () {
        API.publishUnit(productionId, unitId)
          .then(function (res) {
            COMPONENTS.toast(res.message || "Publish result received",
              res.status === "published" ? "ok" : "err");
            App.refresh();
          })
          .catch(function (err) { COMPONENTS.toast(String(err.message || err), "err"); });
      });
    });

    var reconcile = document.querySelector("[data-cc-reconcile]");
    if (reconcile) reconcile.addEventListener("click", function () {
      var target = document.getElementById("cc-reconciliation");
      target.innerHTML = '<p class="hint">Running read-only reconciliation…</p>';
      API.getUnitReconciliation(productionId, unitId).then(function (res) {
        var rec = res.reconciliation || {};
        target.innerHTML =
          '<p><span class="badge ' + (rec.verdict === "PROJECT_COMPLETE" ? "ok" : "warn") + '">' +
            COMPONENTS.esc(rec.verdict) + "</span></p>" +
          "<ul>" + (rec.reasons || []).map(function (reason) {
            return "<li>" + COMPONENTS.esc(reason) + "</li>";
          }).join("") + "</ul>" +
          '<p class="hint">Content: ' + COMPONENTS.esc((rec.content || {}).status) +
          " · Voice: " + COMPONENTS.esc((rec.voice || {}).status) +
          " · Scenes: " + COMPONENTS.esc((rec.scenes || {}).status) +
          " · Captions: " + COMPONENTS.esc((rec.captions || {}).status) +
          " · Assembly: " + COMPONENTS.esc((rec.assembly || {}).status) +
          " · persisted: " + (res.persisted ? "yes" : "no") + "</p>";
      }).catch(function (err) {
        target.innerHTML = '<p class="hint" style="color:var(--danger)">' +
          COMPONENTS.esc(String(err.message || err)) + "</p>";
      });
    });
  }
};


