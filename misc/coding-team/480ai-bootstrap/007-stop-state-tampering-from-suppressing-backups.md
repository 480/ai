Context
- One reviewer still found a tampered-state path where persisted `managed=true` / `pending_cleanup=false` can suppress backup of a user-recreated file after a partial uninstall.
- This is the last known gap against the non-destructive retry requirement.

Objective
- Ensure ambiguous or tampered managed-state cannot cause a recreated user file to be overwritten or deleted without protection.

Scope
- Tighten install-time trust in persisted `managed` / `pending_cleanup` so a recreated live file is only treated as safely managed when the state is both valid and consistent with the actual on-disk situation.
- If the situation is ambiguous, preserve the user file via backup or refuse the overwrite.
- Add a regression test covering: partial uninstall -> state tampering -> user recreates file -> reinstall/uninstall must not lose the recreated file.

Non-goals / Later
- No broader state redesign.
- No new installer features.

Constraints / Caveats
- Prefer conservative refusal over risky automatic recovery.
- Keep the change minimal and local to this safety gap.

Acceptance criteria
- Tampered `managed` / `pending_cleanup` state cannot suppress backup or protection of a recreated user file after partial uninstall.
