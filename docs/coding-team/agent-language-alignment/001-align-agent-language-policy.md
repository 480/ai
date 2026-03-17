Context
- Agent output language policy is currently inconsistent and mostly implicit.
- Prompt source of truth lives in `agents/*.md`.

Objective
- Make all shipped agents default to the user's language for visible outputs and written artifacts.

Scope
- Update `agents/architect.md`, `agents/developer.md`, `agents/code-reviewer.md`, `agents/code-reviewer2.md`, and `agents/code-scanner.md`.
- Add a concise, consistent rule covering replies, reviews, Task Briefs, and reports.
- Include a soft preference that internal reasoning should follow the user's language when feasible, without treating that as a guarantee.
- Define a simple fallback for ambiguous cases, such as using the most recent user message language.

Non-goals / Later
- Do not add runtime language detection logic.
- Do not change installer behavior or config format.
- Do not rewrite unrelated prompt sections.

Constraints / Caveats
- Keep the change minimal and local to prompt text.
- Preserve each agent's existing role, workflow, and authority boundaries.
- Avoid absolute claims about hidden reasoning; phrase it as a best-effort preference only.

Acceptance criteria
- All five agent prompt files contain a materially consistent language policy.
- The policy clearly prioritizes the user's language for visible outputs.
- The wording does not conflict with the existing responsibilities of each agent.
