Context
- Further review found remaining retry-state and parent-path safety gaps after task 003.
- The unresolved cases are still about preserving user files across failed install/uninstall cycles.

Objective
- Make retry behavior safe after interrupted uninstall/install flows, and block parent-directory symlink escapes.

Scope
- Ensure uninstall persists state transitions in an order that does not leave stale managed metadata which could later cause user files to be overwritten without backup.
- Ensure a failed install/retry flow never overwrites the original user backup with repo-managed contents.
- Reject symlinked parent directories relevant to managed agent paths and repo install state, not just the leaf files.
- Add focused regression tests for these paths.

Non-goals / Later
- No new features or UX changes.
- No broader installer redesign.

Constraints / Caveats
- Keep changes minimal and safety-first.
- Maintain idempotent install/uninstall semantics.
- Preserve compatibility with already-written state files where practical.

Acceptance criteria
- Failed uninstall/install retry cycles do not cause newly recreated user files to be overwritten without recoverable backup.
- A failed install followed by retry still restores the original pre-install user file on uninstall.
- Symlinked parent directories such as the target `agents/` directory are rejected before any external files can be created, overwritten, or deleted.
