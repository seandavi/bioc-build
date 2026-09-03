# SPEC-007: Repository layout, serving, and BiocManager integration

Status: draft v0.2 · Phase 1 · Static R2 + CDN (no compute on read path)

## Purpose

Define the user-facing contract: a CRAN-style static repository generated
deterministically from snapshots, served from R2 behind the CDN at
Bioconductor-controlled URLs, consumable by stock `install.packages` /
`BiocManager` with zero client changes.

A bioc-build artifact enters bioc-registry's *existing* served repo
(`/repo/{universe}/…`, already generated from the propagated index) — see
SPEC-000 "What bioc-registry already provides". This spec's tree layout
and index-generation contract below is the phase-2 target once bioc-build
runs its own repo tree; SPEC-014 is normative for phase 1, where entries
land in the existing `bioc`/`bioc-release` universes with no new tree.

## Scope / non-goals

- In scope: repo tree layout, index generation (module executed by
  SPEC-006), URL contract, snapshot→repo determinism, rollback behavior,
  archive access, freeze semantics on the serving side.
- Non-goals: fold semantics (SPEC-001), CDN vendor configuration details,
  phase-2 unified-with-r-universe indexes (extension section, non-normative
  for PoC).

## URL and tree contract

Base: `https://repo.<bioc-domain>/` (placeholder; must be Bioconductor-
controlled DNS — that control IS the anti-lockin mechanism).

```
repo/<bioc_version>/<component>/src/contrib/
  PACKAGES            PACKAGES.gz
  <pkg>_<ver>.tar.gz  → served via same tree (see object strategy)
repo/<bioc_version>/<component>/src/contrib/Archive/<pkg>/   # yanked/superseded, optional (OQ-1.2)
repo/<bioc_version>/<component>/meta/
  snapshot.json       # copy of current snapshot (transparency)
  head.json           # {snapshot_sha256, ledger_seq, generated_at}
```

`<bioc_version>` ∈ {"3.23", "devel", …}; `<component>` ∈
{"data-experiment", "workflows"} for PoC. A stream's channel maps:
release → its bioc_version path; devel → `devel`.

Client usage (PoC): users add
`https://repo.<d>/3.23/data-experiment` etc. via option;
target state: `BiocManager::repositories()` returns these URLs (coordinate
with BiocManager maintainers — tracked as EXT-7.1, external dependency).

## Object strategy

Tarballs are stored once in `blobs/` (SPEC-001). The repo tree references
them by one of:
- (a) copy-on-publish into the tree (simple; duplicates bytes per stream), or
- (b) CDN worker/redirect mapping `src/contrib/<name>_<ver>.tar.gz` →
  blob key (no duplication; adds a routing layer on read path).

**PoC decision: (a)** — pure-static wins; R2 storage is cheap and dedup
across streams matters less than a zero-compute read path. Revisit at
software scale (phase 2) where binary × platform × stream multiplies copies.

## Index generation (module contract; runs inside SPEC-006)

`generate_indexes(snapshot) -> {PACKAGES, PACKAGES.gz, tree_ops[]}`
- Deterministic: same snapshot → byte-identical PACKAGES and PACKAGES.gz.
- Does not ship `PACKAGES.rds` (issue #4 cut, closes OQ-7.1: R serialization
  determinism across versions was never validated, and it wasn't needed —
  `install.packages`/`BiocManager` fall back to `PACKAGES.gz` when
  `PACKAGES.rds` is absent).
- Fields written per package: standard CRAN fields from `description` block;
  `MD5sum` omitted; add nonstandard `SHA256sum:` field (harmless to R
  clients, machine-verifiable installs for those who care).
- `tree_ops[]`: idempotent list of copy/delete operations reconciling the
  tree to the snapshot (drives yank removal from tree; blobs never deleted).

## Rollback and freeze (serving semantics)

- Rollback = publisher rewrites HEAD to prior snapshot + regenerates
  indexes + reconciles tree. Client-visible within CDN TTL.
- CDN TTLs: indexes 5 min; tarballs immutable (cache-forever; filenames are
  version-unique and content never changes for a given name+version —
  enforced by publisher: re-publishing same name+version with different
  sha256 is rejected on release channels; on devel it is just a new
  `publish` record — latest-wins fold semantics apply, no dedicated
  supersede mechanism (cut, SPEC-001 issue #4). **Normative: a tarball
  URL, once served, never changes bytes.**)
- Frozen releases: after `freeze` (fired on the outgoing release stream at
  a branch point, not the newly-opened one — SPEC-001 "Release lifecycle"),
  that stream's tree becomes effectively immutable; exception publishes
  follow the same pipeline. The newly-branched release stream's tree is
  ordinarily writable from the moment it exists.

## Acceptance criteria

- `install.packages(pkg, repos=<repo URL>)` and
  `BiocManager::install(pkg)` (with option-injected repo) succeed on Linux,
  macOS, Windows clients for PoC package set, release and devel.
- Determinism: regenerate indexes from same snapshot on two machines →
  identical bytes (PACKAGES, PACKAGES.gz).
- Rollback drill: publish, rollback, verify clients see prior version
  within TTL; blob for rolled-back version still present.
- `meta/snapshot.json` sha256 matches `head.json.snapshot_sha256`.

## Phase 2 extension (non-normative here; normative in SPEC-010)

The unified repo adds `<bioc_version>/software` trees whose entries fold in
`external_publish` records (r-universe-built artifacts mirrored to blobs),
plus binary contrib paths. Layout reserved now:
`repo/<v>/software/{src/contrib,bin/windows/contrib/<r>,bin/macosx/…}`.

## Open questions

- ~~OQ-7.1: PACKAGES.rds byte-determinism across R versions.~~ Moot (issue
  #4): the file isn't shipped.
- OQ-7.2: Domain + CDN: Cloudflare in front of R2 same-account is default;
  confirm Bioconductor DNS governance for the chosen hostname.
- EXT-7.1: BiocManager upstream change to emit new URLs — file early; PoC
  does not block on it (option-based injection suffices).
