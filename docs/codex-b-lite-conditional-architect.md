# Codex B-lite Design Handoff Decision

## Context

The Codex provider originally used the root `480-architect` prompt as both the architect and orchestrator for the implementation workflow. That kept the system simple, but it also made the root session responsible for both coordination and design authority.

The desired operating priorities remain:

1. Simplicity: prefer the smallest solution that works; avoid overengineering; follow YAGNI.
2. Correctness.
3. Performance only when there is clear evidence it is needed.

The discussion concluded that a full split between orchestration and architecture can raise the correctness ceiling, but mandatory full design for every small change would be overengineering. The right shape is a B-lite model for Codex only: the root orchestrates, and a design-only subagent classifies every implementation handoff before Task Brief authoring.

The conditional boundary is the output artifact, not whether the root calls the design subagent for implementation work. A Design Contract remains conditional on behavior-changing work. Minimal Transfer Analysis exists so non-design maintenance can transfer context without forcing the root orchestrator to make semantic classification or behavior-design decisions itself.

## Decision

Codex now uses a root Software Orchestrator plus a design-only handoff subagent:

- The root Codex session remains identified by the existing primary role id `480-architect`.
- For Codex only, that primary role id now uses `providers/codex/instructions/480-orchestrator.md` as its instruction source.
- The original `providers/codex/instructions/480-architect.md` is preserved as the pre-split architect prompt source.
- A new Codex-only subagent role id, `480-design-architect`, is registered in the common bundle and rendered to `providers/codex/agents/480-design-architect.toml`.

This keeps the install/runtime contract stable while making the Codex-only source boundary explicit.

## Why The Role Id Stayed `480-architect`

The role id `480-architect` is part of the existing bundle and provider model-selection contract. Renaming that id would broaden the change into configuration activation, model-selection keys, legacy cleanup, backups, tests, and non-Codex provider behavior.

For Codex, the root `480-architect` role is not installed as a custom TOML subagent. It is used as the source for the managed root `AGENTS.md` block. Therefore, the smallest stable change is to keep the role id and switch only the Codex target instruction source to `480-orchestrator.md`.

## How `480-design-architect` Works

`480-design-architect` is a separate role id, not an alias of `480-architect`.

It is registered as a Codex-only subagent in `bundles/common/agents.json` with:

- `id`: `480-design-architect`
- `mode`: `subagent`
- `targets`: `["codex"]`
- `sandbox_mode`: `read-only`
- `model_reasoning_effort`: `xhigh`

The target filtering layer ensures it appears in Codex rendered and installed outputs, but not in OpenCode, Claude, Qwen, or Gemini outputs.

## Design Handoff Path

The root Software Orchestrator calls `480-design-architect` for every implementation task before writing a Task Brief. Implementation tasks include code, tests, configuration, docs-only updates, generated-output synchronization, and bug fixes.

Pure non-implementation conversation, review, explanation, or status reporting does not need Design Input.

The orchestrator may pass observed facts, constraints, user intent, repository paths, and open questions to `480-design-architect`, but it does not author design artifacts or fill in design decisions itself.

`480-design-architect` returns a Design Contract when the task crosses a semantic boundary, including:

- new functionality
- policy or invariant changes
- state transition changes
- externally observable behavior changes
- public API, data contract, or schema changes
- configuration semantics changes
- architecture changes

`480-design-architect` returns Minimal Transfer Analysis only for context-preserving maintenance that restores already-defined behavior without new policy, invariant, state transition, public API/data contract/schema, configuration semantics, or architecture changes. Bug fixes are eligible for MTA only when they are non-design maintenance; otherwise the design subagent returns a Design Contract or `BLOCKED`.

If the design subagent returns `BLOCKED`, the root asks only for the missing decision needed to unblock the workflow.

## Why This Remains B-lite

The mandatory step is handoff classification, not mandatory behavior design.

Without MTA, maintenance work would still need some semantic boundary before implementation. If the root decided that boundary itself, the orchestrator would leak back into design authority by deciding that a bug fix, test correction, generated-output sync, or documentation update is semantics-preserving. Routing every implementation task through `480-design-architect` prevents that leak while keeping full Design Contracts conditional.

This preserves the original simplicity goal:

- the root still does not design or implement
- behavior-changing work still gets a Design Contract only when needed
- maintenance gets context transfer instead of behavior design
- blocked or unclear semantic boundaries return to the user as one missing decision
- no separate Design Contract or MTA files are introduced
- delegation depth stays root-to-subagent only

## Design Output Contract

The design subagent returns exactly one of:

- `Design Contract`
- `Minimal Transfer Analysis`
- `BLOCKED`

A `Design Contract` is authoritative behavior design for behavior-changing work.

A `Minimal Transfer Analysis` is context only. It is not a design authority, solution, or implementation instruction.

For v1, the root embeds the design output in the Task Brief under `Design Input`. The workflow does not create separate Design Contract or MTA files.

## MTA Format And Name

The MTA format is shaped for Task Brief authoring while staying non-authoritative:

- `Anchor` identifies the source of truth for the task.
- `Request Summary` gives the orchestrator a concise objective seed.
- `Observed Behavior` and `Expected Behavior` preserve the maintenance delta without prescribing a fix.
- `Constraints` carries execution limits and semantic guardrails.
- `Evidence` ties expected behavior to direct facts instead of new product intent.
- `Out of Scope` maps directly to Task Brief non-goals.

This is sufficient for the orchestrator to embed the MTA as `Design Input` and then write the Task Brief execution fields without inventing behavior. The format intentionally omits implementation steps, file-level guidance, and verification commands because those would turn MTA into an implementation plan.

The name `Minimal Transfer Analysis` is descriptive for this role as long as "minimal" means minimal authority, not minimal useful context. It transfers enough analysis for maintenance implementation while avoiding Design Contract authority. A more explicit future name such as "Maintenance Transfer Analysis" would be compatible with the same MTA acronym, but this change keeps the existing name to avoid terminology churn.

## Reviewer And Developer Contract

`480-developer` treats the Task Brief as the execution request. If the Task Brief includes a Design Contract, the developer treats it as the authoritative behavior contract. If the Task Brief includes an MTA, the developer treats it as context only.

If the parent sends review-driven follow-up that conflicts with the Task Brief or Design Input, materially expands scope, or falls into a hard-boundary escalation axis (`[contract_semantics]`, `[risk_class]`, `[scope_surface]`, `[global_change]`), the developer returns `BLOCKED` to the parent instead of implementing it unilaterally.

MTA-backed minimal maintenance remains local unless the parent explicitly re-approves broader contract or risk-hardening work.

Reviewers check implementation against the Task Brief and any embedded Design Input. Design Contract violations are required changes.

Reviewers preserve the existing three response shapes. When a finding is beyond the Task Brief or hits a hard-boundary trigger, they return exactly one change-request bullet using:

- ``What: Pause and escalate to the parent `480` session before more code changes.``
- `Why:` beginning with exactly one escalation axis: `[contract_semantics]`, `[risk_class]`, `[scope_surface]`, or `[global_change]`
- `Where:` pointing at the affected contract area or changed path

Once such a pause-worthy concern exists, reviewers do not stack downstream hardening requests behind it. They escalate instead of iteratively broadening the implementation.

The root orchestrator owns the Review Escalation Gate and classifies review outcomes as `within_scope` or one escalation axis before any retry goes back to `480-developer`. If the same escalation axis appears again after one developer retry, the root stops the loop, moves back to `PLANNED` or `BLOCKED`, and asks the user whether to stay with the approved minimal fix or expand scope and re-plan.

## Non-goals

This change does not:

- rename the `480-architect` role id
- change non-Codex provider behavior
- make design delegation mandatory for pure non-implementation conversation, review, explanation, or status reporting
- introduce separate Design Contract or MTA artifact files
- increase Codex delegation depth beyond the existing root-to-subagent model

## Validation

The implementation is covered by installation and rendering tests that verify:

- Codex installs include `480-design-architect`.
- OpenCode, Claude, Qwen, and Gemini outputs do not include `480-design-architect`.
- Codex root managed guidance uses Software Orchestrator language.
- Codex root guidance sends every implementation task through `480-design-architect`.
- Bug fixes are not categorically skipped.
- The orchestrator does not author design artifacts.
- The design architect instruction enforces the Design Contract, MTA, and BLOCKED output boundary.
- Developer and reviewer instructions handle Design Contract and MTA semantics.
- Codex reviewer guidance preserves the three response shapes while adding the pause/escalation bullet convention.
- The root orchestrator owns the Review Escalation Gate and repeated-axis stop rule.
- Checked-in rendered artifacts remain in sync with the bundle definitions.
