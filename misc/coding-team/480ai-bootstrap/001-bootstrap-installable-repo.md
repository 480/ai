Context
- `~/work/480ai` is a new repo for packaging our OpenCode agent setup so it can be installed on other machines.
- Current target setup is five agent markdown files plus `default_agent=architect`.
- Keep the distribution private-repo friendly; no npm publish or package-registry dependency.

Objective
- Turn this repo into a self-contained, installable bundle with a simple `clone -> install` and `uninstall` workflow.

Scope
- Initialize the repo contents and connect it to the intended GitHub private repository if not already connected.
- Add source-of-truth agent files to the repo for: `architect`, `developer`, `code-reviewer`, `code-reviewerer`, `repo-scout`.
- Preserve the current model and reasoning settings:
  - `architect`: `openai/gpt-5.4`, `reasoningEffort: xhigh`
  - `developer`: `openai/gpt-5.4`, `reasoningEffort: medium`
  - `code-reviewer`: `openai/gpt-5.4`, `reasoningEffort: high`
  - `code-reviewerer`: `openai/gpt-5.4`, `reasoningEffort: high`
  - `repo-scout`: `openai/gpt-5.4`, `reasoningEffort: medium`
- Implement an idempotent install path that:
  - copies or syncs the repo-managed agent files into `~/.config/opencode/agents/`
  - ensures `~/.config/opencode/opencode.json` has `default_agent` set to `architect`
  - records enough install state to support safe uninstall
- Implement an uninstall path that removes only repo-managed installed assets and safely restores the prior default agent when possible.
- Add concise docs covering install, update, uninstall, and expected file locations.

Non-goals / Later
- No npm package, GitHub Package, or Homebrew distribution.
- No OpenCode plugin, MCP bundle, or OmO-style orchestration runtime.
- No extra agents, skills, or commands beyond the current five-agent setup.

Constraints / Caveats
- Prefer the smallest robust design.
- Make install/uninstall safe to run multiple times.
- Do not clobber unrelated user-managed agents or OpenCode settings.
- If you need persistent install metadata, keep it minimal and clearly namespaced to this repo/setup.
- Follow the repo shape implied by a private bootstrap repo: scripts plus docs, not a complex app.

Acceptance criteria
- A user on another machine can clone the repo and follow the docs to install the five agents and `default_agent=architect`.
- Re-running install does not duplicate or corrupt configuration.
- Running uninstall removes this repo's installed agents and does not delete unrelated OpenCode configuration.
