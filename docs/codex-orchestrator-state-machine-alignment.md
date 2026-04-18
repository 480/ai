# Codex Orchestrator State Machine Alignment

## Context

Codex now uses a root Software Orchestrator plus conditional subagents for design, implementation, review, and scanning. The root orchestrator does not implement code directly. It classifies work, gathers approval, writes Task Briefs, delegates implementation to `480-developer`, and runs the dual-reviewer loop with `480-code-reviewer` and `480-code-reviewer2`.

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

## State Handling

The root orchestrator interprets the states as action constraints:

- `IDLE`: no usable anchor exists; ask only for an anchor.
- `ANCHOR_SET`: an anchor exists; inspect it before asking additional questions.
- `ANALYZED`: actual behavior, constraints, risks, and execution-relevant decision points are understood.
- `PLANNED`: scope, non-scope, design input when needed, task breakdown, and execution decisions are complete.
- `IMPLEMENTING`: after explicit approval, enforce the approved execution contract through Task Briefs, developer execution, and dual-reviewer verification.
- `DONE`: implementation and review are complete, child lifecycle work is closed, and no follow-up remains.
- `BLOCKED`: exactly one missing decision, contract violation, or unresolved infrastructure blocker prevents progress.

Review findings inside the approved scope keep the workflow in `IMPLEMENTING`. Review findings that require new product intent, behavior design, scope expansion, or other execution decisions move the workflow back to `PLANNED` or `BLOCKED`.

Reviewer infrastructure blockers follow the existing retry and low-risk fallback rules. They never count as reviewer approval.

## Non-goals

This change does not:

- change `480-developer`
- change reviewer prompts
- add a new runtime dependency on local user files
- add a separate checked-in state-machine source file
- make reviewer subagents first-class states
- change non-Codex provider behavior
- change the conditional `480-design-architect` workflow

## Validation

The implementation is covered by installation and rendering tests that verify:

- Codex managed guidance includes the root state-machine rules.
- Codex rendered documentation explains that reviewers are the verification gate inside `IMPLEMENTING`.
- `DONE` requires dual reviewer approval and child lifecycle completion.
- Reviewer infrastructure blockers do not count as approval.
- Checked-in rendered artifacts remain in sync with the instruction sources.
