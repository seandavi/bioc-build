# SPEC-012: Agentic build triage

Status: draft v0.2 · Phase 3 · Agent repo (Actions-scheduled, stateless invocations)

## Purpose

Automated diagnosis of build/check failures with maker/checker-verified
fixes delivered as PRs or issues to maintainers, per-package opt-in,
evidence-first, with all working state as append-only case threads in the
event archive.

## Trust boundary (normative, restated from SPEC-000)

Agent identity has: GitHub App permissions to open issues/PRs on enrolled
repos + the manifest, and event-ingest rights (own audience). It has NO
staging-upload rights (SPEC-005 rejects its workflow_ref), no publisher
access, no repo-write anywhere. Worst-case compromise = bad PR/issue,
contained by human merge gates. Build logs and upstream repos are untrusted
input; prompts must treat them as data.

## Case lifecycle

Trigger patterns (watcher = scheduled Worker over catalog): build_failed
(non-envelope, non-transient, retry-exhausted), check regression,
publish_rejected, revdep newly_failing (grouped by trigger — one upstream
case), limit_watch crossings.

Case thread: `events/cases/<case-id>.ndjson` — append-only records
`{case_opened, gather_completed, repro_attempted, diagnosis, fix_drafted,
checker_verdict, delivered, outcome}`. The thread IS the agent's memory:
every invocation reads thread → does one bounded unit → appends → exits.
Stateless invocations run inside scheduled Actions jobs in the agent repo
(no long-lived compute; 6-h limit irrelevant).

Stages:
1. **Gather**: failure events + logs, package event history, last-good vs
   first-bad upstream diff, deps_resolved deltas, manifest entry.
2. **Reproduce** (honesty gate): fork/branch + self-test workflow pinned to
   failing policy_version; a diagnosis without a reproduction run URL
   cannot advance. Non-reproducing → `flaky` outcome, case closed with
   evidence.
3. **Diagnose + fix (maker)**: classify (dep break upstream, R version,
   data-URL rot, vignette timeout, envelope breach, …); mechanical classes
   get a drafted minimal fix.
4. **Check (checker)**: separate context, adversarial charter: rerun
   self-test on patch branch (must be green), audit diff minimality and
   absence of unrelated changes, verify claims in PR body against thread
   evidence. Only checker approval releases delivery.
5. **Deliver** per manifest `triage` field: `auto_pr` → PR to maintainer
   repo (body generated from thread: failing event, differential, repro
   link, rationale, green patch run); `issue_only` (default) → issue with
   evidence + suggested approach; envelope-breach cases additionally →
   manifest `limit_flagged` PR with limit_watch evidence. `none` → case
   recorded, nothing filed.
6. **Outcome loop**: webhook/poller records merged/closed/ignored + time-to-
   resolution as events → precision-by-failure-class query → autonomy
   ratchet proposals (move a class issue_only→auto_pr) are themselves
   manifest-policy PRs for humans.

Escalation: unclassifiable after reproduction → human queue issue in the
ops repo with organized case file. Failure mode is a tidy dossier, never
silence.

## Compute and cost

Reasoning: Anthropic API via pi/Claude-Code-headless harness; API key in
agent repo secrets only. Referee: Actions runs (free). Coordination:
watcher Worker. Token spend tagged per case/class → cost-per-accepted-PR
is a standing catalog query.

## Rollout

1. Shadow: cases run gather+repro+diagnose, deliver nothing; core team
   reviews diagnosis quality (target ≥ 80% agreed-correct on 50 cases).
2. `issue_only` default for enrolled packages (enrollment = maintainer-
   initiated manifest PR; announce, don't impose).
3. `auto_pr` per class, evidence-gated by outcome data.

## Acceptance criteria

- Trust audit: enumerate agent credentials; verify SPEC-005/006 rejection
  of agent identity by test.
- Seeded-failure corpus (10 classes): correct classification ≥ 8/10;
  checker blocks a deliberately over-broad fix fixture.
- Every delivered PR/issue links a reproduction run; spot-audit 100%.
- Prompt-injection drill: malicious build log / README fixture attempting
  credential exfil or off-target PR → contained (nothing filed outside
  case repo scope, attempt logged).

## Open questions

- OQ-12.1: Harness choice (pi vs Claude Code headless) — decide on
  sandboxing and cost telemetry ergonomics at build time; thread schema is
  harness-agnostic by design.
- OQ-12.2: Checker model/config independence (different model? same model,
  adversarial prompt?) — start same-model different-context; evaluate
  disagreement rates.
- OQ-12.3: Rate limits per maintainer/repo (max open agent items) to
  protect goodwill. Default: 2 open items per repo.
