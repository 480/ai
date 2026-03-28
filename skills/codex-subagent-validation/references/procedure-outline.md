# Codex Subagent Validation Procedure

## Scope

Use this document when repeating the following Codex-specific validation:

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
6. Distinguish install issues from known platform constraints such as `codex exec`.

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
- If the normal Codex session validation succeeds and the fallback `codex exec` diagnostic is fork-limited, classify the issue as `exec_path_limitation` and reserve `platform_blocker` for hard diagnostic failures.
- Only raise the likelihood of a real install issue when the normal Codex session shows the same failure.

## Result classification

- Success:
  - Reinstall applied cleanly
  - No legacy leftovers remain
  - The automated `verify` install health is clean, and the fallback `codex exec` diagnostic is either clean or classified as `exec_path_limitation`
- Install issue:
  - Managed guidance is stale or incorrect
  - Legacy leftovers remain after reinstall
  - The automated `verify` install health fails
- Exec path limitation:
  - Install state is fine, and the fallback `codex exec` diagnostic is limited by fork behavior
- Platform blocker:
  - Install state is fine, but the chosen Codex execution path cannot complete the diagnostic flow for a hard reason such as a missing binary or nonzero exit

## Notes

- If `codex exec` is known to have subagent limits, prefer the normal Codex session for the final validation.
- Keep the validation summary short and easy to judge.

## Suggested summary format

```text
install_state: ...
cleanup_result: ...
general_session_validation: not_run | ok | blocked
exec_path_result: ...
final_classification: success | exec_path_limitation | install_issue | platform_blocker
```
