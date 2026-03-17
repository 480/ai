Context
- Review found one remaining issue in the new curl one-liner docs.
- The current `curl ... | sh` examples can mask authentication or fetch failures because the shell pipeline may still exit successfully.

Objective
- Make the documented install/uninstall one-liners fail clearly when the remote script cannot be fetched.

Scope
- Update the README install and uninstall one-liners so fetch/auth failures propagate as a non-zero command failure.
- Keep the flow curl-first and private-repo-friendly.
- Do not redesign the bootstrap scripts themselves unless needed for this exact failure-propagation fix.

Non-goals / Later
- No broader README rewrite.
- No installer behavior changes beyond the user-facing invocation pattern if that is sufficient.

Constraints / Caveats
- The command should remain a practical one-liner.
- It must still support either `gh auth token` or pre-exported `GITHUB_TOKEN`.

Acceptance criteria
- The README install and uninstall one-liners no longer report success when the remote fetch fails due to auth/access errors.
