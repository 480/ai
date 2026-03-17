Context
- Review after the lifecycle simplification found two remaining corrupted-state gaps.
- Both are in the same category: sparse or tampered state should not be treated as safe enough for destructive cleanup or config restoration.

Objective
- Make sparse/corrupted state fail conservatively and validate `previous_default_agent` before any restore logic uses it.

Scope
- Treat existing sparse/unsupported `state.json` objects (for example `{}` or similarly incomplete persisted state) as invalid for uninstall/recovery rather than silently promoting them to a default state.
- Validate `previous_default_agent`; if invalid, do not use it for config restoration and do not crash.
- Add targeted regression tests for both paths.

Non-goals / Later
- No new lifecycle behavior beyond conservative rejection of bad state.

Constraints / Caveats
- Preserve the new conservative direction.
- Prefer a safe failure with preserved live files over best-effort recovery from corrupt state.

Acceptance criteria
- Sparse/corrupted `state.json` does not enable uninstall to delete or replace live files.
- Invalid `previous_default_agent` cannot crash uninstall or corrupt `opencode.json`.
