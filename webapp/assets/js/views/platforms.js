/* AUTO VIDEO MAKER — Platforms / Connections view */
window.VIEWS = window.VIEWS || {};

VIEWS.platforms = {
  title: "Platforms",

  render: async function () {
    const connections = await API.getConnections();

    const cards = COMPONENTS.PLATFORMS.map(function (pf) {
      const st = connections[pf.id] ||
                 { status: pf.live ? "NOT CONNECTED" : "COMING SOON" };
      let actions;
      if (!pf.live) {
        actions = '<button class="btn btn-sm" disabled title="Planned future integration">Coming soon</button>';
      } else if (st.status === "CONNECTED") {
        actions =
          '<button class="btn btn-sm btn-danger" data-action="disconnect" data-platform="' + pf.id + '">Disconnect</button>' +
          '<button class="btn btn-sm" data-action="refresh-status">Refresh</button>';
      } else if (API.live) {
        actions =
          '<button class="btn btn-sm btn-primary" data-action="connect" data-platform="' + pf.id + '">' +
          "Connect " + COMPONENTS.esc(pf.name) + "</button>";
      } else {
        actions = '<button class="btn btn-sm" data-action="connect-local-hint">Connect locally</button>';
      }
      return COMPONENTS.platformCard(pf, st, actions);
    }).join("");

    return (
      '<div class="page-head"><div>' +
        '<h1 class="page-title">Connected Platforms</h1>' +
        '<p class="page-sub">Auto Video Maker treats every destination as a pluggable platform connector. ' +
        "Authorize an account once, then publish any project to it.</p></div>" +
        '<div class="head-actions"><button class="btn" data-action="refresh-status">' + ICONS.activity + ' Refresh status</button></div>' +
      "</div>" +
      '<div class="grid grid-2">' + cards + "</div>" +
      '<p class="hint" style="margin-top:18px">' +
        "Connection uses each platform's official OAuth authorization in a browser window on this machine. " +
        "Credentials are stored only in the local git-ignored <code>credentials/</code> directory and are never displayed." +
      "</p>"
    );
  },

  mount: async function () {
    document.querySelectorAll('[data-action="connect"]').forEach(function (btn) {
      btn.addEventListener("click", function () { VIEWS.platforms.connectFlow(btn.dataset.platform); });
    });
    document.querySelectorAll('[data-action="refresh-status"]').forEach(function (btn) {
      btn.addEventListener("click", function () { App.refresh(); });
    });
    document.querySelectorAll('[data-action="connect-local-hint"]').forEach(function (btn) {
      btn.addEventListener("click", function () {
        COMPONENTS.modal(
          "<h3>Local engine required</h3>" +
          "<p>This screen is currently running with demo data because the AVM engine isn't reachable. " +
          "Start the AVM engine on this machine, then connect for real.</p>" +
          '<div class="modal-actions"><button class="btn" onclick="COMPONENTS.closeModal()">Close</button></div>'
        );
      });
    });
    document.querySelectorAll('[data-action="disconnect"]').forEach(function (btn) {
      btn.addEventListener("click", function () {
        const pf = COMPONENTS.platformById(btn.dataset.platform);
        COMPONENTS.modal(
          "<h3>Disconnect " + COMPONENTS.esc(pf.name) + "?</h3>" +
          "<p>For safety, disconnecting is not performed automatically from the web UI. " +
          "Revoke access on the platform's connected-apps page and remove the token file from the local " +
          "<code>credentials/</code> directory.</p>" +
          '<div class="modal-actions"><button class="btn" onclick="COMPONENTS.closeModal()">Close</button></div>'
        );
      });
    });
  },

  connectFlow: async function (platformId) {
    const pf = COMPONENTS.platformById(platformId);
    if (!pf || !pf.live) return;
    try {
      await API.startConnect(platformId);
    } catch (e) {
      COMPONENTS.toast("Could not start connection: " + e.message, "err");
      return;
    }
    COMPONENTS.modal(
      "<h3>Connecting " + COMPONENTS.esc(pf.name) + "…</h3>" +
      "<p>A browser window has opened for the official " + COMPONENTS.esc(pf.name) +
      " authorization. Approve access there; this dialog updates automatically when you return.</p>" +
      '<div class="modal-actions"><span class="badge info" id="connect-polling">Waiting for authorization…</span></div>'
    );
    let attempts = 0;
    const timer = setInterval(async function () {
      attempts++;
      try {
        const conns = await API.getConnections();
        const st = conns[platformId];
        if (st && st.status === "CONNECTED") {
          clearInterval(timer);
          COMPONENTS.closeModal();
          COMPONENTS.toast(pf.name + " connected as " + (st.account || "authorized account"));
          App.refresh();
        } else if (attempts > 60) {
          clearInterval(timer);
          const el = document.getElementById("connect-polling");
          if (el) { el.className = "badge err"; el.textContent = "Timed out waiting for authorization"; }
        }
      } catch (e) { /* keep polling */ }
    }, 3000);
  }
};
