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

| Spec | Component | Phase | Depends on (contracts only) | Phase-1 realization |
|------|-----------|-------|------------------------------|----------------------|
| 001 | Manifest ledger + snapshots | 1 | — (foundational contract) | SPEC-014 §bioc-registry: `prop/{u}/log/` records **are** the ledger, `prop/{u}/index.json` **is** the fold |
| 002 | Manifest | 1 | 003 (profile names) | SPEC-014 §bioc-manifest: `packages/<name>.yaml` |
| 003 | Policy + profiles | 1 | — | SPEC-014 §bioc-manifest: `policy.yaml` |
| 004 | Build workflows | 1 | 002, 003, 005, 009 | SPEC-014 §bioc-build: `build.yml` / `selftest.yml` |
| 005 | Staging upload service | 1 | — | SPEC-014: GitHub Actions artifacts (`retention-days: 14`) — no presign Worker |
| 006 | Publisher | 1 | 001, 002, 003, 005, 009 | SPEC-014 §bioc-registry: `publish.yml` cron + `POST /publish` |
| 007 | Repo serving + BiocManager | 1 | 001 | SPEC-014: existing `bioc`/`bioc-release` universes, existing `/repo/{u}` |
| 008 | Dispatcher + cron tier | 1 | 002, 009 | SPEC-014 §bioc-build: `dispatch.yml` |
| 009 | Event plane | 1 (minimal) / 2 (full) | 001 (shares R2 conventions) | SPEC-014: `events.ndjson` inside the staged artifact — no ingest Worker |
| 010 | r-universe ingest + mirror | 2 | 001, 006, 009 | — (phase 2; bioc-registry's `/poll` already runs the phase-1-relevant part, see below) |
| 011 | Revdep check scheduler | 3 | 009, 004 (check harness) | — (phase 3) |
| 012 | Agent triage | 3 | 009, 002 | — (phase 3) |
| 013 | Push detection fast path | 2 (Layer 0 opt. 1) | 008, 009 | — (phase 2; SPEC-008's `ls-remote` polling covers phase 1) |

## What bioc-registry already provides

This repo builds and stages; it never runs its own store, index, or served
repo. bioc-registry (`seandavi/bioc-registry`) already has all of that
running for r-universe-built packages, and phase 1 (SPEC-014) reuses it
directly rather than re-specifying it:

| Already running in bioc-registry | What this repo adds |
|---|---|
| Content-addressed artifact store, `prop/{universe}/cas/{sha256}` | New artifacts — from packages `git.bioconductor.org` hosts that no r-universe build produces |
| Propagated index, a fold of observations, `prop/{universe}/index.json` | `origin: bioc-build` entries in that same fold (SPEC-014 `POST /publish`) |
| Parquet archive of build/check history, queried from DuckDB (`parquet/jobs/…`) | A comparable per-run record for bioc-build runs (phase 1: `events.ndjson` inside the staged artifact, SPEC-014; phase 2: full SPEC-009 catalog) |
| Served `/repo/{universe}/…` with `PACKAGES`/`VIEWS`, immutable CAS objects behind the CDN | Nothing new — bioc-build entries land in the existing served repo (`bioc`, `bioc-release`) via the existing route |
| r-universe poller (`/poll`) | Nothing — bioc-build packages never go through r-universe |
| Digest-keyed, idempotent Cloudflare Workflows (bioc-infrastructure ADR 0007) | Reuses the pattern: `publish.yml` (SPEC-014) is a cron, not a queue, but is idempotent and self-healing the same way |

Per ADR 0010, the Worker-shaped components this repo's earlier specs
describe as new (SPEC-005 presign, SPEC-006 publisher, SPEC-008 dispatcher,
SPEC-009 ingest) land as additions *inside* bioc-registry or as GitHub
Actions in the trusted repo, not as new standalone services — see SPEC-014.
The R2 prefixes above stay the contract between bioc-build and
bioc-registry regardless of which side of that line a given check runs on.

Results also need to reach the website: bioc-website renders landing pages
and check-result pages from bioc-registry's data plane, not from this
repo's event archive directly (`docs/DATAPLANE.md` in bioc-registry —
producers publish plain, documented, immutable-where-possible artifacts
over HTTP GET; the SPEC-009 dashboard is a separate, additional SPA, not
the delivery mechanism to bioc-website). A bioc-build result becomes
visible on bioc-website by landing under that contract (extending
`parquet/jobs/…` or a sibling prefix), not by any new
bioc-build-owned surface.

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

**Phase 1 (SPEC-014, issue #18): there is no separate staging trust
domain.** Staging is a GitHub Actions artifact of the untrusted build
repo (`retention-days: 14`); verification (attestation + manifest) happens
in the trusted repo's own CI (`bioc-registry`'s `publish.yml`) before
promotion, not in a dedicated publisher service sitting between build and
store. The domains below still hold as a model of *who can do what*; where
phase 1 realizes a domain differently than the phase-2+ target, both are
noted.

1. **Governance (human)**: manifest repo (`bioc-manifest`) + policy file
   (`policy.yaml`, in the same repo for phase 1 — SPEC-003). All changes
   via PR.
2. **Build (untrusted)**: public `bioc-build` repo on GitHub-hosted
   runners. Phase 1: writes only its own run's GitHub Actions artifact —
   no bucket credential or write path exists at all, staging or otherwise.
   Phase-2 target: write only to a staging prefix via short-lived
   presigned URLs (SPEC-005). Holds no long-lived credentials either way.
3. **Publish (trusted)**: phase 1: `publish.yml`, a cron in the
   `bioc-registry` repo (which already holds the R2 secrets) — not a
   separate publisher Worker/DO. It downloads the build repo's staged
   artifacts (`gh run download`), verifies attestation and manifest state,
   and is the sole writer to `bioc-prop`'s published prefixes. Phase-2
   target: publisher Worker/DO, sole writer to published prefixes and
   ledger, verifying attestations against the manifest before promotion
   (SPEC-006).
4. **Observe (read-mostly)**: phase 1: no separate domain — no ingest
   Worker exists (issue #4 cut); events travel inside the staged artifact
   and are read alongside it during promotion. Phase-2 target: event
   ingest Worker (append-only writes to archive prefix), catalog jobs,
   dashboards (SPEC-009). No write access to published repo state either
   way.
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

**Phase 1 (PoC)**: as originally planned, the build order below; as
actually realized (SPEC-014, issue #18), most of it is reuse rather than
new construction — `bioc-manifest` (002/003) → `bioc-build`'s
`build.yml`/`selftest.yml` (004) → `dispatch.yml` (008) →
`bioc-registry`'s existing `publish.yml` cron + `POST /publish`, which
absorbs what 001/005/006/007/009 describe as separate components.
Original from-scratch order: 003 → 001 → 005 → {002, 004, 006, 007} in
parallel → 008 → minimal 009 (ingest + raw archive only). Exit: PoC
definition met for ≥20 real experiment data packages and ≥5 workflow
packages across release and devel streams.

**Phase 2 (unification)**: full 009 (catalog, badges, dashboard), 010
(r-universe ingest → unified fold → single user-facing repo), 013 (push
fast path; Layer 0 bioc-git hook may land in phase 1 if server access
permits). Exit:
`BiocManager` users install software (r-universe-built) and data
(self-built) from Bioconductor-controlled URLs indistinguishably.

**Phase 3 (intelligence)**: 011, 012. Gated on phase 2 catalog maturity —
but 011 does not architecturally depend on 010 (SPEC-011 targets
r-universe-built packages before any backend flip, by design). It is
placed after 010 here only because it needs phase 2's full event plane
(009), not because unifying data-package publishing (010, which the size
audit found is cheap and rarely needed) is more valuable than detecting
what a software publish breaks (011 — the BBS value nobody else provides).
A team with phase-2 event-plane capacity to spare should run 011 in
parallel with, or ahead of, 010 rather than waiting for phase 3.

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
