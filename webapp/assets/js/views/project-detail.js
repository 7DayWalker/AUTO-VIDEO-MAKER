/* AUTO VIDEO MAKER — project/output detail.
   Project is an execution output, not production intent. */
window.VIEWS = window.VIEWS || {};

VIEWS.projectDetail = {
  title: "Project",

  render: async function (params) {
    var project;
    try { project = await API.getProject(params.id); }
    catch (e) { return '<div class="card cc-state-panel error-panel">' + COMPONENTS.emptyState("alert", "Project data unavailable", String(e.message || e)) + '</div>'; }
    var connections = await API.getConnections().catch(function () { return {}; });
    var media = project.final_video_available && API.live && !project.sample;
    var player = media
      ? '<div class="media-player"><video controls preload="metadata" src="' + COMPONENTS.esc(API.mediaUrl(project.project_id)) + '"></video></div><p class="hint">Existing final-media artifact delivered by the local engine.</p>'
      : '<div class="media-empty"><span class="badge ' + (project.final_video_available ? "warn" : "muted") + '">' + (project.final_video_available ? "Preview unavailable" : "NOT GENERATED") + '</span><p>' + COMPONENTS.esc(project.sample ? "Demo project: real media is not available while the engine is offline." : project.final_video_available ? "The project records final media, but it is not available from the current engine session." : "No final video artifact is safely available.") + '</p></div>';
    var pubs = {};
    (project.publications || []).forEach(function (pub) { pubs[pub.platform] = pub; });
    var destinations = COMPONENTS.PLATFORMS.filter(function (pf) { return pf.live; }).map(function (pf) {
      var conn = connections[pf.id] || {}, pub = pubs[pf.id];
      if (pub && (pub.state === "VERIFIED" || pub.state === "VERIFIED_SANDBOX_PRIVATE")) return '<div class="destination-row"><div><strong>' + COMPONENTS.esc(pf.name) + '</strong><p class="hint">Connected · existing publication record</p></div>' + COMPONENTS.pubBadge(pub.state, false) + '</div>';
      if (pf.id !== "tiktok") return '<div class="destination-row"><div><strong>' + COMPONENTS.esc(pf.name) + '</strong><p class="hint">Connected does not mean this Command Center action is supported.</p></div><span class="badge muted">Unsupported here</span></div>';
      if (!media) return '<div class="destination-row"><div><strong>TikTok</strong><p class="hint">A valid final media artifact is required before publishing.</p></div><span class="badge muted">Not ready</span></div>';
      if (conn.status !== "CONNECTED") return '<div class="destination-row"><div><strong>TikTok</strong><p class="hint">Connect TikTok before using the existing publish action.</p></div><span class="badge muted">Not connected</span></div>';
      return '<div class="destination-row"><div><strong>TikTok</strong><p class="hint">Connected · supported by the existing per-project bridge.</p></div><button class="btn btn-sm btn-primary" data-project-publish data-project-id="' + COMPONENTS.esc(project.project_id) + '" data-project-title="' + COMPONENTS.esc(project.topic) + '">Publish</button></div>';
    }).join("");
    var binding = project.production_binding;
    var relationship = binding
      ? 'Bound to production ' + COMPONENTS.esc(binding.production_id) + ' · unit ' + COMPONENTS.esc(binding.unit_id)
      : project.binding_error === "PROJECT_BINDING_INVALID"
        ? 'Binding evidence is invalid; no production relationship is shown.'
        : 'Unbound/legacy project; no production relationship is inferred.';
    return '<div class="page-head"><div><p class="eyebrow">Execution output</p><h1 class="page-title">' + COMPONENTS.esc(project.topic) + '</h1><p class="page-sub mono">' + COMPONENTS.esc(project.project_id) + ' · created ' + COMPONENTS.esc(project.created_at || "unknown") + '</p></div><div class="head-actions"><a class="btn" href="#/projects">Back to projects</a></div></div>' +
      '<section class="workspace project-workspace"><div class="card card-pad"><p class="eyebrow">Final media</p>' + player + '</div><aside class="card card-pad"><p class="eyebrow">Output identity</p><dl class="detail-list"><div><dt>Project</dt><dd class="mono">' + COMPONENTS.esc(project.project_id) + '</dd></div><div><dt>Stage evidence</dt><dd>' + COMPONENTS.esc(project.stage_status || "Unavailable") + '</dd></div><div><dt>Relationship</dt><dd>' + relationship + '</dd></div></dl></aside></section>' +
      '<section class="card card-pad destination-panel" aria-labelledby="destinations-heading"><div class="section-heading"><div><p class="eyebrow">Publishing</p><h2 id="destinations-heading">Destination availability</h2></div><span class="hint">Explicit action only</span></div><div id="project-publish-feedback" aria-live="polite"></div>' + destinations + '</section>';
  },

  mount: async function () {
    document.querySelectorAll('[data-project-publish]').forEach(function (button) {
      button.addEventListener('click', function () {
        var id = button.getAttribute('data-project-id'), title = button.getAttribute('data-project-title');
        CC.confirmDialog({ title: 'Publish to TikTok?', description: 'Publish the existing final media for ' + title + '?', scope: 'One project output · TikTok only · no batch publishing.', firstAction: 'The existing TikTok publication bridge validates eligibility, duplicate identity, and platform state before submission.', confirmLabel: 'Publish to TikTok' }, function () {
          API.publishTiktok(id, 'SELF_ONLY').then(function (res) { COMPONENTS.toast(res.detail || res.message || 'Publish result received', res.error ? 'err' : 'ok'); App.refresh(); }).catch(function (err) { var message = err.message || String(err), feedback = document.getElementById('project-publish-feedback'); if (feedback) feedback.innerHTML = '<p class="inline-error" role="alert">' + COMPONENTS.esc(message) + '</p>'; COMPONENTS.toast(message, 'err'); });
        });
      });
    });
  }
};
