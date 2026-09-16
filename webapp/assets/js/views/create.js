/* AUTO VIDEO MAKER — V1 durable creation entry point.
   Creation writes Production intent only. Execution remains an explicit,
   confirmed Command Center action delegated to ProductionExecutor. */
window.VIEWS = window.VIEWS || {};

VIEWS.create = {
  title: "Create Video",
  step: 1,
  data: {
    title: "", brief: "", outputIntent: "youtube_shorts",
    duration: 30, scenes: 6, tone: "curious and cinematic",
    strategy: "local",
    created: null, error: ""
  },

  render: function () {
    var d = VIEWS.create.data;
    if (d.created) return VIEWS.create.renderSuccess();
    var tabs = [1, 2].map(function (n) {
      var labels = ["Describe", "Review & create"];
      var cls = VIEWS.create.step === n ? " active" : VIEWS.create.step > n ? " done" : "";
      return '<span class="step-tab' + cls + '"><span class="n">' +
        (VIEWS.create.step > n ? "✓" : n) + "</span>" + labels[n - 1] + "</span>";
    });
    var body = VIEWS.create.step === 1 ? VIEWS.create.renderDescribe() : VIEWS.create.renderReview();
    return VIEWS.create.frame(tabs, body);
  },

  frame: function (tabs, body) {
    return '<div class="page-head"><div><p class="eyebrow">Local-first studio</p><h1 class="page-title">Create Video</h1>' +
      '<p class="page-sub">Turn one idea into a finished video. Describe it, review it, then produce it when you are ready.</p></div></div>' +
      '<div class="steps">' + tabs.join('<span class="step-sep"></span>') + '</div>' +
      '<div class="card card-pad creation-card">' + body + '</div>';
  },

  renderDescribe: function () {
    var d = VIEWS.create.data;
    return '<div class="creation-intro"><span class="creation-icon">' + ICONS.create + '</span><div><h2>Single Video</h2><p class="hint">One production · one final video.</p></div></div>' +
      '<div class="field"><label for="f-title">Working title</label><input class="input" id="f-title" maxlength="200" placeholder="e.g. The hidden life of city trees" value="' + COMPONENTS.esc(d.title) + '"><span class="hint">This becomes the production\'s name.</span></div>' +
      '<div class="field"><label for="f-brief">Creative brief</label><textarea class="input" id="f-brief" maxlength="500" rows="4" placeholder="What should this video explain, show, or make people feel?">' + COMPONENTS.esc(d.brief) + '</textarea><span class="hint">Your brief drives planning, sourcing, and rendering. It is saved with the production.</span></div>' +
      '<div class="field"><label>Output intent</label><div class="choice-grid compact" id="f-output">' +
        [['youtube_shorts', 'YouTube Shorts', '9:16 vertical'], ['tiktok', 'TikTok', '9:16 vertical']].map(function (item) {
          return '<button type="button" class="choice' + (d.outputIntent === item[0] ? ' selected' : '') + '" data-output="' + item[0] + '"><div class="c-title">' + COMPONENTS.esc(item[1]) + '</div><div class="c-sub">' + COMPONENTS.esc(item[2]) + '</div></button>';
        }).join('') + '</div></div>' +
      '<div class="field"><label>Production path</label><div class="choice-grid compact" id="f-strategy">' +
        [
          ['local', 'Local (Recommended)', 'Free — planning, imagery, and rendering stay on this machine.'],
          ['alibaba_image_local_video', 'Alibaba Image → Local Video', 'Uses paid API — Alibaba generates scene images; AVM assembles the video locally.'],
          ['alibaba_video', 'Alibaba Video', 'Uses paid API — Alibaba generates actual motion-video scenes.']
        ].map(function (item) {
          return '<button type="button" class="choice' + (d.strategy === item[0] ? ' selected' : '') + '" data-strategy="' + item[0] + '"><div class="c-title">' + COMPONENTS.esc(item[1]) + '</div><div class="c-sub">' + COMPONENTS.esc(item[2]) + '</div></button>';
        }).join('') + '</div><span class="hint">How scene visuals are made. The recommended path is free; paid paths use your configured provider account and fail loudly if unavailable.</span></div>' +
      '<div class="creation-inline-fields"><div class="field"><label for="f-duration">Target duration</label><select class="input" id="f-duration"><option value="15"' + (d.duration === 15 ? ' selected' : '') + '>15 seconds</option><option value="30"' + (d.duration === 30 ? ' selected' : '') + '>30 seconds</option><option value="45"' + (d.duration === 45 ? ' selected' : '') + '>45 seconds</option><option value="60"' + (d.duration === 60 ? ' selected' : '') + '>60 seconds</option></select></div><div class="field"><label for="f-scenes">Scene count</label><select class="input" id="f-scenes"><option value="4"' + (d.scenes === 4 ? ' selected' : '') + '>4 visual beats</option><option value="6"' + (d.scenes === 6 ? ' selected' : '') + '>6 visual beats</option><option value="8"' + (d.scenes === 8 ? ' selected' : '') + '>8 visual beats</option></select></div></div>' +
      '<div class="creation-actions"><span class="hint">You can adjust details before creating the Production.</span><button class="btn btn-primary" data-create-step="next">Continue</button></div>';
  },

  renderReview: function () {
    var d = VIEWS.create.data;
    var title = d.title.trim() || d.brief.trim();
    var strategyInfo = {
      local: ['Local', 'Planning, imagery, and rendering stay on this machine.'],
      alibaba_image_local_video: ['Alibaba Image → Local Video', 'Alibaba generates scene images; AVM assembles the video locally.'],
      alibaba_video: ['Alibaba Video', 'Alibaba generates actual motion-video scenes.']
    }[d.strategy] || ['Local', 'Planning, imagery, and rendering stay on this machine.'];
    return '<div class="creation-intro"><span class="creation-icon">' + ICONS.sparkle + '</span><div><h2>Ready to create</h2><p class="hint">Review the intent that will be saved. Nothing executes until you explicitly choose Execute.</p></div></div>' +
      '<div class="local-choice"><div><strong>' + COMPONENTS.esc(strategyInfo[0]) + '</strong><p>' + COMPONENTS.esc(strategyInfo[1]) + '</p></div><span class="badge ' + (d.strategy === 'local' ? 'ok' : 'warn') + '">' + COMPONENTS.esc(d.strategy === 'local' ? 'Free · local tools' : 'Uses paid API') + '</span></div>' +
      '<dl class="detail-list creation-review"><div><dt>Title</dt><dd>' + COMPONENTS.esc(title || '—') + '</dd></div><div><dt>Output</dt><dd>' + COMPONENTS.esc(d.outputIntent === 'tiktok' ? 'TikTok' : 'YouTube Shorts') + '</dd></div><div><dt>Production path</dt><dd>' + COMPONENTS.esc(strategyInfo[0]) + '</dd></div><div><dt>Target duration</dt><dd>' + d.duration + ' seconds</dd></div><div><dt>Scenes</dt><dd>' + d.scenes + ' visual beats</dd></div><div><dt>Brief</dt><dd>' + COMPONENTS.esc(d.brief.trim() || title || '—') + '</dd></div></dl>' +
      '<p class="truth-note">Creation saves the plan only — nothing runs until you choose Execute in the Command Center.</p>' +
      (d.error ? '<p class="inline-error" role="alert">' + COMPONENTS.esc(d.error) + '</p>' : '') +
      '<div class="creation-actions"><button class="btn" data-create-step="back">Back</button><button class="btn btn-primary" data-create-submit>' + ICONS.create + ' Create Video</button></div>';
  },

  renderSuccess: function () {
    var created = VIEWS.create.data.created;
    var strategy = created.generation_strategy || "local";
    var strategyInfo = {
      local: ['Local path selected', 'AVM will plan the story, source real imagery, and render the video locally.', 'Free · local tools'],
      alibaba_image_local_video: ['Alibaba Image → Local Video selected', 'Alibaba will generate the scene images; AVM assembles and renders the final video locally.', 'Uses paid API'],
      alibaba_video: ['Alibaba Video selected', 'Alibaba will generate actual motion-video scene clips; AVM finalizes the video. Unavailability fails loudly — never a silent still-image substitute.', 'Uses paid API']
    }[strategy] || ['Local path selected', 'AVM will plan the story, source real imagery, and render the video locally.', 'Free · local tools'];
    return '<div class="creation-success"><div class="success-mark">' + ICONS.check + '</div><p class="eyebrow">Production intent saved</p><h2>' + COMPONENTS.esc(created.title) + '</h2><p class="page-sub">Your production is saved and ready — nothing runs until you choose Execute.</p><div class="local-choice"><div><strong>' + COMPONENTS.esc(strategyInfo[0]) + '</strong><p>' + COMPONENTS.esc(strategyInfo[1]) + '</p></div><span class="badge ' + (strategy === 'local' ? 'ok' : 'warn') + '">' + COMPONENTS.esc(strategyInfo[2]) + '</span></div><div class="creation-actions"><a class="btn btn-primary" href="#/productions/' + encodeURIComponent(created.production_id) + '">Produce now</a><a class="btn" href="#/productions">Command Center</a></div></div>';
  },

  mount: function () {
    var d = VIEWS.create.data;
    if (d.created) return;
    var title = document.getElementById('f-title');
    var brief = document.getElementById('f-brief');
    var duration = document.getElementById('f-duration');
    var scenes = document.getElementById('f-scenes');
    if (title) title.addEventListener('input', function () { d.title = title.value; });
    if (brief) brief.addEventListener('input', function () { d.brief = brief.value; });
    if (duration) duration.addEventListener('change', function () { d.duration = Number(duration.value); });
    if (scenes) scenes.addEventListener('change', function () { d.scenes = Number(scenes.value); });
    document.querySelectorAll('[data-output]').forEach(function (button) {
      button.addEventListener('click', function () { d.outputIntent = button.getAttribute('data-output'); App.refresh(); });
    });
    document.querySelectorAll('[data-strategy]').forEach(function (button) {
      button.addEventListener('click', function () { d.strategy = button.getAttribute('data-strategy'); App.refresh(); });
    });
    document.querySelectorAll('[data-create-step]').forEach(function (button) {
      button.addEventListener('click', function () {
        if (button.getAttribute('data-create-step') === 'back') { d.error = ''; VIEWS.create.step = 1; App.refresh(); return; }
        if (!d.title.trim() && !d.brief.trim()) { d.error = 'Add a working title or creative brief to continue.'; App.refresh(); return; }
        d.error = ''; VIEWS.create.step = 2; App.refresh();
      });
    });
    var submit = document.querySelector('[data-create-submit]');
    if (submit) submit.addEventListener('click', function () {
      if (!API.live) { d.error = 'The local AVM engine is offline. Start it on this machine, then try again.'; App.refresh(); return; }
      submit.disabled = true;
      API.createProduction({
        mode: 'single_video', creation_method: 'local', title: d.title.trim(),
        brief: d.brief.trim(), output_intent: d.outputIntent,
        target_duration: d.duration, scene_count: d.scenes, tone: d.tone,
        generation_strategy: d.strategy
      }).then(function (created) {
        d.created = created; VIEWS.create.step = 1; App.refresh();
      }).catch(function (error) {
        d.error = error.message || String(error); App.refresh();
      });
    });
  }
};