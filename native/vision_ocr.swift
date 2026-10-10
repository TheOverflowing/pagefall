import Foundation
import ImageIO
import Vision

// Uses only the on-device OCR shipped with macOS. No content leaves this process.
struct OCRLine: Codable {
    let text: String
    let confidence: Float
    let x: Double
    let y: Double
    let width: Double
    let height: Double
}

struct OCRResult: Codable {
    let width: Int
    let height: Int
    let lines: [OCRLine]
}

struct OCRFailure: Codable {
    let error: String
}

func fail(_ message: String, status: Int32 = 1) -> Never {
    let encoder = JSONEncoder()
    let data = (try? encoder.encode(OCRFailure(error: message))) ?? Data("{\"error\":\"OCR failed\"}".utf8)
    FileHandle.standardError.write(data)
    FileHandle.standardError.write(Data([10]))
    exit(status)
}

guard CommandLine.arguments.count == 2 else {
    fail("Usage: vision-ocr <image-path>", status: 2)
}

let imageURL = URL(fileURLWithPath: CommandLine.arguments[1])
guard let source = CGImageSourceCreateWithURL(imageURL as CFURL, nil),
      let cgImage = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
    fail("Cannot read image: \(imageURL.path)")
}

let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any]
let rawOrientation = (properties?[kCGImagePropertyOrientation] as? NSNumber)?.uint32Value ?? 1
let orientation = CGImagePropertyOrientation(rawValue: rawOrientation) ?? .up
let swapsAxes = [CGImagePropertyOrientation.leftMirrored, .right, .rightMirrored, .left].contains(orientation)
let orientedWidth = swapsAxes ? cgImage.height : cgImage.width
let orientedHeight = swapsAxes ? cgImage.width : cgImage.height

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = false
// Keep small text in long screenshots eligible for recognition.
request.minimumTextHeight = 0.001
if #available(macOS 13.0, *) {
    request.automaticallyDetectsLanguage = false
}

do {
    let supported = try request.supportedRecognitionLanguages()
    request.recognitionLanguages = ["zh-Hans", "zh-Hant", "en-US"].filter { supported.contains($0) }
    guard request.recognitionLanguages.contains("zh-Hans") else {
        fail("This macOS version does not support Simplified Chinese recognition.")
    }
    let handler = VNImageRequestHandler(cgImage: cgImage, orientation: orientation, options: [:])
    try handler.perform([request])

    let lines = (request.results ?? []).compactMap { observation -> OCRLine? in
        guard let candidate = observation.topCandidates(1).first else { return nil }
        let text = candidate.string.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return nil }
        let box = observation.boundingBox
        return OCRLine(
            text: text,
            confidence: candidate.confidence,
            x: Double(box.minX),
            y: Double(1 - box.maxY),
            width: Double(box.width),
            height: Double(box.height)
        )
    }.sorted {
        if $0.y != $1.y { return $0.y < $1.y }
        return $0.x < $1.x
    }

    // OCR may split a printed row into more than one observation. Group nearby
    // centers first, then read each row left-to-right, without an intransitive
    // fuzzy sorting comparator.
    var rows: [[OCRLine]] = []
    for line in lines {
        if let last = rows.last, let anchor = last.first,
           abs((line.y + line.height / 2) - (anchor.y + anchor.height / 2)) < min(line.height, anchor.height) * 0.5 {
            rows[rows.count - 1].append(line)
        } else {
            rows.append([line])
        }
    }
    let orderedLines = rows.flatMap { $0.sorted { $0.x < $1.x } }
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys]
    let output = try encoder.encode(OCRResult(width: orientedWidth, height: orientedHeight, lines: orderedLines))
    FileHandle.standardOutput.write(output)
    FileHandle.standardOutput.write(Data([10]))
} catch {
    fail("Text recognition failed: \(error.localizedDescription)")
}
