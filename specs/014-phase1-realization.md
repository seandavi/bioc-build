# SPEC-014: Phase-1 realization — what actually runs for the PoC

Status: normative for phase 1 · 2026-09-03 · Supersedes the phase-1 parts of
SPEC-004/005/006/008/009 where they differ. The earlier specs remain the
target architecture; this one is the shortest path that meets the SPEC-000
PoC definition with the components that already exist.

## Design principle (2026-09-03)

**Mirror r-universe exactly; diverge only where necessary.** The build
reuses r-universe's own components, pinned: the `build-source` container
action, `ghcr.io/r-universe-org/base-image`, and the `linux-prep` /
`linux-deps` / `linux-build` / `linux-check` / `bioc-check` composite
actions from `r-universe-org/actions`. Nothing they already do (dependency
installation, check flags, check.Renviron, system requirements) is
re-implemented here. The intended divergences are: source-only (no
windows/macos/wasm jobs) because experiment-data and workflow packages have
no binaries worth building; resolve from bioc-manifest and clone from
git.bioconductor.org instead of their sync/monorepo; the dependency repo is
bioc-registry's `/repo/<universe>`; and the output is an attested staged
artifact instead of `store-package`. No step depends on
`https://bioconductor.org`; the version pair lives in bioc-manifest
`versions.yaml`.

Mechanically: `build.yml` starts as a **verbatim copy** of r-universe's
reusable `build.yml` at a pinned upstream commit (first commit: zero diff),
with every `uses:` pinned to that state; `scripts/upstream-diff.sh` shows
the divergence at any time. Everything we need is then layered on as
configuration read from `policy.yaml` / `versions.yaml`: which jobs run
(linux only for data-experiment and workflows; windows/macos/wasm gated off,
not deleted), size limits and timeouts, build and check steps, and our
resolve + attest + staged-artifact path in place of `store-package`. The
payoff is a direct comparison: a package both systems build should yield
identical artifacts and check verdicts, checkable against the r-universe
results bioc-registry already archives.

## The cuts, in one table

| Spec said | Phase 1 does | Why |
|---|---|---|
| SPEC-005 presign Worker + OIDC + separate staging bucket | **GitHub Actions artifacts are the staging area** (`retention-days: 14`). | Zero new auth surface. The artifact is bound to a run of `build.yml` on `main` by GitHub itself, and `gh attestation verify` binds the tarball digest to that identity for free. |
| SPEC-006 publisher Worker + DO per stream + Queues | **`publish.yml` cron in bioc-registry** (trusted repo, holds the secrets) downloads artifacts, verifies, uploads to the CAS with the existing R2 keys, and POSTs an index entry to one new guarded Worker route. | Existing `x-maint-key` pattern; no bytes through the Worker; sigstore verification where it is free. |
| SPEC-001 hash-chained ledger + snapshots + HEAD | The existing `prop/{u}/log/` write-once records **are** the phase-1 ledger; `prop/{u}/index.json` **is** the fold. | Same shape bioc-registry already keeps for r-universe and seeded entries. Chaining is phase 2. |
| SPEC-009 event ingest Worker | Events are an NDJSON file **inside the artifact**. | Issue #4. |
| SPEC-008 dispatcher Worker | **`dispatch.yml` cron in bioc-build**: ls-remote vs last-attempted commit, then a matrix of `build.yml` jobs in the same run. | No `actions: write` needed; the permissions rule in CLAUDE.md holds. |
| SPEC-007 new repo tree + PACKAGES.rds | Entries land in the **existing** universes `bioc` (devel) and `bioc-release`; served by the existing `/repo/{u}` with no code change. | Phase-2 goal ("one URL, software + data indistinguishable") for free. |
| SPEC-002 branch_map, backend, admission block | One `git_url` (always `https://git.bioconductor.org/packages/<name>`), branches are `devel` / `RELEASE_X_Y` for every package. | Bioconductor's git is the upstream; github.com/bioc is a mirror of it (and missing 31 packages). |

## Components and where they live

```
seandavi/bioc-manifest   packages/<name>.yaml, policy.yaml, validate.yml     (trust root; human PRs)
seandavi/bioc-build      build.yml (reusable), selftest.yml, dispatch.yml      (untrusted; no secrets)
seandavi/bioc-registry   publish.yml (cron) + POST /publish route              (trusted; secrets)
```

Versions: release **3.23** (R 4.6), devel **3.24**. Read them from
`https://bioconductor.org/config.yaml` (`release_version`, `devel_version`),
never hardcode.

Stream → universe → branch → container:

| stream | universe | git branch | container |
|---|---|---|---|
| `release` | `bioc-release` | `RELEASE_3_23` | `bioconductor/bioconductor_docker:RELEASE_3_23` |
| `devel` | `bioc` | `devel` | `bioconductor/bioconductor_docker:devel` |

## bioc-manifest

`packages/<name>.yaml` — flat, so a 30-line parser reads it:

```yaml
name: msdata
git_url: https://git.bioconductor.org/packages/msdata
component: data-experiment        # data-experiment | workflows
profile: data-experiment          # key in policy.yaml
streams: [release, devel]         # which manifests (admin/manifest branches) list it
state: active                     # active | deprecated
since: "2026-09-03"
```

Generated by `scripts/import.py` from `https://git.bioconductor.org/admin/manifest`
(`data-experiment.txt`, `workflows.txt` on branches `devel` and `RELEASE_3_23`).
The importer is idempotent and re-runnable; it never edits `state`.

`policy.yaml`:

```yaml
policy_version: "2026.09.1"
defaults: {max_wall_minutes: 340, vignettes: build}
profiles:
  data-experiment:
    check_args: "--no-manual --no-build-vignettes"   # vignettes built by R CMD build already
    bioccheck: advisory
  workflows:
    check_args: "--no-manual"
    bioccheck: advisory
```

`.github/workflows/validate.yml`: every file parses, `name` == filename,
`component`/`profile`/`state`/`streams` in their enums, `git_url` exact form.
Fails the PR otherwise. Nothing else.

## bioc-build: `build.yml`

`on: workflow_call` with inputs `{package, stream, manifest_ref (default main)}`
plus a thin `workflow_dispatch` wrapper. Permissions exactly
`id-token: write, contents: read, attestations: write`.

Runs in the stream's container. Steps, each writing one line to
`events.ndjson` (`{"ts","event","package","stream",...}`):

1. **resolve** — checkout `seandavi/bioc-manifest@manifest_ref`; read the
   package's YAML and `policy.yaml`; refuse if `state != active` or the
   stream is not in `streams`. Record `manifest_commit`.
2. **fetch** — `git clone --depth 1 --branch <branch> <git_url>`; record
   `commit`. The repo *contains* the data (verified: ChIPXpressData clones at
   7.5 GB); `external_data_store.txt` is informational.
3. **deps** — `BiocManager::install()` of the DESCRIPTION dependencies with
   the container's binary repos. Record `deps_resolved` (installed.packages
   of the closure).
4. **build** — `R CMD build` per profile; sha256 + size.
5. **check** — `R CMD check <profile.check_args>`; then BiocCheck (advisory).
   Logs go in `logs/`. Status ∈ {ok, warning, error}. Gate: `error` fails.
6. **size** — tarball bytes, disk high-water mark, wall minutes.
7. **attest** — `actions/attest-build-provenance` on the tarball
   (`subject-path`). Only on success and only when `github.ref == refs/heads/main`.
8. **stage** — `actions/upload-artifact` named `staged-<package>-<stream>`,
   `retention-days: 14`, containing:

```
<package>_<version>.tar.gz         (absent on failure)
staged.json
events.ndjson
logs/00check.log  logs/00install.out  logs/bioccheck.log  logs/build.log
```

`staged.json`:

```json
{
  "schema_version": "1",
  "package": "msdata", "version": "0.51.1", "stream": "release",
  "status": "ok",                           // ok | warning | error | failed:<stage>
  "tarball": {"file": "msdata_0.51.1.tar.gz", "sha256": "…", "size_bytes": 0},
  "source": {"git_url": "…", "branch": "RELEASE_3_23", "commit": "…", "commit_time": "2026-08-30T12:00:00Z"},
  "manifest_commit": "…", "policy_version": "2026.09.1",
  "build": {"run_id": "…", "run_attempt": 1, "run_url": "…", "container": "…@sha256:…", "r_version": "4.6.1"},
  "check": {"status": "ok", "bioccheck": "ok|warning|error|null"},
  "description": {"Depends": "…", "Imports": "…", "Suggests": "…", "License": "…", "NeedsCompilation": "no", "Priority": null, "LinkingTo": null, "Enhances": null, "OS_type": null},
  "meta": {"Title": "…", "Description": "…", "URL": "…", "BugReports": "…", "Maintainer": "…", "Author": "…", "biocViews": "…"}
}
```

`description` keys are exactly bioc-registry's `Desc` type; `meta` keys are its
`META_FIELDS`. Null for absent fields. A failed build still uploads
`staged.json` + logs + events with `status: failed:<stage>` and no tarball
— that is how the dispatcher learns the attempt happened.

Envelope: job `timeout-minutes: 340`; a disk-free step first (the runner
has ~14 GB free; the standard "free disk space" removals recover ~25 GB); a
disk high-water-mark monitor that fails with `failed:envelope-disk` instead
of letting the runner die.

`selftest.yml`: same steps 1–6, `workflow_call`, no attest/stage, uploads
logs only, usable from a fork.

## bioc-build: `dispatch.yml`

Cron every 6 h + `workflow_dispatch` with `mode: changed|backfill|single`
and optional `packages` (comma list) and `stream`. One job computes the
matrix; a second job `uses: ./.github/workflows/build.yml` with
`strategy: {matrix: {include: [...]}, max-parallel: 8, fail-fast: false}`.

Changed = `git ls-remote <git_url> <branch>` head ≠
`attempts[<package>][<stream>].commit` from
`https://bioc-registry.seandavi.workers.dev/data/state/bioc-build/attempts.json`
(written by the publisher; shape below). Packages absent from that file are
"never attempted" and are dispatched. A package with `status` starting
`failed:` and `attempts >= 3` at the same commit is skipped.

## bioc-registry: `publish.yml` + `POST /publish`

`publish.yml` — cron every 30 min + `workflow_dispatch`. Needs repo secrets
`MAINT_KEY`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_ACCOUNT_ID`
(from GSM `cdsci-*`). Skips with a notice, exit 0, if they are unset.

Per completed run on `main` of `seandavi/bioc-build` (newest 100, any
workflow: a `dispatch.yml` matrix run holds its `build.yml` jobs' artifacts,
a manual `build.yml` run holds its own; `selftest.yml` never uploads
`staged-*`), per artifact named `staged-<package>-<stream>` where
`attempts[package][stream].run_id != run_id`:

1. `gh run download` the artifact; read `staged.json`.
2. If a tarball is present: `sha256sum` must equal `staged.json.tarball.sha256`;
   `gh attestation verify <tarball> --repo seandavi/bioc-build --signer-workflow seandavi/bioc-build/.github/workflows/build.yml` must pass.
3. (moved into the route — bioc-infrastructure ADR 0011: one gate for both
   producers. The publisher does integrity only; steps 3–4 below are the
   route's `manifest-*` and `version-gate` rules.)
4. (as above)
5. `aws s3 cp --endpoint-url https://<account>.r2.cloudflarestorage.com tarball s3://bioc-prop/prop/<universe>/cas/<sha256>` (skip if exists).
   Logs: `s3://bioc-prop/logs/bioc-build/<run_id>/…` (write-once).
6. `POST https://bioc-registry.seandavi.workers.dev/publish` with
   `x-maint-key`, body:

```json
{
  "universe": "bioc-release", "package": "msdata", "run_id": "…",
  "entry": {
    "version": "0.51.1", "sha256": "…", "ts": "…",
    "bioccheck": "ok", "archs": ["linux"], "origin": "bioc-build",
    "artifacts": [{"os": "src", "r": "4.6", "sha256": "…", "file": "msdata_0.51.1.tar.gz"}],
    "desc": {…}, "meta": {…, "commit": {"id": "…", "time": "<source.commit_time>"}, "git_url": "…"}
  },
  "staged": <staged.json verbatim>,
  "attempt": {"commit": "…", "status": "ok", "run_url": "…", "ts": "…"}
}
```

   For a failed build or an integrity rejection, `entry` is omitted and
   `attempt.status` is `failed:<stage>` or `rejected:<check>` with
   `check ∈ {no-tarball, sha256-mismatch, attestation}`. Everything else
   that can say no lives in the route: it runs the consolidated gate
   (`gate()` in bioc-registry `src/repo.ts`, ADR 0011) over `staged` +
   `entry` — build-status, families (the linux check on the R the policy's
   image ships), bioccheck (advisory), version-parse, version-gate (strict
   bump; a `bioconductor`-seeded entry may be replaced at the same version
   once), manifest-state / -git-url / -stream / -component at
   `manifest_commit`, and deps (every hard dependency the registry publishes
   is present at an acceptable version) — records
   `attempt.status = rejected:<rule>` itself, and answers
   `{"propagate": false, "decision": {"reasons": [...]}}`. The same function
   is what r-universe builds pass through, and `POST /gate` exposes it
   read-only.

The route (additive, in `src/index.ts`; pure helpers in `src/repo.ts` with
tests):

- guarded by `x-maint-key` like `/poll`;
- verifies `prop/<u>/cas/<sha256>` exists when `entry` is given, then gates
  (above); an `entry` without `staged` is accepted only as a byte-identical
  re-POST of the record already in `published.json` (the self-heal path);
- writes `prop/<u>/log/<ts>-<pkg>_<ver>.json` (the record, write-once);
- upserts `prop/<u>/index.json` (read → set → put); a later r-universe
  read-modify-write can clobber it, so `publish.yml` re-POSTs every entry in
  `state/bioc-build/published.json` each run and the route is a no-op when
  the index already matches (self-healing, idempotent);
- updates `state/bioc-build/attempts.json`:
  `{"<package>": {"<stream>": {"commit", "status", "run_id", "run_url", "ts", "attempts"}}}`
  and `state/bioc-build/published.json`: `{"<universe>": {"<package>": <entry>}}`.
- `Origin` widens to `"r-universe" | "bioconductor" | "bioc-build"`;
  dashboard/package page treat it like `bioconductor` (never faced the
  r-universe gate) but with its own label. `docs/api.md` and
  `src/openapi.ts` updated in the same PR (bioc-registry CLAUDE.md rule).

Nothing in the existing poll → observe → propagate path changes.

## PoC exit (unchanged from SPEC-000)

≥ 20 data-experiment + ≥ 5 workflow packages published to both universes;
`BiocManager::install(<pkg>, site_repository = "https://bioc-registry.seandavi.workers.dev/repo/bioc-release")`
installs them; every entry's tarball verifies with `gh attestation verify`.

## Deferred (tracked as phase-2 issues)

Hash-chained ledger and snapshots (SPEC-001); presign service (SPEC-005);
event ingest and catalog (SPEC-009); check results into the jobs Parquet
table so bioc-website check pages render them; `limit_flagged` trend query.
