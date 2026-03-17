Context
- One reviewer approved the current implementation.
- The remaining review found two last edge cases around failed final state writes and trust in persisted `managed`/`pending_cleanup` state.

Objective
- Close the final retry-state gaps so interrupted installs and tampered state cannot cause backup-less overwrites or leave managed files undeletable.

Scope
- Ensure a failed final `state.json` write during install cannot cause the next install/uninstall cycle to misclassify repo-managed files as user files.
- Validate or safely reject persisted `managed` and `pending_cleanup` state with the same rigor as backup metadata.
- Add focused regression tests for both paths.

Non-goals / Later
- No broader installer redesign.
- No new UX changes.

Constraints / Caveats
- Keep the changes minimal and safety-first.
- Prefer refusing unsafe recovery over guessing.

Acceptance criteria
- If the final install state write fails, a later install/uninstall cycle still behaves safely and predictably.
- Tampered or partially corrupted `managed` / `pending_cleanup` state cannot suppress backups or cause unintended deletes.
