Context
- The repo now encodes two new operating rules in agent prompts: visible outputs should follow the user's language, and `@architect` should continue an approved plan without unnecessary pauses.
- `README.md` does not mention these runtime expectations yet.

Objective
- Document the new runtime behavior in `README.md` so operators understand how installed agents are expected to behave.

Scope
- Edit `README.md` only.
- Add a short note in an appropriate place describing that installed agents default visible outputs to the user's language and that `architect` continues approved plans automatically unless a real pause condition exists.
- Keep the wording aligned with the repository's existing concise operational style.

Non-goals / Later
- Do not rewrite the install docs.
- Do not change agent prompts in this task.
- Do not add a long design/architecture section.

Constraints / Caveats
- Keep the change minimal.
- Avoid overpromising hidden-reasoning behavior; describe only visible runtime behavior.
- Keep the autopilot wording scoped to post-plan-approval behavior.

Acceptance criteria
- `README.md` mentions both runtime rules in concise operator-facing language.
