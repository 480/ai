---
name: codex-subagent-validation
description: Reinstall Codex, check user-level AGENTS and legacy subagent leftovers, and verify that `480-developer` delegation works in a separate Codex session. Use this when Codex subagents appear to be redelegating incorrectly, when `~/.codex` install state is suspect, or when you need to repeat Codex validation after a harness change.
---

# Codex Subagent Validation

## Overview

Use this skill when you need to repeat the Codex-specific validation flow. It checks install state, the user-level AGENTS scope, whether legacy agent artifacts were cleaned up, and whether actual `480-developer` delegation works.

Prefer the simplest path that can distinguish an install issue from a Codex platform limitation.

## Workflow shape

This skill follows a sequential, workflow-based validation process.

1. Confirm that the issue is actually a Codex subagent validation problem.
2. Check the `~/.codex` install state and any legacy leftovers.
3. Reinstall Codex if needed.
4. Validate delegation behavior in a separate Codex session.
5. Classify the result as success, exec_path_limitation, install_issue, or platform_blocker.

## Quick start

Use this as the default path:

1. Confirm that you are working from the repository root.
2. Check `~/.codex/AGENTS.md` managed blocks and any legacy artifacts under `~/.codex/agents/`.
3. Reapply the Codex user-level install with `python3 -m app.manage_agents install --target codex --scope user` if needed.
4. Start a separate Codex session with `codex --no-alt-screen` and validate `480-developer` delegation from the normal session path.
5. Summarize the result as one of the following:
   - success
   - exec_path_limitation
   - install_issue
   - platform_blocker

Treat `codex exec` only as a secondary path. Responses such as `parent thread rollout unavailable for fork` can be exec-path limitations, so do not classify them as install issues on that signal alone.

## Resources

### references/

Read [references/procedure-outline.md](references/procedure-outline.md) when you need the detailed checklist, expected signals, or the boundary between an install issue and a known `exec` limitation.

Read [references/validation-prompts.md](references/validation-prompts.md) when you need prompt examples and result-reporting formats for a separate Codex session.

Do not add `scripts/` yet unless shell-only guidance becomes too unstable for repeated validation.

## Implementation notes

Keep the main `SKILL.md` short. Move detailed commands, expected output, and known platform caveats into `references/`.

## Reporting

Report these three things at minimum:

1. The install state before the change
2. What changed during reinstall or cleanup
3. Whether the separate Codex validation produced `success`, `exec_path_limitation`, `install_issue`, or `platform_blocker`

If possible, summarize using the following keys:

- `install_state`
- `cleanup_result`
- `general_session_validation`
- `exec_path_result`
- `final_classification`

When using `python3 -m app.manage_agents verify`, treat `general_session_validation` as the separate normal-session check and `exec_path_result` as the diagnostic fallback path. The automated verify command reports the install health separately from the fallback path and does not replace a real normal Codex session validation.

If `exec_path_result` comes back limited by fork behavior, expect `final_classification` to be `exec_path_limitation`; hard failures such as a missing `codex` binary or a nonzero exec run should still surface as `platform_blocker`.
