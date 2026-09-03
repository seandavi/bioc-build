# bioc-build

The build component of Bioconductor's modular build estate. It builds, checks,
and stages packages on GitHub Actions; it never publishes. Siblings:

- **bioc-registry** — the trusted side: presign service, publisher, gate,
  content-addressed store, served repository, Parquet archive, HTTP API. Most
  of what specs 006/007/009/010 describe already runs there. Read its
  `docs/DATAPLANE.md` before specifying any storage or event shape.
- **bioc-manifest** (not yet created) — the governance trust root: one YAML per
  authorized package plus the policy file. Human PRs only.
- **bioc-infrastructure** — the map: roadmap, umbrella issues, ADRs. Decisions
  a spec settles are recorded there as ADRs, not here.

## Naming is settled (bioc-infrastructure ADR 0010)

- The governance list is the **manifest**, never "the registry". `manifest_commit`,
  `manifest_updated`. "Registry" means bioc-registry, the data plane.
- This system is **bioc-build**, not `bioc-builder`. No new GitHub org; repos
  live under `seandavi/`.

## Editing specs

- The specs are the work right now; the issues are the ledger of what to change.
- A change to a contract another component consumes (staged manifest shape,
  event payloads, policy schema, R2 prefixes) names the consuming spec in the PR.
- Phase 1 is a PoC for ~25 experiment-data + workflow packages. Prefer deleting
  a component to adding one; the publisher budget is ≲ 2k lines *added* to
  bioc-registry.
- Current release is BioC 3.23 (R 4.6), devel 3.24. Examples should say so.

## When workflows land

Public repo, no secrets. Permissions are `id-token: write`, `contents: read`,
`attestations: write`, nothing else. A PR adding any other secret or permission
is declined on that ground alone.
