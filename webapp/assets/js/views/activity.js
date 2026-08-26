/* AUTO VIDEO MAKER — Projects view */
window.VIEWS = window.VIEWS || {};

VIEWS.projects = {
  title: "Projects",

  render: async function () {
    const projects = await API.getProjectsWithFallback();
    if (!projects.length) {
      return '<div class="page-head"><div><h1 class="page-title">Projects</h1></div>' +
        '<div class="head-actions"><a class="btn btn-primary" href="#/create">' + ICONS.create + " Create Video</a></div></div>" +
        '<div class="card">' + COMPONENTS.emptyState("film", "No projects yet",
          "Video projects you generate with the AVM engine will appear here, ready to publish.",
          '<a class="btn btn-primary" href="#/create" style="margin-top:8px">Create Video</a>') + "</div>";
    }
    return (
      '<div class="page-head"><div><h1 class="page-title">Projects</h1>' +
      '<p class="page-sub">Your generated video library. Open a project to review it and publish to connected platforms.</p></div>' +
      '<div class="head-actions"><a class="btn btn-primary" href="#/create">' + ICONS.create + " Create Video</a></div></div>" +
      (API.live ? "" : '<p class="hint" style="margin-bottom:14px"><span class="sample-tag">Demo</span> ' +
        "Showing sample layout — start the local engine to load your real projects.</p>") +
      '<div class="grid grid-3">' + projects.map(COMPONENTS.projectCard).join("") + "</div>"
    );
  }
};

/* AUTO VIDEO MAKER — Activity / publishing history view */
window.VIEWS = window.VIEWS || {};

VIEWS.activity = {
  title: "Activity",

  render: async function () {
    const projects = await API.getProjectsWithFallback();
    const rows = [];
    projects.forEach(function (p) {
      (p.publications || []).forEach(function (pub) {
        if (!pub.state) return;
        const pf = COMPONENTS.platformById(pub.platform);
        rows.push({
          when: p.created_at,
          platform: pf ? pf.name : pub.platform,
          icon: pf ? pf.icon : "film",
          project: p.topic,
          state: pub.state,
          sample: !!(p.sample || pub.sample)
        });
      });
    });
    if (!API.live) {
      DEMO_DATA.activity.forEach(function (a) {
        const pf = COMPONENTS.platformById(a.platform);
        rows.push({
          when: a.at, platform: pf ? pf.name : a.platform, icon: pf ? pf.icon : "film",
          project: a.project_topic, state: a.state, sample: true
        });
      });
    }
    rows.sort(function (a, b) { return String(b.when).localeCompare(String(a.when)); });

    const body = rows.length ? rows.map(function (r) {
      return "<tr>" +
        '<td><span style="display:inline-flex;align-items:center;gap:9px;font-weight:600">' +
          (ICONS[r.icon] || "") + COMPONENTS.esc(r.platform) + "</span></td>" +
        "<td>" + COMPONENTS.esc(r.project) + (r.sample ? ' <span class="sample-tag">Sample</span>' : "") + "</td>" +
        "<td>" + COMPONENTS.esc(String(r.when || "").slice(0, 16).replace("T", " ")) + "</td>" +
        "<td>" + COMPONENTS.pubBadge(r.state, false) + "</td></tr>";
    }).join("") :
      '<tr><td colspan="4">' + COMPONENTS.emptyState("activity",
        "No publishing activity yet", "Publishing events from your projects will appear here.") + "</td></tr>";

    return (
      '<div class="page-head"><div><h1 class="page-title">Publishing Activity</h1>' +
      '<p class="page-sub">Platform-neutral history of every publication attempt, sourced from durable project records.</p></div></div>' +
      '<div class="card table-wrap"><table class="data">' +
      "<thead><tr><th>Platform</th><th>Project</th><th>When</th><th>Status</th></tr></thead>" +
      "<tbody>" + body + "</tbody></table></div>"
    );
  }
};
