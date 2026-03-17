Context
- One reviewer still found a path where fully forged local state can make a recreated live file look like a safe managed file.
- The common signal in that path is: a live target file exists while an old backup for that same managed name also still exists.

Objective
- Make that situation unambiguously conservative so local state tampering cannot suppress protection.

Scope
- In install-time safety checks, treat `live file exists` + `backup exists for that managed name` as ambiguous provenance regardless of persisted managed flags, metadata, or matching bytes.
- In that ambiguous case, preserve the live file with backup or refuse overwrite; do not classify it as safely managed.
- Add a regression test covering forged `managed`, `pending_cleanup`, and `managed_file_metadata` together with a repo-identical recreated file.

Non-goals / Later
- No cryptographic provenance system.
- No installer redesign beyond this conservative rule.

Constraints / Caveats
- Prefer false-positive conservatism over destructive guesses.
- Keep the fix local and simple.

Acceptance criteria
- If a live managed filename exists while an older backup for that name also exists, reinstall does not silently treat the live file as safely managed based only on persisted state.
