/* AUTO VIDEO MAKER — Command Center: production detail view
   Unit table, six-state progress summary, Execute/Resume with explicit
   confirmation, and read-only polling that NEVER triggers execution. */
window.VIEWS = window.VIEWS || {};

VIEWS.productionDetail = {
  title: "Production",

  render: async function (params) {
    if (!API.live) {
      return '<div class="card">' + COMPONENTS.emptyState("sparkle", "Engine offline",
        "Start the local engine (python -m app.web.server) to inspect productions.") + "</div>";
    }

    let p;
    try {
      p = await API.getProduction(params.id);
    } catch (e) {
      return '<div class="card">' + COMPONENTS.emptyState("alert", "Production not found",
        String(e.message || e)) + "</div>";
    }

    var rows = p.units.map(function (u) {
      var intent = u.intent, exec = u.execution || {};
      var deps = intent.dependencies.length
        ? intent.dependencies.join(", ") : "—";
      var blocking = (u.blocking_units || []).length
        ? '<div class="hint">blocked by: ' + COMPONENTS.esc(u.blocking_units.join(", ")) + "</div>"
        : "";
      var project = exec.project_id
        ? '<a href="#/projects/' + encodeURIComponent(exec.project_id) + '">' + COMPONENTS.esc(exec.project_id) + "</a>"
        : '<span class="hint">no project</span>';
      var action = exec.state === "COMPLETED"
        ? '<a class="btn btn-sm" href="#/productions/' + encodeURIComponent(p.production_id) +
          "/units/" + encodeURIComponent(intent.unit_id) + '">Unit Detail</a>'
        : (exec.state === "BLOCKED"
            ? '<span class="hint">complete dependencies first</span>'
            : (exec.state === "FAILED"
                ? '<a class="btn btn-sm" href="#/productions/' + encodeURIComponent(p.production_id) +
                  "/units/" + encodeURIComponent(intent.unit_id) + '">Unit Detail</a> ' +
                  '<span class="hint">FAILED — explicit recovery required</span>'
                : '<button class="btn btn-sm btn-primary" data-cc-unit="' + COMPONENTS.esc(intent.unit_id) +
                  '">Execute Unit</button>'));
      return (
        "<tr>" +
          "<td>" + COMPONENTS.esc(intent.ordinal) + "</td>" +
          "<td><a href=\"#/productions/" + encodeURIComponent(p.production_id) +
            "/units/" + encodeURIComponent(intent.unit_id) + "\">" + COMPONENTS.esc(intent.unit_id) + "</a></td>" +
          "<td>" + COMPONENTS.esc(intent.title) + "</td>" +
          "<td>" + COMPONENTS.esc(deps) + blocking + "</td>" +
          "<td>" + CC.stateBadge(exec.state) + "</td>" +
          "<td>" + project + "</td>" +
          "<td>" + action + "</td>" +
        "</tr>"
      );
    }).join("");

    /* F-04 (audit): authoritative scope data for the confirmation dialogs
       (Amendment F). Only API-provided units and executor-derived states are
       used; no execution ordering is invented here — the lowest-ordinal
       actionable unit is displayed as factual state, never claimed as a
       guaranteed execution order. FAILED units are never listed as runnable
       (F-01): the Phase 10 executor will not resume them. */
    var runnable = p.units.filter(function (u) {
      return u.execution && (u.execution.state === "NOT_STARTED" ||
        u.execution.state === "INCOMPLETE" || u.execution.state === "IN_FLIGHT");
    });
    var firstRunnable = runnable.length ? runnable[0].intent.unit_id : "";
    var failedCount = p.units.filter(function (u) {
      return u.execution && u.execution.state === "FAILED";
    }).length;

    return (
      '<div class="page-head"><div>' +
        "<h2>" + COMPONENTS.esc(p.title) + "</h2>" +
        '<p class="hint">' + COMPONENTS.esc(p.production_id) + " · plan v" + COMPONENTS.esc(p.plan_version) +
        " · " + COMPONENTS.esc(p.unit_count) + " units · created " + COMPONENTS.esc(p.created_at) + "</p>" +
      "</div></div>" +
      '<div class="card">' +
        '<div class="pc-head"><div class="pc-glyph">' + ICONS.sparkle + '</div>' +
        '<div><div class="pc-name">Command provenance</div>' +
        '<div class="pc-sub">Workload: ' + COMPONENTS.esc(p.command.workload_shape) +
        (p.command.intended_unit_count ? " · intended units: " + COMPONENTS.esc(p.command.intended_unit_count) : "") +
        "</div></div></div>" +
        '<p class="hint" style="margin:10px 2px">“' + COMPONENTS.esc(p.command.text) + "”</p>" +
        '<div style="margin:10px 0">' + CC.countsRow(p.counts) + "</div>" +
        CC.progressBar(p.counts, p.unit_count) +
        '<div id="cc-run-status" style="margin-top:10px">' + CC.runBadge(p.run) + "</div>" +
      "</div>" +
      '<div class="card">' +
        '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px">' +
          '<button class="btn btn-primary" data-cc-prod="execute" data-total="' + COMPONENTS.esc(p.unit_count) + '" data-first="' + COMPONENTS.esc(firstRunnable) + '" data-failed="' + COMPONENTS.esc(failedCount) + '">Execute Production</button>' +
          '<button class="btn" data-cc-prod="resume" data-total="' + COMPONENTS.esc(p.unit_count) + '" data-first="' + COMPONENTS.esc(firstRunnable) + '" data-failed="' + COMPONENTS.esc(failedCount) + '">Resume Production</button>' +
          '<span class="spacer"></span>' +
          '<button class="btn btn-ghost" data-cc-refresh>Refresh</button>' +
        "</div>" +
        '<div class="table-wrap"><table class="data">' +
          "<thead><tr><th>#</th><th>Unit</th><th>Title</th><th>Dependencies</th><th>State</th><th>Bound project</th><th></th></tr></thead>" +
          "<tbody>" + rows + "</tbody>" +
        "</table></div>" +
        '<p class="hint">States are derived on demand by the Phase 10 ProductionExecutor; ' +
        "dependencies are enforced by Phase 10, only displayed here.</p>" +
      "</div>"
    );
  },

  mount: async function (params) {
    var id = params.id;

    function command(action, unitId) {
      API.productionCommand(id, action, unitId || undefined)
        .then(function (res) {
          COMPONENTS.toast(res.message || "Command accepted", "ok");
          VIEWS.productionDetail.startPolling(id);
        })
        .catch(function (err) { COMPONENTS.toast(String(err.message || err), "err"); });
    }

    document.querySelectorAll("[data-cc-prod]").forEach(function (button) {
      button.addEventListener("click", function () {
        var action = button.getAttribute("data-cc-prod");
        var total = button.getAttribute("data-total") || "?";
        var first = button.getAttribute("data-first");
        var failed = button.getAttribute("data-failed") || "0";
        CC.confirmDialog({
          title: action === "execute" ? "Execute production?" : "Resume production?",
          description: (action === "execute" ? "Execute" : "Resume") + " production " + id + " (" + total + " units total)?",
          scope: "Scope: " + total + " units total; only eligible units run. COMPLETED units are never restarted; in-flight remote jobs are never re-submitted.",
          firstAction: first
            ? "First action: the Phase 10 ProductionExecutor continues the lowest-ordinal actionable unit (" + first + ") in the background."
            : (failed !== "0"
                ? "No eligible units to run; " + failed + " FAILED unit(s) require explicit recovery (see unit detail)."
                : "All units are COMPLETED; the Phase 10 executor has nothing left to run."),
          confirmLabel: action === "execute" ? "Execute Production" : "Resume Production"
        }, function () { command(action, null); });
      });
    });

    document.querySelectorAll("[data-cc-unit]").forEach(function (button) {
      button.addEventListener("click", function () {
        var unitId = button.getAttribute("data-cc-unit");
        CC.confirmDialog({
          title: "Execute unit?",
          description: "Execute unit " + unitId + " of production " + id + "?",
          scope: "Scope: 1 unit (" + unitId + "). Phase 10 enforces dependencies; COMPLETED units are never restarted.",
          firstAction: "First action: ProductionExecutor.execute(unit_id=\"" + unitId + "\") runs in a background thread through the Phase 10 engine.",
          confirmLabel: "Execute Unit"
        }, function () { command("execute", unitId); });
      });
    });

    var refresh = document.querySelector("[data-cc-refresh]");
    if (refresh) refresh.addEventListener("click", function () { App.refresh(); });

    VIEWS.productionDetail.startPolling(id);
  },

  /* Read-only polling: GET progress only. It never issues a POST, never
     retries execution, and stops as soon as no run is active. */
  startPolling: function (id) {
    if (VIEWS.productionDetail._timer) {
      clearInterval(VIEWS.productionDetail._timer);
      VIEWS.productionDetail._timer = null;
    }
    var hash = location.hash;
    VIEWS.productionDetail._timer = setInterval(function () {
      if (location.hash !== hash || !API.live) {
        clearInterval(VIEWS.productionDetail._timer);
        VIEWS.productionDetail._timer = null;
        return;
      }
      API.getProductionProgress(id).then(function (progress) {
        if (!progress.run || !progress.run.running) {
          clearInterval(VIEWS.productionDetail._timer);
          VIEWS.productionDetail._timer = null;
          App.refresh();
        }
      }).catch(function () { /* keep polling silently; errors surface on refresh */ });
    }, 3000);
  }
};

