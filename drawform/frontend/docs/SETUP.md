# Run the approved site

## Prerequisites

Use Node.js 22 or newer, Git and pnpm 10.15.1. The repository pins the package manager and dependency lockfile. If pnpm is not installed, with Node/npm available run:

```sh
npm install --global pnpm@10.15.1
```

Then follow the clone/install commands in README.md. They work in PowerShell, macOS and Linux shells. No database, .env file, LTX key, paid agent subscription or GPU is needed for ordinary manual setup.

For your own ongoing work, fork the repository on GitHub, clone your fork and create a working branch. To restore the approved visual baseline, use tag `v1.0.0`. Do not copy another project's `node_modules` into this project.

## Commands

| Command | Purpose |
| --- | --- |
| `pnpm install --frozen-lockfile` | Install the pinned dependency graph |
| `pnpm dev` | Development server at http://127.0.0.1:5220/ |
| `pnpm test` | Hover, appearance and mocked LTX helper tests |
| `pnpm verify:reference` | Check approved source, media and reference hashes |
| `pnpm build` | Type-check and build the static site |
| `pnpm preview` | Serve `dist/` on the same local port |

The port is intentionally strict. Stop the development server with Ctrl+C before starting the built preview. If another project uses 5220, identify it; do not kill unrelated processes or silently start several copies. For a deliberate alternate port, use `pnpm dev --port 5221` and keep that address throughout your own session.

For physical-device checks on a trusted local network, bind the dev server deliberately with `pnpm dev --host 0.0.0.0` and visit the machine's LAN address. The default binds only to loopback. Do not expose a development server to the public internet.

## Common problems

- **`pnpm` or `node` not found:** install the prerequisite, reopen the terminal and check `node --version` and `pnpm --version`.
- **Platform package error:** use the release package.json and lockfile together, then install afresh. esbuild selects the appropriate platform binary; there is no direct Windows-only dependency in this release.
- **Asset 404:** preserve `public/media/` including `config/`. Run through Vite, not by double-clicking index.html. Paths are case-sensitive on Linux.
- **Different typography:** allow Google Fonts and wait for Space Grotesk to load. Offline fallback text can change wrapping. To self-host, obtain the font and its upstream OFL license, then update the CSS in a separate customisation branch.
- **Black or missing hover:** check all four MP4 requests and browser console; a device may block or fail video decoding. Inspect the files before altering player timing.
- **Reference check fails after edits:** it is a fidelity check. Restore from the release for exact reproduction, or record the deliberate differences for your fork.

## Static hosting in your fork

Build command: `pnpm build`. Publish directory: `dist`. No server-side application is required. Run the same tests and inspect the built preview first. Deployment is a separate action; the reproduction prompt only starts a local site.

This release assumes the site lives at the origin root. A custom domain or root-based static host can serve the build as-is. A GitHub Pages project URL such as `/veyra-interactive-car/` requires changing **both** Vite's base and the root-absolute media URLs in App.tsx, HoverVideo.tsx, appearance.ts and content.ts. Setting Vite base alone will not rewrite those string literals. Keep that adaptation separate from the exact reference version.

Do not publish `.env.ltx.local` or `generated/` with a site. To include newly approved media, copy only the chosen finished assets into `public/media/`, keep their names/metadata deliberate and re-run browser checks.
