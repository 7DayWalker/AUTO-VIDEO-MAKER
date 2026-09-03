/* AUTO VIDEO MAKER — publishing activity view. */
window.VIEWS = window.VIEWS || {};

VIEWS.activity = {
  title: "Activity",
  render: async function () {
    var projects = await API.getProjectsWithFallback();
    var rows = [];
    if (API.live) projects.forEach(function (p) {
      (p.publications || []).forEach(function (pub) {
        if (!pub.state) return;
        var pf = COMPONENTS.platformById(pub.platform);
        rows.push({ when: pub.updated_at || null, platform: pf ? pf.name : pub.platform,
          icon: pf ? pf.icon : "film", project: p.topic, state: pub.state,
          sample: !!(p.sample || pub.sample) });
      });
    });
    if (!API.live) (DEMO_DATA.activity || []).forEach(function (a) {
      var pf = COMPONENTS.platformById(a.platform);
      rows.push({ when: a.at, platform: pf ? pf.name : a.platform,
        icon: pf ? pf.icon : "film", project: a.project_topic, state: a.state,
        sample: true });
    });
    rows.sort(function (a, b) { return String(b.when).localeCompare(String(a.when)); });
    var body = rows.length ? rows.map(function (r) {
      return '<tr><td><span class="platform-inline">' + (ICONS[r.icon] || "") + COMPONENTS.esc(r.platform) + '</span></td>' +
        '<td>' + COMPONENTS.esc(r.project) + (r.sample ? ' <span class="sample-tag">Sample</span>' : '') + '</td>' +
        '<td>' + COMPONENTS.esc(r.when ? String(r.when).slice(0, 16).replace("T", " ") : "Timestamp unavailable") + '</td>' +
        '<td>' + COMPONENTS.pubBadge(r.state, false) + '</td></tr>';
    }).join("") : '<tr><td colspan="4">' + COMPONENTS.emptyState("activity", "No publishing activity yet", "Publishing events from durable project records will appear here.") + '</td></tr>';
    return '<div class="page-head"><div><p class="eyebrow">Distribution record</p><h1 class="page-title">Publishing Activity</h1><p class="page-sub">Platform-neutral history from durable publication records. No automatic publishing is performed here.</p></div></div>' +
      '<div class="card table-wrap"><table class="data"><thead><tr><th>Platform</th><th>Project</th><th>When</th><th>Status</th></tr></thead><tbody>' + body + '</tbody></table></div>';
  }
};