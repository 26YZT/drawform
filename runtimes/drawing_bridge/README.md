# Local drawing intake

`drawing_ocr.swift` uses only the macOS SDK frameworks Vision, PDFKit, AppKit and
ImageIO. It reads local PNG/JPEG images or unlocked PDFs with 1–4 pages. It does
not contact a model provider or network endpoint. Text, confidence, page index
and normalized bottom-left bounding boxes are returned as JSON. PDF text layers
are retained when present; scan-only pages use local Vision OCR.

`drawform.workbench.drawing_intake.ensure_ocr()` builds it with `xcrun swiftc -O`
on first use and caches a source-hash-bound binary in the stable, regenerable
`drawform/out/drawing-native/` directory. Compilation and execution
have deadlines. No Python package is installed or changed.

The Python entry point accepts a parent-controlled request JSON containing
exactly `input_path`, `format`, `output_dir`, `source_sha256`. Formats are `dxf`,
`pdf`, `png`, `jpeg`. The source must be a regular file and match the SHA-256;
the output directory must be new or empty. It writes `analysis.json` and an
actual source `preview.png`.

Supported geometry is deliberately bounded: one axis-aligned rectangle with
0–16 circular holes, or one/two concentric circles (disc/washer). DXF geometry
comes from native entities, dimensions and units. PDF/images require readable,
explicit role labels, a stated rectangular plate/washer/disc, and an explicit
hole declaration (including “无孔”/“no holes”). Pixels never provide dimensions.
Missing thickness, unit or through-hole intent creates questions. Conflicting
source dimensions create a read-only blocking question and require correcting
the drawing; the user cannot silently select a convenient value.

Unsupported/ambiguous outlines, multi-view native drawings, unlocated raster
holes, tolerances and further features are reported, not guessed. This intake
does not establish manufacturing readiness or engineering approval.

For PDF/images, `text_consumption` accounts for every nonempty recognized text
line on all source pages, including low-confidence OCR. Each record retains the
original text and page/bounding-box evidence, with a status of `consumed_role`,
`allowed_text`, `unread_role` or `unexplained`. Only explicit dimension/type/unit/
hole grammar and narrowly listed drawing-number, page-count and scale metadata
are allowed. A valid dimension span never consumes additional free text on the
same line. Unexplained words/digits produce `unexplained_annotation` facts and a
read-only blocking question; the source must be corrected and uploaded again.
Known unsupported features (including boss/rib/stepped/tapped and Chinese
counterparts) retain `unsupported_feature` rather than being silently omitted.

A single unread English length/width label may be recorded as `unread_role`
only when that parameter is genuinely missing and the label differs by at most
one character from the known role. Its number is never used; the existing typed
dimension question remains necessary. An extra unread numeric line when that
dimension already exists is unexplained and blocks continuation. This bounded
text accounting does not claim arbitrary visual feature recognition: a feature
that local OCR never detects still requires checking the source preview.
