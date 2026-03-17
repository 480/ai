Context
- Follow-up review on tasks 001-002 found remaining edge cases around state persistence and path safety.
- These are still within the same core requirement: install/uninstall must not destroy unrelated user configuration.

Objective
- Harden install/uninstall state handling so preexisting files, retries, interrupted writes, and symlink targets are handled safely.

Scope
- Preserve preexisting agent files even when their contents already match the repo-managed version.
- Make reinstall safe when partial uninstall left state behind and some managed filenames have been recreated by the user.
- Persist recovery state/backups before destructive overwrites, and use atomic JSON writes for state/config updates.
- Prevent install/uninstall logic from following or mutating symlink targets outside the intended agent files.
- Add targeted regression tests for the above paths.

Non-goals / Later
- No broader redesign beyond these safety fixes.
- No extra product features or UX work.

Constraints / Caveats
- Keep the implementation simple, but prefer safety over cleverness.
- Maintain idempotent install/uninstall behavior.
- Keep protecting user-modified files; do not trade that away for easier cleanup.

Acceptance criteria
- Uninstall does not delete a preexisting user file merely because its contents matched the managed version.
- Reinstall after partial uninstall does not overwrite newly created user files without recoverable backup/state.
- Config/state writes are atomic enough that interruption or write failure does not leave unrecoverable config loss.
- Symlinked agent targets are not followed in a way that can mutate unrelated files.
