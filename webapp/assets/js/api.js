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
      try {
        const errBody = await res.json();
        // Phase 11 structured errors carry message/error; legacy payloads
        // carry detail. Surface the most specific truthful text available.
        detail = errBody.message || errBody.error || errBody.detail || detail;
      } catch (e) { /* keep */ }
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

    /** Safe same-origin scene delivery URL; the server validates the scene. */
    sceneMediaUrl(projectId, sceneId) {
      return "/api/projects/" + encodeURIComponent(projectId) +
        "/scenes/" + encodeURIComponent(sceneId) + "/media";
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
    },

    /* ---- Phase 11: Personal Command Center (production inspection/control) ---- */

    getProductions() {
      return jsonFetch("/api/productions");
    },

    getProduction(id) {
      return jsonFetch("/api/productions/" + encodeURIComponent(id));
    },

    /** Live six-state progress; re-derived on every call (never cached). */
    getProductionProgress(id) {
      return jsonFetch("/api/productions/" + encodeURIComponent(id) + "/progress");
    },

    getUnit(productionId, unitId) {
      return jsonFetch(
        "/api/productions/" + encodeURIComponent(productionId) +
        "/units/" + encodeURIComponent(unitId)
      );
    },

    /** On-demand read-only reconciliation report (persist=false server-side). */
    getUnitReconciliation(productionId, unitId) {
      return jsonFetch(
        "/api/productions/" + encodeURIComponent(productionId) +
        "/units/" + encodeURIComponent(unitId) + "/reconciliation"
      );
    },

    /** Execute/Resume production (or a single unit). confirm is always true:
        the UI only calls this from an explicit confirmation dialog. */
    productionCommand(id, action, unitId) {
      var url = "/api/productions/" + encodeURIComponent(id) + "/" + action;
      if (unitId) url += "?unit_id=" + encodeURIComponent(unitId);
      return jsonFetch(url, {
        method: "POST",
        headers: { "Accept": "application/json", "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true })
      });
    },

    /** Publish Unit: thin delegation to the existing publishing flow. */
    publishUnit(productionId, unitId, platforms) {
      var body = { confirm: true };
      if (platforms && platforms.length) body.platforms = platforms;
      return jsonFetch(
        "/api/productions/" + encodeURIComponent(productionId) +
        "/units/" + encodeURIComponent(unitId) + "/publish",
        {
          method: "POST",
          headers: { "Accept": "application/json", "Content-Type": "application/json" },
          body: JSON.stringify(body)
        }
      );
    }
  };
})();
