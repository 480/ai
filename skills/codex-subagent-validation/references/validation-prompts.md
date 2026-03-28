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

`codex exec` is a diagnostic fallback path. Try it briefly, and classify fork-limited results as `exec_path_limitation`.

Example:

```text
Use `480-developer` only for a no-op validation and report whether it stayed in role.
```

If you see `parent thread rollout unavailable for fork`, move on to normal Codex session validation.

The automated `verify` command in this repository reports install health separately from the `codex exec` diagnostic path. Do not treat an exec-path limitation as a full install failure unless the install state or cleanup state also fails.

When reading `verify` output, treat a fork-limited `codex exec` run as `exec_path_limitation` and a hard diagnostic failure such as a missing binary or nonzero exit as `platform_blocker`.
