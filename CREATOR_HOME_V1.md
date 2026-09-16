# CREATOR HOME V1 — Design & Implementation Record

> Approved direction: a premium, TopView-inspired **Full Visual Creator Home** for
> AUTO VIDEO MAKER that preserves the existing production engine, provenance,
> routing semantics, and execution architecture exactly.
> Status: Phases 1–6 implemented and verified (see Validation at the bottom).

## 1. Routing map (approved)

| Route | Surface | Notes |
|---|---|---|
| `#/` | **Creator Home** (Create Hub) | Presentation, navigation, create-handoff only |
| `#/create` | Create Video | Unchanged two-step composer; still the ONLY `POST /api/productions` caller (`mode:"single_video"`) |
| `#/productions` | **Command Center** | Single authoritative production surface; `#/dashboard` remains a legacy alias |
| `#/productions/:id`, `#/productions/:id/units/:unitId` | Production / Unit detail | Unchanged semantics |
| `#/projects`, `#/projects/:id` | Finished Projects | Unchanged data rules; hover-preview upgrade only |
| `#/assets` | **Visual Asset Library** (new, Phase 5) | Read-only, manifest-backed |
| `#/platforms`, `#/activity`, `#/settings` | Distribution / Activity / Settings | Unchanged |

`#/` previously rendered the Command Center (Track 3 Increment 3). That decision is
superseded by this record: Home is now the creator-first entry; the Command Center
keeps every operational responsibility. There is still exactly one execution path:
`ProductionExecutor` behind confirmed Command Center commands.

## 2. Guardrails honored

- **No fabrication.** Every tile claiming real output resolves through existing
  endpoints (`/api/projects`, `/api/projects/:id/media`, `/api/productions*`,
  `/api/connections`, new `/api/projects/:id/assets*`). Failures degrade to honest
  placeholders (`onerror` removal → neutral tile). Demo data stays labelled and
  only appears when the engine is offline.
- **Untouched:** orchestrator, planner, providers, provenance, accounting,
  publishing, manifests, production persistence, six-state machine, create.js
  submission semantics.
- **Home never executes.** Continuity rows link to Command Center detail; execute/
  resume/publish remain confirmed CC actions.
- **Server changes are additive and read-only:**
  - `projects_index()` now includes `binding_production_id` — the production id
    from each manifest's OWN `production_binding`, surfaced only when structurally
    well-formed and self-consistent (`binding.project_id == directory name`).
    Zero extra I/O (manifest already loaded). Full binding validation remains the
    authority of `project_detail`.
  - New asset surfaces: `GET /api/projects/:id/assets` (index),
    `GET /api/projects/:id/assets/:sceneId/media` (project-dir-confined,
    range-aware), `GET /api/projects/:id/assets/:sceneId/verify` (explicit
    on-demand SHA-256 re-hash). POST to asset media/verify → 405.
  - `MIME` gained `.jpg/.jpeg/.webp/.mkv` (delivery completeness).

## 3. UI art policy (webapp/assets/img/ui/)

Files named `ui-*.svg` (future `ui-*.webp/.jpg` allowed) are **designed interface
art** — product design assets like marketing illustrations. They are:

- never production output, never bound to a production/project id;
- never served from media endpoints (static files only);
- never rendered inside Command Center / Projects / Unit / Asset provenance
  surfaces as data;
- marked at every usage site with `data-ui-art="true"` and `aria-hidden` context.

V1 inventory: `ui-stage-hero`, `ui-spot-create`, `ui-spot-paths`,
`ui-spot-publish`, `ui-mode-single`, `ui-mode-multi`, `ui-mode-series`,
`ui-band-quiet`, `ui-cap-assets`, `ui-cap-continuity` (layered SVG: gradient sky,
aurora, beams, silhouettes, grain, vignette; accent `#4f8cff` / violet `#8b7cff`).

Real media on Home/CC/Projects/Assets = `<video>`/`<img>` elements pointing at
manifest-backed endpoints only, with hover-to-play and lazy `preload` swapping
(`none` → `metadata` near viewport). The first frame of a real MP4 is real media —
no poster generation, no thumbnails invented.

## 4. Home composition (`webapp/assets/js/views/home.js` + `assets/css/home.css`)

Zones top→bottom: Z0 ambient stage · Z1 spotlight rail (real finished renders
first, then capability art cards with live counts) · Z2 hero (gradient headline +
glass **prompt instrument**: path/destination/length pills = exact create.js
vocabulary `local | alibaba_image_local_video | alibaba_video`,
`youtube_shorts | tiktok`, `15|30|45|60`) · Z3 shortcut presets · Z4 three
production shapes (Single Film = Live; Multiple Independent = Execution-ready;
Series & Episodic = Roadmap·V1-integration per AVM_MASTER_ROADMAP) · Z5
continuity rows (real FAILED/BLOCKED/IN_FLIGHT/INCOMPLETE productions, read-only,
"Inspect" → CC detail; omitted when nothing needs attention) · Z6 studio rail
(finished films only — `final_video_available` — else quiet band; offline shows
labelled samples) · Z7 distribution strip (real connection states) · Z8 truth
footer.

**Create-handoff contract:** the prompt writes `{brief, strategy, outputIntent,
duration}` into `VIEWS.create.data` (Create's own state; the optional working
title draft is left untouched), resets `created/error/step`, and navigates to
`#/create`. No second creation path exists.

Empty/offline doctrine: art + real capability copy keep Home premium at zero
data; nothing invented fills any zone.

## 5. Phase 4 (real media on operational surfaces)

- **Command Center cards**: a media strip renders ONLY when a project's manifest
  `production_binding` points at this production AND that project reports
  `final_video_available`. Hover plays the real final MP4; click opens the
  project. Six-state chips/badges/confirm dialogs untouched. Verified live:
  16 of 39 productions resolve a real strip (27/38 projects carry valid
  bindings; 22 bound finals).
- **Projects grid**: existing `mediaThumb` real-output previews gained hover-play
  and gentle zoom; no data-rule changes.

## 6. Phase 5 (Visual Asset Library, `#/assets`)

Read-only board over the durable asset manifest
(`app/orchestrator/assets.py`): stored copies, media type, provider, bytes,
SHA-256, `supplied_by/source_provider/source_status/source_metadata` provenance
modal, and explicit per-asset **Verify SHA-256** (re-hash on demand; truthful
pass/fail with reason). No demo assets exist anywhere — offline shows an honest
"Engine offline" panel; projects without manifest entries show "No mapped
assets" (generation-provider scenes legitimately have none).

## 7. Validation (at implementation time)

- `python -m pytest tests/test_web_server.py tests/test_asset_workflow.py
  tests/test_web_publish.py -q` → **39 passed** (4 new web-server tests:
  binding exposure, asset index/media/verify, traversal + escape refusal,
  tamper-truthful verify).
- `node --check` clean on all touched/created JS.
- Live smoke (engine on :8799): index/CSS/JS/10 SVGs → 200 (30/30 static walk,
  0 failures); `/api/projects/:id/assets` → 6 real assets; media → 200
  `image/jpeg` 28,666 bytes; verify → `verified:true` with full SHA-256;
  traversal → blocked; `/api/productions` + `/api/projects` cross-reference →
  16 real CC media strips.
- Accessibility/motion: reveal + hover animations neutralized under
  `prefers-reduced-motion`; rails keyboard-scrollable; pills use `aria-pressed`;
  art is `aria-hidden`/labelled decorative.

Note: an already-running engine instance (started before this change) serves the
OLD payloads until restarted — restart `python -m app.web.server` to pick up
`binding_production_id` and the asset endpoints.

