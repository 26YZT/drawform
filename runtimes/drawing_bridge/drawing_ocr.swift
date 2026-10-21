// Local PDF text extraction and Vision OCR. No network or model service.
import AppKit
import CoreImage
import Foundation
import ImageIO
import PDFKit
import Vision

struct IntakeError: Error { let message: String }

func png(_ image: CGImage, to path: String) throws {
    let rep = NSBitmapImageRep(cgImage: image)
    guard let bytes = rep.representation(using: .png, properties: [:]) else {
        throw IntakeError(message: "Cannot encode source preview")
    }
    try bytes.write(to: URL(fileURLWithPath: path), options: .atomic)
}

func recognize(_ image: CGImage) throws -> [[String: Any]] {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["zh-Hans", "en-US"]
    request.usesLanguageCorrection = false
    try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
    return (request.results ?? []).compactMap { observation in
        guard let candidate = observation.topCandidates(1).first else { return nil }
        let b = observation.boundingBox
        return ["text": candidate.string, "confidence": Double(candidate.confidence),
                "bbox": [Double(b.minX), Double(b.minY), Double(b.width), Double(b.height)],
                "method": "vision_ocr"]
    }.sorted { left, right in
        let a = left["bbox"] as! [Double], b = right["bbox"] as! [Double]
        return abs(a[1] - b[1]) < 0.01 ? a[0] < b[0] : a[1] > b[1]
    }
}

func imageFile(_ path: String) throws -> CGImage {
    guard let source = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil),
          CGImageSourceGetCount(source) == 1,
          let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
        throw IntakeError(message: "Only a single readable PNG/JPEG image is supported")
    }
    if image.width > 10000 || image.height > 10000 || image.width * image.height > 40_000_000 {
        throw IntakeError(message: "Image dimensions exceed the local OCR limit")
    }
    let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any]
    let orientation = (properties?[kCGImagePropertyOrientation] as? NSNumber)?.int32Value ?? 1
    let oriented = CIImage(cgImage: image).oriented(forExifOrientation: orientation)
    guard let result = CIContext(options: [.useSoftwareRenderer: false]).createCGImage(oriented, from: oriented.extent) else {
        throw IntakeError(message: "Cannot orient source image")
    }
    return result
}

func run() throws {
    guard CommandLine.arguments.count == 4 else {
        throw IntakeError(message: "Usage: drawing_ocr input_path format output_dir")
    }
    let path = CommandLine.arguments[1], format = CommandLine.arguments[2], output = CommandLine.arguments[3]
    var pages = [[String: Any]]()
    if format == "pdf" {
        guard let document = PDFDocument(url: URL(fileURLWithPath: path)), document.pageCount > 0,
              document.pageCount <= 4, !document.isLocked else {
            throw IntakeError(message: "Only unlocked PDFs with 1 to 4 pages are supported")
        }
        for index in 0..<document.pageCount {
            guard let page = document.page(at: index) else { throw IntakeError(message: "Unreadable PDF page") }
            let bounds = page.bounds(for: .mediaBox)
            guard bounds.width > 0, bounds.height > 0 else { throw IntakeError(message: "Invalid PDF page bounds") }
            let scale = min(200.0 / 72.0, 2400.0 / max(bounds.width, bounds.height))
            let size = NSSize(width: bounds.width * scale, height: bounds.height * scale)
            let thumbnail = page.thumbnail(of: size, for: .mediaBox)
            guard let image = thumbnail.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
                throw IntakeError(message: "Cannot render PDF page")
            }
            var texts = [[String: Any]]()
            if let selection = page.selection(for: bounds) {
                for line in selection.selectionsByLine() {
                    guard let text = line.string?.trimmingCharacters(in: .whitespacesAndNewlines), !text.isEmpty else { continue }
                    let b = line.bounds(for: page)
                    texts.append(["text": text, "confidence": 1.0,
                                  "bbox": [(b.minX - bounds.minX) / bounds.width, (b.minY - bounds.minY) / bounds.height,
                                           b.width / bounds.width, b.height / bounds.height], "method": "pdf_text"])
                }
            }
            // Preserve native PDF text when available; OCR is the fallback for scan-only pages.
            if texts.isEmpty { texts = try recognize(image) }
            let preview = "page-\(index).png"
            try png(image, to: URL(fileURLWithPath: output).appendingPathComponent(preview).path)
            pages.append(["page_index": index, "width": image.width, "height": image.height,
                          "texts": texts, "preview_file": preview])
        }
    } else if format == "png" || format == "jpeg" {
        let image = try imageFile(path)
        try png(image, to: URL(fileURLWithPath: output).appendingPathComponent("page-0.png").path)
        pages.append(["page_index": 0, "width": image.width, "height": image.height,
                      "texts": try recognize(image), "preview_file": "page-0.png"])
    } else { throw IntakeError(message: "Unsupported OCR input format") }
    let bytes = try JSONSerialization.data(withJSONObject: ["schema_version": 1, "pages": pages], options: [.sortedKeys])
    FileHandle.standardOutput.write(bytes)
    FileHandle.standardOutput.write(Data("\n".utf8))
}

do { try run() }
catch {
    let message = (error as? IntakeError)?.message ?? String(describing: error)
    let bytes = try! JSONSerialization.data(withJSONObject: ["error": message], options: [.sortedKeys])
    FileHandle.standardError.write(bytes)
    FileHandle.standardError.write(Data("\n".utf8))
    exit(2)
}
