/* ============================================================
   AUTO VIDEO MAKER — Creator Home (#/)

   Presentation, navigation, and create-handoff ONLY:
   - Every real-media surface resolves through existing API
     endpoints (projects, productions, connections, media).
     Nothing is fabricated; failures degrade to honest tiles.
   - Execution NEVER happens here. Command Center (#/productions)
     remains the single authoritative production surface.
   - The prompt instrument writes into VIEWS.create.data (the
     Create view's own state) and navigates to #/create — there
     is still exactly one creation path: POST /api/productions
     from create.js with mode "single_video".

   UI ART POLICY: files under webapp/assets/img/ui/ are designed
   interface assets (ui-*.svg, including the -warm projector-tone
   variants). They are decorative product art — never production
   output, never bound to a production/project id, never served
   from a media endpoint. Every usage carries data-ui-art="true".
   ============================================================ */
window.VIEWS = window.VIEWS || {};

VIEWS.home = (function () {
  var C = COMPONENTS;
  var ART = "assets/img/ui/";

  /* Exact create.js vocabulary — never invented options. */
  var PATHS = [
    { id: "local", label: "Local", tone: "free" },
    { id: "alibaba_image_local_video", label: "Alibaba Image → Local", tone: "paid" },
    { id: "alibaba_video", label: "Alibaba Video", tone: "paid" }
  ];
  var DESTS = [
    { id: "youtube_shorts", label: "YouTube Shorts" },
    { id: "tiktok", label: "TikTok" }
  ];
  var LENGTHS = [15, 30, 45, 60];

  var PRESETS = {
    yt30:   { duration: 30, outputIntent: "youtube_shorts", strategy: "local" },
    tt15:   { duration: 15, outputIntent: "tiktok", strategy: "local" },
    cin60:  { duration: 60, outputIntent: "youtube_shorts", strategy: "local" },
    motion: { duration: 30, outputIntent: "youtube_shorts", strategy: "alibaba_video" },
    free:   { duration: 30, outputIntent: "youtube_shorts", strategy: "local" }
  };

  /* Inspirational example ideas — static product copy, NEVER production
     records. Used by the rotating placeholder and the "Try" chips. */
  var IDEAS = [
    "A cyberpunk chase through Tokyo, told in one vertical minute",
    "The founder's story behind a small coffee roastery",
    "A fantasy trailer: the last dragon wakes beneath the mountain",
    "How deep-sea creatures survive crushing pressure",
    "A rainy-night neo-noir detective phone call"
  ];
  var rotateTimer = null;
  var rotationIndex = 0;

  /* Rotating placeholder — presentation only. It writes the placeholder
     ATTRIBUTE, never the textarea value; it pauses while the composer holds
     user text or focus; it never starts at all under prefers-reduced-motion
     (the static first idea stays). Self-cleans when the view unmounts. */
  function startRotation() {
    if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    rotateTimer = setInterval(function () {
      var ta = document.getElementById("home-brief");
      if (!ta) { clearInterval(rotateTimer); rotateTimer = null; return; }
      if (ta.value.trim() || document.activeElement === ta) return;
      rotationIndex = (rotationIndex + 1) % IDEAS.length;
      ta.setAttribute("placeholder", IDEAS[rotationIndex]);
    }, 4200);
  }

  function uiArt(file, alt, cls) {
    return '<img class="' + (cls || "") + '" data-ui-art="true" src="' + ART + file +
      '" alt="' + C.esc(alt || "") + '" loading="lazy" draggable="false">';
  }

  function secHead(kicker, title, linkHref, linkLabel) {
    return '<div class="sec-head"><div><p class="sec-kicker">' + C.esc(kicker) +
      '</p><h2 class="sec-title">' + C.esc(title) + "</h2></div>" +
      (linkHref ? '<a class="sec-link" href="' + linkHref + '">' + C.esc(linkLabel || "View all") + ICONS.arrowRight + "</a>" : "") +
      "</div>";
  }

  /* --- Z0 ambient stage (decorative, home-only) --------------------- */
  function ambient() {
    return '<div class="home-ambient" aria-hidden="true">' +
      '<div class="amb-aurora amb-a1"></div><div class="amb-aurora amb-a2"></div>' +
      '<img class="amb-art" data-ui-art="true" src="' + ART + 'ui-stage-hero-warm.svg" alt="">' +
      "</div>";
  }

  /* --- Z1 spotlight rail -------------------------------------------- */
  function spotlightRail(projects, connectedCount) {
    var realCards = "";
    if (API.live) {
      realCards = projects.filter(function (p) {
        return !p.error && p.final_video_available && !p.sample;
      }).slice(0, 3).map(function (p) {
        return '<a class="spot-card" href="#/projects/' + encodeURIComponent(p.project_id) + '" aria-label="Open project ' + C.esc(p.topic || p.project_id) + '">' +
          '<div class="spot-media">' + ICONS.film +
            '<video class="hv-video" preload="none" muted loop playsinline tabindex="-1" aria-hidden="true" src="' +
              C.esc(API.mediaUrl(p.project_id)) + '" onerror="this.remove()"></video>' +
            '<span class="spot-flag ok">Real output</span>' +
          "</div>" +
          '<div class="spot-body"><div class="spot-title">' + C.esc(p.topic || p.project_id) + '</div>' +
          '<div class="spot-sub">Finished render · hover to preview</div></div></a>';
      }).join("");
    }
    var capCards =
      '<article class="spot-card cap"><button type="button" class="spot-inner" data-home="focus-prompt">' +
        '<span class="spot-media">' + uiArt("ui-spot-create-warm.svg", "", "spot-art") + '</span>' +
        '<span class="spot-body"><span class="spot-title">Start your next film</span>' +
        '<span class="spot-sub">Describe it below — planning, sourcing, and rendering stay local-first.</span></span>' +
      "</button></article>" +
      '<a class="spot-card cap" href="#/create"><div class="spot-media">' + uiArt("ui-spot-paths-warm.svg", "", "spot-art") + '</div>' +
        '<div class="spot-body"><div class="spot-title">Three production paths</div>' +
        '<div class="spot-sub">Local &amp; free, or Alibaba imagery / motion-video. You approve every command.</div></div></a>' +
      '<a class="spot-card cap" href="#/platforms"><div class="spot-media">' + uiArt("ui-spot-publish-warm.svg", "", "spot-art") + '</div>' +
        '<div class="spot-body"><div class="spot-title">Publish when you decide</div>' +
        '<div class="spot-sub">YouTube &amp; TikTok · ' + C.esc(String(connectedCount)) + " connected. Publishing is always explicit.</div></div></a>";
    return '<section class="home-sec spot-sec" aria-label="Studio spotlight"><div class="rail-wrap"><div class="rail">' +
      realCards + capCards + "</div></div></section>";
  }

  /* --- Z2 hero + prompt instrument ----------------------------------- */
  function pill(field, val, label, selected, extraCls) {
    return '<button type="button" class="pi-pill' + (selected ? " selected" : "") + (extraCls ? " " + extraCls : "") +
      '" data-hp="' + field + '" data-val="' + C.esc(String(val)) + '" aria-pressed="' + (selected ? "true" : "false") + '">' +
      (extraCls ? '<span class="dot" aria-hidden="true"></span>' : "") + C.esc(label) + "</button>";
  }

  function hero() {
    var d = VIEWS.home.data;
    var engineChip = API.live
      ? '<span class="hero-chip"><span class="live-dot" aria-hidden="true"></span>Engine connected · live data</span>'
      : '<span class="hero-chip"><span class="demo-dot" aria-hidden="true"></span>Demo data · engine offline</span>';
    var pills =
      '<div class="pi-pills" role="group" aria-label="Production path"><span class="pi-group-label">Path</span>' +
        PATHS.map(function (p) {
          return pill("strategy", p.id, p.label, d.strategy === p.id, "tone-" + p.tone);
        }).join("") + "</div>" +
      '<div class="pi-pills" role="group" aria-label="Destination"><span class="pi-group-label">To</span>' +
        DESTS.map(function (o) {
          return pill("output", o.id, o.label, d.outputIntent === o.id, "");
        }).join("") + "</div>" +
      '<div class="pi-pills" role="group" aria-label="Length"><span class="pi-group-label">Length</span>' +
        LENGTHS.map(function (n) {
          return pill("duration", n, n + "s", d.duration === n, "");
        }).join("") + "</div>";
    return '<section class="hero" aria-label="Create a video">' +
      '<div class="hero-chips">' + engineChip +
        '<span class="hero-chip">Local-first studio</span>' +
        '<span class="hero-chip">You approve every command</span></div>' +
      '<h1 class="hero-title">Turn one idea into a<br /><em class="hero-em">finished film.</em></h1>' +
      '<p class="hero-deck">AVM plans the story, sources real imagery, renders the video, and publishes only when you say so — every step confirmed by you.</p>' +
      '<form class="pi" id="home-prompt" autocomplete="off">' +
        '<label class="sr-only" for="home-brief">Describe your video</label>' +
        '<textarea id="home-brief" maxlength="500" rows="3" placeholder="' + C.esc(IDEAS[0]) + '">' + C.esc(d.brief) + "</textarea>" +
        '<div class="pi-toolbar">' + pills +
          '<button type="submit" class="pi-send" aria-label="Continue to Create Video" title="Continue to Create Video">' + ICONS.send + "</button>" +
        "</div>" +
        '<div class="pi-foot"><span class="hint">Nothing runs yet — you review everything in Create Video. Ctrl+Enter to continue.</span>' +
        '<span class="pi-error" id="home-prompt-error" role="alert" hidden></span></div>' +
      "</form>" +
      '<div class="shortcut-row">' +
        '<button type="button" class="shortcut-chip" data-preset="yt30">' + ICONS.film + "30s YouTube Short</button>" +
        '<button type="button" class="shortcut-chip" data-preset="tt15">' + ICONS.tiktok + "15s TikTok hook</button>" +
        '<button type="button" class="shortcut-chip" data-preset="cin60">' + ICONS.sparkle + "60s cinematic</button>" +
        '<button type="button" class="shortcut-chip" data-preset="motion">' + ICONS.bolt + "Alibaba motion clip</button>" +
        '<button type="button" class="shortcut-chip" data-preset="free">' + ICONS.check + "Local &amp; free</button>" +
        '<a class="shortcut-chip" href="#/assets">' + ICONS.layers + "Browse asset library</a>" +
        '<a class="shortcut-chip" href="#/productions">' + ICONS.resume + "Resume attention</a>" +
      "</div>" +
      tryRow() +
      pipeline() +
      "</section>";
  }

  /* --- Inspirational "Try" ideas: populate the composer ONLY. They never
     submit and never create anything — clearly labelled examples. -------- */
  function tryRow() {
    return '<div class="try-row" aria-label="Example ideas">' +
      '<span class="try-label">Try</span>' +
      IDEAS.slice(0, 3).map(function (idea) {
        return '<button type="button" class="idea-chip" data-idea="' + C.esc(idea) + '">' + C.esc(idea) + "</button>";
      }).join("") +
      "</div>";
  }

  /* --- Explanatory pipeline strip: product language, never live state. --- */
  function pipeline() {
    var steps = ["Idea", "Story", "Scenes", "Audio", "Assembly", "Finished film"];
    return '<div class="pipeline">' +
      steps.map(function (s) {
        return '<span class="pipeline-step">' + C.esc(s) + "</span>";
      }).join('<span class="pipeline-arrow" aria-hidden="true">&rarr;</span>') +
      "</div>";
  }

  /* --- Z4 production shapes ------------------------------------------- */
  function modes() {
    function card(art, title, badgeCls, badge, copy, foot) {
      return '<article class="mode-card reveal"><div class="mode-art">' + uiArt(art, "", "mode-img") +
        '<span class="badge ' + badgeCls + ' mode-badge">' + C.esc(badge) + "</span></div>" +
        '<div class="mode-body"><h3 class="mode-title">' + C.esc(title) + "</h3>" +
        '<p class="mode-copy">' + copy + "</p>" + '<div class="mode-foot">' + foot + "</div></div></article>";
    }
    return '<section class="home-sec reveal" aria-label="Ways to produce">' +
      secHead("The studio", "Three ways to produce") +
      '<div class="mode-grid">' +
      card("ui-mode-single-warm.svg", "Single Film", "ok", "Live",
        "One brief &rarr; one finished video. Planning, sourcing, and rendering run through the confirmed Command Center path.",
        '<button type="button" class="btn btn-primary btn-sm" data-home="focus-prompt">Start a Single Film</button>') +
      card("ui-mode-multi-warm.svg", "Multiple Independent", "info", "Execution-ready",
        "One production carrying several standalone videos as units. Multi-unit productions are managed and executed in the Command Center.",
        '<a class="btn btn-sm" href="#/productions">See in Command Center</a>') +
      card("ui-mode-series-warm.svg", "Series & Episodic", "muted", "Roadmap · V1 integration",
        "Episodes with shared context and continuity on the same consolidated Production/Unit path — documented in the AVM master roadmap.",
        '<span class="hint">Creation UI lands with the consolidated mode workflow.</span>') +
      "</div></section>";
  }

  /* --- Z5 continuity / attention (read-only mirror of real states) ----- */
  function attentionPriority(p) {
    var c = p.counts || {};
    if (p.error || !p.counts) return 0;
    if (c.FAILED) return 1;
    if (c.BLOCKED) return 2;
    if (c.IN_FLIGHT) return 3;
    if (c.INCOMPLETE) return 4;
    return 5;
  }

  function continuity(productions) {
    if (!productions || !productions.length) return "";
    var rows = productions.filter(function (p) {
      var c = p.counts || {};
      return p.error || c.FAILED || c.BLOCKED || c.IN_FLIGHT || c.INCOMPLETE;
    }).sort(function (a, b) { return attentionPriority(a) - attentionPriority(b); }).slice(0, 4);
    if (!rows.length) return "";
    var list = rows.map(function (p) {
      var c = p.counts || {};
      var tone = p.error || c.FAILED ? "danger" : c.BLOCKED || c.INCOMPLETE ? "warn" : c.IN_FLIGHT ? "info" : "neutral";
      var summary = p.error ? "Production data requires inspection." : (CC.stateSummary(c) || "No state summary available.");
      var when = CC.relTime(p.revised_at || p.created_at);
      return '<div class="cont-row tone-' + tone + '">' +
        '<div class="cont-main"><div class="cont-title">' + C.esc(p.title || p.production_id) +
          (p.run && p.run.running ? ' <span class="badge info">Active run</span>' : "") + "</div>" +
        '<div class="cont-brief">' + C.esc(p.command_text || "") + "</div></div>" +
        '<div class="cont-meta"><span class="badge ' + (tone === "danger" ? "err" : tone === "warn" ? "warn" : tone === "info" ? "info" : "muted") + '">' + C.esc(summary) + "</span>" +
        (when ? '<span class="hint">' + C.esc(when) + "</span>" : "") +
        '<a class="btn btn-sm" href="#/productions/' + encodeURIComponent(p.production_id) + '">Inspect</a></div></div>';
    }).join("");
    return '<section class="home-sec reveal" aria-label="Productions needing attention">' +
      secHead("Continuity", "Continue the story", "#/productions", "Command Center") +
      '<div class="cont-list">' + list + "</div>" +
      '<p class="hint cont-note">Commands stay explicit: execution and resume are confirmed in the Command Center — never triggered from Home.</p></section>';
  }

  /* --- Z6 studio rail (real finished work only) ------------------------- */
  function stageBadge(p) {
    var stage = p.stage_status === "complete" ? ["ok", "Ready"] :
                p.stage_status === "failed" ? ["err", "Failed"] :
                p.stage_status === "running" ? ["warn", "Generating"] : ["muted", "Draft"];
    return '<span class="badge ' + stage[0] + '">' + stage[1] + "</span>";
  }

  function studioCard(p) {
    var canPlay = API.live && !p.sample && p.final_video_available;
    var media = canPlay
      ? '<video class="hv-video" preload="none" muted loop playsinline tabindex="-1" aria-hidden="true" src="' +
        C.esc(API.mediaUrl(p.project_id)) + '" onerror="this.remove()"></video>'
      : "";
    var pubs = (p.publications || []).map(function (pub) {
      var pf = C.platformById(pub.platform);
      if (!pf) return "";
      return '<span class="chip' + (pub.state === "VERIFIED" ? " ok" : "") + '">' + ICONS[pf.icon] + C.esc(pf.name) + "</span>";
    }).join("");
    return '<a class="studio-card reveal" href="#/projects/' + encodeURIComponent(p.project_id) + '">' +
      '<div class="studio-media">' + ICONS.film + media +
        (p.sample ? '<span class="spot-flag sample">Sample</span>' : canPlay ? '<span class="spot-flag ok">Real output</span>' : "") +
      "</div>" +
      '<div class="studio-body"><div class="studio-title">' + C.esc(p.topic || p.project_id) + "</div>" +
      '<div class="studio-meta">' + stageBadge(p) + pubs + "</div></div></a>";
  }

  function quietBand() {
    return '<div class="quiet-band"><div class="quiet-art">' + uiArt("ui-band-quiet-warm.svg", "", "quiet-img") + "</div>" +
      '<div class="quiet-body"><h3 class="quiet-title">Your studio is quiet.</h3>' +
      '<p class="quiet-copy">Make the first film — describe it above and AVM will plan, source, and render it. Nothing runs until you confirm it.</p>' +
      '<button type="button" class="btn btn-primary" data-home="focus-prompt">Write the first brief</button></div></div>';
  }

  function studioRail(projects) {
    var body;
    if (!API.live) {
      body = '<div class="demo-notice">Showing labelled samples — the local engine is offline. Real previews appear when the engine is running.</div>' +
        '<div class="studio-grid">' + projects.slice(0, 5).map(studioCard).join("") + "</div>";
    } else if (!projects.length) {
      body = quietBand();
    } else {
      /* "Finished films" means finished: only projects whose authoritative
         manifest reports an available final video. Drafts/failed stages stay
         visible in Projects, never mislabelled here. Asymmetric editorial
         grid: the lead film spans the full width; the rest pair beneath it. */
      var finished = projects.filter(function (p) { return !p.error && p.final_video_available; });
      body = finished.length
        ? '<div class="studio-grid">' + finished.slice(0, 7).map(studioCard).join("") + "</div>"
        : quietBand();
    }
    return '<section class="home-sec reveal" aria-label="Finished work">' +
      secHead("From your studio", "Finished films", "#/projects", "All projects") + body + "</section>";
  }

  /* --- Capability: the invisible crew (truthful, presentation-only) ------- */
  function crew() {
    function tile(icon, role, note) {
      return '<li class="crew-item"><span class="crew-icon" aria-hidden="true">' + (ICONS[icon] || "") +
        '</span><div><h3 class="crew-role">' + C.esc(role) + '</h3><p class="crew-note">' + C.esc(note) + "</p></div></li>";
    }
    return '<section class="home-sec crew-sec reveal" aria-label="How AVM produces your film">' +
      '<div class="crew-band">' +
        '<div class="crew-intro"><p class="crew-kicker">The craft, handled</p>' +
          '<h2 class="crew-title">A full production crew, working invisibly.</h2>' +
          '<p class="crew-copy">Every finished film is the work of AVM\u2019s real pipeline — planning, sourcing, rendering, sound, assembly, and evidence checks run quietly underneath. You stay in the director\u2019s chair.</p></div>' +
        '<ul class="crew-grid">' +
          tile("sparkle", "Story & Planning", "Your brief becomes a scene-by-scene production plan — beats, pacing, and sourcing decided before anything renders.") +
          tile("clapper", "Scene Direction", "A consistent look is held across every scene. An unavailable generation path fails loudly — never a silent substitute.") +
          tile("layers", "Media Orchestration", "Generated imagery, motion clips, and uploads are coordinated into one coherent visual language.") +
          tile("mic", "Narration & Score", "Voice, music, and sound design are planned and mixed — intentional silence included.") +
          tile("film", "Edit & Assembly", "Scenes are cut, paced, and finalized to your target length by the assembly engine.") +
          tile("check", "Continuity & QA", "Execution evidence is checked against the plan before anything is called finished.") +
        "</ul>" +
      "</div></section>";
  }

  /* --- Z7 distribution strip --------------------------------------------- */
  function distStrip(connections) {
    connections = connections || {};
    var orbs = C.PLATFORMS.map(function (pf) {
      var st = connections[pf.id] || {};
      var connected = st.status === "CONNECTED";
      var dotCls = connected ? "ok" : st.status === "ERROR" ? "err" : pf.live ? "" : "soon";
      var label = pf.name + " — " + (connected ? "connected" : (st.status === "COMING SOON" || !pf.live) ? "coming soon" : st.status === "ERROR" ? "error" : "not connected");
      return '<span class="dist-orb' + (connected ? " connected" : "") + '" title="' + C.esc(label) + '" role="img" aria-label="' + C.esc(label) + '">' +
        ICONS[pf.icon] + '<span class="orb-state ' + dotCls + '" aria-hidden="true"></span></span>';
    }).join("");
    return '<section class="home-sec reveal" aria-label="Distribution">' +
      '<div class="dist-band"><div class="dist-copy"><h2 class="dist-title">Create once. Publish everywhere.</h2>' +
      '<p class="page-sub">Publishing is always a separate, explicit, confirmed step — from your studio, never silently.</p></div>' +
      '<div class="dist-orbs">' + orbs + "</div>" +
      '<a class="btn" href="#/platforms">Manage platforms</a></div></section>';
  }

  /* --- Z8 footer strip ----------------------------------------------------- */
  function foot() {
    return '<footer class="home-foot"><span>Every preview on this page is real studio media or designed interface art — never a fabricated result.</span>' +
      "<span>Auto Video Maker · local-first studio · every command stays yours.</span></footer>";
  }

  return {
    title: "Creator Home",

    /* Prompt-instrument state; mirrors the exact fields create.js submits. */
    data: { strategy: "local", outputIntent: "youtube_shorts", duration: 30, brief: "" },

    PATHS: PATHS, DESTS: DESTS, LENGTHS: LENGTHS, PRESETS: PRESETS,

    render: async function () {
      var projects = [];
      var productions = null;
      var connections = null;
      try { projects = await API.getProjectsWithFallback(); } catch (e) { projects = []; }
      try { connections = await API.getConnections(); } catch (e) { connections = null; }
      if (API.live) {
        try { productions = await API.getProductions(); } catch (e) { productions = null; }
      }
      var connectedCount = 0;
      C.PLATFORMS.forEach(function (pf) {
        if (connections && connections[pf.id] && connections[pf.id].status === "CONNECTED") connectedCount++;
      });
      return '<div class="home-view">' + ambient() + hero() +
        spotlightRail(projects, connectedCount) + modes() +
        continuity(productions) + studioRail(projects) + crew() +
        distStrip(connections) + foot() + "</div>";
    },

    mount: function () {
      var d = VIEWS.home.data;

      /* Placeholder-rotation lifecycle: clear any timer a previous render
         left behind (the router swaps innerHTML without unmount hooks). */
      if (rotateTimer) { clearInterval(rotateTimer); rotateTimer = null; }

      /* Scroll reveal (neutralized under reduced motion via CSS). */
      var revealEls = document.querySelectorAll(".home-view .reveal");
      if ("IntersectionObserver" in window) {
        var revealObs = new IntersectionObserver(function (entries) {
          entries.forEach(function (entry) {
            if (entry.isIntersecting) { entry.target.classList.add("in"); revealObs.unobserve(entry.target); }
          });
        }, { threshold: 0.12 });
        revealEls.forEach(function (el) { revealObs.observe(el); });
      } else {
        revealEls.forEach(function (el) { el.classList.add("in"); });
      }

      /* Lazy real-media previews: metadata loads only near the viewport,
         hover/focus plays. Real files only; on error the video removes
         itself and the honest fallback tile stays. */
      var videos = document.querySelectorAll(".home-view .hv-video");
      if ("IntersectionObserver" in window && videos.length) {
        var vidObs = new IntersectionObserver(function (entries) {
          entries.forEach(function (entry) {
            if (entry.isIntersecting) { entry.target.preload = "metadata"; vidObs.unobserve(entry.target); }
          });
        }, { rootMargin: "300px" });
        videos.forEach(function (v) { vidObs.observe(v); });
      }
      videos.forEach(function (v) {
        var card = v.closest("a, .spot-card");
        function play() { var pr = v.play(); if (pr && pr.catch) pr.catch(function () { /* first frame stays */ }); }
        function stop() { try { v.pause(); v.currentTime = 0; } catch (e) { /* ignore */ } }
        if (card) {
          card.addEventListener("mouseenter", play);
          card.addEventListener("mouseleave", stop);
          card.addEventListener("focusin", play);
          card.addEventListener("focusout", stop);
        }
      });

      /* Prompt instrument pills — update state + selected class in place
         (no full re-render, so the textarea keeps focus). */
      document.querySelectorAll(".home-view [data-hp]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var field = btn.getAttribute("data-hp"), val = btn.getAttribute("data-val");
          if (field === "strategy") d.strategy = val;
          else if (field === "output") d.outputIntent = val;
          else if (field === "duration") d.duration = Number(val);
          VIEWS.home.syncPills();
        });
      });

      /* Creative shortcut presets — fill the instrument, never auto-navigate. */
      document.querySelectorAll(".home-view [data-preset]").forEach(function (chip) {
        chip.addEventListener("click", function () {
          var preset = PRESETS[chip.getAttribute("data-preset")];
          if (!preset) return;
          if (preset.strategy) d.strategy = preset.strategy;
          if (preset.outputIntent) d.outputIntent = preset.outputIntent;
          if (preset.duration) d.duration = preset.duration;
          VIEWS.home.syncPills();
          var ta = document.getElementById("home-brief");
          if (ta && !ta.value.trim()) ta.focus();
        });
      });

      /* Focus-prompt buttons (spotlight card, mode card, quiet band). */
      document.querySelectorAll('.home-view [data-home="focus-prompt"]').forEach(function (btn) {
        btn.addEventListener("click", function () {
          var ta = document.getElementById("home-brief");
          if (!ta) return;
          ta.scrollIntoView({ behavior: "smooth", block: "center" });
          setTimeout(function () { ta.focus(); }, 260);
        });
      });

      var textarea = document.getElementById("home-brief");
      if (textarea) {
        textarea.addEventListener("input", function () { d.brief = textarea.value; });
        textarea.addEventListener("keydown", function (event) {
          if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
            event.preventDefault();
            VIEWS.home.handoff();
          }
        });
      }

      /* "Try" example ideas — fill the composer only. Never submit, never
         create: the single creation path still runs through Create Video. */
      document.querySelectorAll(".home-view [data-idea]").forEach(function (chip) {
        chip.addEventListener("click", function () {
          var ta = document.getElementById("home-brief");
          if (!ta) return;
          ta.value = chip.getAttribute("data-idea") || "";
          d.brief = ta.value;
          var err = document.getElementById("home-prompt-error");
          if (err) err.hidden = true;
          ta.focus();
          try { ta.setSelectionRange(ta.value.length, ta.value.length); } catch (e) { /* ignore */ }
        });
      });

      var form = document.getElementById("home-prompt");
      if (form) form.addEventListener("submit", function (event) {
        event.preventDefault();
        VIEWS.home.handoff();
      });

      startRotation();
    },

    /* Re-apply selected state after a preset (pills are static HTML). */
    syncPills: function () {
      var d = VIEWS.home.data;
      document.querySelectorAll(".home-view [data-hp]").forEach(function (btn) {
        var field = btn.getAttribute("data-hp"), val = btn.getAttribute("data-val");
        var on = (field === "strategy" && d.strategy === val) ||
                 (field === "output" && d.outputIntent === val) ||
                 (field === "duration" && String(d.duration) === val);
        btn.classList.toggle("selected", on);
        btn.setAttribute("aria-pressed", on ? "true" : "false");
      });
    },

    /* Create-handoff: writes into the Create view's own state, then navigates.
       This does NOT create anything — the single POST /api/productions still
       happens only from create.js after the user reviews. The user's optional
       working title draft in create.js is left untouched. */
    handoff: function () {
      var d = VIEWS.home.data;
      var err = document.getElementById("home-prompt-error");
      var ta = document.getElementById("home-brief");
      if (!ta || !ta.value.trim()) {
        if (err) { err.textContent = "Describe your video first — one sentence is enough."; err.hidden = false; }
        if (ta) ta.focus();
        return;
      }
      if (err) { err.textContent = ""; err.hidden = true; }
      var cd = VIEWS.create.data;
      cd.brief = ta.value.trim();
      cd.strategy = d.strategy;
      cd.outputIntent = d.outputIntent;
      cd.duration = d.duration;
      cd.created = null;
      cd.error = "";
      VIEWS.create.step = 1;
      location.hash = "#/create";
    }
  };
})();
