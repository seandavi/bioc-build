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

Design. The component specs are in [`specs/`](specs/), starting with
[`000-overview.md`](specs/000-overview.md). No workflow runs yet.

## Trust model

This repo is public and holds no secrets. Workflows here run as the untrusted
build identity: they can write only to a per-run staging prefix through
short-lived presigned URLs obtained with the job's OIDC token, and hold no
credential to anything published. See `specs/004-build-workflows.md` and
`specs/005-staging-upload-service.md`.
