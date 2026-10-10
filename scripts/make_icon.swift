import AppKit
import Foundation

// An ivory tile and a lifted blue page: a small, legible mark for 拾页.
// Vector geometry is retained here so every Dock/Finder size is drawn natively.
let folder = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
func color(_ r: CGFloat, _ g: CGFloat, _ b: CGFloat) -> NSColor {
    NSColor(calibratedRed: r / 255, green: g / 255, blue: b / 255, alpha: 1)
}
func draw(_ pixels: Int) -> Data {
    let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels,
        bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
        colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
    let context = NSGraphicsContext.current!.cgContext
    context.scaleBy(x: CGFloat(pixels) / 1024, y: CGFloat(pixels) / 1024)
    context.setShouldAntialias(true)
    let tile = NSBezierPath(roundedRect: NSRect(x: 64, y: 64, width: 896, height: 896), xRadius: 202, yRadius: 202)
    NSGraphicsContext.saveGraphicsState()
    let tileShadow = NSShadow(); tileShadow.shadowColor = NSColor.black.withAlphaComponent(0.14)
    tileShadow.shadowBlurRadius = 18; tileShadow.shadowOffset = NSSize(width: 0, height: -9); tileShadow.set()
    color(246, 243, 236).setFill(); tile.fill()
    NSGraphicsContext.restoreGraphicsState()
    NSGradient(starting: color(255, 254, 250), ending: color(236, 234, 229))!.draw(in: tile, angle: -90)
    if pixels >= 64 {
        NSColor.white.withAlphaComponent(0.8).setStroke(); tile.lineWidth = 2; tile.stroke()
    }

    // The rear leaf provides depth, without a literal book illustration.
    NSGraphicsContext.saveGraphicsState()
    context.translateBy(x: 505, y: 514); context.rotate(by: -0.105); context.translateBy(x: -505, y: -514)
    let rear = NSBezierPath(roundedRect: NSRect(x: 277, y: 232, width: 448, height: 586), xRadius: 59, yRadius: 59)
    color(179, 193, 233).setFill(); rear.fill()
    NSGraphicsContext.restoreGraphicsState()

    let page = NSBezierPath()
    page.move(to: NSPoint(x: 362, y: 822))
    page.line(to: NSPoint(x: 647, y: 822))
    page.curve(to: NSPoint(x: 761, y: 708), controlPoint1: NSPoint(x: 690, y: 822), controlPoint2: NSPoint(x: 761, y: 752))
    page.line(to: NSPoint(x: 761, y: 287))
    page.curve(to: NSPoint(x: 704, y: 230), controlPoint1: NSPoint(x: 761, y: 251), controlPoint2: NSPoint(x: 740, y: 230))
    page.line(to: NSPoint(x: 362, y: 230))
    page.curve(to: NSPoint(x: 305, y: 287), controlPoint1: NSPoint(x: 326, y: 230), controlPoint2: NSPoint(x: 305, y: 251))
    page.line(to: NSPoint(x: 305, y: 765))
    page.curve(to: NSPoint(x: 362, y: 822), controlPoint1: NSPoint(x: 305, y: 801), controlPoint2: NSPoint(x: 326, y: 822))
    page.close()
    NSGraphicsContext.saveGraphicsState()
    let paperShadow = NSShadow(); paperShadow.shadowColor = color(50, 65, 111).withAlphaComponent(0.22)
    paperShadow.shadowBlurRadius = 22; paperShadow.shadowOffset = NSSize(width: 0, height: -13); paperShadow.set()
    color(68, 97, 203).setFill(); page.fill()
    NSGraphicsContext.restoreGraphicsState()
    NSGradient(starting: color(102, 132, 237), ending: color(63, 86, 188))!.draw(in: page, angle: -75)

    // A folded corner makes the silhouette read as a page at small sizes.
    let fold = NSBezierPath()
    fold.move(to: NSPoint(x: 641, y: 821)); fold.line(to: NSPoint(x: 641, y: 738))
    fold.curve(to: NSPoint(x: 680, y: 699), controlPoint1: NSPoint(x: 641, y: 712), controlPoint2: NSPoint(x: 654, y: 699))
    fold.line(to: NSPoint(x: 760, y: 699)); fold.close()
    color(188, 206, 255).setFill(); fold.fill()

    // Quiet white strokes and a warm bookmark are the only interior details.
    for (y, width) in [(610.0, 217.0), (535.0, 264.0), (460.0, 188.0)] {
        color(250, 251, 255).withAlphaComponent(y == 610 ? 0.96 : 0.76).setFill()
        NSBezierPath(roundedRect: NSRect(x: 392, y: y, width: width, height: 22), xRadius: 11, yRadius: 11).fill()
    }
    let ribbon = NSBezierPath()
    ribbon.move(to: NSPoint(x: 585, y: 351)); ribbon.line(to: NSPoint(x: 654, y: 351))
    ribbon.line(to: NSPoint(x: 654, y: 203)); ribbon.line(to: NSPoint(x: 619.5, y: 225))
    ribbon.line(to: NSPoint(x: 585, y: 203)); ribbon.close()
    NSGradient(starting: color(247, 172, 137), ending: color(230, 126, 105))!.draw(in: ribbon, angle: -90)
    NSGraphicsContext.restoreGraphicsState()
    return bitmap.representation(using: .png, properties: [:])!
}
for size in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let name = "icon_\(size)x\(size)" + (scale == 2 ? "@2x" : "") + ".png"
        try draw(size * scale).write(to: folder.appendingPathComponent(name))
    }
}
