# Contributing

Open an issue for a proposed behaviour change, or send a focused pull request with the problem, resulting behaviour and checks performed.

1. Fork and clone the repository; create a working branch.
2. Read AGENTS.md and the architecture guide.
3. Install with `pnpm install --frozen-lockfile`.
4. Make a scoped change, keeping unrelated visual and media work intact.
5. Run `pnpm test` and `pnpm build`. Include screenshots for UI changes and describe keyboard/mobile checks.

`pnpm verify:reference` checks fidelity to the initial release, not whether every future change is valid. If intentionally changing a protected file, report the expected mismatches in the PR. Do not silently update `reference-manifest.json`; a new approved reference belongs to a deliberate release.

Do not include node_modules, generated experiments, local logs, API keys or customer assets. Media contributions need clear provenance and permission to redistribute. Keep the original license notice in forks.
