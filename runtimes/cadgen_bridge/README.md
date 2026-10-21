# Bounded plate modelling runtime

This directory is the isolated OCCT 7.9 side of DrawForm's first real modelling
loop. The existing drawing service remains on its existing OCCT 8 interpreter.
Never install this runtime into `spike/.venv`, and never import this worker from
the drawing service. The service invokes its locked Python as a subprocess.

## Entrypoint

```text
runtimes/cadgen_bridge/.venv/bin/python runtimes/cadgen_bridge/plate.py request.json
```

The request contains exactly these fields:

```json
{
  "schema_version": 1,
  "length_mm": 100,
  "width_mm": 60,
  "thickness_mm": 8,
  "hole_diameter_mm": 20,
  "output_dir": "/absolute/private/attempt/output"
}
```

All dimensions are finite positive numbers, and the centered hole diameter must
be smaller than both plate dimensions. `output_dir` must be a new or empty,
canonical absolute directory selected by the parent. The model uses millimetres,
X/Y centered at zero, plate bottom Z=0, plate top Z=thickness, and a Z-axis
centered through hole. User-supplied Python, commands and arbitrary output names
are not accepted.

The worker writes its own fixed `work/model.py` and `work/parameters.json`.
`@step(out="out/plate.step")` resolves beside that model script, so the staged
file is `work/out/plate.step`. The same isolated interpreter runs
`model.py --json`. On successful completion, the worker moves the STEP to
`plate.step`, reads that saved file afresh with build123d/OCCT, validates it and
derives preview tessellation from the saved geometry.

Cold execution sets `CADGEN_DAEMON=0`, disables telemetry/update checks, and
uses a private `work/cache`. The worker does not download, launch GUI tools or
start a daemon. Native children inherit the parent's process group. **The
parent supervisor must enforce deadlines, kill the process group on
cancellation, and prevent a stale attempt from being published.**

## Output contract

A successful exit is code 0. Stdout's final line equals `result.json`:

- `schema_version: 1`, `status: "succeeded"`.
- `parameters`: the normalized four numeric dimensions.
- `runtime`: actual Python, cadgen, build123d and OCP distribution versions.
- `step_sha256`: exact saved STEP bytes.
- `files`: `step`, `validation`, `preview`, `source`, `parameters`; each carries
  `path` relative to `output_dir`, `sha256` and `size_bytes`.
- `validation`: `passed: true` and the complete named check list.

`validation.json` carries the same STEP hash, factual bounds/volume/cylindrical
faces, numerical tolerances and individual check verdicts. Checks cover a
single solid, valid topology, closed shells, the requested bounds and origin,
positive and analytic volume, hole diameter/axis/through span, and sampled air
and material points. Numerical tolerances are not manufacturing tolerances.
A failed geometry check never creates a successful result.

`preview.json` carries `schema_version`, `step_sha256`, `units: "mm"`,
`vertices: [[x,y,z], ...]`, `faces: [[i,j,k], ...]`, and
`bbox: {"min": [x,y,z], "max": [x,y,z], "size": [dx,dy,dz]}`. Indices are
zero-based. It is an interactive display derivative, not an STL/GLB export or
an exact dimensional measurement authority. Source STEP remains authoritative.

Invalid requests exit 2 with one structured JSON error on stderr, before CAD
imports or output writes. Runtime/build/validation failures exit 3. Private
build diagnostics are retained in `work/model-stdout.log` and
`work/model-stderr.log`; these logs are not a success signal. An existing output
is never overwritten. `result.json` is written last; an incomplete output must
not be adopted by the caller.

## Verification

From the repository root, using the existing DrawForm environment:

```text
PYTHONPATH=drawform/src spike/.venv/bin/python -m pytest drawform/tests/test_plate_runtime.py -q
```

`DRAWFORM_MODEL_PYTHON` optionally selects the isolated interpreter. Positive
cases build real 100×60×8/Ø20 and 120×80×10/Ø12 plates, verify saved hashes and
preview references, then independently read the STEP in DrawForm's OCCT 8 and
recognize the correct centered through hole. They skip only when the isolated
runtime is not provisioned; skipped tests are not acceptance. Invalid requests
run without a CAD kernel and must leave no artifacts. No native modelling is
mocked.

## Schema 2 plate / washer increment

`simple.py request.json` is a separate entrypoint; schema 1 `plate.py` is retained.
Request schema 2 contains only `schema_version`, `spec`, `output_dir`. The spec
is validated with DrawForm's shared pure-data `validate_spec`, imported from the
controlled repository source path. The original increment admitted `plate` and `washer`; the two-stage solid shaft
extension below is now admitted with independent shoulder and finite-span checks.

Plate spec has `parameters: {length_mm,width_mm,thickness_mm}` and 0–16 explicit
`holes: [{x_mm,y_mm,diameter_mm}]`. XY is relative to the plate center; all holes
are parallel to Z and through the plate. Washer spec has
`parameters: {outer_diameter_mm,inner_diameter_mm,thickness_mm}` and `holes: []`;
inner diameter 0 is an unperforated disc. Dimensions are millimetres. Shared
validation rejects unsupported, overlapping, tangent or out-of-bound features.

The worker writes `model.step`, `preview.json`, `validation.json`, `result.json`
with schema version 2, the normalized `spec`, actual `runtime`, STEP hash,
relative file manifest and validation verdicts. Preview fields and coordinates
retain the schema 1 layout, including `bbox: {min,max,size}` and zero-based
triangles; its schema version is 2. Geometry checks match physical cylinder
locations, diameters, counts and finite axial spans, closed solid topology,
bounds, analytic volume, and hole-air/material sample points. Preview comes
from saved STEP geometry, not constructor objects.

`python -m drawform.workbench.inspect_simple request.json` runs with DrawForm's
OCCT 8 interpreter; its request is `{step,spec,output}`. It independently reopens
the saved STEP and uses the existing DrawForm feature recognizer plus native
closure, mass and point-classification checks. It imports neither cadgen nor
build123d. Both checks must pass before adopting a generated model.

Targeted acceptance:

```text
PYTHONPATH=drawform/src spike/.venv/bin/python -m pytest drawform/tests/test_simple_runtime.py -q
```

Positive tests actually construct a two-hole plate, a washer and a disc, verify
both kernels, invoke the original `render_part` pipeline and inspect critical
native DXF dimensions. These tests do not change or bypass the old delivery
gate and do not establish arbitrary-shape or manufacturing-standard coverage.

## Two-stage solid stepped shaft increment (2026-10-18)

Schema2 kind `stepped_shaft` has exactly `diameter_1_mm`, `length_1_mm`,
`diameter_2_mm`, `length_2_mm`, and `holes: []`. Stage1 occupies Z0..L1;
stage2 occupies L1..L1+L2. Diameters may increase or decrease; equal/degenerate
diameters are rejected. All dimensions are positive finite mm, individual values
and overall length bounded by1000mm.

The fixed worker source fuses two adjacent full cylinders. Both isolated OCCT7.9
and the independently implemented host OCCT8 checker re-open the actual saved
STEP, inspect finite cylinder intervals, end/annular-shoulder face areas, single
closed solid, analytical volume, and solid/air point classification. Merely matching
bbox and volume is insufficient: swapped end order is rejected by finite spans.

The unchanged pipeline recognizes a strictly bounded two-cylinder/three-plane
solid. A source-bound shoulder dimension, total length, both diameters and genuine
centerlines use the original Drawing/DXF/SVG/PDF/JSON chain. See
`docs/implementation/first-bundle/STEPPED_SHAFT_CONTRACT.md`; this does not admit
arbitrary multistage, hollow, filleted, threaded or tapered shafts.
