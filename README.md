# bioc-build

Bioconductor's standalone package build system on GitHub Actions: one
configuration-driven build-and-check pipeline for every package type, starting
with the experiment-data and workflow packages that r-universe does not build.

It is the **build** component of a modular estate. It produces staged artifacts
and events; it never publishes. What is fit to publish, and where it is served,
is [bioc-registry](https://github.com/seandavi/bioc-registry)'s job. Which
packages are authorized, and under which policy, is the manifest's job. The
map of components and the decisions behind them live in
[bioc-infrastructure](https://github.com/seandavi/bioc-infrastructure)
(see [ADR 0010](https://github.com/seandavi/bioc-infrastructure/blob/main/adr/0010-two-new-repos-for-the-build-system-and-the-manifest-name.md)).

## Status

Phase 1 (see [`specs/014-phase1-realization.md`](specs/014-phase1-realization.md)).
`build.yml` (the r-universe engine), `build-package.yml` (our resolve +
call entry point) and `dispatch.yml` are live. The component specs are in
[`specs/`](specs/), starting with [`000-overview.md`](specs/000-overview.md).

## Relationship to r-universe

`build.yml` starts from a verbatim, sha-pinned copy of
[r-universe-org/workflows](https://github.com/r-universe-org/workflows)'
reusable `build.yml` -- same `build-source`/`linux-*`/`bioc-check` actions,
same `check.Renviron`/`getdeps.R`. We only diverge where our own constraints
force it: `actions/build-source/` is a local fork with the packaging-source
100MB cap turned into a policy-driven env var (SPEC-014's whole reason to
exist is packages too large for r-universe's own limit); our own `resolve`
job reads `seandavi/bioc-manifest` and clones `git.bioconductor.org`
directly instead of r-universe's sync mechanism; dependencies resolve
against `bioc-registry`'s served repo instead of a `*.r-universe.dev`
universe; and our output is `staged.json`/`events.ndjson`/attestation
instead of r-universe's store-package/deploy. Run
`scripts/upstream-diff.sh` to see exactly how far `build.yml` has drifted
from upstream at any point.

## Reproducing a build locally

There's no bespoke script to run by hand any more -- the actual build/check
steps are r-universe's own `linux-*` actions, running inside
`ghcr.io/r-universe-org/base-image`. Two ways to reproduce a run:

- **One package, by hand**: run `build-package.yml`'s `workflow_dispatch`
  from the Actions tab with a `package` and `stream`, or call it from
  another workflow:
  ```yaml
  jobs:
    build:
      uses: seandavi/bioc-build/.github/workflows/build-package.yml@main
      with:
        package: msdata
        stream: devel   # release | devel
  ```
- **Reproduce r-universe's own steps locally**: since `build.yml` is a
  sha-pinned copy of r-universe's reusable workflow (see below), running it
  under [`act`](https://github.com/nektos/act) reproduces the same
  `build-source`/`linux-deps`/`linux-build`/`linux-check` steps a real run
  uses, against `ghcr.io/r-universe-org/base-image` directly.

## Trust model

This repo is public and holds no secrets. Workflow permissions are exactly
`id-token: write, contents: read, attestations: write`. Phase 1 stages a
build's output as a `retention-days: 14` GitHub Actions artifact rather than
through a separate presigned-upload service; `actions/attest-build-provenance`
binds the tarball's digest to this repo's identity, which is what the trusted
`bioc-registry` publisher verifies before it will touch the artifact. See
`specs/014-phase1-realization.md` (normative for phase 1) and
`specs/004-build-workflows.md` / `specs/005-staging-upload-service.md` (the
target architecture phase 1 approximates).
