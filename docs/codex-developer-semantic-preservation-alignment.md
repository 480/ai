# Codex Developer Semantic Preservation Alignment

## Context

The Codex `480-developer` role implements exactly one approved Task Brief at a time. It receives behavior authority from the parent `480` Software Orchestrator through the Task Brief and any embedded `Design Input`.

A local implementation-agent reference emphasized deterministic implementation, semantic preservation, and a strict distinction between behavior-changing work and maintenance work. That reference was useful, but copying it wholesale would introduce extra concepts that do not match the current Codex B-lite workflow.

The operating priorities remain:

1. Simplicity: prefer the smallest solution that works; avoid overengineering; follow YAGNI.
2. Correctness.
3. Performance only when there is clear evidence it is needed.

## Decision

The Codex-only `480-developer` prompt now includes a compact implementation-agent contract and two execution modes:

- Mode A: contract-driven implementation
- Mode B: direct maintenance implementation

This is a partial alignment only. The checked-in prompt does not depend on local user files such as `~/.codex/AGENTS.md` or `~/.codex/STATE-MACHINE.md`, and it does not introduce separate design-contract files or implementation-plan artifacts.

## Implementation Contract

`480-developer` now treats behavior authority as explicit:

- Behavior-changing work requires a Design Contract in Task Brief `Design Input`.
- Without a Design Contract, the developer may only perform semantics-preserving maintenance.
- The developer must not silently introduce behavior, expand scope, change policy, or change invariants.
- If behavior authority, execution mode, or an implementation-critical decision is unclear, the developer returns `BLOCKED` to the parent `480` session with one targeted blocker before coding.

## Execution Modes

Mode A applies only when the Task Brief includes a Design Contract in `Design Input`. In that mode, the developer implements behavior only within the contract.

Mode B applies when there is no Design Contract and the work is local, minimal, and semantics-preserving. Examples include failing tests, compile errors, wiring fixes, assertion corrections, and minimal defect remediation.

If satisfying a requested expected behavior would require behavior-changing work, the developer must stop and request a Design Contract instead of inferring new behavior.

## MTA Handling

Minimal Transfer Analysis remains context only.

An MTA may constrain:

- the problem boundary
- the expected correctness target

An MTA must not be treated as:

- design authority
- a solution
- an implementation plan
- an instruction set
- permission to introduce behavior

This keeps MTA useful for context transfer while preventing it from bypassing the Design Contract requirement for behavior-changing work.

## Non-goals

This change does not:

- change the root orchestrator prompt
- change reviewer, scanner, or design-architect prompts
- change non-Codex provider prompts
- add a runtime dependency on local user files
- introduce separate Design Contract or implementation-plan artifacts
- move review lifecycle responsibility from the parent `480` session to `480-developer`

## Validation

The implementation is covered by installation and rendering tests that verify:

- Codex rendered `480-developer` instructions include the implementation-agent contract.
- Mode A and Mode B semantics are present.
- Behavior-changing work requires a Design Contract.
- No Design Contract means semantics-preserving maintenance only.
- MTA cannot authorize new behavior, policy changes, invariant changes, or scope expansion.
- Unclear behavior authority returns `BLOCKED`.
- Non-Codex developer prompts do not gain Codex-only implementation-agent wording.
- Checked-in rendered artifacts remain in sync with the instruction source.
