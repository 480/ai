# Codex B-lite Conditional Architect Decision

## Context

The Codex provider originally used the root `480-architect` prompt as both the architect and orchestrator for the implementation workflow. That kept the system simple, but it also made the root session responsible for both coordination and design authority.

The desired operating priorities remain:

1. Simplicity: prefer the smallest solution that works; avoid overengineering; follow YAGNI.
2. Correctness.
3. Performance only when there is clear evidence it is needed.

The discussion concluded that a full split between orchestration and architecture can raise the correctness ceiling, but applying that split unconditionally would be overengineering. The right shape is a conditional B-lite model for Codex only.

## Decision

Codex now uses a root Software Orchestrator plus a conditional design-only subagent:

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

## Conditional Design Path

The root Software Orchestrator calls `480-design-architect` only when the task crosses a semantic boundary, including:

- new functionality
- policy or invariant changes
- state transition changes
- externally observable behavior changes
- public API, data contract, or schema changes
- configuration semantics changes
- architecture changes

The root skips design delegation for maintenance work, including bug fixes, failing tests, compile errors, wiring fixes, assertion corrections, documentation-only updates, generated-output synchronization, and minimal defect remediation.

If the design subagent returns `BLOCKED`, the root asks only for the missing decision needed to unblock the workflow.

## Design Output Contract

The design subagent returns exactly one of:

- `Design Contract`
- `Minimal Transfer Analysis`
- `BLOCKED`

A `Design Contract` is authoritative behavior design for behavior-changing work.

A `Minimal Transfer Analysis` is context only. It is not a design authority, solution, or implementation instruction.

For v1, the root embeds the design output in the Task Brief under `Design Input`. The workflow does not create separate Design Contract or MTA files.

## Reviewer And Developer Contract

`480-developer` treats the Task Brief as the execution request. If the Task Brief includes a Design Contract, the developer treats it as the authoritative behavior contract. If the Task Brief includes an MTA, the developer treats it as context only.

Reviewers check implementation against the Task Brief and any embedded Design Input. Design Contract violations are required changes.

## Non-goals

This change does not:

- rename the `480-architect` role id
- change non-Codex provider behavior
- make design delegation mandatory for all work
- introduce separate Design Contract or MTA artifact files
- increase Codex delegation depth beyond the existing root-to-subagent model

## Validation

The implementation is covered by installation and rendering tests that verify:

- Codex installs include `480-design-architect`.
- OpenCode, Claude, Qwen, and Gemini outputs do not include `480-design-architect`.
- Codex root managed guidance uses Software Orchestrator language.
- Codex root guidance references conditional use of `480-design-architect`.
- The design architect instruction enforces the Design Contract, MTA, and BLOCKED output boundary.
- Developer and reviewer instructions handle Design Contract and MTA semantics.
- Checked-in rendered artifacts remain in sync with the bundle definitions.
