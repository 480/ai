Context
- Review found two remaining gaps in retry behavior and trust in persisted state.
- Both matter because this bootstrap repo must avoid destroying unrelated user files even after interrupted flows or corrupted state.

Objective
- Protect newly recreated user files after failed uninstall flows, and stop `state.json` from steering restore/delete logic outside the managed scope.

Scope
- Ensure reinstall after failed uninstall either preserves a newly recreated user file with recoverable backup or refuses to overwrite it.
- Validate persisted state so only known managed agent names are accepted and backup paths cannot escape the intended backup directory.
- Add targeted regression tests for both paths.

Non-goals / Later
- No new installer features.
- No broader persistence redesign beyond what is needed for safety.

Constraints / Caveats
- Safety over convenience.
- Keep behavior simple and deterministic.
- Continue to support retryable install/uninstall flows.

Acceptance criteria
- After a failed uninstall, a user-created replacement file is not silently lost on reinstall/uninstall.
- A tampered or corrupted `state.json` cannot cause restore/delete operations outside the managed agent/backup scope.
