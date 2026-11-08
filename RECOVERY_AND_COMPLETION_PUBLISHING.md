# Resumable vision and explicit completion publishing

## Stage 1

- Stage 1 retains the existing quality-first Gemini/Qwen observer order. The
  selected text storyteller is never used as an image observer or silently changed.
- Real chapter stills are 640px JPEGs. Four frames/request is the default;
  overrides are capped at eight and raw image bytes at 2 MiB/request. Output
  tokens scale with batch size; sockets have independent 45-second budgets.
- Validated timestamp observations survive changed batch boundaries. Cached
  images are not re-extracted or retransmitted; accepted chapter plans are
  revalidated, not regenerated.
- Transient observer failures have a bounded cooldown and exclusive half-open
  probe. A new explicit Resume may retry network/timeout failures, but cannot
  clear an active quota, authentication or capability blocker.
- Provider diagnostics preserve sanitized status/code/request identity and a
  bounded redacted message. A 400 invalid parameter is not a missing model.
- Pro text truncation persists a two-part recovery shape for a subsequent explicit
  Resume. No invocation can purchase a third storyteller completion.

## Export

- Connections are noninteractive. Reauthorization can start only through the
  explicit YouTube/TikTok Connect/Reconnect buttons. In-flight consent is locked,
  has an operation identity, and can be inspected without checking credentials.
- Export entry automatically reads connections and exact-project readiness.
  Readiness never promotes/prepares/uploads or reconciles publication records.
  Credential refresh can rotate its private token storage; provider creator/account
  queries are read-only even if the official API uses POST.
- One click on Publish confirms the displayed account, unit/master and privacy.
  Core's explicit confirmation, acceptance and duplicate-protection checks remain.
  Passive readiness never clears uncertain upload-response guards; explicit
  reconciliation does.

## Auto-Publish on Completion

Default off. Choose platforms and explicitly authorize consent in Summary's
Advanced panel or Export. UI toggles alone never publish. Summary consent binds
its source/model/mode/length recipe and authorizes promotion; an already completed
master may dispatch immediately after this explicit command.

Core stores versioned consent separately from generation intent under
`productions/<production>/units/<unit>/publishing/completion.json`. Consent includes
the account ID, privacy, generation revision and metadata policy. It contains no
OAuth tokens. Prior consent is retained as history; submission claims record the
consent revision and accepted file/metadata fingerprints.

Real assembly/executor completion and a one-shot startup recovery sweep invoke
the shared publishing service. Inspection GETs never start jobs. Claims are durable
before submission; OS/process locks serialize consent and dispatch. Existing
YouTube video/TikTok publish IDs are reconciled, never resubmitted. A claim without
identity remains uncertain until explicit reconciliation. Rebuild/account/metadata
changes invalidate stale authorization. Sandbox is refused. Publication failure
leaves the finished video intact; reconnect is never launched automatically.

YouTube defaults to Unlisted; TikTok defaults to private SELF_ONLY. Scheduling and
Reels remain planned. API-client approval, account privacy/creator limits, and
TikTok's content-sharing/consent requirements still apply; a usable token does not
prove that Direct Post is permitted or that content is public. This implementation
does not claim TikTok audit approval or authorize posting arbitrary third-party content.

## Verification

Core tests must use `tools/run_hermetic_pytest.py`: no inherited provider keys,
dotenv disabled, private/runtime paths isolated, external networking blocked.
The real Dragon Queen incident was resumed with protected chapters, completed as
an 8m46s Pro recap, and was **not published**. Local reports/media are ignored.

`tools/verify_publishing_recovery_ui.py` checks real recap playback/layout on
desktop/mobile and fixture-backed Export connection/readiness behavior. It blocks
all mutation requests; screenshots are written only.