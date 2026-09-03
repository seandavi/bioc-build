# Fork of r-universe-org/build-source

Full checkout of `r-universe-org/build-source@1ee4788f93b7a9f2da882e757ceeff34759a7ab5`
(2026-09-03) -- the Dockerfile does `COPY . /pkg` (this repo doubles as the
source for their `buildtools` R package), so the whole tree is the build
context, not just `Dockerfile`/`entrypoint.sh`. One change from upstream:
the source-package size cap (`entrypoint.sh`, checked right after `R CMD
build`) is `${SOURCE_SIZE_LIMIT_MB:-100}M` instead of a hardcoded `100M`.
That is the whole reason this fork exists -- SPEC-014's PoC package list
includes experiment-data packages well over 100MB (e.g. ChIPXpressData
clones at 7.5GB), and upstream's cap is not configurable. `build.yml` sets
`SOURCE_SIZE_LIMIT_MB` from `policy.yaml`'s per-profile size limit.

`action.yml` also points `runs.image` at the local `Dockerfile` instead of
`docker://ghcr.io/r-universe-org/build-source`, so this action builds at run
time rather than pulling a published image. That costs a Docker build every
run (a few minutes) instead of a pull, but needs no `packages:write`
permission (CLAUDE.md: exactly `id-token: write, contents: read,
attestations: write`, nothing else). Upgrade path once/if
r-universe-org/build-source accepts the env var upstream: point `runs.image`
back at their published tag and delete this directory.

Diff against upstream:
```
git clone https://github.com/r-universe-org/build-source /tmp/build-source-upstream
cd /tmp/build-source-upstream && git checkout 1ee4788f93b7a9f2da882e757ceeff34759a7ab5
diff -ru --exclude=.git --exclude=.github /tmp/build-source-upstream <path-to>/actions/build-source
```
