import Foundation
import SwiftUI
import AppKit

enum InterfaceLanguage: String, CaseIterable, Identifiable {
    case system, simplifiedChinese = "zh-Hans", english = "en"
    var id: String { rawValue }
    var label: String {
        switch self {
        case .system: return L("跟随系统")
        case .simplifiedChinese: return "简体中文"
        case .english: return "English"
        }
    }
}

/// Interpolated values remain data; only the surrounding interface copy is translated.
struct LocalizedCopy: ExpressibleByStringLiteral, ExpressibleByStringInterpolation {
    var key: String
    var arguments: [String]
    init(stringLiteral value: String) { key = value; arguments = [] }
    init(stringInterpolation: StringInterpolation) {
        key = stringInterpolation.key; arguments = stringInterpolation.arguments
    }
    struct StringInterpolation: StringInterpolationProtocol {
        var key = ""
        var arguments: [String] = []
        init(literalCapacity: Int, interpolationCount: Int) { key.reserveCapacity(literalCapacity) }
        mutating func appendLiteral(_ literal: String) { key += literal }
        mutating func appendInterpolation<T>(_ value: T) { key += "%@"; arguments.append(String(describing: value)) }
    }
}

final class Localization: ObservableObject {
    static let shared = Localization()
    @Published var language: InterfaceLanguage {
        didSet {
            preferences.set(language.rawValue, forKey: "interfaceLanguage")
            Task { @MainActor in self.updateNativeMenus() }
        }
    }
    var identifier: String {
        if language != .system { return language.rawValue }
        return ((preferredLanguages ?? Locale.preferredLanguages).first ?? "en").hasPrefix("zh") ? "zh-Hans" : "en"
    }
    var locale: Locale { Locale(identifier: identifier) }
    private let english: [String: String]
    private let preferences: UserDefaults
    private let preferredLanguages: [String]?
    private let messageTemplates: [(key: String, value: String, source: NSRegularExpression, translated: NSRegularExpression)]

    init(preferences: UserDefaults = .standard, preferredLanguages: [String]? = nil) {
        self.preferences = preferences
        self.preferredLanguages = preferredLanguages
        language = InterfaceLanguage(rawValue: preferences.string(forKey: "interfaceLanguage") ?? "system") ?? .system
        let bundled = Bundle.main.resourceURL?.appendingPathComponent("en.lproj/Localizable.strings")
        let source = URL(fileURLWithPath: #filePath).deletingLastPathComponent().appendingPathComponent("Resources/en.lproj/Localizable.strings")
        let candidates = [bundled, source].compactMap { $0 }
        // Prefer the bundled resource without opening the development source
        // tree, which can require a separate macOS folder-access prompt.
        english = candidates.lazy.compactMap { url -> [String: String]? in
            guard let data = try? Data(contentsOf: url),
                  let value = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: String] else { return nil }
            return value
        }.first ?? [:]
        messageTemplates = english.filter { $0.key.contains("%@") }.sorted { $0.key.count > $1.key.count }.compactMap { key, value in
            func regex(_ format: String) -> NSRegularExpression? {
                let pattern = format.components(separatedBy: "%@").map(NSRegularExpression.escapedPattern).joined(separator: "(.*?)")
                return try? NSRegularExpression(pattern: "^" + pattern + "$", options: [.dotMatchesLineSeparators])
            }
            guard let source = regex(key), let translated = regex(value) else { return nil }
            return (key, value, source, translated)
        }
    }

    func text(_ key: String, arguments: [String] = []) -> String {
        let format = identifier == "en" ? english[key] ?? key : key
        // Replace placeholders directly: a title containing '%' is always literal.
        let parts = format.components(separatedBy: "%@")
        guard parts.count > 1 else { return format }
        var result = parts[0]
        for index in 1..<parts.count { result += (index - 1 < arguments.count ? arguments[index - 1] : "%@") + parts[index] }
        return result
    }

    /// Translate known backend status copy without translating books or their contents.
    func message(_ raw: String) -> String {
        if english[raw] != nil { return text(raw) }
        if let key = english.first(where: { $0.value == raw })?.key { return text(key) }
        let range = NSRange(raw.startIndex..<raw.endIndex, in: raw)
        for template in messageTemplates {
            for regex in [template.source, template.translated] {
                guard let match = regex.firstMatch(in: raw, range: range) else { continue }
                let arguments = (1..<match.numberOfRanges).map { index -> String in
                    guard let range = Range(match.range(at: index), in: raw) else { return "" }
                    return String(raw[range])
                }
                return text(template.key, arguments: arguments)
            }
        }
        return raw
    }

    @MainActor func updateNativeMenus() {
        guard let app = NSApp else { return }
        let labels = [
            "file": ("文件", "File"), "edit": ("编辑", "Edit"), "view": ("显示", "View"),
            "window": ("窗口", "Window"), "help": ("帮助", "Help"),
            "services": ("服务", "Services"), "speech": ("语音", "Speech"),
            "spelling": ("拼写和语法", "Spelling and Grammar"), "substitutions": ("替换", "Substitutions"),
            "transformations": ("转换", "Transformations"), "font": ("字体", "Font")
        ]
        let actions: [String: String] = [
            "orderFrontStandardAboutPanel:": "关于拾页", "hide:": "隐藏拾页", "hideOtherApplications:": "隐藏其他", "unhideAllApplications:": "显示全部", "terminate:": "退出拾页",
            "performClose:": "关闭窗口", "undo:": "撤销", "redo:": "重做", "cut:": "剪切", "copy:": "复制", "paste:": "粘贴", "pasteAsPlainText:": "粘贴并匹配样式", "selectAll:": "全选", "delete:": "删除",
            "performMiniaturize:": "最小化", "performZoom:": "缩放", "arrangeInFront:": "全部置于前台", "toggleFullScreen:": "进入全屏幕", "toggleSidebar:": "切换边栏", "showHelp:": "拾页帮助"
        ]
        func visit(_ menu: NSMenu) {
            for item in menu.items {
                if let action = item.action, let key = actions[NSStringFromSelector(action)] { item.title = text(key) }
                else if let match = labels.values.first(where: { item.title == $0.0 || item.title == $0.1 }) { item.title = identifier == "en" ? match.1 : match.0 }
                else if item.title == "拾页" || item.title == "Shiye" || item.title == "Pagefall" { item.title = text("拾页") }
                if let submenu = item.submenu { submenu.title = item.title; visit(submenu) }
            }
        }
        if let menu = app.mainMenu { visit(menu) }
        for window in app.windows where window.title == "拾页" || window.title == "Shiye" || window.title == "Pagefall" { window.title = text("拾页") }
    }
}

func L(_ copy: LocalizedCopy) -> String { Localization.shared.text(copy.key, arguments: copy.arguments) }
func LS(_ key: String) -> String { Localization.shared.text(key) }
func LM(_ message: String) -> String { Localization.shared.message(message) }

func localizedError(_ error: Error) -> String {
    if let value = error as? URLError {
        switch value.code {
        case .timedOut: return L("请求超时，请稍后重试。")
        case .cannotConnectToHost, .cannotFindHost, .notConnectedToInternet, .networkConnectionLost:
            return L("无法连接本地服务，请稍后重试。")
        case .cancelled: return L("操作已取消。")
        default: break
        }
    }
    return LM(error.localizedDescription)
}

struct LanguagePicker: View {
    @ObservedObject private var localization = Localization.shared
    var body: some View {
        Picker(L("界面语言"), selection: $localization.language) {
            ForEach(InterfaceLanguage.allCases) { language in Text(language.label).tag(language) }
        }
        .accessibilityLabel(L("界面语言"))
    }
}
