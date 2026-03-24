# Codex Validation Prompt Examples

## Normal Codex session

In a separate Codex session, keep the intent below:

- Call `480-developer`.
- Do not allow code edits.
- Ask it to report only whether it kept its role and redelegated.

Example:

```text
Spawn `480-developer` for a no-op validation only.
Do not edit files.
Have the child inspect its current role and report only JSON with:
{"developer_role":"...","redelegated":false,"notes":"..."}
```

The output does not have to be JSON, but the following two values must be easy to read:

- `developer_role`
- `redelegated`

## exec path

`codex exec` is a diagnostic fallback path. Try it briefly, and classify the result as a platform blocker if the exec path shows fork limitations.

Example:

```text
Use `480-developer` only for a no-op validation and report whether it stayed in role.
```

If you see `parent thread rollout unavailable for fork`, move on to normal Codex session validation.
