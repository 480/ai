Context
- `@architect` currently requires explicit approval for requirements and plan, but it does not clearly state what happens after plan approval.
- We want the architect to keep driving the approved plan forward without pausing for unnecessary user confirmations.

Objective
- Update the `@architect` prompt so that, once the user approves the plan, the architect continues through the planned tasks until the plan is complete.

Scope
- Edit `agents/architect.md` only.
- Add a clear rule that after plan approval the architect should proceed task-by-task automatically: write the current Task Brief, delegate to `@developer`, wait for the implementation/review loop to finish, then continue to the next planned task.
- Add narrow exceptions for when the architect should pause and return to the user: approved scope is invalidated, a destructive/security-sensitive decision needs user input, credentials/external values are required, or there is a true blocker that cannot be resolved within the developer/reviewer loop.
- Preserve the existing requirement that requirements approval and plan approval must still happen before implementation starts.

Non-goals / Later
- Do not change other agent prompts.
- Do not change installer behavior.
- Do not redesign the developer/reviewer workflow.

Constraints / Caveats
- Keep the change minimal and local to the architect prompt.
- The new rule must not conflict with the existing approval gates.
- Phrase the autopilot behavior as applying after plan approval, not before.

Acceptance criteria
- `agents/architect.md` explicitly says the architect keeps executing the approved plan to completion without additional user approval between tasks.
- The prompt also clearly lists the limited cases where the architect should pause and return to the user.
