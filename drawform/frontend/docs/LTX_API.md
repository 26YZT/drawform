# LTX API: optional media generation

The website runs entirely from the bundled files. This guide is for creating a new take or adapting the concept to your own vehicle. It does not regenerate the approved release and does not add live generation to the webpage.

## Console and credentials

1. Open **[https://console.ltx.io/api-keys](https://console.ltx.io/api-keys)** and create your own API key.
2. Check your account's available credits and [current pricing](https://docs.ltx.io/pricing).
3. Copy `.env.example` to `.env.ltx.local`, then put the key after `LTX_API_KEY=` using your local editor. Both POSIX shells and PowerShell can use `cp .env.example .env.ltx.local`.
4. Keep the file local. The helper reads it through Node's `--env-file`; it does not enter the Vite bundle. Never use `VITE_LTX_API_KEY`.

The [LTX authentication guide](https://docs.ltx.io/authentication) describes bearer-key authentication. Agent-provider billing and LTX billing are separate. The key is not needed for a dry run.

## Free dry run

From the repository root:

```sh
node scripts/ltx-generate.mjs examples/ltx/hood-open.json
node scripts/ltx-generate.mjs examples/ltx/battery-reveal.json
```

These commands read the local image, validate the example request and print a compact summary. They make no network request. The initial examples use LTX-2.5 Fast, 6 seconds, 1920x1080, 24fps and no audio. Those settings are supported by the [LTX-2.5 model matrix](https://docs.ltx.io/models/ltx-2-5), checked on 2026-09-11. They are generation settings, not the 60fps metadata of the approved AE exports.

Our helper deliberately supports a small subset: Fast/Pro, fixed 6/8/10 seconds, landscape 720p/1080p and the listed frame rates. Consult the current provider reference before extending that validation. No price is hard-coded.

## Submit one paid generation

After you approve the input upload and generation cost:

```sh
node --env-file=.env.ltx.local scripts/ltx-generate.mjs examples/ltx/hood-open.json --submit --out generated/hood-01
```

This is the command that sends the image to LTX and can incur a charge. Use a new take directory for each deliberate generation. There is no automatic batch or retry of the submission.

The helper uses [LTX v2 image-to-video](https://docs.ltx.io/api-documentation/api-reference/async-video-generation/submit-image-to-video). It saves the request and submission marker before posting, then immediately stores the returned job ID. It polls the job and downloads the MP4 into that take directory. The browser never sees the API key.

Successful output:

```text
generated/hood-01/
  request.json
  submission-started.json
  job.json
  status.json
  video.mp4
```

`request.json` can include inline image data. The whole generated directory is ignored by Git; it is a private working area, not a public log.

## Resume after an interruption

```sh
node --env-file=.env.ltx.local scripts/ltx-generate.mjs --resume generated/hood-01
```

Resume uses the saved ID and does not create another generation. Transient polling/download requests can retry. An uncertain submission never retries automatically. If a take has `submission-started.json` but no `job.json`, inspect your console/account history before any new request; do not just create a new take and repeat the charge.

The [async job guide](https://docs.ltx.io/async-jobs) explains status and result retention. Download completed output promptly: provider result URLs are temporary. The helper stops on failure, unexpected status or its polling time limit and preserves the job record. It refuses to overwrite an existing `video.mp4`.

## First and last frames

The API accepts `image_uri` for the first frame and optional `last_frame_uri` for the endpoint. In this helper those fields may also be local PNG/JPEG/WebP paths, which it converts to inline data. Local paths are resolved from your terminal's current directory, so run from the repository root. Larger inputs should use a provider upload URI or a public HTTPS image URL; see [input formats](https://docs.ltx.io/input-formats).

The examples deliberately supply only the neutral exterior. For stricter endpoint control, first create and approve a registered hood-open or overview battery-reveal still, then add its path as `last_frame_uri`. Do not use `battery-blue.png` as the hover endpoint: it is a close-up cutaway with different composition. Use that image only when intentionally designing a separate camera transition.

Do not copy Kling fields such as `last_frame_url` into LTX requests. Model and API field names are provider-specific. The helper rejects unknown fields. Its prompts are starting points, not guarantees against geometric distortion.

## Integrate a reviewed result

Review the generated take for motion, shape stability and seams, then finish timing and reverse movement in your editor. Follow [MEDIA.md](MEDIA.md). Only after approval should you copy final exports into your fork's media directory and adjust mappings if necessary.

Using the helper does not change App.tsx, HoverVideo.tsx or the approved public files. It is not a full asset pipeline, upload manager or commercial generation backend.

## Troubleshooting and validation limits

- **401:** check the local key and its validity in the console; never print it for debugging.
- **402:** check account credits/billing.
- **400/422:** compare your request with the current model/endpoint schema and supported input formats.
- **429/5xx/network on polling:** the helper retries a few GET requests, then leaves the ID available for resume.
- **404 when resuming:** the job may have expired or the ID is wrong; inspect the console before resubmitting.
- **HTTP success but no MP4:** the helper rejects the download and preserves the job ID for investigation.

Validation for this release covers local preflight and mocked submit/poll/download, resume, no-overwrite and uncertain-submission behaviour. No paid LTX job was submitted as part of packaging. Provider-side validation, billing and output quality require a real authorized generation.
