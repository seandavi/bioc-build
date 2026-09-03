# SPEC-005: Staging upload service

Status: draft v0.2 · Phase 1 · Cloudflare Worker · **Prototype first**

## Purpose

Exchange a GitHub Actions OIDC token for short-lived presigned R2 upload
URLs scoped to a single run's staging prefix. Eliminates long-lived R2
credentials from the build (untrusted) domain and cryptographically binds
staged objects to a verified workflow identity.

## Scope / non-goals

- In scope: OIDC verification, claim policy, presign issuance, staging
  prefix conventions, TTL/GC.
- Non-goals: reading/verifying staged content (SPEC-006), event ingest auth
  (SPEC-009 reuses the verification module — factor it as a library).

## Interface

`POST /v1/presign`
- Auth: `Authorization: Bearer <GitHub OIDC JWT>` (audience
  `bioc-build-staging`).
- Body: `{"run_id": "<workflow run id>", "files": [{"name":
  "curatedTCGAData_1.28.1.tar.gz", "sha256": "…", "size_bytes": N}, …]}`
- Response 200: `{"prefix": "staging/<run_id>/", "urls": [{"name": …,
  "put_url": "…", "expires_at": "…"}], "max_bytes_remaining": N}`
- Errors: 401 (bad token), 403 (claim policy), 413 (per-run byte budget),
  422 (filename/count limits).

## OIDC claim policy (normative)

Verify signature against GitHub's JWKS (cached, kid-rotation tolerant);
then require ALL of:
- `aud == "bioc-build-staging"`
- `repository == "seandavi/bioc-build"` (exact, configured)
- `workflow_ref` starts with `seandavi/bioc-build/.github/workflows/build.yml@refs/`
  (self-test workflow is NOT authorized — it must never stage)
- `run_id` claim == body `run_id`
- token `iat` within 15 min; single-use per (run_id, name) — replay of an
  identical presign request is idempotent-OK; conflicting sha256 for a
  name already presigned in this run → 409.

## Presign properties

- URLs are S3-style presigned PUTs against the staging bucket, key
  `staging/<run_id>/<name>`, TTL 30 min, content-length-range pinned to
  declared `size_bytes` ± 0, and `x-amz-content-sha256` pinned to declared
  sha256 where R2 supports it (else publisher re-verifies — it re-verifies
  regardless; the pin is defense in depth).
- Per-run budget: configurable, default 20 GB / 50 files. Prevents a
  compromised run from filling the bucket.
- Worker never proxies bytes; it only signs.

## Storage conventions (contract with SPEC-004/006)

- `staging/<run_id>/` contains: tarball, `attestation.bundle`,
  `staged.json`, `logs/*.log`.
- Lifecycle rule: staging prefix TTL 14 days (policy `staging_ttl_days`).
  Publisher promotion copies out of staging; nothing served from staging.

## Reuse

Export the OIDC verification as a small library (`gh-oidc-verify`):
consumed by SPEC-009 ingest (different audience + laxer repo policy) and
any future upload surface (Nextflow telemetry, build-tracker). API:
`verify(token, {audience, repository?, workflow_prefix?}) -> claims | error`.

## Acceptance criteria

- End-to-end from a real Actions run: token → presign → upload → object
  present with correct key; from a fork/self-test run: 403.
- Replay, oversize (length-range violation), expired-URL, and wrong-run_id
  cases each rejected with the specified codes.
- Presign latency p99 < 300 ms; zero R2 credentials in build repo secrets.
- `gh-oidc-verify` published with test vectors (valid, expired, wrong-aud,
  wrong-repo, tampered).

## Open questions

- OQ-5.1: Cloudflare API token scoping — can the Worker's R2 binding be
  restricted to the staging bucket only via separate bucket (per OQ-0.1)?
  Default: yes, separate bucket.
- OQ-5.2: Non-GitHub upstreams (git.bioconductor.org-only packages, OQ-2.2)
  still build in the central repo, so OIDC identity is unaffected — but
  attestation `source` binding weakens. Document as accepted risk or
  require GitHub mirrors for bioc-build backend.
