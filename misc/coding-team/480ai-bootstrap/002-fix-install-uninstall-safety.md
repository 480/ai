Context
- Task 001 implementation is mostly in place, but review found two safety gaps in failure/cleanup paths.
- The repo's core value is safe, idempotent install/uninstall against a user's existing `~/.config/opencode` setup.

Objective
- Close the install/uninstall edge cases so failed installs and partial uninstalls remain recoverable and non-destructive.

Scope
- Fix install so malformed or non-object `~/.config/opencode/opencode.json` cannot leave overwritten agent files without enough state/backup to recover.
- Fix uninstall so when cleanup is deferred because the user modified an installed agent file, the repo keeps the state/backups needed for a later retry.
- Add targeted regression tests for both paths.

Non-goals / Later
- No redesign of the repo layout or install UX.
- No new features beyond the reviewed safety fixes.

Constraints / Caveats
- Preserve the simple scripts-plus-docs architecture from task 001.
- Prefer minimal changes that keep install/uninstall idempotent.
- Do not weaken the existing protection against clobbering user-modified files.

Acceptance criteria
- If install fails because `opencode.json` is invalid or not a JSON object, user agent files are not left in an unrecoverable overwritten state.
- If uninstall defers cleanup because of user-modified installed files, a later uninstall run can still complete once the conflict is resolved.
