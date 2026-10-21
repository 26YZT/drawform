# Work with Codex, Claude Code or Hermes

All three workflows use the same local repository, commands, media and acceptance criteria. The agent needs file access, terminal execution and permission to install the pinned project dependencies. Browser access is useful for visual verification.

## First task

Either open a cloned checkout, or start in an empty parent directory and paste the complete text of [RECREATE.md](RECREATE.md). The prompt includes the public repository URL and approved release tag, so it can bootstrap the project. An agent without terminal/network access cannot perform the full installation; download the GitHub release yourself and open that folder.

## Codex

Install/sign in using the [official Codex CLI guide](https://learn.chatgpt.com/docs/codex/cli). In a terminal:

```sh
cd veyra-interactive-car
codex
```

Paste the reproduction prompt, or ask it to read `docs/RECREATE.md` and execute it. In the desktop app, open the cloned folder as the project and send the same task. Give the agent access to this project and the package registry as needed; use the normal permission controls. No special Codex plugin is required by this repository.

## Claude Code

Install and sign in following [Claude Code's quickstart](https://code.claude.com/docs/en/quickstart). Then:

```sh
cd veyra-interactive-car
claude
```

Use the same task text. `CLAUDE.md` directs Claude to the shared `AGENTS.md`; no separate interpretation of the design is required. If using [Claude Code on the web](https://code.claude.com/docs/en/web-quickstart), fork the repository, connect that fork and select it for the task. Make the package registry available in the environment. Local preview addresses in a cloud container are not addresses on your laptop.

## Hermes Agent

Install and configure a provider using the [official Hermes quickstart](https://hermes-agent.nousresearch.com/docs/getting-started/quickstart). Then:

```sh
cd veyra-interactive-car
hermes
```

Paste the prompt and explicitly tell it to read `AGENTS.md`. For a file-based request, the documented CLI also supports:

```sh
hermes chat --query-file docs/RECREATE.md
```

See [Hermes CLI](https://hermes-agent.nousresearch.com/docs/user-guide/cli). Enable its terminal/file tools in your environment. Provider and model credentials belong to Hermes configuration; LTX credentials are a separate optional concern.

## Continue after reproduction

Keep the first task focused on restoring and verifying the release. Start a branch before adaptation. Good follow-up tasks name the intended change and the contracts to retain, for example:

> Adjust the footer copy for my fictional vehicle while retaining the approved composition, fonts, four hotspots, media and state logic. Run the existing tests, build, and compare desktop/mobile screenshots. Report intentional differences from the reference.

For optional media generation:

> Read docs/LTX_API.md and prepare one hood-opening experiment in generated/. Validate the request with a dry run. Show the model, duration, resolution, input images and current cost basis before making a paid submission. Keep existing website media untouched.

Set your API key in your terminal or local environment file yourself. Do not paste secrets into the agent's prompt. If an agent asks to put a key in a VITE_ variable, direct it back to the terminal helper.

When handing work to another agent, include the current branch/commit, changed files, checks already run, observed defects and next concrete action. It should need repository context, not the author's original conversation.

Documentation links checked on 2026-09-11. Agent installation and interface details can change; the repository commands are the project contract.
