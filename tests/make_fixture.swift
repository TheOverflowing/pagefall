import AppKit
import Foundation

// Entirely original sample prose, used to exercise Chinese OCR and page overlap.
let outputDirectory = URL(fileURLWithPath: CommandLine.arguments.count > 1
    ? CommandLine.arguments[1] : "artifacts/test-fixtures", isDirectory: true)
try FileManager.default.createDirectory(at: outputDirectory, withIntermediateDirectories: true)

let prose = [
    "第一章  雨后的来信",
    "清晨，林溪推开旧书店的窗，雨水正沿着屋檐落下。",
    "她在门边发现一封信，信封上只写着今天的日期。",
    "信里没有署名，只有一句话：请替我保管那盏灯。",
    "林溪抬头看向柜台，昨夜熄灭的台灯竟亮着微光。",
    "她没有立刻拆开抽屉，而是先记下了信纸的颜色。",
    "纸是浅蓝色的，右下角画着一只歪歪扭扭的小船。",
    "这让她想起十年前，哥哥教她折纸船的那个下午。",
    "那时河水很慢，所有人都相信夏天不会结束。",
    "街角传来自行车的铃声，送报员停在书店门前。",
    "他说，昨晚有个穿灰色外套的人在这里等了很久。",
    "“你看清他的脸了吗？”林溪问。",
    "送报员摇摇头，却从车篮里取出一本没有封面的书。",
    "“他让我把这个交给你，说答案就在第三十七页。”",
    "林溪翻开书，发现每一页都写着不同人的回忆。",
    "第三十七页却是空白的，边缘留下了一圈潮湿的印记。",
    "她把台灯移近，纸面上慢慢浮现出一条细细的线。",
    "线条弯过桥梁，穿过广场，最后停在一扇绿色的门前。",
    "窗外的雨停了，阳光照亮了书店门口的第一块石砖。",
    "林溪合上书，拿起钥匙，决定先去看一看那扇门。"
]

func render(_ name: String, body: [String]) throws {
    let width = 1100
    let height = 1080
    guard let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil,
        pixelsWide: width, pixelsHigh: height, bitsPerSample: 8, samplesPerPixel: 4,
        hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0),
          let context = NSGraphicsContext(bitmapImageRep: bitmap) else {
        fatalError("Cannot create fixture image")
    }
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = context
    NSColor(calibratedRed: 0.985, green: 0.97, blue: 0.94, alpha: 1).setFill()
    NSRect(x: 0, y: 0, width: width, height: height).fill()
    let bodyFont = NSFont(name: "PingFangSC-Regular", size: 27) ?? .systemFont(ofSize: 27)
    let titleFont = NSFont(name: "PingFangSC-Semibold", size: 31) ?? .boldSystemFont(ofSize: 31)
    for (index, line) in body.enumerated() {
        let font = line.hasPrefix("第一章") ? titleFont : bodyFont
        (line as NSString).draw(at: NSPoint(x: 70, y: height - 110 - index * 69), withAttributes: [
            .font: font,
            .foregroundColor: NSColor(calibratedWhite: 0.14, alpha: 1)
        ])
    }
    NSGraphicsContext.restoreGraphicsState()
    guard let png = bitmap.representation(using: .png, properties: [:]) else {
        fatalError("Cannot encode fixture image")
    }
    try png.write(to: outputDirectory.appendingPathComponent(name))
}

try render("original-01.png", body: Array(prose[0...12]))
try render("original-02.png", body: Array(prose[10...19]))
try (prose.joined(separator: "\n") + "\n").write(to: outputDirectory.appendingPathComponent("original-text.txt"), atomically: true, encoding: .utf8)
print(outputDirectory.path)
