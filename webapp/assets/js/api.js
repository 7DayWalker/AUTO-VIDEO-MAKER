/* ============================================================
   AUTO VIDEO MAKER — API client
   Talks to the local engine bridge (app/web/server.py).
   If unreachable, the app degrades to clearly-labelled demo data.
   ============================================================ */

window.API = (function () {

  async function jsonFetch(url, options) {
    const res = await fetch(url, Object.assign({ headers: { "Accept": "application/json" } }, options || {}));
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (e) { /* keep */ }
      throw new Error(detail);
    }
    return res.json();
  }

  return {
    /** True when the local engine bridge answered at least once. */
    live: false,

    async probe() {
      try {
        await jsonFetch("/api/health");
        this.live = true;
      } catch (e) {
        this.live = false;
      }
      return this.live;
    },

    getConnections() {
      if (!this.live) return Promise.resolve(DEMO_DATA.connections);
      return jsonFetch("/api/connections");
    },

    startConnect(platform) {
      // Fire-and-forget: the browser opens on the local machine; the UI polls.
      return jsonFetch("/api/platforms/" + encodeURIComponent(platform) + "/connect", { method: "POST" });
    },

    getProjects() {
      if (!this.live) return Promise.resolve(DEMO_DATA.projects);
      return jsonFetch("/api/projects");
    },

    /** Real projects when the engine is reachable, clearly-labelled samples otherwise. */
    getProjectsWithFallback() {
      return this.getProjects().catch(function () {
        API.live = false;
        return DEMO_DATA.projects;
      });
    },

    getProject(id) {
      if (!this.live) {
        const p = DEMO_DATA.projects.find(function (x) { return x.project_id === id; });
        return p ? Promise.resolve(p) : Promise.reject(new Error("Not found"));
      }
      return jsonFetch("/api/projects/" + encodeURIComponent(id));
    },

    mediaUrl(projectId) {
      return "/api/projects/" + encodeURIComponent(projectId) + "/media";
    },

    /** Explicit publish request — only ever called from the confirm dialog. */
    publishTiktok(projectId, privacyLevel) {
      return jsonFetch(
        "/api/projects/" + encodeURIComponent(projectId) + "/publish/tiktok",
        {
          method: "POST",
          headers: { "Accept": "application/json", "Content-Type": "application/json" },
          body: JSON.stringify({ privacy_level: privacyLevel || null })
        }
      );
    },

    /** Safe, repeatable status read (never uploads, never opens a browser). */
    tiktokPublishStatus(projectId) {
      return jsonFetch(
        "/api/projects/" + encodeURIComponent(projectId) + "/publish/tiktok/status"
      );
    }
  };
})();
