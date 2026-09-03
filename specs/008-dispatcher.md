# SPEC-008: Dispatcher and cron tier

Status: draft v0.1 · Phase 1 (stub acceptable for PoC) · Scheduled Worker

## Purpose

Decide *what to build when*: detect upstream changes for registry packages
on `backend: bioc-builder`, fire `workflow_dispatch` on the build workflow,
and run the scheduled tiers (retry sweep, optional refresh). Holds no
authoritative state — the ledger/catalog answer "last built sha."

## Scope / non-goals

- In scope: change detection, dispatch mechanics, retry sweep, concurrency
  limits, backfill mode.
- Non-goals: dependency-driven scheduling (SPEC-011, and even there
  check-only), building (SPEC-004), r-universe-backend packages (SPEC-010's
  poller owns those).

## Intake paths

Two intakes, one dispatch decision: (a) polling (below, the correctness
baseline), (b) `upstream_push` events from the fast path (SPEC-013),
dispatched immediately through the same memo/idempotence logic. Polling
cadence is adaptive per package: fast-path-covered packages poll at
reconciliation cadence (default 6 h), uncovered at standard cadence.
Coverage status folds from `fastpath_coverage_changed` events (SPEC-013).

## Change detection (polling baseline)

Scheduled run (default every 30 min for uncovered packages) per active
stream:

1. Enumerate registry entries (pinned commit) with `backend: bioc-builder`,
   state ∈ {active, limit_flagged}, stream membership per version range.
2. For each, resolve branch_map → ref; `git ls-remote <git_url> <ref>` to
   get upstream head sha. (Batch with per-host concurrency limit; GitHub
   API alternative acceptable but ls-remote is host-agnostic.)
3. Compare against last-attempted sha from the catalog (query: latest
   `build_started.source.commit` per package/stream — NOT last *published*,
   to avoid re-dispatching known failures every cycle).
4. Changed → `workflow_dispatch(build.yml, {package, stream_id, source_ref,
   registry_commit, policy_version})`; emit `build_dispatched` event.

## Retry sweep (daily)

Packages whose latest terminal event for (package, stream) is
`build_failed` with `stage ∈ {fetch, deps}` (transient classes) and age
< 7 days → re-dispatch, max 3 total attempts per upstream sha. Persistent
failures are the triage surface (phase 3), not the dispatcher's problem.

## Modes

- `backfill`: dispatch every registry package for a stream regardless of
  change state (initial population; policy-version bumps; R version bumps).
  Rate-limited (default 30 concurrent dispatches, honoring GH concurrency).
- `single`: manual dispatch of one package (ops/debug). For the PoC, this
  mode + backfill may be exercised manually before automation lands —
  nothing downstream depends on dispatcher automation.

## Constraints

- Idempotence: dispatching the same (package, stream, sha) twice is
  harmless (build re-stages; publisher dedups by content) but wasteful;
  dispatcher keeps a short-window KV memo to suppress duplicates.
- The dispatcher never reads or writes ledger/repo state directly — catalog
  queries only (seam discipline; also means dispatcher dev needs only
  SPEC-009 fixtures).

## Acceptance criteria

- Synthetic registry of 50 packages: commit to one upstream → exactly one
  dispatch within one cycle; no dispatches for unchanged.
- Failure with transient stage retried per sweep policy; non-transient not
  retried.
- Backfill of PoC package set completes within GH concurrency limits
  without dispatch errors.

## Open questions

- OQ-8.1: ls-remote fan-out cost at 4k packages (phase 2 scale) —
  partially addressed by SPEC-013 adaptive cadence (coverage shrinks the
  30-min polling set). Residual: sharding strategy for the polling sweep
  itself (Worker subrequest limits cap a single invocation near ~900
  ls-remotes; shard by package-name prefix or relocate sweep to an Actions
  cron). Decide at phase-2 package counts.
- OQ-8.2: Where the "policy bump ⇒ backfill which packages?" mapping lives
  (dispatcher config vs policy file annotation). Default: manual backfill
  invocation in PoC; automate in phase 2.
