# Media and production workflow

## Included files

All 18 media files are tracked directly by Git, including the approved runtime set and earlier stills retained for context. No external CDN or expired generation URL is required. `reference-manifest.json` records file bytes and SHA-256 digests. An exact asset can also be retrieved from:

```text
https://raw.githubusercontent.com/amirmushichge/veyra-interactive-car/v1.0.0/public/media/exterior-polished.png
```

Use the corresponding repository path for another file. Prefer cloning the release to hotlinking GitHub assets from a production site; serve media with your own build.

| Path under `public/media/` | Role |
| --- | --- |
| `exterior-polished.png` | Approved neutral car; base for Silver and Multi-spoke |
| `hood-hover-forward.mp4` / `hood-hover-reverse.mp4` | Authored hood movement and return |
| `battery-hover-forward.mp4` / `battery-hover-reverse.mp4` | Authored X-ray reveal and return |
| `drive-blue.png` / `battery-blue.png` | Active detail destinations |
| `config/electric-green.png` | Electric Green paint |
| `config/lime-green.png` | Lime Green paint |
| `config/sky-blue.png` | Sky Blue paint |
| `config/graphite.png` | Graphite paint |
| `config/wheels-aero.png` | Aero Disc wheels |
| `config/wheels-forged.png` | Sport Forged wheels |
| `lenses-blue.png` | Dormant optics study retained in content definitions |
| `exterior-blue.png`, `exterior.png`, `drive.png`, `battery.png` | Earlier reference stills, not the active hero |

There are four working hotspots. Do not treat the reserved `battery-forward.mp4`, `drive-forward.mp4` or optics clip declarations in content.ts as missing runtime dependencies; those detail flights are disabled and not implemented.

## Reuse the approved media

For the same website, do not regenerate, resize, transcode, recompress or recolour these files. The four final hover exports are 60fps exports finished by Amir in After Effects. Their independent forward/reverse timing is part of the interaction contract.

Keep the full car, both visible wheels, the open hood and contact shadow. Images and videos may differ slightly in native dimensions, so preserve the current `object-fit: contain` rules and shared image plane.

## Make a new set

1. Establish one neutral exterior, with enough space above the hood and below the wheels. Fix the camera, focal perspective, lighting, crop and studio background.
2. Prepare matching end states: hood open and battery reveal. A battery cutaway close-up is **not** the end frame of an overview hover.
3. Generate only the intended motion between registered endpoints. Use the optional LTX guide, or another tool whose output you can export and redistribute.
4. Review the source at normal and accelerated speed. Watch roof curvature, pillars, door edges, wheel arches, reflections and floor contacts. Prompt constraints reduce unwanted movement; they do not guarantee rigid geometry.
5. Finish in an editor. Trim to a short deliberate interaction, use restrained easing and export forward and reverse files separately. Match neutral/open seam poses. A mathematically reversed clip can be a starting point for a new asset, but is not equivalent to an independently authored return.
6. Review every seam against the stills. Preserve the last frame until the next source has decoded. Never compensate for mismatched assets with arbitrary player seeks.
7. Store candidate outputs outside public/media. After approval, replace the whole affected pair, document the new metadata and test all interaction states.

## Timing and FPS

Speeding a 24fps source uniformly by 2.5x can display its original frames at 60fps without synthesis. A variable time-remap with easing may still repeat frames near slow endpoints. Exporting a 24fps timeline at 60fps alone does not invent intermediate motion. Inspect any optical-flow interpolation for deformation before accepting it.

The site does not retime video in JavaScript. Its player expects the final creative timing to be embedded in the exported files. Preserve colour, dimensions, audio policy and timing deliberately when making your own set.

## Media checklist

- Neutral exterior matches both reverse endpoints.
- Forward endpoints match the intended open/revealed poses.
- No roof warping, silhouette drift, camera motion or scale jump.
- No black first/last frames or one-frame flashes at the handoff.
- Hotspots stay attached to their intended details.
- Hover is still readable after final speed changes.
- Appearance variants share the neutral composition and do not alter unintended parts.
- Files decode on target browsers and devices; credit/provenance accompanies redistributed media.

See THIRD_PARTY_NOTICES.md for the actual origin and licensing of the bundled set.
