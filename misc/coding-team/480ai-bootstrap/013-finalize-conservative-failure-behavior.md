Context
- The acceptance criteria are now explicitly conservative: ambiguous or corrupted state should fail safely rather than attempting full automatic recovery.
- Current implementation is close, but two remaining paths still cut against that direction.

Objective
- Finish the bootstrap installer so failed install/uninstall flows preserve user files/config and stop early when recovery state is not trustworthy.

Scope
- In `install()`, if a prior run copied repo-managed files but failed before the final persisted state was fully written (for example, failure writing `opencode.json` or the last `state.json`), a retry must not promote those repo-managed files into fake user backups.
- In `uninstall()`, validate/read `~/.config/opencode/opencode.json` before any file deletion/restoration work that depends on later config restoration. If config is invalid, fail before touching managed files.
- Update the user-facing docs to state the conservative contract clearly: ambiguous `live file + backup` or corrupted state/config may require manual cleanup, but the installer avoids destructive guesses.
- Add focused regression tests for the two safety paths above.

Non-goals / Later
- No automatic healing for every interrupted lifecycle.
- No more provenance heuristics.

Constraints / Caveats
- Prefer a clean early failure over partial uninstall/install.
- Keep the implementation simple and aligned with the new conservative policy.

Acceptance criteria
- A failed install followed by retry does not create fake backups of repo-managed files.
- Invalid `opencode.json` causes uninstall to fail before agent files are deleted/restored.
- README documents that ambiguous/corrupted states are intentionally handled conservatively and may require manual resolution.
