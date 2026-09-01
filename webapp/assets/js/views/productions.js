/* ============================================================
   AUTO VIDEO MAKER — Command Center: shared helpers + production list
   Phase 11: inspection/control surface over the AVM core.
   State vocabulary comes verbatim from the Phase 10 executor API.
   ============================================================ */

/* Shared Command Center helpers (loaded before the other CC views). */
window.CC = (function () {
  var C = COMPONENTS;

  /* Six-state vocabulary — display only; classification authority is the
     Phase 10 ProductionExecutor (never re-implemented here). */
  var STATES = {
    COMPLETED:   { badge: "ok",    label: "Completed" },
    IN_FLIGHT:   { badge: "info",  label: "In flight" },
    INCOMPLETE:  { badge: "warn",  label: "Incomplete" },
    FAILED:      { badge: "err",   label: "Failed" },
    BLOCKED:     { badge: "muted", label: "Blocked" },
    NOT_STARTED: { badge: "",      label: "Not started" }
  };
  var ORDER = ["COMPLETED", "IN_FLIGHT", "INCOMPLETE", "FAILED", "BLOCKED", "NOT_STARTED"];

  function stateBadge(state) {
    var s = STATES[state] || { badge: "muted", label: state || "Unknown" };
    return '<span class="badge ' + s.badge + '">' + C.esc(s.label) + "</span>";
  }

  function countsRow(counts) {
    if (!counts) return '<span class="hint">progress unavailable</span>';
    return ORDER.filter(function (key) { return counts[key] > 0; })
      .map(function (key) {
        var s = STATES[key];
        return '<span class="chip' + (key === "COMPLETED" ? " ok" : "") + '">' +
               counts[key] + " " + C.esc(s.label) + "</span>";
      }).join(" ") || '<span class="hint">no units</span>';
  }

  function progressBar(counts, total) {
    if (!counts || !total) return "";
    var done = counts.COMPLETED || 0;
    var pct = Math.round((done / total) * 100);
    return (
      '<div class="statebar" title="' + done + " of " + total + ' units completed">' +
        '<div class="statebar-fill" style="width:' + pct + '%"></div>' +
      "</div>" +
      '<div class="hint">' + done + "/" + total + " units completed (" + pct + "%)</div>"
    );
  }

  function runBadge(run) {
    if (run && run.running) {
      return '<span class="badge info">Executing…</span>';
    }
    if (run && run.error) return '<span class="badge err">Last run failed</span>';
    return "";
  }

  C.stateBadge = stateBadge;
  C.countsRow = countsRow;
  C.progressBar = progressBar;
  C.runBadge = runBadge;
  return C;
})();

/* Explicit confirmation dialog (AMENDMENT F). onConfirm runs ONLY when the
   operator clicks Confirm; Cancel/backdrop closes without any request.
   Defined after CC so it can live in the shared namespace. */
CC.confirmDialog = function (opts, onConfirm) {
  COMPONENTS.modal(
    '<h3>' + COMPONENTS.esc(opts.title) + "</h3>" +
    '<p>' + COMPONENTS.esc(opts.description) + "</p>" +
    (opts.scope ? '<p class="hint">' + COMPONENTS.esc(opts.scope) + "</p>" : "") +
    (opts.firstAction ? '<p class="hint">' + COMPONENTS.esc(opts.firstAction) + "</p>" : "") +
    '<div class="modal-actions">' +
      '<button class="btn" data-cc-action="cancel">Cancel</button>' +
      '<button class="btn btn-primary" data-cc-action="confirm">' + COMPONENTS.esc(opts.confirmLabel || "Confirm") + "</button>" +
    "</div>"
  );
  var root = document.getElementById("modal-root");
  root.querySelector('[data-cc-action="cancel"]').addEventListener("click", COMPONENTS.closeModal);
  root.querySelector('[data-cc-action="confirm"]').addEventListener("click", function () {
    COMPONENTS.closeModal();
    onConfirm();
  });
};

/* ============================================================
   Production list (Command Center overview)
   ============================================================ */
window.VIEWS = window.VIEWS || {};

VIEWS.productions = {
  title: "Command Center",

  render: async function () {
    if (!API.live) {
      return '<div class="card">' + COMPONENTS.emptyState("sparkle",
        "Engine offline",
        "The Command Center reads live production intent and progress from the local engine. Start it with: python -m app.web.server") + "</div>";
    }

    let items;
    try {
      items = await API.getProductions();
    } catch (e) {
      return '<div class="card">' + COMPONENTS.emptyState("alert",
        "Could not load productions", String(e.message || e)) + "</div>";
    }

    if (!items.length) {
      return '<div class="card">' + COMPONENTS.emptyState("sparkle",
        "No productions yet",
        "Productions are durable Phase 9 intents created via produce.py. The Command Center inspects and controls them; it never creates them.") + "</div>";
    }

    var cards = items.map(function (p) {
      return (
        '<article class="card card-hover production-card">' +
          '<div class="pc-head">' +
            '<div class="pc-glyph">' + ICONS.sparkle + "</div>" +
            '<div><div class="pc-name">' + COMPONENTS.esc(p.title) + "</div>" +
            '<div class="pc-sub">' + COMPONENTS.esc(p.production_id) + " · v" +
              COMPONENTS.esc(p.plan_version) + " · " + COMPONENTS.esc(p.unit_count) + " units</div></div>" +
          "</div>" +
          '<div class="pc-body">' +
            '<div class="hint" style="margin-bottom:8px">Command: ' + COMPONENTS.esc(p.command_text) + "</div>" +
            (p.error
              ? '<div class="hint" style="color:var(--danger)">' + COMPONENTS.esc(p.error) + "</div>"
              : CC.countsRow(p.counts)) +
            CC.progressBar(p.counts, p.unit_count) +
          "</div>" +
          '<div class="pc-foot">' +
            '<a class="btn btn-sm" href="#/productions/' + encodeURIComponent(p.production_id) + '">View Detail</a>' +
            '<span class="spacer"></span>' +
            '<button class="btn btn-sm btn-primary" data-cc="execute" data-id="' + COMPONENTS.esc(p.production_id) + '" data-total="' + COMPONENTS.esc(p.unit_count) + '">Execute Production</button>' +
            '<button class="btn btn-sm" data-cc="resume" data-id="' + COMPONENTS.esc(p.production_id) + '" data-total="' + COMPONENTS.esc(p.unit_count) + '">Resume Production</button>' +
          "</div>" +
        "</article>"
      );
    }).join("");

    return '<div class="view-head">' +
      "<h2>Command Center</h2>" +
      '<p class="hint">Inspect and control durable productions through the Phase 10 execution engine. ' +
      "Every action is explicit and confirmed; nothing ever runs automatically.</p></div>" +
      '<div class="grid productions-grid">' + cards + "</div>";
  },

  mount: async function () {
    document.querySelectorAll("[data-cc]").forEach(function (button) {
      button.addEventListener("click", function () {
        var id = button.getAttribute("data-id");
        var total = button.getAttribute("data-total");
        var action = button.getAttribute("data-cc");
        CC.confirmDialog({
          title: action === "execute" ? "Execute production?" : "Resume production?",
          description: (action === "execute" ? "Execute" : "Resume") + " production " + id + " (" + total + " units total)?",
          scope: "Scope: eligible units only. COMPLETED units are never restarted; remote in-flight jobs are never re-submitted (Phase 10 semantics).",
          firstAction: "The Phase 10 ProductionExecutor will begin with the first eligible unit in the background.",
          confirmLabel: action === "execute" ? "Execute Production" : "Resume Production"
        }, function () {
          API.productionCommand(id, action).then(function (res) {
            COMPONENTS.toast(res.message || "Command accepted", "ok");
            location.hash = "#/productions/" + encodeURIComponent(id);
          }).catch(function (err) {
            COMPONENTS.toast(String(err.message || err), "err");
          });
        });
      });
    });
  }
};

