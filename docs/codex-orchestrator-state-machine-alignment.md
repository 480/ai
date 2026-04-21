# Codex Orchestrator State Machine Alignment

## Context

Codex now uses a root Software Orchestrator plus dedicated subagents for design handoff, implementation, review, and scanning. The root orchestrator does not implement code directly. It classifies work, gathers approval, writes Task Briefs, delegates implementation to `480-developer`, and runs the dual-reviewer loop with `480-code-reviewer` and `480-code-reviewer2`.

A separate local state-machine reference described a general Codex lifecycle:

- `IDLE`
- `ANCHOR_SET`
- `ANALYZED`
- `PLANNED`
- `IMPLEMENTING`
- `DONE`
- `BLOCKED`

That reference was useful for constraining actions by phase, but it was written before the current reviewer subagent workflow. Applying it literally would make `IMPLEMENTING -> DONE` too weak, because developer completion alone is not enough in the current Codex workflow.

The operating priorities remain:

1. Simplicity: prefer the smallest solution that works; avoid overengineering; follow YAGNI.
2. Correctness.
3. Performance only when there is clear evidence it is needed.

## Decision

The Codex root orchestrator now embeds a compact root state-machine guard in `providers/codex/instructions/480-orchestrator.md`.

The state-machine reference is treated as a behavioral reference, not as a runtime or repository dependency. The repository does not introduce a checked-in `STATE-MACHINE.md` artifact for this change, and installed prompts remain self-contained.

Reviewer subagents are not modeled as separate states. Instead, they are the verification gate inside `IMPLEMENTING`.

The root orchestrator also owns a Review Escalation Gate before any reviewer-requested retry goes back to `480-developer`. The gate classifies review outcomes as `within_scope` or one of four escalation axes: `[contract_semantics]`, `[risk_class]`, `[scope_surface]`, `[global_change]`.

## Reviewer Integration

For the root orchestrator, `IMPLEMENTING` means enforcing the approved execution contract through:

- Task Brief authoring
- `480-developer` implementation
- parallel review by `480-code-reviewer` and `480-code-reviewer2`
- any required developer retry loop
- final result collection and child lifecycle closure

Developer completion alone does not satisfy `DONE`.

Normal completion requires:

- implementation is complete
- both reviewers approve with exactly `Approved.`
- required child sessions are explicitly closed
- no follow-up, retry, or result wait remains

If the existing low-risk reviewer-infrastructure fallback is used, the orchestrator's independent diff review must find no required changes before final delivery.

## Review Escalation Gate

Before sending any reviewer-requested retry back to `480-developer`, the root orchestrator classifies the review outcome for that Task Brief as `within_scope` or one of `[contract_semantics]`, `[risk_class]`, `[scope_surface]`, or `[global_change]`.

`within_scope` remains the baseline classification for ordinary in-scope retries.

Axis classification is loop telemetry and a retry guard, not an immediate stop condition on first appearance.

On the first axis-tagged finding for a Task Brief, the root records the escalation axis and allows one developer retry to absorb the review-driven follow-up.

The root tracks escalation history per Task Brief. If the same escalation axis appears again after one developer retry, the orchestrator stops the loop, moves back to `PLANNED` or `BLOCKED`, and asks the user for review instead of continuing reviewer/developer churn.

Pause reports to the user include:

- the current approved scope
- the new reviewer concern
- why it exceeds scope
- the recommended default `stay with the approved minimal fix`
- the alternate `expand scope and re-plan`
- the single decision needed to continue

Reviewers and `480-developer` emit structured escalation signals only. The root orchestrator remains the sole owner of pause, re-plan, and user-review transitions.

## State Handling

The root orchestrator interprets the states as action constraints:

- `IDLE`: no usable anchor exists; ask only for an anchor.
- `ANCHOR_SET`: an anchor exists; inspect it before asking additional questions.
- `ANALYZED`: actual behavior, constraints, risks, and execution-relevant decision points are understood.
- `PLANNED`: scope, non-scope, Design Input for implementation tasks, task breakdown, and execution decisions are complete.
- `IMPLEMENTING`: after explicit approval, enforce the approved execution contract through Task Briefs, developer execution, and dual-reviewer verification.
- `DONE`: implementation and review are complete, child lifecycle work is closed, and no follow-up remains.
- `BLOCKED`: exactly one missing decision, contract violation, or unresolved infrastructure blocker prevents progress.

Review findings inside the approved scope keep the workflow in `IMPLEMENTING` when the Review Escalation Gate classifies them as `within_scope`. Review findings classified to an escalation axis remain in `IMPLEMENTING` on first appearance as loop telemetry and a retry guard, and move the workflow back to `PLANNED` or `BLOCKED` only if the same axis appears again after one developer retry.

Reviewer infrastructure blockers follow the existing retry and low-risk fallback rules. They never count as reviewer approval.

## Non-goals

This change does not:

- change non-Codex `480-developer` behavior
- change reviewer prompts
- add a new runtime dependency on local user files
- add a separate checked-in state-machine source file
- make reviewer subagents first-class states
- change non-Codex provider behavior

## Validation

The implementation is covered by installation and rendering tests that verify:

- Codex managed guidance includes the root state-machine rules.
- Codex rendered documentation explains that reviewers are the verification gate inside `IMPLEMENTING`.
- Codex managed guidance and rendered docs describe the Review Escalation Gate, the four escalation axes, the retry-first axis telemetry rule, and the repeated-axis stop rule.
- `DONE` requires dual reviewer approval and child lifecycle completion.
- Reviewer infrastructure blockers do not count as approval.
- Checked-in rendered artifacts remain in sync with the instruction sources.
