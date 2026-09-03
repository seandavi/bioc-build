# SPEC-009: Event plane (ingest, archive, catalog, surfaces)

Status: draft v0.2 · Phase 1 minimal (ingest + raw archive) / Phase 2 full

## Purpose

Single observability substrate: authenticated ingest of NDJSON events from
all components, append-only raw archive on R2, nightly-compacted
DuckDB/Parquet catalog, and read surfaces (badges, status dashboard, trend
queries). Also the inter-component signaling fabric (publisher trigger,
phase-3 case threads).

## Scope / non-goals

- In scope: event envelope, ingest auth, archive layout, catalog schema
  approach, compaction, badges/dashboard, trend queries, publisher-trigger
  forwarding.
- Non-goals: ledger records (SPEC-001 — publication facts, different
  contract), what any producer means by its event types (producers' specs
  own their payload schemas; this spec owns the envelope + registry of
  types).

## Event envelope

```json
{
  "schema_version": "1",
  "event_id": "<uuidv7>",
  "event_type": "build_failed",
  "occurred_at": "…", "received_at": "…set by ingest…",
  "producer": {"component": "build", "identity": "<verified identity>", "version": "…"},
  "subject": {"package": "…", "stream_id": "…", "run_id": "…"},   // fields optional per type
  "payload": { …type-specific… }
}
```

Event type registry lives in this spec's repo as LinkML: one payload schema
per type, producers PR new types here (keeps the catalog columnarizable).
Phase-1 types: build_started, deps_resolved, tarball_built,
check_completed, size_report, build_failed, artifact_staged, published,
publish_rejected, manifest_updated, build_dispatched, policy (mirror).

## Ingest

`POST /v1/events` (batch NDJSON body, ≤ 1 MB):
- Auth per producer class: GitHub OIDC via `gh-oidc-verify` (SPEC-005 lib;
  audience `bioc-build-events`; repo allowlist includes build repo AND
  self-test forks — self-test events are accepted but tagged
  `producer.identity` accordingly and excluded from official surfaces);
  Cloudflare service bindings for internal producers (publisher,
  dispatcher, manifest CI via a scoped token).
- Ingest validates envelope only (payload schema validation is
  catalog-time, permissive-in strict-out); stamps received_at; appends to
  `events/raw/<yyyy-mm-dd>/<uuid>.ndjson` batch objects.
- Availability contract: producers treat ingest as best-effort (SPEC-004
  rule); ingest itself targets simple 99.9% (it's ~100 lines).
- Forwarder: `artifact_staged` events additionally enqueued to the
  publisher queue (OQ-6.1).

## Catalog (phase 2; minimal viable in phase 1 for dispatcher queries)

- Nightly compaction job: raw NDJSON → partitioned Parquet
  `events/catalog/type=<t>/date=<d>/*.parquet`, envelope columns + payload
  struct; malformed payloads quarantined to `events/quarantine/` with
  reason.
- Phase 1 minimal: a single "latest terminal state per (package, stream)"
  Parquet view refreshed by the compactor — sufficient for dispatcher
  change/retry queries and PoC status page.
- Joins available cross-domain: snapshot Parquet (SPEC-001) + catalog share
  DuckDB conventions; e.g. manifest_updated × published for
  "authorized-when-published" audits.

## Read surfaces

- Badges: `GET /badge/<stream>/<package>.svg` from a KV map refreshed by
  compactor (and eagerly on published/build_failed via forwarder). Cacheable,
  no query per request.
- Dashboard: zero-backend SPA, DuckDB-WASM over catalog Parquet (public
  read via CDN). Package build history, current stream status, log links
  (logs live in staging short-term; published-build logs copied to
  `blobs/` by publisher — amend SPEC-006 step 6 to include log blobs).
- Trend query (governance input): scheduled query over size_report events
  producing `limit_watch.parquet` (packages within 20% of envelope on
  disk/wall, or tarball growth > X%/release) — the evidence feed for
  manifest `limit_flagged` PRs.

## Acceptance criteria

- Phase 1: events from a real build run land in raw archive ≤ 60 s;
  self-test events tagged and excluded from badges; dispatcher's
  latest-state view answers its two queries correctly on fixtures.
- Phase 2: compaction is idempotent and re-runnable over any date;
  quarantine captures a malformed fixture without halting; dashboard loads
  catalog over CDN with no server component; badge p99 < 100 ms.

## Open questions

- OQ-9.1: Event retention: raw forever (it's small relative to blobs) vs
  compact-then-expire raw after N days. Default: raw forever, revisit at
  phase-2 volumes.
- OQ-9.2: Real-time-ish surfaces (webhook-assisted lifecycle events from
  GitHub `workflow_run` as a second producer) — valuable for stuck-run
  detection; slot as phase-2 producer, envelope already accommodates.
