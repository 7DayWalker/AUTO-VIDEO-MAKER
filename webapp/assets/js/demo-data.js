/* ============================================================
   AUTO VIDEO MAKER — DEMO DATA
   Clearly-labelled sample content used ONLY when the local AVM
   engine/bridge is unreachable. Never mixed with real account
   or publishing state. Replace-by-API is automatic.
   ============================================================ */

window.DEMO_DATA = {
  projects: [
    {
      project_id: "sample_ocean_facts_001",
      topic: "5 strange facts about the ocean",
      created_at: "2026-08-21T09:14:00",
      sample: true,
      stage_status: "complete",
      final_video_available: true,
      publications: [
        { platform: "youtube", state: "VERIFIED", sample: true },
        { platform: "tiktok", state: null, sample: true }
      ]
    },
    {
      project_id: "sample_plants_sleep_002",
      topic: "Why some plants sleep at night",
      created_at: "2026-08-22T16:40:00",
      sample: true,
      stage_status: "complete",
      final_video_available: false,
      publications: [ { platform: "tiktok", state: "PREPARED", sample: true } ]
    },
    {
      project_id: "sample_desert_survival_003",
      topic: "How deserts survive without rain",
      created_at: "2026-08-23T11:05:00",
      sample: true,
      stage_status: "running",
      final_video_available: false,
      publications: []
    }
  ],
  activity: [
    { platform: "youtube", project_topic: "5 strange facts about the ocean",
      state: "VERIFIED", at: "2026-08-21T12:30:00", sample: true },
    { platform: "tiktok", project_topic: "Why some plants sleep at night",
      state: "PREPARED", at: "2026-08-22T17:02:00", sample: true }
  ],
  connections: {
    youtube: { status: "CONNECTED", account: "Sample Channel", sample: true },
    tiktok:  { status: "NOT CONNECTED", sample: true },
    instagram: { status: "COMING SOON" },
    facebook:  { status: "COMING SOON" }
  }
};
