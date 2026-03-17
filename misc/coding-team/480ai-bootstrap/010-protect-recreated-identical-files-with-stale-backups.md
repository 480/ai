Context
- One reviewer still found a non-forged path: a normal partial uninstall can leave a stale backup, then a user recreates the same file with repo-identical contents, and reinstall/uninstall can still restore the stale backup over the recreated file.

Objective
- Treat any `live file + stale backup` combination as ambiguous, even without state tampering, so a recreated repo-identical file is not later lost.

Scope
- In install/uninstall logic, if a live managed filename exists while an older backup for that same name also exists, do not let later uninstall blindly restore the stale backup over the live file merely because contents match the repo.
- Preserve the recreated live file or fail conservatively; do not silently delete/replace it.
- Add a regression test for: partial uninstall -> backup remains -> repo-identical file recreated -> reinstall -> uninstall must not lose the recreated file.

Non-goals / Later
- No attempt to keep both historical versions automatically unless needed for this safety fix.
- No broader state model redesign.

Constraints / Caveats
- Keep the change minimal.
- Prefer conservative preservation of the current live file.

Acceptance criteria
- A stale backup left by partial uninstall cannot later overwrite or delete a repo-identical recreated live file during reinstall/uninstall.
