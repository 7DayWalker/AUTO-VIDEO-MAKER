/* ============================================================
   AUTO VIDEO MAKER — application bootstrap, router & navigation
   ============================================================ */

window.App = (function () {
  const C = COMPONENTS;

  const NAV = [
    { section: "Studio" },
    { id: "dashboard", route: "#/",           label: "Dashboard", icon: "dashboard", view: "dashboard" },
    { id: "productions", route: "#/productions", label: "Command Center", icon: "sparkle", view: "productions" },
    { id: "projects",  route: "#/projects",   label: "Projects",  icon: "projects",  view: "projects" },
    { id: "create",    route: "#/create",     label: "Create Video", icon: "create", view: "create" },
    { section: "Distribution" },
    { id: "platforms", route: "#/platforms",  label: "Platforms", icon: "platforms", view: "platforms" },
    { id: "activity",  route: "#/activity",   label: "Activity",  icon: "activity",  view: "activity" },
    { section: "System" },
    { id: "settings",  route: "#/settings",   label: "Settings",  icon: "settings",  view: "settings" }
  ];

  const ROUTES = [
    { id: "dashboard",  pattern: /^#\/$/,                      view: function () { return VIEWS.dashboard; }, params: {} },
    { id: "create",     pattern: /^#\/create\/?$/,             view: function () { return VIEWS.create; },    params: {} },
    { id: "productions", pattern: /^#\/productions\/?$/,       view: function () { return VIEWS.productions; }, params: {} },
    { id: "production", pattern: /^#\/productions\/([^\/]+)\/?$/, view: function () { return VIEWS.productionDetail; },
      params: function (m) { return { id: decodeURIComponent(m[1]) }; } },
    { id: "unit",       pattern: /^#\/productions\/([^\/]+)\/units\/([^\/]+)\/?$/, view: function () { return VIEWS.unitDetail; },
      params: function (m) { return { productionId: decodeURIComponent(m[1]), unitId: decodeURIComponent(m[2]) }; } },
    { id: "projects",   pattern: /^#\/projects\/?$/,           view: function () { return VIEWS.projects; },  params: {} },
    { id: "project",    pattern: /^#\/projects\/([^\/]+)\/?$/, view: function () { return VIEWS.projectDetail; }, params: function (m) { return { id: decodeURIComponent(m[1]) }; } },
    { id: "platforms",  pattern: /^#\/platforms\/?$/,          view: function () { return VIEWS.platforms; }, params: {} },
    { id: "activity",   pattern: /^#\/activity\/?$/,           view: function () { return VIEWS.activity; },  params: {} },
    { id: "settings",   pattern: /^#\/settings\/?$/,           view: function () { return VIEWS.settings; },  params: {} }
  ];

  let currentRoute = null;

  function buildNav(activeView) {
    const nav = document.getElementById("nav");
    const navView = activeView === "production" || activeView === "unit"
      ? "productions" : activeView === "project" ? "projects" : activeView;
    nav.innerHTML = NAV.map(function (item) {
      if (item.section) return '<div class="nav-section">' + item.section + "</div>";
      const cls = item.view === navView ? " active" : "";
      return '<a class="nav-item' + cls + '" href="' + item.route + '">' +
             ICONS[item.icon] + "<span>" + item.label + "</span></a>";
    }).join("");
  }

  function setEnvChip() {
    const chip = document.getElementById("env-chip");
    chip.className = "env-chip " + (API.live ? "live" : "demo");
    chip.textContent = API.live ? "Engine connected · live data"
                                : "Demo data · engine offline";
  }

  async function render() {
    const hash = location.hash || "#/";
    const match = ROUTES.find(function (r) { return r.pattern.test(hash); });
    const route = match || ROUTES[0];
    const raw = hash.match(route.pattern) || [];
    const params = typeof route.params === "function" ? route.params(raw) : route.params;
    currentRoute = route;

    buildNav(route.id);
    setEnvChip();

    const viewEl = document.getElementById("view");
    viewEl.setAttribute("aria-busy", "true");
    try {
      viewEl.innerHTML = '<div class="empty" role="status" aria-live="polite"><span class="hint">Loading…</span></div>';
      const html = await route.view().render(params);
      viewEl.innerHTML = html;
      document.title = route.view().title + " — Auto Video Maker";
      if (typeof route.view().mount === "function") await route.view().mount(params);
    } catch (err) {
      viewEl.innerHTML = '<div class="card">' + C.emptyState("alert",
        "Something went wrong", String(err.message || err)) + "</div>";
    }
    viewEl.setAttribute("aria-busy", "false");
    document.getElementById("sidebar").classList.remove("open");
  }

  async function boot() {
    await API.probe();
    window.addEventListener("hashchange", render);
    document.getElementById("nav-toggle").addEventListener("click", function () {
      document.getElementById("sidebar").classList.toggle("open");
    });
    await render();
  }

  return {
    boot,
    refresh: function () { render(); }
  };
})();

document.addEventListener("DOMContentLoaded", App.boot);
