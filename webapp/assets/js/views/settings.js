/* AUTO VIDEO MAKER — Settings view (read-only, never shows secrets) */
window.VIEWS = window.VIEWS || {};

VIEWS.settings = {
  title: "Settings",

  render: function () {
    function row(k, v) {
      return '<div class="meta-row"><div class="meta-key">' + k + "</div><div>" + v + "</div></div>";
    }
    return (
      '<div class="page-head"><div><h1 class="page-title">Settings</h1>' +
      '<p class="page-sub">Local configuration summary. Secrets are stored only in git-ignored local files and are never displayed anywhere in this interface.</p></div></div>' +
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
