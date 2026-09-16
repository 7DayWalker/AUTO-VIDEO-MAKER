/* ============================================================
   AUTO VIDEO MAKER — Visual Asset Library (#/assets)

   Read-only browsing of project-owned scene assets recorded in
   the durable asset manifest (app/orchestrator/assets.py):
   stored copies, SHA-256 fingerprints, provider provenance, and
   rich source metadata. Assets are REAL studio files — there are
   no demo assets anywhere; provenance is real or honestly absent.
   Verification (SHA-256 re-hash) is an explicit per-asset action.
   ============================================================ */
window.VIEWS = window.VIEWS || {};

VIEWS.assets = (function () {
  var C = COMPONENTS;
  var d = { projectId: "", error: "", index: null, projects: [], verify: {} };

  function fmtBytes(n) {
    if (n == null || isNaN(n)) return "—";
    if (n < 1024) return n + " B";
    if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
    return (n / 1048576).toFixed(1) + " MB";
  }

  function shortSha(s) {
    return s ? String(s).slice(0, 12) + "…" : "not recorded";
  }

  function verifyBadge(sceneId) {
    var v = d.verify[sceneId];
    if (!v) return "";
    return v.verified
      ? '<span class="badge ok">SHA-256 verified</span>'
      : '<span class="badge err" title="' + C.esc(v.reason || "") + '">Integrity failed</span>';
  }

  function assetCard(a) {
    var thumb;
    if (!a.file_present) {
      thumb = '<div class="asset-thumb missing">' + ICONS.alert + "</div>";
    } else if (a.media_type === "video") {
      thumb = '<div class="asset-thumb"><video class="asset-media hv-play" preload="metadata" muted loop playsinline tabindex="-1" aria-hidden="true" src="' +
        C.esc(API.assetMediaUrl(d.projectId, a.scene_id)) + '" onerror="this.remove()"></video>' + ICONS.film + "</div>";
    } else {
      thumb = '<div class="asset-thumb"><img class="asset-media" loading="lazy" alt="Stored asset for scene ' + C.esc(a.scene_id) +
        '" src="' + C.esc(API.assetMediaUrl(d.projectId, a.scene_id)) + '" onerror="this.remove()">' + ICONS.film + "</div>";
    }
    var issue = a.file_present ? "" : '<p class="inline-error">' + C.esc(a.file_issue || "stored file unavailable") + "</p>";
    return '<article class="card asset-card' + (a.file_present ? "" : " asset-missing") + '">' + thumb +
      '<div class="asset-body">' +
        '<div class="asset-title mono">' + C.esc(a.scene_id) + "</div>" +
        '<div class="asset-chips">' +
          '<span class="badge ' + (a.media_type === "video" ? "info" : "muted") + '">' + C.esc(a.media_type || "unknown") + "</span>" +
          (a.provider ? '<span class="badge muted">' + C.esc(a.provider) + "</span>" : "") +
          (a.file_present ? "" : '<span class="badge err">File unavailable</span>') +
          verifyBadge(a.scene_id) +
        "</div>" +
        '<div class="asset-src">' + C.esc(a.source_name || "—") + " · " + C.esc(fmtBytes(a.bytes)) + "</div>" +
        '<div class="asset-sha mono" title="' + C.esc(a.sha256 || "") + '">SHA-256 ' + C.esc(shortSha(a.sha256)) + "</div>" +
        issue +
        '<div class="asset-actions">' +
          '<button class="btn btn-sm" data-asset-prov="' + C.esc(a.scene_id) + '">Provenance</button>' +
          '<button class="btn btn-sm" data-asset-verify="' + C.esc(a.scene_id) + '"' + (a.file_present ? "" : ' disabled aria-disabled="true"') + ">Verify SHA-256</button>" +
        "</div>" +
      "</div></article>";
  }

  function provenanceRows(a) {
    function row(label, value) {
      if (value == null || value === "") return "";
      return "<div><dt>" + C.esc(label) + '</dt><dd class="mono">' + C.esc(String(value)) + "</dd></div>";
    }
    var html = row("Scene", a.scene_id) + row("Source name", a.source_name) +
      row("Stored as", a.stored_as) + row("Media type", a.media_type) +
      row("Provider", a.provider) + row("Bytes", fmtBytes(a.bytes)) +
      row("SHA-256", a.sha256) + row("Supplied by", a.supplied_by) +
      row("Source provider", a.source_provider) + row("Source status", a.source_status) +
      row("File state", a.file_present ? "present" : (a.file_issue || "unavailable"));
    var meta = a.source_metadata;
    if (meta && typeof meta === "object") {
      Object.keys(meta).sort().forEach(function (key) {
        var value = meta[key];
        if (value != null && typeof value !== "object") html += row("Metadata · " + key, value);
        else if (value != null) html += row("Metadata · " + key, JSON.stringify(value));
      });
    }
    return '<dl class="detail-list asset-prov">' + html + "</dl>";
  }

  return {
    title: "Asset Library",
    data: d,

    render: async function () {
      var head = '<div class="page-head"><div><p class="eyebrow">Real studio media</p>' +
        '<h1 class="page-title">Visual Asset Library</h1>' +
        '<p class="page-sub">Project-owned scene assets from the durable asset manifest — stored copies, SHA-256 fingerprints, and source provenance. Read-only; nothing here is ever demo data.</p></div></div>';

      if (!API.live) {
        return head + '<div class="card cc-state-panel">' + C.emptyState("alert", "Engine offline",
          "Asset provenance is only shown from real project manifests. Start the local AVM engine, then refresh — no demo assets are invented.") + "</div>";
      }

      try {
        d.projects = await API.getProjects();
      } catch (e) {
        return head + '<div class="card cc-state-panel">' + C.emptyState("alert", "Project list unavailable",
          String(e.message || e)) + "</div>";
      }
      if (!d.projects.length) {
        return head + '<div class="card cc-state-panel">' + C.emptyState("layers", "No projects yet",
          "Assets belong to execution projects. Create and produce a video first — mapped scene assets will appear here with full provenance.",
          '<a class="btn btn-primary" href="#/create">Create Video</a>') + "</div>";
      }
      if (!d.projectId || !d.projects.some(function (p) { return p.project_id === d.projectId; })) {
        d.projectId = d.projects[0].project_id;
      }

      var selector = '<div class="asset-toolbar card card-pad"><label class="field-inline"><span class="asset-sel-label">Project</span>' +
        '<select class="input" id="asset-project">' + d.projects.map(function (p) {
          return '<option value="' + C.esc(p.project_id) + '"' + (p.project_id === d.projectId ? " selected" : "") + ">" +
            C.esc((p.topic || p.project_id).slice(0, 60)) + " · " + C.esc(p.project_id) + "</option>";
        }).join("") + "</select></label>" +
        '<span class="hint">Assets come from each project\u2019s durable asset manifest (intake-verified copies).</span></div>';

      d.error = "";
      d.index = null;
      try {
        d.index = await API.getProjectAssets(d.projectId);
      } catch (e) {
        d.error = String(e.message || e);
      }

      var board;
      if (d.error) {
        board = '<div class="card cc-state-panel">' + C.emptyState("alert", "Asset index unavailable", d.error) + "</div>";
      } else if (!d.index.assets.length) {
        board = '<div class="card cc-state-panel">' + C.emptyState("layers", "No mapped assets",
          "This project has no asset-manifest entries — its scenes were generated by providers or no local assets were mapped at intake. Only real manifest entries ever appear here.") + "</div>";
      } else {
        board = '<div class="grid asset-grid">' + d.index.assets.map(assetCard).join("") + "</div>";
      }
      return head + selector + board;
    },

    mount: function () {
      var select = document.getElementById("asset-project");
      if (select) select.addEventListener("change", function () {
        d.projectId = select.value;
        d.verify = {};
        App.refresh();
      });

      document.querySelectorAll("[data-asset-prov]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var sceneId = btn.getAttribute("data-asset-prov");
          var asset = (d.index && d.index.assets || []).find(function (a) { return a.scene_id === sceneId; });
          if (!asset) return;
          C.modal("<h3>Asset provenance — " + C.esc(sceneId) + "</h3>" +
            "<p>Recorded by intake into the durable asset manifest. The stored copy's SHA-256 matched the original at intake; re-verify any time.</p>" +
            provenanceRows(asset) +
            '<div class="modal-actions"><button class="btn btn-primary" data-asset-close>Close</button></div>');
          var close = document.querySelector("[data-asset-close]");
          if (close) close.addEventListener("click", C.closeModal);
        });
      });

      document.querySelectorAll("[data-asset-verify]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var sceneId = btn.getAttribute("data-asset-verify");
          btn.disabled = true;
          API.verifyProjectAsset(d.projectId, sceneId).then(function (res) {
            d.verify[sceneId] = res;
            C.toast(res.verified ? sceneId + ": SHA-256 matches the manifest." : sceneId + ": integrity check FAILED — " + (res.reason || "see reason"), res.verified ? "ok" : "err");
            App.refresh();
          }).catch(function (err) {
            btn.disabled = false;
            C.toast(err.message || String(err), "err");
          });
        });
      });

      /* Hover preview for stored video assets (real files only). */
      document.querySelectorAll(".asset-card .hv-play").forEach(function (v) {
        var card = v.closest(".asset-card");
        if (!card) return;
        card.addEventListener("mouseenter", function () { var pr = v.play(); if (pr && pr.catch) pr.catch(function () {}); });
        card.addEventListener("mouseleave", function () { try { v.pause(); v.currentTime = 0; } catch (e) {} });
      });
    }
  };
})();
