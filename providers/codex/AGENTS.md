# Codex CLI Agents

Documentation for the checked-in Codex CLI artifacts and install behavior.

## Main Prompt

Codex uses the 480ai managed block in the root `AGENTS.md` as the Software Orchestrator main prompt.
The managed block source is the Codex-specific orchestrator instruction body (`providers/codex/instructions/480-orchestrator.md`), and the design architect is a separate design handoff subagent.

## Name mapping

- `480-design-architect` -> `480-design-architect` (`providers/codex/agents/480-design-architect.toml`)
- `480-developer` -> `480-developer` (`providers/codex/agents/480-developer.toml`)
- `480-code-reviewer` -> `480-code-reviewer` (`providers/codex/agents/480-code-reviewer.toml`)
- `480-code-reviewer2` -> `480-code-reviewer2` (`providers/codex/agents/480-code-reviewer2.toml`)
- `480-code-scanner` -> `480-code-scanner` (`providers/codex/agents/480-code-scanner.toml`)

## Custom agents

Codex custom agents provide the five subagents below.
Checked-in Codex custom agent TOMLs omit `model`, so spawned sessions inherit the parent session's model by default.

- `480-design-architect`
  - maps from: `480-design-architect`
  - file: `providers/codex/agents/480-design-architect.toml`
  - reasoning: `xhigh`
  - sandbox: `read-only`

- `480-developer`
  - maps from: `480-developer`
  - file: `providers/codex/agents/480-developer.toml`
  - reasoning: `medium`
  - sandbox: `workspace-write`

- `480-code-reviewer`
  - maps from: `480-code-reviewer`
  - file: `providers/codex/agents/480-code-reviewer.toml`
  - reasoning: `high`
  - sandbox: `read-only`

- `480-code-reviewer2`
  - maps from: `480-code-reviewer2`
  - file: `providers/codex/agents/480-code-reviewer2.toml`
  - reasoning: `medium`
  - sandbox: `read-only`

- `480-code-scanner`
  - maps from: `480-code-scanner`
  - file: `providers/codex/agents/480-code-scanner.toml`
  - reasoning: `low`
  - sandbox: `workspace-write`

## Install names and paths

Install files are copied to the selected Codex user root `agents/` directory or `<project>/.codex/agents/`.
Codex user scope defaults to `~/.codex`, and install/uninstall/verify may target an alternate user root such as `~/.codex-harness` with `--codex-user-root` or `BOOTSTRAP_CODEX_USER_ROOT`.
User scope adds the 480ai managed block to `<codex-user-root>/AGENTS.md`; project scope adds it to the repository root `AGENTS.md`.
Codex config follows the official contract and applies only minimal merges to `<codex-user-root>/config.toml` or `<project>/.codex/config.toml`.
Install preserves existing settings and only applies `features.multi_agent = true`, `agents.max_depth = 1`, and `agents.max_threads = 200`.
An empty interactive root input keeps the default `~/.codex` target.
Alternate-root installs isolate managed agents, `AGENTS.md`, `config.toml`, bootstrap state, and optional desktop notification assets under that selected root only.
Alternate-root verification reports install-state health for the selected root only and does not claim runtime profile activation.
Codex CLI uses the `name` field in each TOML as the custom agent name.
The root `AGENTS.md` 480ai managed block uses the Software Orchestrator main prompt body verbatim.
This orchestrator workflow is for the root Codex session only. Spawned `480-design-architect`/developer/reviewer/scanner sessions must ignore those root-only rules and follow their own custom agent instructions.
Existing user content is preserved and only the 480ai managed block is appended.
Reinstall replaces the existing 480ai managed block rather than duplicating it.
Uninstall removes only the 480ai managed block.
Codex install/uninstall also clean up legacy `480-architect.toml` and `480.toml` leftovers when present.

## Codex delegation model

- Codex uses a native subagent workflow. The root Software Orchestrator spawns `480-design-architect`, `480-developer`, reviewer, and scanner subagents as needed.
- The default delegation depth is 1: orchestrator(depth 0) -> subagents(depth 1). Subagents do not spawn additional subagents.
- The root calls `480-design-architect` for every implementation task before Task Brief authoring, including code, tests, configuration, docs-only updates, generated-output synchronization, and bug fixes.
- Pure non-implementation conversation, review, explanation, or status reporting does not need Design Input.
- The root does not author design artifacts; it passes observed facts, constraints, and user intent to `480-design-architect`, then embeds the returned Design Contract or Minimal Transfer Analysis into the Task Brief under `Design Input`.
- Bug fixes are not categorically skipped: `480-design-architect` decides whether a bug fix is MTA-backed maintenance, behavior-changing Design Contract work, or BLOCKED.
- Design Contract or Minimal Transfer Analysis output is embedded into every implementation Task Brief under `Design Input`; v1 does not create separate design artifact files.
- Root state machine: reviewer subagents are the verification gate inside `IMPLEMENTING`, not separate states.
- Developer completion alone does not satisfy `DONE`; normal completion requires both `480-code-reviewer` and `480-code-reviewer2` to approve with exactly `Approved.`, required child sessions to be closed, and no follow-up, retry, or result wait to remain.
- Review findings inside approved scope keep the workflow in `IMPLEMENTING` only when the Review Escalation Gate classifies them as `within_scope`; findings classified to an escalation axis move the workflow back to `PLANNED` or `BLOCKED`.
- Reviewer infrastructure blockers follow the existing retry and low-risk fallback rules and never count as reviewer approval.
- The default reviewer flow is parallel: call `480-code-reviewer` and `480-code-reviewer2` together.

## Review Escalation Gate

- Before sending any reviewer-requested retry back to `480-developer`, the root runs a Review Escalation Gate that classifies the outcome for that Task Brief as `within_scope` or one of `[contract_semantics]`, `[risk_class]`, `[scope_surface]`, or `[global_change]`.
- Only `within_scope` findings go back to `480-developer`; pause-worthy findings stay owned by the root orchestrator.
- Immediate pause triggers include public-contract reinterpretation, exact schema/runtime-equivalence or invariant/failure-semantics decisions not already closed in the Task Brief or Design Input, new risk classes such as precision, overflow, DoS, security, or performance hardening outside the approved scope, and dependency/global-config/refactor requirements beyond the local fix.
- Track escalation history per Task Brief. If the same escalation axis appears again after one developer retry, stop the loop, move back to `PLANNED` or `BLOCKED`, and ask the user for review instead of continuing reviewer/developer churn.
- Pause reports to the user include the current approved scope, the new reviewer concern, why it exceeds scope, the recommended default `stay with the approved minimal fix`, the alternate `expand scope and re-plan`, and the single decision needed to continue.
- The root orchestrator is the only role that may pause, re-plan, or ask the user for review. Reviewers and `480-developer` emit structured escalation signals only.

## Review Loop Details

- If `480-code-reviewer2` returns a delegation infrastructure blocker, do not re-request `480-code-reviewer`; wait for `480-code-reviewer` to finish if it is still pending, then retry `480-code-reviewer2` alone exactly once before surfacing the blocker upstream.
- Reviewers review in-thread. `480-code-reviewer` and `480-code-reviewer2` do not spawn additional subagents.
- Keep the concurrent agent budget narrow. Outside the review step, the default path activates only one child agent at a time.
- When possible, the orchestrator plans and delegates with a dedicated worktree and task branch as the default operating model.
- Merge or completed worktree deletion only happens when the user explicitly requests it.
- The current parent session owns each child lifecycle end-to-end: spawn, follow-up, retry, result collection, wait, and explicit close.
- Do not treat the active workflow as complete while any child still has pending follow-up, retry, result collection, or wait work owned by that parent session.
- Close a child only after its latest loop is complete and the parent session has no remaining follow-up, retry, result collection, or wait responsibility for it.
- When waiting on a Codex child agent, prefer longer waits over short polling loops.
- Do not repeat user-facing `still waiting` messages when there is no meaningful state change.
- User-facing wait updates should only report blockers, completion, real state changes, or long delays that help decision-making.
- Use follow-up status checks sparingly and do not make them the default waiting pattern.
- Workspace resolution should prefer the Task Brief path and explicit absolute repo/worktree paths, falling back to the current working directory only when there is no stronger hint.
- Treat a spawn response with no `agent_id`, or any non-structured spawn response, as `spawn_failure`.
- Classify `spawn_failure`, thread limit failures, and usage limit failures as delegation infrastructure blockers, not implementation blockers.
- If the blocker remains after one retry in the same session, return only a structured blocker report to the current parent session/thread.
- Low-risk fallback: if one reviewer has approved and the other reviewer is blocked only by delegation infrastructure, the orchestrator may run an independent diff review when the changed files are limited to prompts, docs, config metadata, or tests. Continue only if that review finds no required changes. Do not waive any explicit change request from either reviewer.
- Do not make `new session` or `exception allowed` the default path for users.

You can call this directly from a Codex CLI prompt like this:
The document and examples use Codex's actual natural-language call pattern.

```text
Plan the next work for docs/480ai/example-topic/001-example-task.md.
Have 480-design-architect produce Design Input for the implementation task and embed it in the Task Brief under `Design Input`.
Have 480-developer implement docs/480ai/example-topic/001-example-task.md.
Have 480-code-reviewer and 480-code-reviewer2 review in parallel, then return required changes or `Approved.`.
If changes are requested, have 480-developer apply them, then re-run the parallel review.
```
Recommended installs use the checked-in artifacts in `providers/codex/agents/` as-is.
Advanced installs render temporary artifacts from the selected model combination and copy them to the same install path.

## Scope notes

The Codex CLI installer manages only the custom agents and the 480ai managed AGENTS block.
Root orchestrator rules apply only to the root session, and spawned subagents explicitly ignore those root-only rules in favor of their own custom agent instructions.
Do not touch user-written content or any AGENTS.md content outside the 480ai managed block.

## Source of truth

- Common agent definitions: `bundles/common/agents.json`.
- Common instruction bodies: `bundles/common/instructions/`.
- Codex provider-specific instruction bodies: `providers/codex/instructions/`.
- Provider install paths and model-selection schema: `app/providers.py`.
- Provider artifact rendering: `app/render_agents.py`.
- Install/uninstall entrypoint: `app/manage_agents.py`.
- State storage and restore: `app/installer_core.py`.
- User guidance: `README.md`.
