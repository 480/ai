Context
- Review has uncovered repeated edge cases around ambiguous lifecycle states.
- The current implementation is trying too hard to auto-resolve ambiguous situations, and that complexity is creating new safety gaps.

Objective
- Simplify the installer/uninstaller so ambiguous states are handled conservatively instead of guessed through heuristics.

Scope
- For any managed filename where both a live file and an old backup exist, treat the state as ambiguous by default:
  - install should not silently classify the live file as safe-managed based on bytes, metadata, timestamps, or persisted flags
  - uninstall should not overwrite or delete the live file while the backup also exists
  - leave enough state for a later retry once the user or operator resolves the ambiguity
- Remove dependence on `mtime` or similar heuristics for deciding whether a stale backup may overwrite a live file.
- Ensure reinstall never overwrites an existing backup with repo-managed contents.
- Normalize legacy state that lacks `pending_cleanup` so retry/upgrade flows do not dead-end.
- Add focused regression tests for:
  - repeated install then uninstall preserving the original backup
  - partial uninstall with live file + backup remaining, then reinstall/uninstall preserving the live file
  - legacy state without `pending_cleanup`, interrupted uninstall, then successful retry

Non-goals / Later
- No attempt to automatically reconcile both live and backup versions in ambiguous cases.
- No further heuristic provenance detection.

Constraints / Caveats
- Favor conservative stop-and-preserve behavior over convenience.
- Keep the change as a simplification, not another layer of special cases.

Acceptance criteria
- Ambiguous `live file + backup` states never lead to silent overwrite or deletion of the live file.
- Reinstall does not corrupt the original backup.
- Legacy state without `pending_cleanup` can still be normalized and recovered on retry.
