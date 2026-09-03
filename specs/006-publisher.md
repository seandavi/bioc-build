# SPEC-006: Publisher

Status: draft v0.2 · Phase 1 · Cloudflare Worker + Durable Object per stream

## Purpose

The sole trusted writer. Consumes `artifact_staged` triggers, verifies the
full chain (attestation → manifest authorization → policy), promotes blobs
into the content-addressed store, appends ledger records, folds, writes
snapshots + HEAD, and regenerates repo indexes. ~Small by design; every
check enumerable.

A bioc-build artifact enters bioc-registry's *existing* content-addressed
store and served repo (SPEC-000 "What bioc-registry already provides") —
this component does not stand up a new store or a new repo tree. The
"~2k LoC" budget below is lines *added* to bioc-registry, not the size of
a new service.

## Scope / non-goals

- In scope: promotion pipeline, verification checks, ledger append
  mechanics, snapshot/HEAD writes, index regeneration trigger, rejection
  path, freeze/yank admin operations.
- Non-goals: build (SPEC-004), serving (SPEC-007 — publisher writes the
  repo tree; serving spec owns layout/URL contract), external ingest
  (SPEC-010 adds a second intake path to this same component).

## Architecture

**Phase 1: see SPEC-014.** No Worker front, no per-stream Durable Object,
no Cloudflare Queues. `publish.yml`, a cron in the trusted bioc-registry
repo, scans `gh run list --workflow build.yml --status completed` for
`staged-*` artifacts not yet recorded in `attempts.json` (issue #4: staging
is already the durable record, and the publisher already backfills every
run, so a push-triggered queue would duplicate work the polling loop does
anyway). This closes OQ-6.1 in favor of the simpler polling design.
`publish.yml` reuses bioc-registry's existing digest-keyed, idempotent
Cloudflare Workflows pattern (bioc-infrastructure ADR 0007) rather than
introducing Queues.

## Target architecture (phase 2+): architecture

- Stateless Worker front: receives triggers (queue consumer on
  `artifact_staged` events, plus `POST /v1/admin/*` for freeze/yank behind
  Cloudflare Access), routes to the stream's DO.
- One DO per stream id: serializes all ledger writes for that stream, holds
  `{next_seq, prev_sha256, head}` as authoritative in-memory/durable state,
  reconstructible by ledger replay (startup self-check verifies DO state
  against segment tail).

## Promotion pipeline (per staged run)

1. Read `staging/<run_id>/staged.json`; verify its sha256 matches the
   `artifact_staged` event's `staged_manifest_sha256`.
2. Download tarball from staging; recompute sha256 == declared. Extract
   DESCRIPTION; compute dcf_sha256; parse fields.
3. Verify attestation: phase 1 (SPEC-014) runs `gh attestation verify
   <tarball> --repo seandavi/bioc-build --signer-workflow
   seandavi/bioc-build/.github/workflows/build.yml` in the scheduled
   Action — sigstore verification is free there, so it never has to run
   inside a Worker (closes OQ-6.2: no Workers-runtime sigstore library to
   validate). Subject digest == tarball sha256; certificate identity
   claims: repository == `seandavi/bioc-build`, workflow == `build.yml`,
   and `source` claims consistent with staged.json `{git_url, commit}`.
4. Manifest checks 1–7 verbatim from SPEC-002 at the publisher's pinned
   manifest commit (recorded in the ledger record).
5. Policy consistency: staged `policy_version` is current-or-recent
   (configurable window, default: current or previous) for the stream; if
   the version is unseen in this stream's ledger, first append a `policy`
   record.
6. Copy blob staging → `blobs/sha256/<aa>/<sha>` (skip if exists — dedup).
   Same for attestation bundle (stored under its own sha).
7. Append `publish` record (SPEC-001 schema; seq/prev from DO state).
8. Fold incrementally (DO holds current fold), write canonical snapshot
   JSON (Parquet twin cut, issue #4 — JSON only), rewrite HEAD atomically.
9. Regenerate index files for the stream (delegated to the index writer
   module defined in SPEC-007; runs in-publisher).
10. Emit `published{package, version, sha256, seq}` event.

Any check failure: emit `publish_rejected{run_id, failed_check,
detail}` event; leave staging intact for the TTL (forensics); no ledger
write. Rejections are events, not ledger records.

## Admin operations

- `freeze(stream, label)`: fold, write snapshot, append `freeze` record.
- `yank(stream, package, version, reason, reference)`: append, refold,
  reindex. Both require Cloudflare Access identity; identity recorded in
  `recorded_by`.
- Post-freeze release publishes require `exception` fields in the admin
  trigger (normal queue-path publishes to a frozen release are rejected).

## Failure and consistency model

- DO serialization ⇒ no concurrent seq assignment. Crash between ledger
  append and HEAD write ⇒ startup replay detects HEAD behind tail, redoes
  fold/snapshot/index idempotently (all derived writes are
  deterministic-by-content).
- Ledger segment writes: append to open segment object via
  read-modify-write within DO (single writer makes this safe); rotate per
  SPEC-001.
- Queue redelivery: idempotency key = staged_manifest_sha256; a second
  delivery after successful publish is a no-op (detected via DO-held recent
  set + ledger scan fallback).

## Acceptance criteria

- Property test: pipeline over fixture corpus (valid + each violation
  class) produces exactly the SPEC-001 fixtures' expected ledgers;
  `ledger-verify` recomputes and matches (SPEC-001 criterion, issue #4 —
  one fold implementation plus a verifier, not two implementations).
- Kill-at-every-step crash test recovers to consistent state with no
  duplicate seq and correct HEAD.
- End-to-end: real build (SPEC-004) → published; tampered tarball in
  staging → `publish_rejected{attestation}`.
- Total lines added to bioc-registry for this pipeline ≲ 2k LoC excluding
  generated validators (guard against scope creep — if it grows past this,
  something is in the wrong component).

## Open questions

- ~~OQ-6.1: Cloudflare Queues vs direct event-plane consumer.~~ Closed
  (issue #4): phase 1 uses neither — a cron scanning the staging prefix
  (SPEC-014). Queues remain a phase-2+ option if push-triggered promotion
  latency is ever needed.
- ~~OQ-6.2: Sigstore verification inside a Worker.~~ Closed (issue #4):
  phase 1 never verifies inside a Worker — `gh attestation verify` runs in
  the scheduled Action, where it's free (SPEC-014). The Workers-runtime
  sigstore-library question doesn't need answering for phase 1.
