# Codex Subagent Validation Procedure

## Scope

Use this document when repeating the following Codex-specific harness validation:

- user-level `~/.codex` install state
- the scope of `~/.codex/AGENTS.md` managed guidance
- legacy `480-architect.toml` / `480.toml` leftovers
- actual `480-developer` delegation behavior in a separate Codex session

## Checklist

1. Check the install state before changing anything.
2. Confirm that `~/.codex/AGENTS.md` contains the expected managed guidance.
3. Confirm that legacy `480-architect.toml` or `480.toml` artifacts are not present.
4. Reinstall Codex when that matches the task intent.
5. Validate `480-developer` delegation in a separate Codex session.
6. Distinguish harness regressions from known platform constraints such as `codex exec`.

## Recommended command order

Use the following order from the repository root:

```bash
pwd
if [ -d ~/.codex/agents ]; then ls -la ~/.codex/agents; else echo "NO_CODEX_AGENTS_DIR"; fi
if [ -f ~/.codex/AGENTS.md ]; then sed -n '1,220p' ~/.codex/AGENTS.md; else echo "NO_CODEX_AGENTS_MD"; fi
python3 -m app.manage_agents install --target codex --scope user
if [ -d ~/.codex/agents ]; then ls -la ~/.codex/agents; else echo "NO_CODEX_AGENTS_DIR"; fi
if [ -f ~/.codex/AGENTS.md ]; then sed -n '1,220p' ~/.codex/AGENTS.md; else echo "NO_CODEX_AGENTS_MD"; fi
```

While checking the output, confirm the following in particular:

- `~/.codex/agents/` should not contain `480-architect.toml` or `480.toml`.
- The `~/.codex/AGENTS.md` managed block should include root-session-only scope language.

## General Codex session validation

Prefer a normal Codex session for the final validation.

1. Open a separate terminal and move to the repository root.
2. Start a new Codex session with `codex --no-alt-screen`.
3. Use a prompt like the one below to call `480-developer`.
   - Do not edit code
   - Report only the current role and whether the session redelegated
   - Prefer JSON or key-value output if possible

Success signals are:

- The child or root report shows `developer_role: "480-developer"`
- The report shows `redelegated: false` or an equivalent meaning
- The developer does not try to redelegate implementation work to `480-developer` again

## exec path interpretation

Use `codex exec` only as a secondary diagnostic path.

- Treat responses such as `parent thread rollout unavailable for fork` as exec-path limitations.
- If the normal Codex session validation succeeds, classify the issue as a platform blocker rather than a harness regression.
- Only raise the likelihood of a real regression when the normal Codex session shows the same failure.

## Result classification

- Success:
  - Reinstall applied cleanly
  - No legacy leftovers remain
  - In a separate Codex session, `480-developer` keeps its role and does not redelegate to itself
- Harness regression:
  - Managed guidance is stale or incorrect
  - Legacy leftovers remain after reinstall
  - In a separate Codex session, `480-developer` behaves like the architect or redelegates itself
- Platform blocker:
  - Install state is fine, but the chosen Codex execution path does not support fork behavior or cannot complete the validation flow

## Notes

- If `codex exec` is known to have subagent limits, prefer the normal Codex session for the final validation.
- Keep the validation summary short and easy to judge.

## Suggested summary format

```text
install_state: ...
cleanup_result: ...
general_session_validation: ...
exec_path_result: ...
final_classification: success | harness_regression | platform_blocker
```
