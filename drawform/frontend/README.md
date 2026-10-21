# VEYRA — Electric, inside out.

An interactive automotive design study by **Amir Mušić**. Explore an electric vehicle through short films, technical cutaways and a compact appearance configurator.

The idea: give rendered media the behaviour of a product interface. The craft lives in the relationship between the image, the interaction and the handoff between states.

![The approved VEYRA desktop experience](docs/images/desktop.png)

**[Run locally](docs/SETUP.md) · [One-prompt reproduction](docs/RECREATE.md) · [Work with an agent](docs/AGENTS_GUIDE.md) · [LTX API guide](docs/LTX_API.md) · [Русский старт](docs/START_RU.md)**

## Start from the finished experience

All images and four hover films are included in `public/media`. No API key, GPU, account, asset service or Git LFS is needed to run the site. The font loads from Google Fonts; other runtime media are local.

Install [Node.js](https://nodejs.org/en/download) 22 or later, Git and pnpm 10.15.1, then:

```sh
git clone --branch v1.0.0 https://github.com/amirmushichge/veyra-interactive-car.git
cd veyra-interactive-car
pnpm install --frozen-lockfile
pnpm test
pnpm verify:reference
pnpm dev
```

Open **http://127.0.0.1:5220/**. Cloning a tag gives a detached checkout; create your own branch with `git switch -c my-veyra` before making changes. To follow ongoing development instead, clone without `--branch v1.0.0`.

## What you can explore

- **Electric drive:** hover to open the hood, leave to close it, click to inspect the drive unit.
- **Battery:** hover to reveal the battery, leave to restore the exterior, click to inspect the pack.
- **Paint:** five body finishes, shown only after the car returns to its neutral pose.
- **Wheels:** three wheel designs; one appearance mode can be active at a time.
- **Detail views:** component annotations, return control, keyboard operation and reduced-motion handling.

The final composition uses Space Grotesk Regular, a blue studio, white typography, electric-green controls and a white 3px media frame. Appearance menus sit below the image. Desktop and phone layouts share the same coordinate system.

This release is an interaction scene, not a scroll-scrubbed landing page. Click-to-detail uses a CSS zoom/crossfade to a still cutaway. Hover uses real forward/reverse video files. No WebGL or 3D model is required.

## Reproduce it with one prompt

Copy **[the complete reproduction prompt](docs/RECREATE.md)** into Codex, Claude Code, Hermes Agent or another coding agent with terminal and file access. It tells the agent to obtain the pinned release, preserve its exact assets and behaviour, install, test and open the site.

The release is the reference. A fresh generative rewrite cannot promise identical pixels; restoring the reference files makes the result reproducible. The prompt also specifies layout, state contracts, media paths and acceptance checks for agents adapting the project.

## Generate new media with LTX

Create a key in the **[LTX API Console](https://console.ltx.io/api-keys)**, then follow **[LTX API setup and generation](docs/LTX_API.md)**. The optional Node script supports a free local dry run, explicit paid submission, saved job IDs and resuming an existing job.

LTX is an optional production tool for new assets. The approved hover films included here were generated with Kling 3.0 through Pika and retimed/exported by Amir in After Effects. They are not presented as LTX outputs. Reusing this release does not call either service.

## Learn, fork, extend

| Guide | What it covers |
| --- | --- |
| [Setup](docs/SETUP.md) | Windows/macOS/Linux, preview, build, troubleshooting, static hosting |
| [Agent workflows](docs/AGENTS_GUIDE.md) | Codex, Claude Code, Hermes, safe iteration and task handoffs |
| [Architecture](docs/ARCHITECTURE.md) | State transitions, decode gates, neutral pose, responsive geometry |
| [Media workflow](docs/MEDIA.md) | Asset map, forward/reverse preparation, new media review |
| [LTX API](docs/LTX_API.md) | Console, credentials, dry run, submission, resume, output handling |
| [Reproduction prompt](docs/RECREATE.md) | Copy/paste instructions for the approved site |
| [Verification](docs/VERIFICATION.md) | Automated coverage, manual checks, known limits |

React 18 · TypeScript · Vite · Tailwind CSS · Lucide icons. No backend is required for playback. `pnpm build` produces a static `dist/` directory. See the hosting notes before using a subpath such as GitHub Pages.

## License and attribution

[MIT](LICENSE) for the project code and documentation. Bundled concept media are also offered under MIT to the extent of the author's rights; see [asset provenance and third-party notices](THIRD_PARTY_NOTICES.md). Retain the license when redistributing. If you share a fork or use the method in a case study, a link to this repository is appreciated.

VEYRA is a fictional independent design study. It is not an automaker, a verified engineering model, or an affiliation with any car manufacturer. No range, power, charging, pricing or certification claims are made.

Created by [Amir Mušić](https://github.com/amirmushichge).
