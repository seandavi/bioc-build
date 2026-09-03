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
`build.yml`, `selftest.yml` and `dispatch.yml` are live. The component specs
are in [`specs/`](specs/), starting with [`000-overview.md`](specs/000-overview.md).

## Reproducing a build locally (`selftest.yml`)

`selftest.yml` runs the same resolve/fetch/deps/build/check/size pipeline as
`build.yml` (they are literally the same script, `scripts/build.sh`), minus
the attestation and staged-artifact upload. Call it from any fork:

```yaml
jobs:
  selftest:
    uses: seandavi/bioc-build/.github/workflows/selftest.yml@main
    with:
      package: msdata
      stream: devel   # release | devel, default devel
```

Or run `workflow_dispatch` on it directly from the Actions tab.

The exact same script is runnable on a laptop with Docker, to reproduce a
failure bit-for-bit before pushing a fix upstream:

```bash
git clone https://github.com/seandavi/bioc-build && cd bioc-build
mkdir -p work/scripts work/logs
cp scripts/*.R scripts/build.sh work/scripts/
docker run --rm -v "$PWD/work:/work" -w /work \
  -e PACKAGE=msdata -e STREAM=release -e BRANCH=RELEASE_3_23 -e MANIFEST_REF=main \
  -e RUN_ID=local -e RUN_ATTEMPT=1 -e RUN_URL=local \
  -e CONTAINER=bioconductor/bioconductor_docker:RELEASE_3_23 \
  bioconductor/bioconductor_docker:RELEASE_3_23 bash /work/scripts/build.sh
```

`STREAM=release` needs `BRANCH=RELEASE_3_23` (the current release branch,
from https://bioconductor.org/config.yaml); `STREAM=devel` needs
`BRANCH=devel` with the same-tagged container. Output lands in `work/`:
`staged.json`, `events.ndjson`, `logs/`, and the tarball on success.

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
