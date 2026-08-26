/* AUTO VIDEO MAKER — Project Detail / publishing workspace */
window.VIEWS = window.VIEWS || {};

VIEWS.projectDetail = {
  title: "Project",

  render: async function (params) {
    let p;
    try {
      p = await API.getProject(params.id);
    } catch (e) {
      return '<div class="card">' + COMPONENTS.emptyState("alert", "Project not found",
        "This project does not exist in the current data source.") + "</div>";
    }
    const connections = await API.getConnections().catch(function () { return {}; });

    /* Video preview: real media when the engine serves it, honest placeholder otherwise. */
    let player;
    if (!p.sample && API.live && p.final_video_available) {
      player = '<video controls preload="metadata" src="' + API.mediaUrl(p.project_id) + '"></video>';
    } else {
      player =
        '<div class="empty" style="height:100%">' +
          '<div class="e-icon">' + ICONS.film + "</div>" +
          "<h4>" + (p.final_video_available ? "Preview available via local engine" : "No rendered video yet") + "</h4>" +
          "<p>" + (p.sample
            ? "Sample project card — start the local engine to browse your real library."
            : "Generate this project with the production engine to produce its final MP4.") + "</p>" +
        "</div>";
    }

    const pubs = {};
    (p.publications || []).forEach(function (x) { pubs[x.platform] = x; });

    /* Publish destinations — connection-aware, registry-driven, truthful. */
    const destinations = COMPONENTS.PLATFORMS.filter(function (pf) { return pf.live; })
      .map(function (pf) {
        return VIEWS.projectDetail.destination(pf, p, pubs, connections);
      }).join("");

    return VIEWS.projectDetail.frame(p, player, destinations, pubs);
  },

  /** One destination row — truthful per platform/connection/publication state. */
  destination: function (pf, p, pubs, connections) {
    const conn = connections[pf.id] || {};
    const connected = conn.status === "CONNECTED";
    const pub = pubs[pf.id];
    let stateText, actionHtml = "";

    if (!connected) {
      stateText = "Not connected";
      actionHtml = API.live
        ? '<button class="btn btn-sm" data-action="connect-platform" data-platform="' + pf.id + '">Connect ' + COMPONENTS.esc(pf.name) + "</button>"
        : '<span class="hint">engine offline</span>';
    } else if (pub && pub.state === "VERIFIED") {
      stateText = "Published" + (pub.post_id ? " (" + pub.post_id + ")" : "");
      actionHtml = '<a class="btn btn-sm btn-ghost" href="#/activity">History</a>';
    } else if (pub && pub.state === "VERIFIED_SANDBOX_PRIVATE") {
      stateText = "Verified — Sandbox Private";
      actionHtml = '<span class="hint">Sandbox/unaudited private publication (no public post id)</span>';
    } else if (pub && ["UPLOAD_SUBMITTED", "IDENTITY_PERSISTED", "PROCESSING",
                       "UNKNOWN_RECONCILIATION_REQUIRED"].indexOf(pub.state) >= 0) {
      stateText = pub.state === "UNKNOWN_RECONCILIATION_REQUIRED"
        ? "Needs manual reconciliation — retry forbidden"
        : "Publishing in progress";
      actionHtml = '<button class="btn btn-sm" data-action="check-status" data-project-id="' +
        COMPONENTS.esc(p.project_id) + '">Check status</button>';
    } else if (p.stage_status !== "complete") {
      stateText = "Project not ready";
    } else if (pf.id === "tiktok" && API.live && !p.sample) {
      stateText = pub && pub.state === "PREPARED"
        ? "Prepared & fingerprint-verified — ready to publish" : "Connected — ready to publish";
      actionHtml = '<button class="btn btn-sm btn-primary" data-action="publish-tiktok"' +
        ' data-project-id="' + COMPONENTS.esc(p.project_id) + '"' +
        ' data-project-title="' + COMPONENTS.esc(p.topic) + '">' +
        ICONS.upload + " Publish</button>";
    } else if (API.live && !p.sample) {
      stateText = "Ready"; actionHtml = '<span class="hint">via local publish flow</span>';
    } else {
      stateText = "Requires local engine";
    }

    return (
      '<div class="platform-mini">' +
        '<div class="dest-glyph">' + ICONS[pf.icon] + "</div>" +
        '<div class="dest-info"><div class="dest-name">' + COMPONENTS.esc(pf.name) + "</div>" +
        '<div class="dest-state">' + COMPONENTS.esc(stateText) + "</div></div>" +
        actionHtml +
      "</div>"
    );
  },

  frame: function (p, player, destinations, pubs) {
    /* Status timeline mirrors the REAL durable PublicationState machine. */
    const tk = pubs.tiktok ? pubs.tiktok.state : null;
    const stages = [
      { label: "Preparing", desc: "Eligibility gate · artifact fingerprint bound" },
      { label: "Uploading", desc: "Submission started · intent persisted before network" },
      { label: "Submitted", desc: "Durable platform identity persisted" },
      { label: "Processing", desc: "Awaiting authoritative platform status" },
      { label: "Complete", desc: "Verified from official platform APIs" }
    ];
    const stageIndexMap = {
      PREPARED: 0, UPLOAD_SUBMITTED: 1,
      IDENTITY_PERSISTED: 2, PROCESSING: 3, VERIFIED: 4,
      VERIFIED_SANDBOX_PRIVATE: 4
    };
    let currentIndex = -1;
    if (tk && tk in stageIndexMap) currentIndex = stageIndexMap[tk];
    else if (pubs.youtube && pubs.youtube.state === "VERIFIED") currentIndex = 4;

    return (
      '<div class="page-head"><div>' +
        "<h1 class=\"page-title\">" + COMPONENTS.esc(p.topic) +
          (p.sample ? ' <span class="sample-tag">Sample</span>' : "") + "</h1>" +
        '<p class="page-sub">Publishing workspace — review content and choose destinations.</p></div>' +
        '<div class="head-actions"><a class="btn btn-ghost btn-sm" href="#/projects">Back to projects</a></div>' +
      "</div>" +
      '<div class="workspace">' +
        "<div>" +
          '<div class="player-shell">' + player + "</div>" +
          '<div class="card card-pad" style="margin-top:16px"><div class="section-label" style="margin-top:0">Content details</div>' +
            '<div class="meta-list">' +
              "<div class=\"meta-row\"><div class='meta-key'>Title</div><div>" + COMPONENTS.esc(p.topic) + "</div></div>" +
              "<div class=\"meta-row\"><div class='meta-key'>Description</div><div>Derived from the AI script and call-to-action at publish time.</div></div>" +
              "<div class=\"meta-row\"><div class='meta-key'>Caption</div><div>Title plus hashtags generated from the plan metadata.</div></div>" +
              "<div class=\"meta-row\"><div class='meta-key'>Created</div><div>" + COMPONENTS.esc(String(p.created_at || "").slice(0, 16).replace("T", " ")) + "</div></div>" +
            "</div></div>" +
        "</div>" +
        "<div>" +
          '<div class="card card-pad dest-panel"><div class="section-label" style="margin-top:0">Publish destinations</div>' +
            destinations +
            '<p class="hint" style="margin-top:12px">Publishing uses each platform\'s official API with durable duplicate protection. Unavailable destinations show honestly — nothing is faked.</p>' +
          "</div>" +
          '<div class="card card-pad" style="margin-top:16px"><div class="section-label" style="margin-top:0">Publishing status</div>' +
            (currentIndex >= 0 || (!p.sample && pubs.tiktok)
              ? COMPONENTS.timeline(stages, Math.max(currentIndex, 0))
              : '<p class="hint">Not published yet. Connect a platform and run the publish flow.</p>') +
          "</div>" +
        "</div>" +
      "</div>"
    );
  },

  mount: function () {
    const view = document.getElementById("view");

    view.querySelectorAll('[data-action="connect-platform"]').forEach(function (btn) {
      btn.addEventListener("click", function () {
        VIEWS.platforms.connectFlow(btn.dataset.platform);
      });
    });

    /* Explicit confirmation dialog — Cancel/close send NOTHING. */
    view.querySelectorAll('[data-action="publish-tiktok"]').forEach(function (btn) {
      btn.addEventListener("click", function () {
        VIEWS.projectDetail.openPublishDialog(btn.dataset.projectId, btn.dataset.projectTitle, btn);
      });
    });

    view.querySelectorAll('[data-action="check-status"]').forEach(function (btn) {
      btn.addEventListener("click", async function () {
        const projectId = btn.dataset.projectId;
        if (!projectId) return;
        btn.disabled = true;
        try {
          const st = await API.tiktokPublishStatus(projectId);
          COMPONENTS.toast(
            "TikTok durable state: " + (st.state || "none") +
            (st.error ? " — " + st.error : ""),
            (st.state === "VERIFIED" || st.state === "VERIFIED_SANDBOX_PRIVATE") ? "ok" : undefined
          );
          App.refresh();
        } catch (e) {
          COMPONENTS.toast("Status check failed: " + e.message, "err");
          btn.disabled = false;
        }
      });
    });
  },

  openPublishDialog: function (projectId, projectTitle, triggerBtn) {
    if (!projectId) return;
    const C = COMPONENTS;
    C.modal(
      "<h3>Publish to TikTok?</h3>" +
      "<p>You are about to submit this video to your connected TikTok account using the " +
      "official TikTok Content Posting API.</p>" +
      "<p>The existing project publication safeguards will check the video and prevent " +
      "duplicate publication where an existing publication identity is already recorded.</p>" +
      '<div class="meta-list card card-pad" style="margin:14px 0">' +
        "<div class=\"meta-row\"><div class='meta-key'>Video</div><div>" + C.esc(projectTitle || projectId) + "</div></div>" +
        "<div class=\"meta-row\"><div class='meta-key'>Platform</div><div>TikTok</div></div>" +
        '<div class="meta-row"><div class="meta-key">Privacy</div><div>' +
          '<select class="select" id="publish-privacy" style="width:100%">' +
            '<option value="SELF_ONLY" selected>Self only (safe default)</option>' +
            '<option value="MUTUAL_FOLLOW_FRIENDS">Mutual follow friends</option>' +
            '<option value="FOLLOWER_OF_CREATOR">Followers of creator</option>' +
            '<option value="PUBLIC_TO_EVERYONE">Public to everyone</option>' +
          "</select>" +
          '<div class="hint">Validated against this account\'s actual permitted levels before submission; unaudited apps are restricted to private posting.</div>' +
        "</div></div>" +
      "</div>" +
      '<div class="modal-actions">' +
        '<button class="btn" data-role="cancel-publish">Cancel</button>' +
        '<button class="btn btn-primary" data-role="confirm-publish">' + ICONS.upload + ' Publish to TikTok</button>' +
      "</div>"
    );

    /* Cancel and backdrop close: ZERO side effects. */
    document.querySelector('[data-role="cancel-publish"]').addEventListener("click", C.closeModal);

    document.querySelector('[data-role="confirm-publish"]').addEventListener("click", async function (event) {
      const confirmBtn = event.currentTarget;
      const privacy = document.getElementById("publish-privacy").value;
      confirmBtn.disabled = true;
      confirmBtn.textContent = "Submitting…";
      const cancelBtn = document.querySelector('[data-role="cancel-publish"]');
      if (cancelBtn) cancelBtn.disabled = true;

      let result;
      try {
        result = await API.publishTiktok(projectId, privacy);
      } catch (e) {
        C.closeModal();
        C.toast("Publishing failed: " + e.message, "err");
        App.refresh();
        return;
      }
      C.closeModal();
      if (result && result.error) {
        C.toast(result.error + ": " + (result.detail || ""), "err");
      } else if (result && (result.state === "VERIFIED" || result.state === "VERIFIED_SANDBOX_PRIVATE")) {
        C.toast(result.state === "VERIFIED_SANDBOX_PRIVATE"
          ? "Published — Private Sandbox"
          : "Published successfully" + (result.post_id ? " (" + result.post_id + ")" : ""));
      } else if (result) {
        C.toast("Submission accepted — durable state: " + result.state);
      }
      App.refresh();
    });
  }
};
    });
  }
};
