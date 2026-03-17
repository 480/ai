Context
- The repo currently lives at `480/480ai`, while the desired slug is `480/ai`.
- The current docs still mention `git clone` and only provide curl bootstrap for install, not uninstall.
- The product direction is a private-repo bootstrap that is operated through authenticated curl one-liners.

Objective
- Rename the GitHub repo to `480/ai` and make the bootstrap UX curl-first for both install and uninstall.

Scope
- Rename the GitHub repository from `480/480ai` to `480/ai`.
- Update the local git remote to the new slug and verify it works.
- Add a remote uninstall bootstrap script that fetches the repo to a temporary directory and runs the normal uninstall path.
- Update the existing remote install bootstrap default slug from `480/480ai` to `480/ai`.
- Rewrite repo docs so install and uninstall are documented as authenticated curl one-liners for the private repo.
- Remove clone-first guidance from README unless still needed as a secondary maintenance note.
- Update any repo documentation that mentions the old slug.

Non-goals / Later
- No local directory rename; keep `/Users/matthew/work/480ai`.
- No public anonymous install flow.
- No package registry or Homebrew support.
- No installer logic redesign beyond adding the remote uninstall entrypoint.

Constraints / Caveats
- Reuse the existing repo-managed install/uninstall logic; do not duplicate installer behavior.
- Fail clearly when GitHub authentication or repo access is missing.
- Keep the current conservative failure policy intact.

Acceptance criteria
- `gh repo view 480/ai` succeeds.
- Local `origin` points to `https://github.com/480/ai.git`.
- README documents authenticated curl one-liners for both install and uninstall using the new slug.
- Remote bootstrap scripts fetch a temporary copy of the repo and call the normal `install.sh` / `uninstall.sh` paths.
