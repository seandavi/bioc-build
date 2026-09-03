# SPEC-001: Manifest ledger and snapshots

Status: draft v0.2 · Phase 1 · Pure data contract (no runtime component)

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
  "stream": {"bioc_version": "3.23", "component": "data-experiment"},
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

~~**supersede**~~ — cut (issue #4): its fold effect was already identical to
the new `publish` under devel's latest-wins rule, so it recorded nothing a
`publish` record doesn't already carry. Audit queries that want "what
replaced what" on devel read consecutive `publish` records for a name, not
a dedicated record type.

**freeze** — pins a release stream. Fields: `snapshot_sha256`, `as_of_seq`,
`label`. After a freeze, subsequent publishes to that stream require
`exception {approved_by, reference}`. See "Release lifecycle" below for
*which* stream a freeze record applies to and when it fires — it is not
the stream being branched.

**policy** — records that policy_version changed for this stream. Fields:
`policy_version`, `policy_sha256` (of the policy file at that version),
`reference` (PR URL). Self-describes what rules were in force per seq range.

### Release lifecycle (normative)

Bioconductor's lifecycle has four states, not the two ("release" vs
"devel") the record types above might suggest — issue #5, this spec's
earlier `label` example ("3.23 branch point" on the freeze it pins) named
the wrong event as the freeze:

1. **devel** — `bioc_version: "devel"`. Always open, latest-wins fold,
   never freezes.
2. **branch** — the branch-point event, roughly twice a year. Devel's
   current content becomes the seed of a new release stream (e.g. `3.23`);
   devel itself is unaffected and keeps moving toward the *next*
   `bioc_version`.
3. **open release** — the just-branched stream (`3.23`, immediately after
   branching). It *opens*, not freezes, for roughly six months of ordinary
   patch-level `publish` records (patch-level is a Bioconductor/manifest
   convention, not enforced by the ledger).
4. **frozen old release** — the *previous* release stream (`3.22`) is what
   actually freezes, at the same branch-point event that opens `3.23`. A
   `freeze` record is appended to *its* ledger (`3.22`'s), not `3.23`'s.
   `label` should describe that: e.g. `"frozen at the 3.23 branch point"`,
   not `"3.23 branch point"` — the label names what happened to *this*
   stream, not the sibling event that triggered it.

SPEC-006's `freeze(stream, label)` admin op and SPEC-007's "frozen
releases" serving semantics both freeze the outgoing release stream at a
branch point, per this lifecycle — see those specs for the write and
serving mechanics.

### Fold semantics

Fold(stream, up_to_seq) → map of package name → publish record:

1. Process records in seq order, ≤ up_to_seq.
2. `publish`/`external_publish`: a release-type stream (`bioc_version` a
   release number, e.g. `"3.23"`) → insert; replacing an existing (name)
   entry is permitted only pre-freeze or with `exception`. The `devel`
   stream (`bioc_version == "devel"`) → latest-wins by seq (not by version
   comparison).
3. `yank`: remove (name) if current entry's version matches; else no-op with
   verifier warning.
4. `freeze`, `policy`: no fold effect.

The fold MUST be deterministic: same segments → byte-identical snapshot.

## Snapshots

- Canonical JSON: JCS-serialized array of folded publish records, sorted by
  package name. Stored at `snapshots/<sha256-of-itself>.json`. JSON only for
  phase 1 (issue #4 cut) — DuckDB reads JSON natively, so a Parquet twin
  buys nothing until snapshot volume justifies columnar storage; add it in
  phase 2 if profiling shows JSON scan cost matters.
- `head/<stream-id>.json`: `{"schema_version":"1","snapshot_sha256":"…",
  "ledger_seq":4217,"updated_at":"…"}`. The ONLY mutable object. Rollback =
  rewrite HEAD to a prior snapshot; reproduce any state = fold to seq.

## Validation tooling (deliverables of this spec)

- LinkML model → generated JSON Schema + one validator (issue #4 cut: one
  implementation, not dual Python + TS — the fold is small and pure enough
  that a second from-scratch implementation buys correctness confidence a
  verifier gets more cheaply).
- `ledger-verify` CLI: chain integrity, seq continuity, fold determinism,
  snapshot recomputation vs HEAD — this verifier *is* the cross-check
  (independently recomputes the fold from segments and diffs against the
  implementation's snapshot + HEAD, rather than requiring a second
  from-scratch fold implementation to agree byte-for-byte). Target: < 20 s
  for 100k records.
- Fixture generator producing synthetic ledgers for downstream dev
  (consumed by SPEC-004/006/007 test suites).

## Acceptance criteria

- `ledger-verify` recomputes the fold from segments and matches the
  implementation's snapshot + HEAD byte-for-byte over the fixture corpus.
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
