# SPEC-000: System overview, seams, and phasing

Status: draft v0.2 · Owner: TBD · Last updated: 2026-09-03

## Context

A Bioconductor-owned build and distribution system for packages that r-universe
will not or should not build, designed so that (a) every component can be
developed and replaced independently, (b) the r-universe relationship is a
per-package configuration value rather than an architectural commitment, and
(c) all state is plain-text-inspectable, append-only, and sha256-anchored.

## PoC definition

**Experiment data packages and workflow packages, unified under the new build
system**: both classes built by `bioc-build` (SPEC-004), published through
one ledger (SPEC-001), served from one CRAN-style static repo (SPEC-007) that
`BiocManager::repositories()` can consume. Success = a user installs an
experiment data package and a workflow package from the new repo with stock
`BiocManager`, with full provenance chain verifiable from tarball sha256 back
to upstream commit.

Explicitly out of PoC scope: software packages, binaries, r-universe ingest,
revdep checking, agent triage.

## Components and seams

Every seam is one of three things: **a file in a git repo** (manifest, policy),
**an object in R2** (blobs, ledger, snapshots, indexes), or **an event**
(NDJSON record in the archive). No component calls another component's code.
Components may be developed, tested, and replaced independently so long as
they honor the data contracts in their spec.

| Spec | Component | Phase | Depends on (contracts only) |
|------|-----------|-------|------------------------------|
| 001 | Manifest ledger + snapshots | 1 | — (foundational contract) |
| 002 | Manifest | 1 | 003 (profile names) |
| 003 | Policy + profiles | 1 | — |
| 004 | Build workflows | 1 | 002, 003, 005, 009 |
| 005 | Staging upload service | 1 | — |
| 006 | Publisher | 1 | 001, 002, 003, 005, 009 |
| 007 | Repo serving + BiocManager | 1 | 001 |
| 008 | Dispatcher + cron tier | 1 | 002, 009 |
| 009 | Event plane | 1 (minimal) / 2 (full) | 001 (shares R2 conventions) |
| 010 | r-universe ingest + mirror | 2 | 001, 006, 009 |
| 011 | Revdep check scheduler | 3 | 009, 004 (check harness) |
| 012 | Agent triage | 3 | 009, 002 |
| 013 | Push detection fast path | 2 (Layer 0 opt. 1) | 008, 009 |

## Dependency notes for parallel development

- SPEC-001 (ledger schema) and SPEC-003 (policy schema) are pure data
  contracts with no runtime. Land these first; everything else codes against
  fixtures generated from them.
- SPEC-005 (upload service) has zero dependencies. Prototype first — it is
  the only genuinely novel glue.
- SPEC-004 (build) and SPEC-006 (publisher) never communicate directly. The
  seam is: staged object in R2 + `artifact_staged` event. Build-side dev can
  target a mock staging bucket; publisher-side dev can consume synthetic
  staged objects.
- SPEC-007 (serving) consumes only snapshots. It can be developed against a
  hand-written snapshot fixture before the publisher exists.
- SPEC-008 (dispatcher) writes only `workflow_dispatch` calls and events. It
  can be stubbed with manual dispatch for the entire PoC.

## Trust domains

1. **Governance (human)**: manifest repo + policy repo. All changes via PR.
2. **Build (untrusted)**: public build repo on GitHub-hosted runners. Can
   write only to staging prefix via short-lived presigned URLs. Holds no
   long-lived credentials.
3. **Publish (trusted)**: publisher Worker/DO. Sole writer to published
   prefixes and ledger. Verifies attestations against the manifest before any
   promotion.
4. **Observe (read-mostly)**: event ingest Worker (append-only writes to
   archive prefix), catalog jobs, dashboards. No write access to published
   repo state.
5. **Agents (phase 3, untrusted)**: outputs limited to PRs, issues, and
   events. No staging or publish rights.

## Global conventions

- All timestamps UTC ISO 8601 with `Z`.
- All hashes sha256, lowercase hex, prefixed field names `*_sha256`.
- All NDJSON records carry `schema_version` (string) as first field.
- Canonical JSON serialization for hashing: RFC 8785 (JCS).
- R2 bucket layout: the existing bioc-registry bucket `bioc-prop`, whose live
  prefixes are `prop/{universe}/…` (index, content-addressed artifacts,
  propagation log), `obs/`, `parquet/`, `logs/`, `state/` — see
  bioc-registry's `docs/api.md` "Storage keys". This repo does not invent a
  parallel layout; anything it writes goes under these prefixes or into a
  build repo's own GitHub artifacts (SPEC-014). The `ledger/`, `snapshots/`,
  and `head/` prefixes in SPEC-001 are **phase 2** — not live in `bioc-prop`
  today.
- Stream id format: `<bioc_version>/<component>` (no separate channel
  segment — it is derivable from `bioc_version`: a release number implies
  `release`, the literal `devel` implies `devel`), e.g. `3.23/data-experiment`,
  `devel/workflows`.

## Sequencing

**Phase 1 (PoC)**: 003 → 001 → 005 → {002, 004, 006, 007} in parallel →
008 → minimal 009 (ingest + raw archive only). Exit: PoC definition met for
≥20 real experiment data packages and ≥5 workflow packages across release
and devel streams.

**Phase 2 (unification)**: full 009 (catalog, badges, dashboard), 010
(r-universe ingest → unified fold → single user-facing repo), 013 (push
fast path; Layer 0 bioc-git hook may land in phase 1 if server access
permits). Exit:
`BiocManager` users install software (r-universe-built) and data
(self-built) from Bioconductor-controlled URLs indistinguishably.

**Phase 3 (intelligence)**: 011, 012. Gated on phase 2 catalog maturity.

## Cross-cutting open questions

- OQ-0.1: Single R2 bucket with prefix-scoped API tokens vs. separate buckets
  per trust domain. Default: separate buckets for `staging` vs everything
  else; revisit if token scoping proves sufficient.
- ~~OQ-0.2: Naming.~~ Resolved by bioc-infrastructure
  [ADR 0010](https://github.com/seandavi/bioc-infrastructure/blob/main/adr/0010-two-new-repos-for-the-build-system-and-the-manifest-name.md):
  this system is **bioc-build**; the governance list is the **manifest**
  (bioc-manifest), never "the registry" — "registry" means bioc-registry,
  the data plane.
- ~~OQ-0.3: Which GitHub org hosts build/agent repos.~~ Resolved by ADR 0010:
  no new GitHub org; repos live under `seandavi/`
  (`seandavi/bioc-build`, `seandavi/bioc-manifest`, `seandavi/bioc-registry`).
