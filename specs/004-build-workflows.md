# SPEC-004: Build workflows (bioc-build)

Status: draft v0.2 · Phase 1 · GitHub Actions in public build repo

## Purpose

Build, check, and stage packages on GitHub-hosted runners under a resolved
policy profile, producing (a) a staged artifact + attestation and (b) a
stream of build events. Reuses r-universe workflow components where they
fit; deviates only where profiles require.

## Scope / non-goals

- In scope: reusable build workflow, per-step event emission, staging
  upload, attestation, the maintainer-facing self-test workflow.
- Non-goals: deciding *what* to build (SPEC-008), publication (SPEC-006),
  any credential to published storage (forbidden — trust domain 2).

## Repo

`seandavi/bioc-build` (public). Contains:
- `.github/workflows/build.yml` — reusable workflow, inputs:
  `{package, stream_id, source_ref, manifest_commit, policy_version}`.
- `.github/workflows/selftest.yml` — maintainer-droppable test workflow
  (mirrors build.yml, skips staging/attestation, same envelope).
- Composite actions vendored or referenced from r-universe-org where
  license-compatible and profile-compatible (pin by sha; document
  divergences in `UPSTREAM.md`).

Reused build **containers**, not only composite actions (issue #5 — the
container is the bulk of what "reuses r-universe workflow components"
actually means in practice): phase 1 (SPEC-014) runs the official
`bioconductor/bioconductor_docker:RELEASE_3_23` (release) and
`bioconductor/bioconductor_docker:devel` (devel) images directly — these
are Bioconductor's own containers, not r-universe's — resolving
`{release_version, devel_version}` from `https://bioconductor.org/config.yaml`
rather than hardcoding the tag. r-universe's own build containers are not
reused in phase 1; a phase-2 profile could pin one of theirs if a build
target needs their toolchain instead.

## Workflow steps (normative sequence)

1. **Resolve**: check out the manifest at `manifest_commit`; load entry; resolve
   effective policy (SPEC-003 resolver); resolve branch_map → confirm
   `source_ref` matches (defense in depth vs dispatcher bug). Emit
   `build_started`.
2. **Fetch source** at `source_ref`; record `commit` sha. Fail →
   `build_failed{stage: fetch}`. The git repo *contains* the data — verified:
   ChIPXpressData's git.bioconductor.org clone is 7.5 GB, its tarball 3.2 GB.
   `external_data_store.txt` (Bioconductor's convention for re-attaching
   large data at build/install time from an external store) is
   **informational only** here: cloning the repo already pulls that data
   in, so the file doesn't reduce what this step fetches. The real
   constraint is runner disk, not source size — tracked as bioc-build
   issue #11 (envelope/disk decision: self-hosted runner? build-from-tarball
   only? skip install-check for pure data?), not solved by this spec.
3. **Dependency setup**: install deps. **Phase 1 (normative, issue #5 —
   the unified repo this step named doesn't exist until phase 2, SPEC-007
   OQ-4.2 was unresolved): Linux binaries from the container's built-in
   binary repos** — `BiocManager::install()` inside
   `bioconductor/bioconductor_docker` resolves against Bioconductor's own
   binary repo plus CRAN, both already configured in the image; no
   `bioc.r-universe.dev` fallback needed for phase-1's package set.
   Workflow packages carry heavy dependency trees, and source installs are
   where the 340-minute budget goes, so binary resolution here is load-
   bearing, not an optimization. The phase-2 target below (unified repo)
   applies once that repo exists. Record resolved dependency versions as
   `deps_resolved` event (feeds phase-3 differential diagnosis).
4. **Build**: `R CMD build` per profile (vignettes per profile). Produces
   tarball; compute sha256. Emit `tarball_built{sha256, size_bytes}`.
5. **Check**: run profile check list in order; each check emits
   `check_completed{id, status, log_r2_key}`. Full logs uploaded to staging
   alongside artifact. Gate per `fail_on`.
6. **Size report**: emit `size_report{tarball_bytes, disk_hwm_gb,
   wall_minutes}` (always, even on failure paths where measurable).
7. **Attest**: `actions/attest-build-provenance` over the tarball digest.
   Store attestation bundle; compute bundle sha256.
8. **Stage**: request presigned URLs from SPEC-005 (OIDC); upload tarball,
   attestation bundle, logs, and a `staged.json` manifest
   `{package, version, stream_id, sha256, attestation_bundle_sha256,
   manifest_commit, policy_version, source: {git_url, commit, ref},
   build: {workflow_run_url, run_attempt, runner_image}}`.
   Emit `artifact_staged{run_id, staged_manifest_sha256}` — this event is
   the publisher's trigger.

All event emission via SPEC-009 ingest with OIDC auth; events are
best-effort with one retry — a build MUST NOT fail because the event plane
is down (staging manifest is the durable record; publisher backfills).

## Profile-specific behavior (PoC)

- `data-experiment`: source-only, standard vignette build. Watch: large
  `data/` dirs — build step monitors disk HWM and aborts with
  `build_failed{stage: envelope, reason: disk}` at threshold rather than
  letting the runner die opaquely.
- `workflows`: source-only, vignettes mandatory, long wall budget. Timeout
  handling: job-level `timeout-minutes` set from envelope; a timeout emits
  `build_failed{stage: envelope, reason: wall}` via a monitor step
  (`always()` guard) before job death.

## Self-test workflow contract

Same steps 1–6 (no attest/stage), runnable from any fork with
`uses: seandavi/bioc-build/.github/workflows/selftest.yml@v1`. Inputs
default to `{stream: devel, profile: from-manifest-or-input}`. This is the
reproduction harness cited by governance (limit_flagged evidence) and
phase-3 agents; treat its interface as stable API.

## Acceptance criteria

- 20 real experiment data packages + 5 workflow packages build green on
  release + devel streams; failures produce correctly staged logs + events.
- Deliberately oversized fixture package fails with
  `envelope/disk`, not opaque runner death.
- Self-test run in a fork reproduces a seeded failure bit-for-bit
  (same check output) vs central run.
- No step has access to any secret other than the OIDC token exchange
  (verified by audit of workflow permissions: `id-token: write`,
  `contents: read`, `attestations: write`, nothing else).

## Open questions

- OQ-4.1: Vendored vs referenced r-universe actions — vendoring isolates
  from upstream churn but forfeits fixes. Default: reference pinned shas +
  quarterly review.
- ~~OQ-4.2: Dependency installation source for phase 1.~~ Resolved (issue
  #5): the container's built-in binary repos (Bioconductor + CRAN) —
  see step 3 above. Reopens as source-order question (unified repo vs
  CRAN) once the unified repo exists in phase 2.
- OQ-4.3: `--run-donttest` for workflows profile may be too aggressive;
  validate against current BBS behavior.
