# SPEC-003: Policy file and build profiles

Status: draft v0.2 · Phase 1 · Versioned config (no runtime)

## Purpose

A single versioned policy file defining build profiles — the bundles of
build matrix, check set, severities, resource envelope, and rebuild triggers
that replace "which universe a package lives in." Policy changes are PRs;
every ledger publish record cites the `policy_version` it was built under,
making policy part of provenance.

## Scope / non-goals

- In scope: policy file schema, profile definitions for `data-experiment`
  and `workflows` (PoC), override schema, versioning rules.
- Non-goals: enforcement (build workflow SPEC-004 consumes; publisher
  SPEC-006 records), software/binary profiles (phase 2+ draft included as
  non-normative appendix).

## Location and versioning

`policy/policy.yaml` in the manifest repo (same governance, one PR can
coherently change profile + affected package overrides). `policy_version`
is CalVer `YYYY.MM.patch`, bumped by CI on any change to the file; the file
embeds its own version. Publisher records `policy_version` +
`policy_sha256`; a change lands in each affected stream's ledger as a
`policy` record (SPEC-001) emitted by the publisher on first observation.

## Schema

```yaml
schema_version: "1"
policy_version: "2026.09.1"
defaults:
  runner_envelope:
    runner: ubuntu-24.04
    max_wall_minutes: 340          # under GH 360 ceiling with margin
    max_disk_gb: 13
  staging_ttl_days: 14
profiles:
  data-experiment:
    build:
      targets: [source]            # arch-independent; no binaries
      r_versions: [release, devel] # per-stream mapping resolved by workflow
      vignettes: build
    checks:
      - {id: rcmdcheck, args: "--no-manual", fail_on: error}
      - {id: bioccheck, fail_on: error, warn_promote: []}
      - {id: size_report, fail_on: never}   # always record, never fail
    rebuild:
      on_push: true
      on_dependency_invalidation: false
      scheduled_refresh: monthly
    envelope: inherit
  workflows:
    build:
      targets: [source]
      r_versions: [release, devel]
      vignettes: build             # vignettes ARE the product; never skip
    checks:
      - {id: rcmdcheck, args: "--no-manual --run-donttest", fail_on: error}
      - {id: bioccheck, fail_on: warning_off}   # BiocCheck advisory only
      - {id: size_report, fail_on: never}
    rebuild:
      on_push: true
      on_dependency_invalidation: false        # phase 3 revisits
      scheduled_refresh: monthly
    envelope:
      max_wall_minutes: 340        # explicit: workflow vignettes are slow
overrides_schema:                  # what manifest per-package overrides may set
  allowed_keys: [checks[].fail_on, envelope.max_wall_minutes, build.vignettes]
```

## Semantics

- Layering: defaults → profile → manifest per-package `policy.overrides`.
  Only `overrides_schema.allowed_keys` may be overridden; manifest CI
  enforces (SPEC-002).
- `runner_envelope` is the published maintainer contract ("your package must
  build within standard GitHub Actions limits"); `size_report` check emits
  disk high-water-mark and tarball size as events (SPEC-009) feeding the
  limit_flagged trend query. There is NO hard tarball size limit in policy —
  the envelope (disk/time) is the limit.
- `fail_on` values: `error | warning | never | warning_off` (run, record,
  never gate).
- Workflow packages: BiocCheck advisory reflects current practice; revisit
  after PoC data (OQ-3.2).

## Deliverables

- LinkML model + generated validators (shared codegen pipeline with
  SPEC-001/002).
- Resolver library (`resolve_policy(profile, overrides) -> effective`) in
  Python and TS, property-tested for layering determinism; consumed by
  SPEC-004 (workflow) and SPEC-006 (recording).

## Acceptance criteria

- Effective-policy resolution identical across both implementations on
  fixture corpus.
- CalVer bump automation: PR changing policy.yaml without version bump is
  rejected; merged bump appears as `policy` ledger record in a test stream.

## Open questions

- OQ-3.1: r_version → stream mapping table location (here vs SPEC-004).
  Default: here, as `r_map: {devel: "R-devel", "3.23": "4.6"}` block.
- OQ-3.2: Should workflow-package check severity be stricter than BBS
  historical practice? Gather PoC failure data first.
- OQ-3.3: `scheduled_refresh: monthly` — needed at all for source-only
  profiles? Only value is re-check against moved dependencies; may drop in
  favor of phase-3 revdep checks.
