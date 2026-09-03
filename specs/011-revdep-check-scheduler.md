# SPEC-011: Reverse-dependency check scheduler

Status: draft v0.1 · Phase 3 · DO per stream + Actions check harness

## Purpose

Prompt breakage detection (BBS's real value-add): when a package publishes,
schedule *checks* — not rebuilds — of its reverse dependencies, producing
events that feed propagation decisions and triage. Deployable against
r-universe-built packages before any backend flip; commits to nothing
architecturally.

## Scope / non-goals

- In scope: DAG maintenance, cascade scheduling (debounce/budget), check
  harness invocation, result events, storm handling.
- Non-goals: rebuild/republish scheduling (cron tier, SPEC-008 refresh
  covers binary staleness), propagation *decisions* (consumer of these
  events; separate future spec), CRAN-revdep checking (Bioc-internal edges
  only, v1).

## DAG

- Source: current snapshot(s) for the stream — `description` dependency
  fields across all components (software + data + workflows), edges =
  Depends/Imports/LinkingTo (Suggests configurable, default off for
  cascades, on for direct-revdep-of-core-package option).
- Held by a DO per stream; rebuilt from snapshot on HEAD change
  (`published` events carry enough to incrementally patch; full rebuild is
  the correctness fallback, target < 10 s at 4k nodes).

## Cascade scheduling

On `published{package P}`:
1. Compute reverse closure to depth D (default 1; depth > 1 only for a
   configured core-package list — S4Vectors-class packages — where indirect
   breakage is likely).
2. Debounce: coalesce triggers per dependent package over a window
   (default 2 h) — a burst of upstream publishes yields one check per
   dependent, recording all trigger shas.
3. Budget: per-stream daily check budget (default 500 jobs) with
   priority = (direct revdeps first, then by dependent's downstream count).
   Over-budget remainder deferred, visible as `check_deferred` events —
   storms are explicit and queryable, never silent.
4. Dispatch check jobs: the SPEC-004 self-test harness in check-only mode
   (`checkonly.yml`: fetch dependent at its *current published version's
   source*, install deps from unified repo — thereby picking up P's new
   version — run profile checks, no build/stage).
5. Results: `revdep_check_completed{dependent, triggers[], status, delta}`
   where `delta` compares against dependent's last known check status —
   *newly failing* is the signal; already-failing is noise-suppressed.

## Consumers (informative)

- Triage agent (SPEC-012): `revdep_check_completed{delta: newly_failing}`
  with trigger attribution → one upstream case, not N downstream cases.
- Propagation tooling: newly-failing sets per candidate version.
- Dashboard: "what did S4Vectors 0.44 break" as a stored query.

## Acceptance criteria

- Synthetic DAG fixtures: closure, debounce, and budget behavior
  property-tested (including diamond deps and cycles — cycles logged,
  broken arbitrarily, never infinite).
- Live shadow run against one release stream for a month: check volume
  within budget; ≥ 1 real breakage detected ahead of user report
  (qualitative but decisive).
- Zero writes outside events (audit).

## Open questions

- OQ-11.1: Check environment fidelity — dependent's released source vs its
  devel head (default: published source; devel-head mode as option for
  pre-release sweeps).
- OQ-11.2: Budget sizing vs GH org concurrency shared with builds —
  needs SPEC-008/011 shared concurrency config.
- OQ-11.3: Whether scheduled full-stream check sweeps (BBS-nightly
  equivalent) remain necessary once event-driven checks run, or only
  pre-freeze. Default: pre-freeze sweep only.
