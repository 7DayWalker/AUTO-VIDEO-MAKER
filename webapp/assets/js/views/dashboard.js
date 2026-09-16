/* AUTO VIDEO MAKER ? attention-first Overview. */
window.VIEWS = window.VIEWS || {};

VIEWS.dashboard = {
  title: "Dashboard",

  render: async function () {
    var engineLive = API.live;
    var connectionError = null, projectError = null, productionError = null;
    var connections = {}, projects = [], productions = [];

    try { connections = await API.getConnections(); }
    catch (error) { connectionError = error; }

    if (engineLive) {
      try { projects = await API.getProjects(); }
      catch (error) { projectError = error; }
      try { productions = await API.getProductions(); }
      catch (error) { productionError = error; }
    } else {
      projects = DEMO_DATA.projects || [];
      connections = DEMO_DATA.connections || {};
    }

    var published = projects.filter(function (project) {
      return (project.publications || []).some(function (record) {
        return record.state === "VERIFIED" || record.state === "VERIFIED_SANDBOX_PRIVATE";
      });
    }).length;
    var ready = projects.filter(function (project) { return project.stage_status === "complete"; }).length;
    var connectedCount = COMPONENTS.PLATFORMS.filter(function (platform) {
      return platform.live && connections[platform.id] && connections[platform.id].status === "CONNECTED";
    }).length;

    function dimension(label, value, detail, tone) {
      return '<article class="card overview-dimension"><p class="eyebrow">' + COMPONENTS.esc(label) +
        '</p><p class="dimension-value"><span class="badge ' + COMPONENTS.esc(tone || "muted") + '">' +
        COMPONENTS.esc(value) + '</span></p><p class="hint">' + COMPONENTS.esc(detail) + '</p></article>';
    }

    var malformedProduction = productions.some(function (production) {
      return production.error || !production.counts;
    });
    var productionState = !engineLive ? ["Unavailable", "Start the local engine to load live production data.", "muted"] :
      productionError ? ["Unavailable", "The production data request failed; no empty state is inferred.", "err"] :
      malformedProduction ? ["Intent malformed", "At least one production record could not be read; inspect it in the Command Center.", "err"] :
      productions.length ? ["Live available", productions.length + " production" + (productions.length === 1 ? "" : "s") + " on disk.", "ok"] :
      ["Live empty", "The production source is live and contains no records.", "muted"];
    var malformedProject = projects.some(function (project) { return !!project.error; });
    var projectState = !engineLive ? ["Demo samples", "Real project data is unavailable while the engine is offline.", "warn"] :
      projectError ? ["Manifest unavailable", "The project data request failed; demo data was not substituted.", "err"] :
      malformedProject ? ["Manifest unavailable", "At least one project manifest could not be read; no project relationship was inferred.", "err"] :
      projects.length ? ["Live available", projects.length + " execution project" + (projects.length === 1 ? "" : "s") + ".", "ok"] :
      ["No projects", "The live project source contains no execution outputs.", "muted"];
    var platformState = connectionError ? ["Connection error", "Platform status could not be loaded.", "err"] :
      [connectedCount + " connected", "YouTube and TikTok capability remains action-specific.", connectedCount ? "ok" : "muted"];

    var statusGrid = '<div class="grid grid-4 overview-status-grid">' +
      dimension("Engine", engineLive ? "Connected" : "Offline", engineLive ? "Local AVM engine responded." : "Production commands and live state are unavailable until the engine is running.", engineLive ? "ok" : "warn") +
      dimension("Production data", productionState[0], productionState[1], productionState[2]) +
      dimension("Project data", projectState[0], projectState[1], projectState[2]) +
      dimension("Platforms", platformState[0], platformState[1], platformState[2]) + '</div>';

    function attentionPriority(production) {
      var counts = production.counts || {};
      if (production.error || !production.counts) return 0;
      if ((counts.FAILED || 0) > 0) return 1;
      if ((counts.BLOCKED || 0) > 0) return 2;
      if ((counts.IN_FLIGHT || 0) > 0) return 3;
      if ((counts.INCOMPLETE || 0) > 0) return 4;
      return 99;
    }
    function attentionReason(production) {
      var counts = production.counts || {};
      if (production.error || !production.counts) return "Production data could not be read — inspect the record.";
      if ((counts.FAILED || 0) > 0) return "Failed units require inspection.";
      if ((counts.BLOCKED || 0) > 0) return "Blocked units are waiting for dependencies.";
      if ((counts.IN_FLIGHT || 0) > 0) return "Execution or a remote job is in flight.";
      return "Incomplete work can continue through explicit Resume.";
    }

    var attention = productions.filter(function (production) {
      return attentionPriority(production) < 99;
    }).sort(function (left, right) {
      return attentionPriority(left) - attentionPriority(right);
    }).slice(0, 5);

    var productionPanel;
    if (!engineLive) {
      productionPanel = '<div class="card cc-state-panel">' + COMPONENTS.emptyState("sparkle", "Production state unavailable", "Production data is never replaced by demo productions. Start the local engine on this machine to load live production data.") + '</div>';
    } else if (productionError) {
      productionPanel = '<div class="card cc-state-panel error-panel">' + COMPONENTS.emptyState("alert", "Production data unavailable", String(productionError.message || productionError)) + '</div>';
    } else if (!productions.length) {
      productionPanel = '<div class="card cc-state-panel">' + COMPONENTS.emptyState("sparkle", "No productions yet", "The engine is connected and there are no productions yet. Create your first video and it will appear here.", '<a class="btn btn-primary" href="#/create">Create Video</a>') + '</div>';
    } else if (!attention.length) {
      productionPanel = '<div class="card cc-state-panel">' + COMPONENTS.emptyState("check", "No production attention required", "No failed, blocked, in-flight, or incomplete production work is currently reported.", '<a class="btn" href="#/productions">View all productions</a>') + '</div>';
    } else {
      productionPanel = '<div class="grid overview-attention-list">' + attention.map(function (production) {
        return '<article class="card attention-summary"><div><p class="eyebrow">Production attention</p><h2>' + COMPONENTS.esc(production.title || production.production_id) + '</h2><p class="hint mono">' + COMPONENTS.esc(production.production_id) + '</p></div><p>' + COMPONENTS.esc(attentionReason(production)) + '</p><div class="state-counts">' + CC.countsRow(production.counts) + '</div><a class="btn btn-sm" href="#/productions/' + encodeURIComponent(production.production_id) + '">Inspect production</a></article>';
      }).join('') + '</div>';
    }

    var recent = projects.filter(function (project) { return !project.error; }).slice(0, 3);
    var recentHtml = recent.length ? '<div class="grid grid-3">' + recent.map(COMPONENTS.projectCard).join("") + '</div>' :
      COMPONENTS.emptyState("film", engineLive ? "No projects yet" : "No demo projects", engineLive ? "Execution outputs will appear here when they exist." : "No labeled project samples are available.");

    var hour = new Date().getHours();
    var greeting = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
    return '<div class="hero"><span class="hero-kicker">' + ICONS.sparkle + ' Home</span><h1>' + greeting + ', Paul</h1><p>See what needs attention across your productions, videos, and connected platforms.</p><div class="hero-actions"><a class="btn btn-primary btn-lg" href="#/create">Create Video</a><a class="btn btn-lg" href="#/productions">Open Command Center</a><a class="btn btn-lg btn-ghost" href="#/projects">View Projects</a><a class="btn btn-lg btn-ghost" href="#/platforms">Connect Platforms</a></div></div>' +
      '<div class="section-label">System and data status</div>' + statusGrid +
      '<div class="section-label overview-section-label"><span>Production attention</span><a class="btn btn-ghost btn-sm" href="#/productions">View all</a></div>' + productionPanel +
      '<div class="section-label">Output summary</div><div class="grid grid-4">' +
        COMPONENTS.statTile("film", "Video projects", projects.length) +
        COMPONENTS.statTile("check", "Ready to publish", ready) +
        COMPONENTS.statTile("upload", "Published", published, true) +
        COMPONENTS.statTile("plug", "Connected platforms", connectedCount, true) + '</div>' +
      '<div class="section-label overview-section-label"><span>Recent outputs</span><a class="btn btn-ghost btn-sm" href="#/projects">View all</a></div>' + recentHtml;
  }
};
