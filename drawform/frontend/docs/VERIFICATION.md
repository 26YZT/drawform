# Verification and known limits

## Reproducible checks

```sh
pnpm install --frozen-lockfile
pnpm test
pnpm verify:reference
pnpm build
```

`production/test-hover.cjs` exercises decoded-frame visibility, endpoint sequencing, reverse hold, rapid target changes, frozen detail entry, reduced motion and cleanup. `production/test-appearance.cjs` exercises the neutral gate, exclusive modes, selection/pin, closing lock and reset. These are the original lightweight lifecycle/handler tests, not full browser simulations.

`scripts/ltx-generate.test.mjs` uses mocked HTTP responses. It covers preflight validation, one submission, saved IDs, output download without the API authorization header, refused overwrite, resumed polling and an uncertain POST. Tests do not use real credentials or incur charges.

`reference-manifest.json` fingerprints the approved application source, index/config presentation files, media and screenshots. Text hashing normalizes CRLF to LF for portable Git checkouts. Binary media are hashed byte-for-byte. Packaging files and guides can evolve independently. Intentional UI/media changes in a fork can legitimately fail this comparison; report them instead of erasing the baseline.

CI repeats install, tests, reference verification and build on Linux and Windows. Its current status is visible in the repository's Actions tab; a configuration file alone is not proof that a run passed.

## Approved design checks

Before public packaging, the original local design was inspected in Chromium at 1920x1080, 1440x900, 1280x720, 768x1024, 390x844 and 360x800. That review covered all paint/wheel options, hood and battery playback, both detail views, annotations, Escape and pointer travel to the dock. No horizontal overflow or black endpoint was observed in the sampled checks. Frame geometry remained stable during detail entry at short desktop and phone sizes.

Reference screenshots from that approved review are `docs/images/desktop.png` and `docs/images/mobile.png`. They are page captures, not design mockups.

Public packaging on 2026-09-11 additionally verified a fresh pnpm 10.15.1 frozen-lockfile install, all local tests and the production build. The build produced the same CSS/JS bundle hashes as the approved local project. A smoke check of the packaged build at 1440x900 and 390x844 exercised Paint, Wheels, both detail views and an annotation; no horizontal overflow was observed.

For each new fork or media change, repeat the relevant interactions in the actual browser. A screenshot cannot verify timing or keyboard behaviour, and simulated phone dimensions are not physical-device testing.

## Known limitations in v1.0.0

- Closing an appearance menu with Escape can leave keyboard focus on BODY rather than restoring it to the hotspot. This was present before the visual redesign: the existing handler requests focus before React commits the button's re-enabled state. The reproduction release preserves that behaviour; a dedicated follow-up fix should have real keyboard regression coverage.
- Physical iPhone/Safari, native touch devices and 200% text enlargement were not verified in the original approval review.
- The application requests Google Fonts; offline typography can differ.
- AI-generated cutaways are illustrative, not validated engineering/CAD.
- New LTX generation was not run for this release. The helper's network lifecycle is tested with mocks; provider-side results and costs remain unverified here.
- The page is a working design study, not a complete vehicle sales platform. No production backend or configurator pricing system is included.

## Browser review checklist

1. Inspect overview: loaded font, complete car, frame, alignment, four points and footer.
2. Move between Drive and Battery rapidly, leave during opening, then wait for neutral.
3. Enter Paint while a hover is returning; options must wait for neutral. Try every option and close.
4. Open Wheels, try every option and close; other points must become available again.
5. Enter both detail views, open every annotation, use Back and Escape.
6. Use only Tab/Enter/Escape and record focus results. Verify reduced motion separately.
7. Repeat at desktop, short desktop and phone sizes. Check overflow and menu reachability.

For exact restoration, keep source and media unchanged. For improvements, document the before/after behaviour and test the specific change.
