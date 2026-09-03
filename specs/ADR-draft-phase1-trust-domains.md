# 0011 (draft) — Phase 1 collapses the staging trust domain into the build and publish repos

- **Status:** Proposed — drafted in bioc-build per issue #18; not filed. A human should open
  this as a PR in bioc-infrastructure's `adr/`, renumbering if 0011 is taken by then.
- **Date:** 2026-09-03

## Context

SPEC-000 (bioc-build's overview spec) originally modeled five trust domains for the build
system: governance (human PRs), build (untrusted, writes only to a staging prefix via
short-lived presigned URLs), publish (a trusted publisher Worker/Durable-Object, sole writer
to published state, verifying attestations before promotion), observe (a read-mostly event
ingest Worker plus catalog/dashboards), and agents (phase 3). Staging was its own domain
because the specs assumed a dedicated presign service (SPEC-005) sitting between the
untrusted build repo and the trusted publisher, with its own R2 credentials and its own OIDC
claim policy.

SPEC-014 ("phase-1 realization"), written once `bioc-manifest` and `bioc-build` existed and
bioc-registry's actual capabilities were accounted for (see [ADR
0010](0010-two-new-repos-for-the-build-system-and-the-manifest-name.md)), does not build that
presign service. It uses a mechanism GitHub already provides for free: `actions/upload-artifact`
on a `workflow_call` run of `build.yml`, with `retention-days: 14`. The artifact is bound to a
specific run of a specific workflow on `main` by GitHub itself; `actions/attest-build-provenance`
+ `gh attestation verify` binds the tarball's digest to that same identity. There is no bucket
write path from the untrusted build repo at all — not to a staging prefix, not to anything.

Verification and promotion (attestation check, manifest check, version gate, copy into the
content-addressed store, propagation index update) all happen in one place: `publish.yml`, a
cron in the *trusted* `bioc-registry` repo, which already holds the R2 secrets for its existing
propagation pipeline. There is no separate publisher service between "build finishes" and
"artifact is promoted" — the trusted repo's own CI is that boundary.

This makes the five-domain model in SPEC-000 inaccurate for phase 1: domain 2 (build) never
touches a bucket, domain 3 (publish) is a cron in an existing repo rather than a dedicated
Worker/DO, and domain 4 (observe) has no separate ingest Worker to be a domain around — events
travel inside the staged artifact and are read by the same trusted CI that promotes it.

## Decision

**Phase 1 has four effective trust boundaries, not five, and "staging" is not one of them.**

| Domain (phase 1) | Who | What it can write |
|---|---|---|
| Governance (human) | `bioc-manifest` PR reviewers | `packages/<name>.yaml`, `policy.yaml` |
| Build (untrusted) | `bioc-build`'s `build.yml`, GitHub-hosted runners | Its own run's GitHub Actions artifact only. No credential to any bucket. |
| Publish (trusted) | `bioc-registry`'s `publish.yml` cron | `bioc-prop`'s published prefixes (`prop/{universe}/cas/`, `prop/{universe}/index.json`, `prop/{universe}/log/`, `state/bioc-build/*`) — after verifying attestation + manifest state itself |
| Agents (phase 3, untrusted) | enrolled per package | PRs, issues, events only — unchanged from the original model |

Staging is not a trust domain because it is not a shared resource with its own access-control
surface: it is a GitHub Actions artifact scoped to one run, governed by GitHub's own workflow
identity and retention, not by anything bioc-build or bioc-registry operates. Verification
happens as the first thing the trusted domain does with an artifact it downloads, not as a
gate a separate service enforces before the trusted domain sees it.

This is a phase-1 realization decision, not a reversal of the phase-2+ target architecture:
SPEC-005 (presign service), SPEC-006 (publisher Worker/DO), and SPEC-009 (ingest Worker) remain
the documented target for when push-triggered promotion latency or a dedicated event surface
is actually needed. bioc-build's specs record both — see SPEC-000 "Trust domains" for the
phase-1 vs phase-2+ text side by side, and SPEC-014 for the mechanism this ADR describes.

## Consequences

- SPEC-000's trust-domain and sequencing sections in bioc-build now describe both the phase-1
  reality and the phase-2+ target explicitly, rather than only the target.
- No new OIDC claim policy, no new Worker, no new bucket credential is needed to reach the PoC
  exit criteria — one fewer thing to build, test, and operate before ≥20 data-experiment and
  ≥5 workflow packages are published.
- The phase-2 decision to build a dedicated staging/publish/ingest service tier is not
  foreclosed by this ADR; it is deferred until phase-1 operation shows a concrete need (push
  latency, a live dashboard, a second producer besides bioc-build) that the cron-based design
  can't meet.
