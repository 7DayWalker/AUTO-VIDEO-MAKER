/* ============================================================
   AUTO VIDEO MAKER — inline icon set (consistent 24px grid)
   ============================================================ */

window.ICONS = (function () {
  function stroke(paths, extra) {
    return '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" ' +
      'stroke="currentColor" stroke-width="1.7" stroke-linecap="round" ' +
      'stroke-linejoin="round" aria-hidden="true"' + (extra || "") + ">" + paths + "</svg>";
  }
  function fill(path, vb) {
    return '<svg viewBox="' + (vb || "0 0 24 24") + '" width="18" height="18" ' +
      'fill="currentColor" aria-hidden="true">' + path + "</svg>";
  }
  return {
    dashboard: stroke('<rect x="3.5" y="3.5" width="7" height="7" rx="1.6"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.6"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.6"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.6"/>'),
    create: stroke('<path d="M12 5v14M5 12h14"/>'),
    projects: stroke('<path d="M3.5 7.5c0-1.1.9-2 2-2h4l2 2.5h7a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>'),
    platforms: stroke('<path d="M9.5 14.5 14.5 9.5"/><circle cx="6.2" cy="17.8" r="2.7"/><circle cx="17.8" cy="6.2" r="2.7"/><path d="M8 12 5.5 9.5a3.9 3.9 0 0 1 0-5.5 3.9 3.9 0 0 1 5.5 0L14 7"/><path d="m16 17 2.5 2.5a3.9 3.9 0 0 0 0-5.5L16 12"/>'),
    activity: stroke('<path d="M3.5 12h4l2.5-6.5 4 13 2.5-6.5h4"/>'),
    settings: stroke('<circle cx="12" cy="12" r="3.2"/><path d="M19 12a7 7 0 0 0-.14-1.4l2-1.55-2-3.46-2.37.95a7 7 0 0 0-2.42-1.4L13.7 2.6h-3.4l-.37 2.54a7 7 0 0 0-2.42 1.4l-2.37-.95-2 3.46 2 1.55A7 7 0 0 0 5 12c0 .47.05.94.14 1.4l-2 1.55 2 3.46 2.37-.95a7 7 0 0 0 2.42 1.4l.37 2.54h3.4l.37-2.54a7 7 0 0 0 2.42-1.4l2.37.95 2-3.46-2-1.55c.09-.46.14-.93.14-1.4Z"/>'),
    film: stroke('<rect x="3.5" y="4.5" width="17" height="15" rx="2.4"/><path d="M8 4.5v15M16 4.5v15M3.5 9.5h4.5M3.5 14.5h4.5M16 9.5h4.5M16 14.5h4.5"/>'),
    upload: stroke('<path d="M12 16V5m0 0 -4 4m4-4 4 4"/><path d="M4.5 16.5v2a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-2"/>'),
    check: stroke('<path d="m5 12.5 4.5 4.5L19 7.5"/>'),
    clock: stroke('<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>'),
    alert: stroke('<path d="M12 4 2.8 19.5h18.4Z"/><path d="M12 10v4.2M12 17.1v.2"/>'),
    plug: stroke('<path d="M9 3.5V9m6-5.5V9M7 9h10v3.2a5 5 0 0 1-5 5 5 5 0 0 1-5-5Z"/><path d="M12 17.5V21"/>'),
    external: stroke('<path d="M9 5H6a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-3"/><path d="M13 4h7v7M20 4 11 13"/>'),
    copy: stroke('<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" transform="translate(1.5 1.5) scale(.92)"/>'),
    sparkle: stroke('<path d="M12 3.5 13.8 9l5.7 1.8-5.7 1.8L12 18.5l-1.8-5.9L4.5 10.8 10.2 9Z"/><path d="M19 3v3M20.5 4.5h-3"/>'),
    mic: stroke('<rect x="9.5" y="3.5" width="5" height="10" rx="2.5"/><path d="M6 11a6 6 0 0 0 12 0M12 17v3.5"/>'),
    youtube: fill('<path d="M21.58 7.19a2.5 2.5 0 0 0-1.76-1.77C18.25 5 12 5 12 5s-6.25 0-7.82.42A2.5 2.5 0 0 0 2.42 7.2 26.2 26.2 0 0 0 2 12a26.2 26.2 0 0 0 .42 4.81 2.5 2.5 0 0 0 1.76 1.77C5.75 19 12 19 12 19s6.25 0 7.82-.42a2.5 2.5 0 0 0 1.76-1.77A26.2 26.2 0 0 0 22 12a26.2 26.2 0 0 0-.42-4.81zM10 15.2V8.8L15.5 12Z"/>'),
    tiktok: fill('<path d="M16.6 5.82A4.28 4.28 0 0 1 15.54 3h-3.09v12.4a2.59 2.59 0 1 1-2.59-2.59c.27 0 .54.04.8.13V9.79a5.9 5.9 0 0 0-.8-.06 5.68 5.68 0 1 0 5.68 5.68V9.24a7.32 7.32 0 0 0 4.28 1.37V7.53a4.3 4.3 0 0 1-3.22-1.71z"/>'),
    instagram: stroke('<rect x="3.8" y="3.8" width="16.4" height="16.4" rx="4.4"/><circle cx="12" cy="12" r="3.9"/><circle cx="17" cy="7" r=".9" fill="currentColor" stroke="none"/>', ''),
    facebook: fill('<path d="M13.6 21v-7.9h2.65l.4-3.08H13.6V8.06c0-.89.25-1.5 1.53-1.5h1.64V3.8c-.28-.04-1.26-.12-2.4-.12-2.37 0-4 1.45-4 4.1V10H7.7v3.08h2.67V21Z"/>')
  };
})();
