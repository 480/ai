# Codex CLI Agents

Documentation for the checked-in Codex CLI artifacts and install behavior.

## Main Prompt

Codex uses the 480ai managed block in the root `AGENTS.md` as the architect main prompt.
The managed block source is the Codex-specific architect instruction body (`providers/codex/instructions/480-architect.md`), and there is no separate architect custom agent.

## Name mapping

- `480-developer` -> `480-developer` (`providers/codex/agents/480-developer.toml`)
- `480-code-reviewer` -> `480-code-reviewer` (`providers/codex/agents/480-code-reviewer.toml`)
- `480-code-reviewer2` -> `480-code-reviewer2` (`providers/codex/agents/480-code-reviewer2.toml`)
- `480-code-scanner` -> `480-code-scanner` (`providers/codex/agents/480-code-scanner.toml`)

## Custom agents

Codex custom agents provide only the four subagents below.

- `480-developer`
  - maps from: `480-developer`
  - file: `providers/codex/agents/480-developer.toml`
  - model: `gpt-5.4-mini`
  - reasoning: `medium`
  - sandbox: `workspace-write`

- `480-code-reviewer`
  - maps from: `480-code-reviewer`
  - file: `providers/codex/agents/480-code-reviewer.toml`
  - model: `gpt-5.4`
  - reasoning: `medium`
  - sandbox: `read-only`

- `480-code-reviewer2`
  - maps from: `480-code-reviewer2`
  - file: `providers/codex/agents/480-code-reviewer2.toml`
  - model: `gpt-5.4-mini`
  - reasoning: `high`
  - sandbox: `read-only`

- `480-code-scanner`
  - maps from: `480-code-scanner`
  - file: `providers/codex/agents/480-code-scanner.toml`
  - model: `gpt-5.3-codex-spark`
  - reasoning: `low`
  - sandbox: `workspace-write`

## Install names and paths

Install files are copied to `~/.codex/agents/` or `<project>/.codex/agents/`.
User scope adds the 480ai managed block to `~/.codex/AGENTS.md`; project scope adds it to the repository root `AGENTS.md`.
Codex config follows the official contract and applies only minimal merges to `~/.codex/config.toml` or `<project>/.codex/config.toml`.
Install preserves existing settings and only applies `features.multi_agent = true` and `agents.max_depth = 2`.
Codex CLI uses the `name` field in each TOML as the custom agent name.
The root `AGENTS.md` 480ai managed block uses the architect main prompt body verbatim.
This architect workflow is for the root Codex session only, and the `480-developer`/reviewer/scanner subagents follow their own custom agent instructions.
Existing user content is preserved and only the 480ai managed block is appended.
Reinstall replaces the existing 480ai managed block rather than duplicating it.
Uninstall removes only the 480ai managed block.
Codex install/uninstall also clean up legacy `480-architect.toml` and `480.toml` leftovers when present.

## Codex delegation model

- Codex uses a native subagent workflow. The architect spawns `480-developer`, and the developer uses reviewer/scanner subagents only when needed.
- The default delegation depth is 2: architect(depth 0) -> developer(depth 1) -> reviewer/scanner(depth 2).
- The default reviewer flow is parallel: call `480-code-reviewer` and `480-code-reviewer2` together.
- Reviewers review in-thread. `480-code-reviewer` and `480-code-reviewer2` do not spawn additional subagents.
- Keep the concurrent agent budget narrow. Outside the review step, the default path activates only one child agent at a time.
- When possible, the architect plans and delegates with a dedicated worktree and task branch as the default operating model.
- Merge or completed worktree deletion only happens when the user explicitly requests it.
- Codex manages child thread lifecycle itself. Do not add explicit close enforcement unless a separate platform contract requires it.
- When waiting on a Codex child agent, prefer longer waits over short polling loops.
- Do not repeat user-facing `still waiting` messages when there is no meaningful state change.
- User-facing wait updates should only report blockers, completion, real state changes, or long delays that help decision-making.
- Use follow-up status checks sparingly and do not make them the default waiting pattern.
- Workspace resolution should prefer the Task Brief path and explicit absolute repo/worktree paths, falling back to the current working directory only when there is no stronger hint.
- Treat a spawn response with no `agent_id`, or any non-structured spawn response, as `spawn_failure`.
- Classify `spawn_failure`, thread limit failures, and usage limit failures as delegation infrastructure blockers, not implementation blockers.
- If the blocker remains after one retry in the same session, return only a structured blocker report to the current parent session/thread.
- Low-risk fallback: if one reviewer has approved and the other reviewer is blocked only by delegation infrastructure, the architect may run an independent diff review when the changed files are limited to prompts, docs, config metadata, or tests. Continue only if that review finds no required changes. Do not waive any explicit change request from either reviewer.
- Do not make `new session` or `exception allowed` the default path for users.

You can call this directly from a Codex CLI prompt like this:
The document and examples use Codex's actual natural-language call pattern.

```text
Plan the next work for docs/480ai/example-topic/001-example-task.md.
Have 480-developer implement docs/480ai/example-topic/001-example-task.md.
Have 480-developer request review from 480-code-reviewer and 480-code-reviewer2 in parallel, then return a completion report after both approvals.
```
Recommended installs use the checked-in artifacts in `providers/codex/agents/` as-is.
Advanced installs render temporary artifacts from the selected model combination and copy them to the same install path.

## Scope notes

The Codex CLI installer manages only the custom agents and the 480ai managed AGENTS block.
Architect rules apply only to the root session, and subagents follow their own custom agent instructions.
Do not touch user-written content or any AGENTS.md content outside the 480ai managed block.

## Source of truth

- Common agent definitions: `bundles/common/agents.json`.
- Common instruction bodies: `bundles/common/instructions/`.
- Codex provider-specific override bodies, if any: `providers/codex/instructions/`.
- Provider install paths and model-selection schema: `app/providers.py`.
- Provider artifact rendering: `app/render_agents.py`.
- Install/uninstall entrypoint: `app/manage_agents.py`.
- State storage and restore: `app/installer_core.py`.
- User guidance: `README.md`.
