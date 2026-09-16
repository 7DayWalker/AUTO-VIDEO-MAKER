/* AUTO VIDEO MAKER — Settings view (read-only, never shows secrets).
   Track 3 Increment 3: the Command Center is the authoritative Home; the
   former Dashboard's unique system-status diagnostics live here now. */
window.VIEWS = window.VIEWS || {};

VIEWS.settings = {
  title: "Settings",

  render: async function () {
    function row(k, v) {
      return '<div class="meta-row"><div class="meta-key">' + k + "</div><div>" + v + "</div></div>";
    }
    function dimension(label, value, detail, tone) {
      return '<article class="card overview-dimension"><p class="eyebrow">' + COMPONENTS.esc(label) +
        '</p><p class="dimension-value"><span class="badge ' + COMPONENTS.esc(tone || "muted") + '">' +
        COMPONENTS.esc(value) + '</span></p><p class="hint">' + COMPONENTS.esc(detail) + "</p></article>";
    }
    var statusHtml;
    if (API.live) {
      var connectionError = null, projectError = null, productionError = null;
      var connections = {}, projects = [], productions = [];
      try { connections = await API.getConnections(); }
      catch (error) { connectionError = error; }
      try { projects = await API.getProjects(); }
      catch (error) { projectError = error; }
      try { productions = await API.getProductions(); }
      catch (error) { productionError = error; }
      var connectedCount = COMPONENTS.PLATFORMS.filter(function (platform) {
        return platform.live && connections[platform.id] && connections[platform.id].status === "CONNECTED";
      }).length;
      var malformedProduction = productions.some(function (production) {
        return production.error || !production.counts;
      });
      var productionState = productionError ? ["Unavailable", "The production data request failed; no empty state is inferred.", "err"] :
        malformedProduction ? ["Intent malformed", "At least one production record could not be read; inspect it in the Command Center.", "err"] :
        productions.length ? ["Live available", productions.length + " production" + (productions.length === 1 ? "" : "s") + " on disk.", "ok"] :
        ["Live empty", "The production source is live and contains no records.", "muted"];
      var malformedProject = projects.some(function (project) { return !!project.error; });
      var projectState = projectError ? ["Manifest unavailable", "The project data request failed; demo data was not substituted.", "err"] :
        malformedProject ? ["Manifest unavailable", "At least one project manifest could not be read; no project relationship was inferred.", "err"] :
        projects.length ? ["Live available", projects.length + " execution project" + (projects.length === 1 ? "" : "s") + ".", "ok"] :
        ["No projects", "The live project source contains no execution outputs.", "muted"];
      var platformState = connectionError ? ["Connection error", "Platform status could not be loaded.", "err"] :
        [connectedCount + " connected", "YouTube and TikTok capability remains action-specific.", connectedCount ? "ok" : "muted"];
      statusHtml = '<div class="grid grid-4 overview-status-grid">' +
        dimension("Engine", "Connected", "Local AVM engine responded.", "ok") +
        dimension("Production data", productionState[0], productionState[1], productionState[2]) +
        dimension("Project data", projectState[0], projectState[1], projectState[2]) +
        dimension("Platforms", platformState[0], platformState[1], platformState[2]) + "</div>";
    } else {
      statusHtml = '<div class="grid grid-4 overview-status-grid">' +
        dimension("Engine", "Offline", "Production commands and live state are unavailable until the engine is running.", "warn") +
        dimension("Production data", "Unavailable", "Live production data requires the local engine.", "muted") +
        dimension("Project data", "Unavailable", "Live project data requires the local engine.", "muted") +
        dimension("Platforms", "Unknown", "Platform status requires the local engine.", "muted") + "</div>";
    }
    return (
      '<div class="page-head"><div><h1 class="page-title">Settings</h1>' +
      '<p class="page-sub">Local configuration summary. Secrets are stored only in git-ignored local files and are never displayed anywhere in this interface.</p></div></div>' +
      '<div class="section-label">System status</div>' + statusHtml +
      '<div class="grid grid-2">' +
        '<div class="card card-pad"><div class="section-label" style="margin-top:0">Engine</div>' +
          '<div class="meta-list">' +
            row("Product", "Auto Video Maker") +
            row("Pipeline", "Content → Voice → Scenes → Assembly") +
            row("Engine host", API.live ? "This machine (connected)" : "Not reachable (demo data)") +
            row("Data source", API.live ? "Live manifests" : "Labelled demo samples") +
          "</div></div>" +
        '<div class="card card-pad"><div class="section-label" style="margin-top:0">Platform integration</div>' +
          '<div class="meta-list">' +
            row("YouTube", "YouTube Data API v3 · OAuth installed-app flow") +
            row("TikTok", "Content Posting API · Login Kit (PKCE)") +
            row("OAuth redirect", "<code>http://localhost:3455/callback/</code> (loopback only)") +
            row("Credentials", "credentials/ · git-ignored · never displayed") +
          "</div></div>" +
        '<div class="card card-pad"><div class="section-label" style="margin-top:0">Publishing safety</div>' +
          '<div class="meta-list">' +
            row("Duplicates", "Blocked by durable publication identities") +
            row("Verification", "Authoritative platform APIs only") +
            row("Default privacy", "Conservative defaults until explicitly changed") +
            row("Unknown outcomes", "Quarantined for manual reconciliation") +
          "</div></div>" +
        '<div class="card card-pad"><div class="section-label" style="margin-top:0">About</div>' +
          '<p class="hint" style="font-size:12.5px">Auto Video Maker is an independent multi-platform AI video creation and publishing studio. It is not affiliated with any social platform. All publishing happens through official platform APIs after explicit user authorization.</p>' +
        "</div>" +
      "</div>"
    );
  }
};
