/* ============================================================
   AUTO VIDEO MAKER — shared render components & platform registry
   ============================================================ */

window.COMPONENTS = (function () {

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  /* Platform registry — the single place a new destination is added. */
  const PLATFORMS = [
    { id: "youtube", name: "YouTube", icon: "youtube",
      sub: "Shorts & long-form publishing", live: true,
      caps: ["Channel connection", "Video publishing", "Metadata updates"] },
    { id: "tiktok", name: "TikTok", icon: "tiktok",
      sub: "Content Posting API · Login Kit", live: true,
      caps: ["Account connection", "Direct post publishing", "Status verification"] },
    { id: "instagram", name: "Instagram", icon: "instagram",
      sub: "Reels publishing", live: false,
      caps: ["Planned"] },
    { id: "facebook", name: "Facebook", icon: "facebook",
      sub: "Video publishing", live: false,
      caps: ["Planned"] }
  ];

  function platformById(id) {
    return PLATFORMS.find(function (p) { return p.id === id; }) || null;
  }

  /** Map a backend PublicationState to badge class + label. */
  function pubBadge(state, sample) {
    const map = {
      VERIFIED:                        ["ok",   "Published"],
      PROCESSING:                      ["info", "Processing"],
      IDENTITY_PERSISTED:              ["info", "Submitted"],
      UPLOAD_SUBMITTED:                ["warn", "Uploading"],
      PREPARED:                        ["warn", "Ready"],
      NOT_STARTED:                     ["muted", "Not started"],
      FAILED:                          ["err",  "Failed"],
      UNKNOWN_RECONCILIATION_REQUIRED: ["err",  "Needs review"]
    };
    if (!state) return '<span class="badge">Not published</span>';
    const m = map[state] || ["muted", state];
    return '<span class="badge ' + m[0] + '">' + esc(m[1]) +
           (sample ? ' <span class="sample-tag">Sample</span>' : "") + "</span>";
  }

  function connBadge(status) {
    if (status === "CONNECTED")   return '<span class="badge ok">Connected</span>';
    if (status === "ERROR")       return '<span class="badge err">Error</span>';
    if (status === "COMING SOON") return '<span class="badge muted">Coming soon</span>';
    return '<span class="badge">Not connected</span>';
  }

  function platformCard(platform, status, actionsHtml) {
    const soon = !platform.live;
    return (
      '<article class="card card-hover platform-card' + (soon ? " soon" : "") + '">' +
        '<div class="pc-head">' +
          '<div class="pc-glyph">' + ICONS[platform.icon] + "</div>" +
          "<div><div class=\"pc-name\">" + esc(platform.name) + "</div>" +
          '<div class="pc-sub">' + esc(platform.sub) + "</div></div>" +
        "</div>" +
        '<div class="pc-body">' +
          '<ul class="pc-caps">' + platform.caps.map(function (c) {
            return "<li>" + esc(c) + "</li>";
          }).join("") + "</ul>" +
          "<div>" + connBadge(status) +
            (status && status.account
              ? '<div class="hint" style="margin-top:6px">Account: ' + esc(status.account) + "</div>"
              : "") +
          "</div>" +
        "</div>" +
        '<div class="pc-foot"><span class="spacer"></span>' + (actionsHtml || "") + "</div>" +
      "</article>"
    );
  }

  function projectCard(p) {
    const chips = [];
    (p.publications || []).forEach(function (pub) {
      const pf = platformById(pub.platform);
      if (!pf) return;
      chips.push('<span class="chip' + (pub.state === "VERIFIED" ? " ok" : "") + '">' +
                 ICONS[pf.icon] + esc(pf.name) + "</span>");
    });
    const stage = p.stage_status === "complete" ? ["ok", "Ready"] :
                  p.stage_status === "running" ? ["warn", "Generating"] : ["muted", "Draft"];
    return (
      '<a class="card card-hover project-card" href="#/projects/' + encodeURIComponent(p.project_id) + '">' +
        '<div class="pc-thumb">' + ICONS.film + "</div>" +
        '<div class="pc-meta">' +
          '<div class="project-title">' + esc(p.topic) +
            (p.sample ? ' <span class="sample-tag">Sample</span>' : "") + "</div>" +
          '<div class="project-date">' + esc((p.created_at || "").slice(0, 10)) + "</div>" +
          '<div class="project-chips"><span class="badge ' + stage[0] + '">' + stage[1] + "</span>" +
            chips.join("") + "</div>" +
        "</div>" +
      "</a>"
    );
  }

  return { esc, PLATFORMS, platformById, pubBadge, connBadge, platformCard, projectCard };
})();

/* Part 2 — tiles, timeline, empty states, modal & toast helpers. */
window.COMPONENTS = (function (C) {
  function statTile(icon, label, value, violet) {
    return (
      '<div class="card stat-tile">' +
        '<div class="stat-top"><span class="stat-label">' + C.esc(label) + "</span>" +
        '<span class="stat-icon' + (violet ? " violet" : "") + '">' + (ICONS[icon] || "") + "</span></div>" +
        '<div class="stat-value">' + C.esc(value) + "</div>" +
      "</div>"
    );
  }

  /** states: [{label, desc}] ; currentIndex = active step */
  function timeline(states, currentIndex) {
    return "<ul class=\"timeline\">" + states.map(function (s, i) {
      const cls = i < currentIndex ? "done" : i === currentIndex ? "now" : "";
      return '<li class="' + cls + '"><div class="t-label">' + C.esc(s.label) + "</div>" +
             '<div class="t-desc">' + C.esc(s.desc) + "</div></li>";
    }).join("") + "</ul>";
  }

  function emptyState(icon, title, text, actionHtml) {
    return (
      '<div class="empty"><div class="e-icon">' + (ICONS[icon] || "") + "</div>" +
      "<h4>" + C.esc(title) + "</h4><p>" + C.esc(text) + "</p>" +
      (actionHtml || "") + "</div>"
    );
  }

  function toast(message, kind) {
    const root = document.getElementById("toast-root");
    const el = document.createElement("div");
    el.className = "toast " + (kind === "err" ? "err" : "ok");
    el.innerHTML = (kind === "err" ? ICONS.alert : ICONS.check) +
                   "<span>" + C.esc(message) + "</span>";
    root.appendChild(el);
    setTimeout(function () { el.remove(); }, 3800);
  }

  function modal(html) {
    const root = document.getElementById("modal-root");
    root.innerHTML = '<div class="modal-backdrop"><div class="modal">' + html + "</div></div>";
    root.querySelector(".modal-backdrop").addEventListener("click", function (e) {
      if (e.target === this) closeModal();
    });
  }

  function closeModal() {
    document.getElementById("modal-root").innerHTML = "";
  }

  C.statTile = statTile;
  C.timeline = timeline;
  C.emptyState = emptyState;
  C.toast = toast;
  C.modal = modal;
  C.closeModal = closeModal;
  return C;
})(window.COMPONENTS);

