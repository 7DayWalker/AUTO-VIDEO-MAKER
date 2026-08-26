/* AUTO VIDEO MAKER — Dashboard view */
window.VIEWS = window.VIEWS || {};

VIEWS.dashboard = {
  title: "Dashboard",

  render: async function () {
    const connections = await API.getConnections();
    const projects = await API.getProjectsWithFallback();
    const published = projects.filter(function (p) {
      return (p.publications || []).some(function (x) { return x.state === "VERIFIED"; });
    }).length;
    const ready = projects.filter(function (p) { return p.stage_status === "complete"; }).length;
    const connectedCount = COMPONENTS.PLATFORMS.filter(function (pf) {
      return pf.live && connections[pf.id] && connections[pf.id].status === "CONNECTED";
    }).length;

    const hour = new Date().getHours();
    const greeting = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";

    const platformRow = COMPONENTS.PLATFORMS.slice(0, 4).map(function (pf) {
      const st = connections[pf.id] || { status: pf.live ? "NOT CONNECTED" : "COMING SOON" };
      return '<tr><td><span style="display:inline-flex;align-items:center;gap:9px;font-weight:600">' +
             ICONS[pf.icon] + COMPONENTS.esc(pf.name) + "</span></td>" +
             "<td>" + COMPONENTS.connBadge(st.status) + "</td>" +
             '<td class="hint">' + COMPONENTS.esc(st.account || (pf.live ? "—" : "Planned integration")) + "</td>" +
             '<td style="text-align:right"><a class="btn btn-sm' + (st.status === "CONNECTED" || !pf.live ? " btn-ghost" : "") +
             '" href="#/platforms">' + (st.status === "CONNECTED" ? "Manage" : pf.live ? "Connect" : "Details") + "</a></td></tr>";
    }).join("");

    const recent = projects.slice(0, 3);
    const recentHtml = recent.length
      ? '<div class="grid grid-3">' + recent.map(COMPONENTS.projectCard).join("") + "</div>"
      : COMPONENTS.emptyState("film", "No video projects yet",
          "Create your first AI-generated short-form video to see it here.",
          '<a class="btn btn-primary" href="#/create" style="margin-top:8px">Create Video</a>');

    return (
      '<div class="hero">' +
        '<span class="hero-kicker">' + ICONS.sparkle + " Multi-platform publishing studio</span>" +
        "<h1>" + greeting + ", Paul</h1>" +
        "<p>Create, manage and publish your content from one place. " +
        "Produce AI-generated short-form video once, then publish it to every connected platform.</p>" +
        '<div class="hero-actions">' +
          '<a class="btn btn-primary btn-lg" href="#/create">' + ICONS.create + " Create Video</a>" +
          '<a class="btn btn-lg" href="#/projects">' + ICONS.projects + " View Projects</a>" +
          '<a class="btn btn-lg btn-ghost" href="#/platforms">' + ICONS.plug + " Connect Platforms</a>" +
        "</div>" +
      "</div>" +

      '<div class="section-label">Overview</div>' +
      '<div class="grid grid-4">' +
        COMPONENTS.statTile("film", "Video projects", projects.length) +
        COMPONENTS.statTile("check", "Ready to publish", ready) +
        COMPONENTS.statTile("upload", "Published", published, true) +
        COMPONENTS.statTile("plug", "Connected platforms", connectedCount, true) +
      "</div>" +

      '<div class="section-label">Connected platforms</div>' +
      '<div class="card table-wrap"><table class="data">' +
        "<thead><tr><th>Platform</th><th>Status</th><th>Account</th><th></th></tr></thead>" +
        "<tbody>" + platformRow + "</tbody></table></div>" +

      '<div class="section-label" style="display:flex;justify-content:space-between;align-items:center">' +
        "<span>Recent projects</span>" +
        '<a class="btn btn-ghost btn-sm" href="#/projects">View all</a></div>' +
      recentHtml
    );
  }
};
