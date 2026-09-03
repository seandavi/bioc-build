# SPEC-013: Push detection fast path

Status: draft v0.1 · Phase 2 (Layer 0 optionally phase 1) · Hook + GitHub App + receiver Worker

## Purpose

Reduce build-trigger latency from polling cadence (~30 min) to seconds and
cut polling volume, via push notifications from upstreams — without ever
making webhooks a correctness dependency. Polling (SPEC-008) remains the
reconciliation baseline; this spec is purely a latency/cost optimization
layer.

## Design principle (normative)

Detection correctness MUST NOT depend on any component operated by a
maintainer or third party. Every fast-path signal is advisory; the polling
sweep must independently converge to the same dispatch decisions. A total
fast-path outage degrades latency only.

## Layer 0: git.bioconductor.org post-receive hook

- Server-side post-receive hook on the Bioconductor git server POSTs
  `{package, ref, old_sha, new_sha, pushed_at}` to the receiver Worker
  (shared-secret HMAC; secret held in bioc-git server config and Worker
  env).
- Covers every package with zero maintainer action (Bioconductor operates
  the upstream). For registry entries whose `source.git_url` is bioc-git,
  this is complete coverage; for GitHub-canonical packages it still
  catches pushes flowing through bioc-git (e.g. release branches).
- May land in phase 1 if bioc-git server access is straightforward;
  otherwise phase 2 with the App.

## Layer 1: GitHub App ("Bioconductor Builder")

- App configuration (one-time, by Bioconductor): centrally declared
  webhook URL → receiver Worker; subscribes to `push` events; permissions:
  `contents: read`, `metadata: read`. Nothing else.
- Maintainer action: Install → select repos (or whole org). Two clicks.
  Org-level install covers all repos in the org.
- Receiver verifies GitHub App webhook HMAC (X-Hub-Signature-256), maps
  repo → registry package(s) (via `source.git_url` index), filters pushed
  ref against resolved branch_map for active streams, and emits
  `upstream_push{package, stream_ids[], ref, sha, via: "github-app"}`.
  Non-registry repos and non-tracked refs are dropped silently.
- Installation lifecycle events (`installation`,
  `installation_repositories`) are emitted as `fastpath_coverage_changed`
  events → catalog maintains per-package coverage status.

## Receiver Worker

- Single Worker, two auth schemes (bioc-git HMAC, GitHub App HMAC); ~100
  lines + shared event-emission. Emits `upstream_push` into the event plane
  (SPEC-009) and forwards to the dispatcher fast path (service binding or
  queue, mirroring the OQ-6.1 decision).
- Idempotence: (package, stream, sha) dedup via short-window KV memo
  (shared convention with SPEC-008 dispatch memo).

## Dispatcher integration (amends SPEC-008)

- Fast path: on `upstream_push`, dispatch immediately (same memo
  suppression, same inputs as polled dispatch).
- Adaptive polling cadence keyed on coverage: covered packages (Layer 0 or
  App-installed) poll at reconciliation cadence (default 6 h); uncovered
  packages poll at standard cadence (30 min). Coverage status from
  catalog (`fastpath_coverage_changed` fold).
- Reconciliation property: a dropped webhook is detected by the next
  poll of that package; no state distinguishes "webhook missed" from
  "no fast path" — by design.

## Adoption surfaces (informative)

Ambient, not campaigned: install link in new-package submission checklist;
dashboard package page prompt showing trigger-latency benefit; self-test
workflow README; footer of agent-filed issues (phase 3). Coverage % is a
standing catalog query; adoption directly reduces polling volume, so the
system's own economics reward it, but no outcome depends on it.

## Acceptance criteria

- Push to a covered GitHub repo → `build_dispatched` within 60 s (p95).
- Layer 0: push to bioc-git → same, with zero maintainer configuration.
- Kill the receiver for 24 h → all changed packages still dispatched by
  reconciliation sweep; no permanent divergence (chaos test).
- Forged webhook (bad HMAC, unknown repo, untracked ref) → dropped, logged,
  no dispatch.
- Coverage report query returns correct status for install/uninstall
  fixture sequence.

## Open questions

- OQ-13.1: bioc-git hook deployment path — who administers the git server,
  and change-management process for server-side hooks. Determines whether
  Layer 0 is phase 1 or 2.
- OQ-13.2: GitLab/other-host upstreams — App pattern is GitHub-only;
  generic webhook config offered as documented manual option for the long
  tail, or accept polling-only for non-GitHub. Default: polling-only,
  revisit on demand.
- OQ-13.3: Whether `upstream_push` for a package on `backend: r-universe`
  should do anything (e.g. schedule an expectation timer for the SPEC-010
  poller). Default: event recorded, no action.
