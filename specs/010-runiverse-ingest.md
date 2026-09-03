# SPEC-010: r-universe ingest and unified repository

Status: draft v0.1 · Phase 2 · Poller Worker + publisher intake path

## Purpose

Bring r-universe-built packages (software; anything on `backend:
r-universe`) into the same ledger, blob store, and served repo — making
backends invisible to users, turning the escrow mirror into the primary
record, and reducing r-universe to a swappable build farm.

## Scope / non-goals

- In scope: polling r-universe state, artifact mirroring, external_publish
  intake path in the publisher, unified fold/serving, drift detection,
  binary handling.
- Non-goals: building software ourselves (registry flip + SPEC-004 profile
  work, deliberately deferred), revdep logic (SPEC-011).

## Poller

Scheduled Worker against `https://bioc.r-universe.dev` (and any other
relevant universes) using r-universe's public APIs (`/api/packages`,
snapshot API):

1. Enumerate current packages/versions per platform; diff against ledger
   fold for the corresponding stream.
2. New/changed version → fetch artifact(s); sha256; upload to staging via
   an internal path (service binding, not the OIDC route); write
   `staged.json` variant with `external` block
   `{backend: "r-universe", source_repo_url, upstream_meta, observed_at}`.
3. Emit `artifact_staged{intake: external}` → publisher.
4. Emit `external_state_observed` events (full universe state hash) for
   drift detection: if r-universe removes/changes a version we've published
   (their devel latest-wins churn), the diff drives supersede/yank
   proposals — devel channel auto-applies latest-wins; release channels
   never auto-yank (human review).

## Publisher intake (extends SPEC-006)

`external_publish` path replaces attestation verification with:
- artifact sha256 matches fetched bytes (poller-declared, publisher
  re-verified);
- registry checks: entry exists, `backend: r-universe`, state/stream/
  component checks as standard (source-repo check compares registry
  git_url to r-universe's upstream URL for the package);
- provenance recorded is honest about its weaker basis: `external` block,
  no attestation. Trust differential is explicit in the ledger, queryable.

## Unified serving (extends SPEC-007)

- Software trees under `repo/<v>/software/` incl. binary contrib paths
  (`bin/windows/contrib/<r>/`, `bin/macosx/<arch>/contrib/<r>/`); fold is
  per (stream × platform) for binaries — ledger `artifact` gains
  `platform` field (schema_version bump to SPEC-001, additive).
- Target: `BiocManager::repositories()` returns only Bioconductor URLs
  (EXT-7.1 becomes blocking for phase-2 exit).
- Rollout: unified repo runs shadow (mirror-complete, correctness-checked
  vs r-universe-served) ≥ 1 release cycle before URL cutover.

## Exit criteria (phase 2)

- 100% of bioc.r-universe.dev artifacts mirrored with ledger records;
  continuous drift check green for 30 days.
- Install-equivalence test matrix: package set installs identically from
  unified repo vs r-universe URLs (source + binaries, 3 platforms).
- Documented, rehearsed runbook: "flip package X to backend: bioc-builder"
  (registry PR → backfill dispatch → published from own build) exercised on
  ≥ 5 real software packages as a drill (then optionally flipped back).

## Open questions

- OQ-10.1: Poll cadence vs r-universe rate limits / courtesy; coordinate
  with rOpenSci — this component is also the natural artifact of a
  "Bioconductor supplies storage" conversation if the 100MB discussion
  reopens.
- OQ-10.2: wasm binaries: mirror or skip initially. Default: mirror
  (cheap; completeness of escrow).
- OQ-10.3: Whether external_publish for binaries should later be
  re-attested by a verification rebuild (spot-check reproducibility) —
  nice phase-3 audit, not required.
