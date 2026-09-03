# SPEC-001: Manifest ledger and snapshots

Status: draft v0.1 · Phase 1 · Pure data contract (no runtime component)

## Purpose

Define the authoritative record of what has been published: an append-only,
hash-chained NDJSON **ledger** per stream, and derived, reproducible
**snapshots** (the folded current state) from which repository indexes are
generated. The ledger is the source of truth; snapshots and PACKAGES files
are caches.

## Scope / non-goals

- In scope: record schemas, hash-chain rules, fold semantics, snapshot
  serialization, HEAD pointer, storage layout.
- Non-goals: who writes the ledger (SPEC-006), how snapshots become a served
  repo (SPEC-007), event archive records (SPEC-009 — related but distinct;
  ledger records are publication facts, events are operational telemetry).

## Ledger

One logical ledger per stream id (SPEC-000 format). Physical storage:
NDJSON segment objects `ledger/<stream-id>/<seq-start>-<seq-end>.ndjson`,
segment rotation at 10,000 records or 64 MB, whichever first. Segments are
immutable once a later segment exists.

### Common envelope (all record types)

```json
{
  "schema_version": "1",
  "seq": 4217,
  "prev_sha256": "<sha256 of JCS serialization of record seq-1>",
  "record_type": "publish",
  "stream": {"bioc_version": "3.22", "component": "data-experiment", "channel": "release"},
  "recorded_at": "2026-09-03T18:42:11Z",
  "recorded_by": {"actor": "publisher", "version": "<publisher release>"}
}
```

- `seq` starts at 1 per stream; strictly increasing, no gaps.
- `prev_sha256` for seq 1 is the sha256 of the stream id string.
- Chain verification MUST be possible with only the segment objects.

### Record types

**publish** — adds/updates a package version in the fold.
Additional fields: `package {name, version}`, `artifact {sha256, size_bytes,
filename, media_type}`, `source {git_url, commit, ref}`, `build
{workflow_run_url, run_attempt, runner_image, policy_version, attestation
{type, bundle_sha256}}`, `description {depends[], imports[], suggests[],
enhances[], linking_to[], license, needs_compilation, dcf_sha256}`.
Parsed `description` fields are advisory; `dcf_sha256` pins ground truth
(the raw DESCRIPTION inside the blob).

**external_publish** (phase 2) — same shape as publish; `build` replaced by
`external {backend: "r-universe", source_repo_url, observed_at, upstream_meta_sha256}`.
Artifact sha256 refers to a blob mirrored into the content-addressed store.

**yank** — removes `package {name, version}` from the fold. Blob retained.
Fields: `reason` (free text), `reference` (URL: issue/PR/advisory).

**supersede** — devel-channel only. Marks `package {name, old_version}` as
replaced by `new_version` (which must have its own publish record). Exists to
distinguish replacement from coexisting versions in audit queries; fold
effect identical to the new publish under latest-wins.

**freeze** — pins a release. Fields: `snapshot_sha256`, `as_of_seq`,
`label` (e.g. "3.22 branch point"). After a freeze on a `release` channel,
subsequent publishes require `exception {approved_by, reference}`.

**policy** — records that policy_version changed for this stream. Fields:
`policy_version`, `policy_sha256` (of the policy file at that version),
`reference` (PR URL). Self-describes what rules were in force per seq range.

### Fold semantics

Fold(stream, up_to_seq) → map of package name → publish record:

1. Process records in seq order, ≤ up_to_seq.
2. `publish`/`external_publish`: channel `release` → insert; replacing an
   existing (name) entry is permitted only pre-freeze or with `exception`.
   Channel `devel` → latest-wins by seq (not by version comparison).
3. `yank`: remove (name) if current entry's version matches; else no-op with
   verifier warning.
4. `freeze`, `policy`, `supersede`: no fold effect.

The fold MUST be deterministic: same segments → byte-identical snapshot.

## Snapshots

- Canonical JSON: JCS-serialized array of folded publish records, sorted by
  package name. Stored at `snapshots/<sha256-of-itself>.json`.
- Parquet twin at `snapshots/<sha256>.parquet`, one row per package,
  `description` fields columnarized. Same sha in filename refers to the JSON
  canonical form; Parquet carries it as metadata.
- `head/<stream-id>.json`: `{"schema_version":"1","snapshot_sha256":"…",
  "ledger_seq":4217,"updated_at":"…"}`. The ONLY mutable object. Rollback =
  rewrite HEAD to a prior snapshot; reproduce any state = fold to seq.

## Validation tooling (deliverables of this spec)

- LinkML model → generated JSON Schema + Python/TS validators.
- `ledger-verify` CLI: chain integrity, seq continuity, fold determinism,
  snapshot recomputation vs HEAD. Target: < 20 s for 100k records.
- Fixture generator producing synthetic ledgers for downstream dev
  (consumed by SPEC-004/006/007 test suites).

## Acceptance criteria

- Two independent fold implementations (Python + the publisher's TS) produce
  byte-identical snapshots over the fixture corpus.
- Chain tamper (any byte in any historical record) detected by verifier.
- Freeze + post-freeze exception flow round-trips in fixtures.

## Open questions

- OQ-1.1: Should `external_publish` blobs be mandatory-mirrored (escrow) or
  optionally metadata-only? Default: mandatory (see SPEC-010 rationale).
- OQ-1.2: Multi-version retention in release folds (CRAN-style archive dir)
  — current design keeps one current version per name in fold; archive
  access is via ledger history + blobs. Confirm this satisfies
  `install.packages` version-pinning use cases or add archive index to
  SPEC-007.
