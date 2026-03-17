Context
- The local bootstrap repo exists at `~/work/480ai` and already has `origin` pointed at `https://github.com/480/480ai.git`.
- The GitHub repository itself does not exist yet, so remote installation from GitHub is not possible.

Objective
- Create the private GitHub repository `480/480ai` and make the local repo point to it successfully.

Scope
- Use GitHub CLI to create `480/480ai` as a private repository under the authenticated `480` account/org.
- Verify the repository exists and that local `origin` matches it.
- Do not create commits or push unless separately requested.

Non-goals / Later
- No commit creation.
- No initial push.

Constraints / Caveats
- Keep this limited to repo creation and remote verification.

Acceptance criteria
- `gh repo view 480/480ai` succeeds.
- Local `origin` remains configured to `https://github.com/480/480ai.git`.
