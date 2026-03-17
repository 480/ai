Context
- Review found one final edge case still open.
- After partial uninstall plus state tampering, a user can recreate a file with the same bytes as the repo-managed version, and current logic may still treat it as safely managed and later delete it.

Objective
- Close the same-content recreated-file gap so tampered state cannot suppress protection merely because the recreated file matches repo contents byte-for-byte.

Scope
- Tighten the install-time safety check so persisted managed-state is not enough to treat a live file as safely managed when the prior lifecycle was ambiguous.
- In this ambiguous case, preserve the live file via backup or refuse the overwrite/uninstall path rather than deleting it later.
- Add a regression test for: partial uninstall -> state tampering -> recreated file with repo-identical contents -> reinstall/uninstall must not lose the file.

Non-goals / Later
- No broader lifecycle redesign.
- No extra UX or documentation work unless needed for clarity.

Constraints / Caveats
- Prefer conservative behavior over guessing provenance from identical bytes.
- Keep the fix minimal and local to this gap.

Acceptance criteria
- Tampered lifecycle state cannot cause a recreated file with repo-identical contents to be silently deleted on a later uninstall.
