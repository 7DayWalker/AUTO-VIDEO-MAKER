/* AUTO VIDEO MAKER — Create Video wizard
   Options mirror REAL engine capabilities (produce.py / OrchestratorConfig):
   topic, duration 10–180s, scene count, voice, target format.
   Generation itself is executed by the local production engine — the UI
   prepares a precise command and never fabricates a "generated" result. */
window.VIEWS = window.VIEWS || {};

VIEWS.create = {
  title: "Create Video",
  step: 1,
  data: { topic: "", notes: "", duration: 30, scenes: 6, voice: "en-US-GuyNeural", format: "youtube_shorts" },

  render: function () {
    const d = VIEWS.create.data;
    const tabs = [1, 2, 3].map(function (n) {
      const names = ["Content", "Video settings", "Generate"];
      const cls = VIEWS.create.step === n ? " active" : VIEWS.create.step > n ? " done" : "";
      return '<span class="step-tab' + cls + '"><span class="n">' +
             (VIEWS.create.step > n ? "✓" : n) + "</span>" + names[n - 1] + "</span>";
    });
    let body;
    if (VIEWS.create.step === 1) {
      body =
        '<div class="field"><label>Topic</label>' +
          '<input class="input" id="f-topic" placeholder="e.g. 5 strange facts about the ocean" value="' + COMPONENTS.esc(d.topic) + '">' +
          '<span class="hint">The AI script planner turns this into scenes and narration.</span></div>' +
        '<div class="field"><label>Optional instructions</label>' +
          '<textarea class="input" id="f-notes" placeholder="Tone, audience, must-include points…">' + COMPONENTS.esc(d.notes) + "</textarea></div>";
    } else if (VIEWS.create.step === 2) {
      body =
        '<div class="field"><label>Target duration</label><div class="choice-grid" id="f-duration">' +
          [15, 30, 45, 60].map(function (v) {
            return '<button type="button" class="choice' + (d.duration === v ? " selected" : "") + '" data-v="' + v + '">' +
                   '<div class="c-title">' + v + " seconds</div><div class=\"c-sub\">Narration-paced</div></button>";
          }).join("") + "</div></div>" +
        '<div class="field"><label>Scenes</label><div class="choice-grid" id="f-scenes">' +
          [4, 6, 8].map(function (v) {
            return '<button type="button" class="choice' + (d.scenes === v ? " selected" : "") + '" data-v="' + v + '">' +
                   '<div class="c-title">' + v + " scenes</div><div class=\"c-sub\">Visual beats</div></button>";
          }).join("") + "</div></div>" +
        '<div class="field"><label>Voice</label><div class="choice-grid" id="f-voice">' +
          [["en-US-GuyNeural", "Guy · US English"], ["en-US-JennyNeural", "Jenny · US English"],
           ["en-GB-RyanNeural", "Ryan · UK English"]].map(function (v) {
            return '<button type="button" class="choice' + (d.voice === v[0] ? " selected" : "") + '" data-v="' + v[0] + '">' +
                   '<div class="c-title">' + ICONS.mic + " " + COMPONENTS.esc(v[1]) + "</div></button>";
          }).join("") + "</div></div>" +
        '<div class="field"><label>Format</label><div class="choice-grid" id="f-format">' +
          [["youtube_shorts", "youtube", "YouTube Shorts", "9:16 vertical"],
           ["tiktok", "tiktok", "TikTok", "9:16 vertical"],
           ["instagram_reels", "instagram", "Instagram Reels", "Planned"]].map(function (v) {
            return '<button type="button" class="choice' + (d.format === v[0] ? " selected" : "") +
                   '" data-v="' + v[0] + '"' + (v[0] === "instagram_reels" ? " disabled" : "") + ">" +
                   '<div class="c-title">' + ICONS[v[1]] + " " + v[2] + "</div>" +
                   '<div class="c-sub">' + v[3] + "</div></button>";
          }).join("") + "</div></div>";
    } else {
      return VIEWS.create.renderStep3(tabs);
    }
    return VIEWS.create.frame(tabs, body);
  },

  frame: function (tabs, body) {
    return (
      '<div class="page-head"><div><h1 class="page-title">Create Video</h1>' +
      '<p class="page-sub">From idea to publish-ready short-form video.</p></div></div>' +
      '<div class="steps">' + tabs.join('<span class="step-sep"></span>') + "</div>" +
      '<div class="card card-pad" style="max-width:760px">' + body + "</div>"
    );
  },

  renderStep3: function (tabs) {
    const d = VIEWS.create.data;
    const cmd = 'python produce.py --topic "' + d.topic.replace(/"/g, "") + '"' +
                " --duration " + d.duration + " --scenes " + d.scenes +
                " --voice " + d.voice + " --platform " + d.format +
                " --flow-project-url <your-flow-project-url>";
    const body =
      '<p style="margin-bottom:12px;color:var(--text-muted)">Review your project, then generate it with the local production engine. ' +
      "Generation runs on this machine using your configured AI providers and video-creation credits.</p>" +
      '<div class="meta-list card card-pad" style="margin-bottom:16px">' +
        "<div class=\"meta-row\"><div class='meta-key'>Topic</div><div>" + COMPONENTS.esc(d.topic || "—") + "</div></div>" +
        "<div class=\"meta-row\"><div class='meta-key'>Length</div><div>" + d.duration + "s · " + d.scenes + " scenes</div></div>" +
        "<div class=\"meta-row\"><div class='meta-key'>Voice</div><div>" + COMPONENTS.esc(d.voice) + "</div></div>" +
        "<div class=\"meta-row\"><div class='meta-key'>Format</div><div>" + COMPONENTS.esc(d.format) + "</div></div>" +
      "</div>" +
      '<div class="field"><label>Engine command</label><div class="code-line" id="engine-cmd">' +
        COMPONENTS.esc(cmd) + "</div>" +
      '<span class="hint">Copy this into a terminal to run the real pipeline. Completed projects appear under Projects.</span></div>';
    return (
      '<div class="page-head"><div><h1 class="page-title">Create Video</h1>' +
      '<p class="page-sub">From idea to publish-ready short-form video.</p></div></div>' +
      '<div class="steps">' + tabs.join('<span class="step-sep"></span>') + "</div>" +
      '<div class="card card-pad" style="max-width:760px">' + body +
        '<div style="display:flex;justify-content:space-between;margin-top:22px">' +
          '<button class="btn" data-step="back">Back</button>' +
          '<button class="btn btn-primary" data-step="copy">' + ICONS.copy + " Copy command</button>" +
        "</div>" +
      "</div>"
    );
  }
};


