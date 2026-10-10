import SwiftUI
import AppKit
import UniformTypeIdentifiers

struct Chapter: Decodable, Identifiable {
    var id: String
    var title: String
    var summary: String?
    var url: String?
    var state: String
    var text: String?
    var raw_text: String?
    var characters: Int?
    var method: String?
    var error: String?
    var screenshots: [String]?
    var review: Bool?
    var incomplete: Bool?
    var book_id: String?
    var ordinal: Int?
    var proofreading: ChapterProofreading?
    var translation: ChapterTranslation?
    var label: String {
        if state != "reviewed", let proof = proofreading, proof.isCurrent {
            return (proof.issues_count ?? 0) == 0 ? L("已自动检查") : L("\(proof.issues_count ?? 0) 处需注意")
        }
        return ["pending":L("未采集"), "capturing":L("正在识别"), "done":L("已保存"), "review":L("待校对"), "reviewed":L("已校对"), "blocked":L("不可读"), "failed":L("需重试")][state] ?? state
    }
    var color: Color {
        if state != "reviewed", let proof = proofreading, proof.isCurrent { return (proof.issues_count ?? 0) == 0 ? .secondary : .orange }
        switch state {
        case "done", "reviewed": return .green
        case "review": return .orange
        case "blocked", "failed": return .red
        case "capturing": return .accentColor
        default: return .secondary
        }
    }
}

struct Book: Decodable, Identifiable {
    var id: String
    var title: String
    var author: String
    var url: String
    var chapters: [Chapter]
    var progress: ProgressInfo?
    var active: Bool?
    var phase: String?
    var cover: String?
    var platform: String?
    var platform_name: String?
    var proofreading: BookProofreading?
    var translation: BookTranslation?
    var metadata: BookMetadata?
}

struct ProgressInfo: Decodable {
    var done: Int
    var total: Int
    var missing: Int?
    var review: Int?
    var auto_processed: Int?
}

struct ProofreadIssue: Decodable {
    var kind: String?
    var message: String?
    var line: Int?
    var before: String?
    var after: String?
    var source_line_start: Int?
    var source_line_end: Int?
    var candidate: String?
    var label: String {
        LS(["low_ocr_confidence": "低置信度识别", "ocr_disagreement": "识别结果差异", "missing_ocr_geometry": "段落位置待确认", "text_changed_since_ocr": "已有文字修改", "multiple_source_images": "跨图段落", "unrecognized_characters": "无法识别的字符", "possible_watermark": "疑似水印", "possible_heading": "疑似混入标题", "cjk_spacing": "汉字间距", "possible_missing_text": "疑似缺失正文", "unexpected_length": "正文长度异常", "capture_incomplete": "采集不完整"][kind ?? ""] ?? "其他疑点")
    }
}
struct ProofreadIssueGroup: Identifiable { var id: String; var issues: [ProofreadIssue] }
struct ChapterProofreading: Decodable {
    var status: String?
    var summary: String?
    var issues_count: Int?
    var changes_count: Int?
    var issues: [ProofreadIssue]?
    var changes: [ProofreadIssue]?
    var human_reviewed: Bool?
    var isCurrent: Bool { ["checked", "needs_attention"].contains(status ?? "") }
    var issueGroups: [ProofreadIssueGroup] {
        var order: [String] = []
        let grouped = Dictionary(grouping: issues ?? []) { issue -> String in
            let kind = issue.kind ?? "other"
            if !order.contains(kind) { order.append(kind) }
            return kind
        }
        return order.map { ProofreadIssueGroup(id: $0, issues: grouped[$0] ?? []) }
    }
}
struct BookProofreading: Decodable {
    var can_undo: Bool?
    var done: Int?
    var total: Int?
    var changed: Int?
    var issues: Int?
    var completed_at: String?
}
struct ProofreadingProgress: Decodable {
    var book_id: String?
    var running: Bool?
    var done: Int?
    var total: Int?
    var changed: Int?
    var issues: Int?
    var message: String?
}

struct AppState: Decodable {
    var busy: Bool
    var browser_open: Bool
    var phase: String
    var message: String
    var book: Book?
    var progress: ProgressInfo
    var account: AccountInfo?
    var account_waiting: Bool?
    var library_syncing: Bool?
    var background: Bool?
    var downloads: [Book]?
    var queue: [QueueItem]?
    var proofreading: ProofreadingProgress?
    var translation: TranslationProgress?
    static let empty = AppState(busy: false, browser_open: false, phase: "idle", message: L("正在连接本地识别服务…"), book: nil, progress: ProgressInfo(done: 0, total: 0))
}

struct AccountInfo: Decodable, Equatable {
    var state: String
    var nickname: String?
    var verified_at: String?
    var message: String?
}
struct LibraryState: Decodable, Equatable {
    var books: [LibraryBook]
    var account: AccountInfo
    var expected_count: Int?
    var complete: Bool?
    var syncing: Bool?
    var message: String?
    var classification_loading: Bool?
    var classification_count: Int?
    var classification_basic_count: Int?
    static let empty = LibraryState(books: [], account: AccountInfo(state: "unknown"))
}
struct QueueItem: Decodable, Identifiable {
    var id: String
    var url: String
    var mode: String
}
struct PlatformInfo: Decodable, Identifiable {
    var id: String
    var name: String
    var status: String
    var notes: String
    var hosts: [String]
    var tested: Bool { status == "verified" || status == "public_verified" }
}
struct PlatformList: Decodable { var platforms: [PlatformInfo] }
struct Acknowledgement: Decodable { var ok: Bool }
struct ExportResult: Decodable { var path: String; var url: String }
struct APIError: LocalizedError { var message: String; var status: Int?; var errorDescription: String? { LM(message) } }

@MainActor
final class Model: ObservableObject {
    @Published var state = AppState.empty
    @Published var libraryState = LibraryState.empty
    private(set) var libraryFilterIndex = LibraryFilterIndex(books: [])
    @Published var downloads: [Book] = []
    @Published var queued: [QueueItem] = []
    @Published var detail: Book?
    @Published var loadingBook = false
    @Published var url = ""
    @Published var mode = UserDefaults.standard.string(forKey: "captureMode") ?? "auto" { didSet { UserDefaults.standard.set(mode, forKey: "captureMode") } }
    @Published var cleanExport = UserDefaults.standard.object(forKey: "cleanExport") as? Bool ?? true {
        didSet { UserDefaults.standard.set(cleanExport, forKey: "cleanExport") }
    }
    @Published var connected = false
    @Published var sending = false
    @Published var notice: String?
    @Published var selection: String?
    @Published var editing: Chapter?
    @Published var reading: OnlineChapter?
    @Published var readingChapter = false
    @Published var exported: URL?
    @Published var platforms: [PlatformInfo] = []
    @Published var choosingLibraryFolder = false
    private var didLaunch = false
    private var pendingScan = false
    private var checkedLibrary = false
    private var openRequestID = UUID()
    private var refreshingChapter = false
    private var libraryAccessURL: URL?
    private let endpoint = "http://127.0.0.1:8765"
    let root: URL
    var working: Bool { state.busy || sending }
    var downloading: Bool { state.busy && ["capturing", "scanning"].contains(state.phase) }
    func proofreading(_ bookID: String?) -> Bool { state.proofreading?.running == true && state.proofreading?.book_id == bookID }
    func translating(_ bookID: String?) -> Bool { state.translation?.running == true && state.translation?.book_id == bookID }
    func refreshState() async {
        do { apply(try await request("/api/state")) }
        catch { notice = localizedError(error) }
    }

    init() {
        root = URL(fileURLWithPath: Bundle.main.object(forInfoDictionaryKey: "NovelistProjectRoot") as? String ?? Bundle.main.bundleURL.deletingLastPathComponent().path, isDirectory: true)
        if !UserDefaults.standard.bool(forKey: "jinjiangUpstreamEnabled") {
            mode = "auto"
            UserDefaults.standard.set(true, forKey: "jinjiangUpstreamEnabled")
        }
        if let bookmark = UserDefaults.standard.data(forKey: "libraryFolderBookmark") {
            var stale = false
            if let saved = try? URL(resolvingBookmarkData: bookmark, options: .withSecurityScope, bookmarkDataIsStale: &stale),
               saved.standardizedFileURL == root.standardizedFileURL {
                _ = saved.startAccessingSecurityScopedResource()
                libraryAccessURL = saved
            }
        }
        Task { await run() }
    }

    func request<T: Decodable>(_ path: String, data: [String: Any]? = nil) async throws -> T {
        var req = URLRequest(url: URL(string: endpoint + path)!)
        req.timeoutInterval = (path.hasPrefix("/api/jinjiang/") || ["/api/book-details", "/api/read-chapter"].contains(path)) ? 240 : 8
        if let data {
            req.httpMethod = "POST"
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONSerialization.data(withJSONObject: data)
        }
        let (bytes, response) = try await URLSession.shared.data(for: req)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            let message = (try? JSONSerialization.jsonObject(with: bytes) as? [String: Any])?["detail"] as? String
            throw APIError(message: message ?? L("操作未完成，请稍后重试。"), status: (response as? HTTPURLResponse)?.statusCode)
        }
        return try JSONDecoder().decode(T.self, from: bytes)
    }

    private func run() async {
        var failures = 0
        var tick = 0
        while !Task.isCancelled {
            do {
                let fresh: AppState = try await request("/api/state")
                if !connected { notice = nil; Localization.shared.updateNativeMenus() }
                apply(fresh)
                if tick % 5 == 0 || fresh.library_syncing == true || libraryState.syncing == true || libraryState.classification_loading == true {
                    updateLibrary(try await request("/api/library"))
                }
                if platforms.isEmpty {
                    let available: PlatformList = try await request("/api/platforms")
                    platforms = available.platforms
                }
                tick += 1
                connected = true
                failures = 0
                if !checkedLibrary && fresh.account_waiting != true {
                    checkedLibrary = true
                    let _: Acknowledgement = try await request("/api/library/refresh", data: [:])
                }
            } catch {
                connected = false
                failures += 1
                if !didLaunch {
                    didLaunch = true
                    launchBackend()
                } else if failures > 8 {
                    notice = L("本机书库暂时无法读取。请允许访问小说文件夹，或确认项目未移动。")
                }
            }
            try? await Task.sleep(for: .seconds(1))
        }
    }

    private func updateLibrary(_ fresh: LibraryState) {
        guard fresh != libraryState else { return }
        if fresh.books != libraryState.books { libraryFilterIndex = LibraryFilterIndex(books: fresh.books) }
        libraryState = fresh
    }

    private func apply(_ fresh: AppState) {
        let previousProof = state.proofreading
        let previousTranslation = state.translation
        state = fresh
        downloads = fresh.downloads ?? []
        queued = (fresh.queue ?? []).filter { !(fresh.busy && $0.id == fresh.book?.id) }
        var freshLibrary = libraryState
        if let account = fresh.account { freshLibrary.account = account }
        freshLibrary.syncing = fresh.library_syncing
        updateLibrary(freshLibrary)
        if var book = fresh.book, detail?.id == book.id { book.metadata = book.metadata ?? detail?.metadata; detail = book }
        if previousProof?.running == true, fresh.proofreading?.running != true,
           let identifier = previousProof?.book_id, detail?.id == identifier { openBook(id: identifier) }
        if previousTranslation?.running == true, fresh.translation?.running != true,
           let identifier = previousTranslation?.book_id {
            if detail?.id == identifier { openBook(id: identifier) }
            if let chapter = editing, chapter.book_id == identifier, !AppDelegate.editorHasUnsavedChanges { refreshOpenChapter(chapter) }
        } else if let chapter = editing, chapter.book_id == fresh.book?.id, !AppDelegate.editorHasUnsavedChanges,
                  let metadata = fresh.book?.chapters.first(where: { $0.id == chapter.id })?.translation,
                  metadata.status != chapter.translation?.status || (metadata.updated_at != nil && metadata.updated_at != chapter.translation?.updated_at) {
            refreshOpenChapter(chapter)
        }
        if pendingScan && !sending && !fresh.busy {
            pendingScan = false
            loadingBook = false
            if fresh.phase == "ready" { detail = fresh.book }
            else { notice = fresh.message }
        }
    }

    private func launchBackend() {
        let process = Process()
        process.executableURL = root.appendingPathComponent(".venv/bin/python")
        process.arguments = ["-m", "uvicorn", "novelist.server:app", "--host", "127.0.0.1", "--port", "8765", "--no-access-log"]
        process.currentDirectoryURL = root
        // Startup diagnostics must be writable before project-folder access is
        // restored. Opening protected project files here can block the UI.
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("Shiye", isDirectory: true)
        let log = support.appendingPathComponent("backend.log")
        try? FileManager.default.createDirectory(at: support, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        FileManager.default.createFile(atPath: log.path, contents: nil, attributes: [.posixPermissions: 0o600])
        if let output = try? FileHandle(forWritingTo: log) {
            process.standardOutput = output
            process.standardError = output
        }
        do {
            try process.run()
            AppDelegate.ownedBackend = process
        } catch {
            notice = L("无法启动本地服务：\(localizedError(error))")
        }
    }

    func authorizeLibraryFolder() {
        guard !choosingLibraryFolder else { return }
        let panel = NSOpenPanel()
        panel.title = L("允许访问本机书库")
        panel.message = L("请选择当前的 Novelist 文件夹。")
        panel.prompt = L("允许访问")
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.directoryURL = root
        guard panel.runModal() == .OK, let selected = panel.url else { return }
        guard selected.standardizedFileURL == root.standardizedFileURL else {
            notice = L("请选择当前的 Novelist 文件夹。")
            return
        }
        _ = selected.startAccessingSecurityScopedResource()
        libraryAccessURL = selected
        if let bookmark = try? selected.bookmarkData(options: .withSecurityScope, includingResourceValuesForKeys: nil, relativeTo: nil) {
            UserDefaults.standard.set(bookmark, forKey: "libraryFolderBookmark")
        }
        choosingLibraryFolder = true
        notice = nil
        Task {
            defer { choosingLibraryFolder = false }
            if let previous = AppDelegate.ownedBackend, previous.isRunning {
                previous.terminate()
                for _ in 0..<20 {
                    if !previous.isRunning { break }
                    try? await Task.sleep(for: .milliseconds(100))
                }
                guard !previous.isRunning else {
                    notice = L("本机服务尚未退出，请重新打开拾页后继续。")
                    return
                }
            }
            didLaunch = true
            launchBackend()
        }
    }

    func action(_ name: String, payload: [String: Any] = [:]) {
        guard !sending else { return }
        sending = true
        notice = nil
        Task {
            defer { sending = false }
            do {
                let _: Acknowledgement = try await request("/api/" + name, data: payload)
                apply(try await request("/api/state"))
            } catch {
                if name == "scan" { pendingScan = false; loadingBook = false }
                notice = localizedError(error)
            }
        }
    }

    func refreshLibrary() { action("library/refresh") }
    func connectAccount() { action("account/connect") }
    func finishConnection() { action("account/finish") }
    func cancelConnection() { action("account/cancel") }
    func showReader() { connectAccount() }

    func requestDownload(url: String) {
        action("download", payload: ["url": url, "mode": mode, "delay": 3])
    }

    func proofread(_ bookID: String, undo: Bool = false) {
        action(undo ? "proofread/undo" : "proofread", payload: ["book_id": bookID])
        if undo, detail?.id == bookID {
            Task { while sending { try? await Task.sleep(for: .milliseconds(100)) }; openBook(id: bookID) }
        }
    }

    func openBook(id: String) {
        let requestID = UUID()
        openRequestID = requestID
        loadingBook = true
        notice = nil
        Task {
            defer { if openRequestID == requestID { loadingBook = false } }
            if let entry = libraryState.books.first(where: { $0.id == id }) {
                detail = Book(id: entry.id, title: entry.title, author: entry.author, url: entry.url, chapters: [], cover: entry.cover)
            }
            do {
                if let saved: Book = try? await request("/api/book?id=" + id), openRequestID == requestID { detail = saved }
                let book: Book = try await request("/api/book-details", data: ["id": id])
                if openRequestID == requestID { detail = book }
            } catch { if openRequestID == requestID { notice = localizedError(error) } }
        }
    }

    func addBook(url: String) {
        guard !sending else { return }
        self.url = url
        if state.busy { requestDownload(url: url); return }
        pendingScan = true
        loadingBook = true
        action("scan", payload: ["url": url])
    }

    func openChapter(bookID: String, chapterID: String) {
        Task {
            do { editing = try await request("/api/chapter?id=" + chapterID + "&book_id=" + bookID) }
            catch { notice = localizedError(error) }
        }
    }

    func readChapter(_ book: Book, _ chapter: Chapter) {
        guard !readingChapter else { return }
        if ["done", "review", "reviewed"].contains(chapter.state) { openChapter(bookID: book.id, chapterID: chapter.id); return }
        readingChapter = true
        Task {
            defer { readingChapter = false }
            do { let chapter: OnlineChapter = try await request("/api/read-chapter", data: ["book_id": book.id, "chapter_id": chapter.id]); if detail?.id == book.id { reading = chapter } }
            catch { notice = localizedError(error) }
        }
    }

    private func refreshOpenChapter(_ chapter: Chapter) {
        guard !refreshingChapter, let bookID = chapter.book_id else { return }
        refreshingChapter = true
        Task {
            defer { refreshingChapter = false }
            guard let refreshed: Chapter = try? await request("/api/chapter?id=" + chapter.id + "&book_id=" + bookID),
                  editing?.id == chapter.id, editing?.book_id == bookID, !AppDelegate.editorHasUnsavedChanges else { return }
            editing = refreshed
        }
    }

    func revealExports() { NSWorkspace.shared.open(root.appendingPathComponent("exports")) }

    func installChromeBridge() {
        guard !sending else { return }
        sending = true
        let process = Process()
        process.executableURL = root.appendingPathComponent(".venv/bin/python")
        process.arguments = [root.appendingPathComponent("scripts/install_chrome_bridge.py").path]
        process.currentDirectoryURL = root
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        process.terminationHandler = { task in
            Task { @MainActor in
                self.sending = false
                if task.terminationStatus == 0 {
                    NSWorkspace.shared.open(self.root.appendingPathComponent("chrome-extension"))
                    self.notice = L("本机连接已准备好。请在 Chrome 中加载扩展。")
                } else { self.notice = L("本机连接安装未完成，请检查项目目录后重试。") }
            }
        }
        do { try process.run() }
        catch { sending = false; notice = L("无法安装本机连接：\(localizedError(error))") }
    }

    func saveChapter(_ chapter: Chapter, text: String) async -> Bool {
        do {
            var payload: [String: Any] = ["id": chapter.id, "text": text]
            if let bookID = chapter.book_id { payload["book_id"] = bookID }
            let _: Acknowledgement = try await request("/api/chapter", data: payload)
            apply(try await request("/api/state"))
            if let bookID = chapter.book_id, detail?.id == bookID { openBook(id: bookID) }
            return true
        } catch { notice = localizedError(error); return false }
    }

    func export(_ format: String, bookID: String? = nil, variant: String = "source") {
        guard !sending else { return }
        let clean = cleanExport
        sending = true
        notice = nil
        Task {
            defer { sending = false }
            do {
                var payload: [String: Any] = ["format": format, "variant": variant, "clean": clean]
                if let bookID { payload["book_id"] = bookID }
                let result: ExportResult = try await request("/api/export", data: payload)
                let source = URL(fileURLWithPath: result.path)
                let panel = NSSavePanel()
                panel.nameFieldStringValue = source.lastPathComponent
                panel.allowedContentTypes = [UTType(filenameExtension: format) ?? .data]
                panel.canCreateDirectories = true
                panel.title = L("导出小说")
                panel.prompt = L("保存")
                panel.nameFieldLabel = L("文件名：")
                guard await panel.begin() == .OK, let destination = panel.url else { return }
                if source.standardizedFileURL != destination.standardizedFileURL {
                    try Data(contentsOf: source).write(to: destination, options: .atomic)
                }
                exported = destination
                notice = L("已导出：\(destination.lastPathComponent)")
            } catch { notice = localizedError(error) }
        }
    }

    func showSource(_ path: String) {
        let prefix = "/sources/"
        guard path.hasPrefix(prefix), let relative = String(path.dropFirst(prefix.count)).removingPercentEncoding else { return }
        let sources = root.appendingPathComponent(".local/books", isDirectory: true).standardizedFileURL
        let file = sources.appendingPathComponent(relative).standardizedFileURL
        guard file.path.hasPrefix(sources.path + "/"), FileManager.default.fileExists(atPath: file.path) else { return }
        NSWorkspace.shared.open(file)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    static var ownedBackend: Process?
    static var editorHasUnsavedChanges = false
    func applicationDidFinishLaunching(_ notification: Notification) {
        // Use this build's artwork even while Launch Services still caches the
        // previous development build's icon for the same bundle identifier.
        if let url = Bundle.main.url(forResource: "AppIcon-v04", withExtension: "icns"), let icon = NSImage(contentsOf: url) {
            NSApp.applicationIconImage = icon
        }
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
        Task { @MainActor in Localization.shared.updateNativeMenus() }
    }
    func applicationDidBecomeActive(_ notification: Notification) {
        DispatchQueue.main.async { Localization.shared.updateNativeMenus() }
    }
    func applicationWillTerminate(_ notification: Notification) { Self.ownedBackend?.terminate() }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag { sender.windows.first(where: { $0.canBecomeMain && !$0.isSheet })?.makeKeyAndOrderFront(nil) }
        return true
    }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard Self.editorHasUnsavedChanges else { return .terminateNow }
        let alert = NSAlert()
        alert.messageText = L("退出拾页？")
        alert.informativeText = L("未保存的修改会丢失。")
        alert.addButton(withTitle: L("继续编辑"))
        alert.addButton(withTitle: L("放弃修改并退出"))
        return alert.runModal() == .alertFirstButtonReturn ? .terminateCancel : .terminateNow
    }
}

struct ChapterView: View {
    @ObservedObject var model: Model
    @ObservedObject private var localization = Localization.shared
    let chapter: Chapter
    @State private var text: String
    @State private var saving = false
    @State private var changed = false
    @State private var confirmDiscard = false
    @State private var saveError: String?
    @State private var showOriginal: Bool
    @Environment(\.dismiss) private var dismiss
    private var canEdit: Bool { chapter.state != "blocked" && (!model.downloading || model.state.book?.id != chapter.book_id) && !model.proofreading(chapter.book_id) && !model.translating(chapter.book_id) }
    private var hasTranslation: Bool { chapter.translation != nil || model.detail?.id == chapter.book_id && model.detail?.translation != nil || model.downloads.contains { $0.id == chapter.book_id && $0.translation != nil } }
    private var displayedTitle: String { !showOriginal ? chapter.translation?.translated_title ?? chapter.title : chapter.title }
    init(model: Model, chapter: Chapter) {
        self.model = model; self.chapter = chapter
        _text = State(initialValue: chapter.text ?? "")
        _showOriginal = State(initialValue: chapter.translation == nil && !(model.detail?.id == chapter.book_id && model.detail?.translation != nil) && !model.downloads.contains { $0.id == chapter.book_id && $0.translation != nil })
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text(chapter.ordinal.map { L("第 \($0) 章 · \(displayedTitle)") } ?? displayedTitle).font(.headline)
                    Text(showOriginal ? chapter.label : chapter.translation?.label ?? L("尚未翻译")).font(.caption).foregroundStyle(showOriginal ? chapter.color : .secondary)
                }
                Spacer()
                if showOriginal, let source = chapter.screenshots?.first {
                    Button { model.showSource(source) } label: { Label(L("查看原图"), systemImage: "photo") }
                }
            }
            if hasTranslation {
                HStack {
                    Label(showOriginal ? L("原文校对") : L("译文"), systemImage: showOriginal ? "doc.text" : "character.bubble").font(.subheadline.weight(.medium))
                    if !showOriginal, let target = chapter.translation?.target_language ?? model.detail?.translation?.target_language { Text(TranslationLanguage.label(target)).font(.caption).foregroundStyle(.secondary) }
                    Spacer()
                    Toggle(L("显示原文"), isOn: $showOriginal).toggleStyle(.switch).controlSize(.small).fixedSize().disabled(saving)
                }
            }
            if showOriginal, let error = chapter.error, !error.isEmpty,
               !(chapter.proofreading?.isCurrent == true && chapter.incomplete != true && ["done", "review"].contains(chapter.state) && error == "OCR 已保存，请对照原图校对。") {
                Text(LM(error)).font(.caption).foregroundStyle(.secondary)
            }
            if let saveError { Text(LM(saveError)).font(.caption).foregroundStyle(.red) }
            if showOriginal, let proof = chapter.proofreading, proof.isCurrent {
                VStack(alignment: .leading, spacing: 7) {
                    if (proof.issues_count ?? 0) > 0 { Text(L("本章有 \(proof.issues_count ?? 0) 处疑点，请对照原图确认。")) .font(.caption).foregroundStyle(.secondary) }
                    if let issues = proof.issues, !issues.isEmpty {
                        DisclosureGroup(L("识别疑点")) {
                            ScrollView {
                                LazyVStack(alignment: .leading, spacing: 12) {
                                    ForEach(proof.issueGroups) { group in
                                        Text(group.issues.first?.label ?? L("其他疑点")).font(.caption.weight(.semibold))
                                        ForEach(Array(group.issues.enumerated()), id: \.offset) { _, issue in
                                        VStack(alignment: .leading, spacing: 4) {
                                            if let line = issue.line ?? issue.source_line_start {
                                                Text((issue.source_line_end ?? line) > line ? L("第 \(line)–\(issue.source_line_end ?? line) 行") : L("第 \(line) 行")).font(.caption.weight(.medium))
                                            }
                                            if let message = issue.message { Text(LM(message)).font(.caption) }
                                            if let before = issue.before, !before.isEmpty { Text(before).font(.caption.monospaced()).textSelection(.enabled) }
                                            if let after = issue.after, !after.isEmpty { Text(after).font(.caption.monospaced()).textSelection(.enabled) }
                                            if let candidate = issue.candidate, !candidate.isEmpty {
                                                Text(L("另一识别结果：\(candidate)")).font(.caption.monospaced()).textSelection(.enabled)
                                            }
                                        }.frame(maxWidth: .infinity, alignment: .leading)
                                        }
                                    }
                                }.padding(.top, 6)
                            }.frame(maxHeight: 130)
                        }.font(.caption)
                    }
                }
            }
            if !showOriginal {
                translationReader
            } else if !(chapter.text ?? "").isEmpty {
                TextEditor(text: $text).font(.system(size: 15)).lineSpacing(6).padding(8)
                    .background(Color(nsColor: .textBackgroundColor)).clipShape(RoundedRectangle(cornerRadius: 8))
                    .disabled(saving || !canEdit)
                    .onChange(of: text) { _, _ in changed = true; AppDelegate.editorHasUnsavedChanges = true }
                    .accessibilityLabel(L("章节正文"))
            } else {
                ContentUnavailableView(L("还没有正文"), systemImage: "doc.text.magnifyingglass", description: Text(L("采集成功后，可以在这里对照原图校对。")))
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
            HStack {
                Text(!showOriginal ? "" : model.translating(chapter.book_id) ? L("这本书正在翻译，暂停后可校对原文。") : chapter.state == "blocked" ? L("尚未取得本章正文，不能标记为已校对。") : model.proofreading(chapter.book_id) ? L("这本书正在自动校对，完成或暂停后可编辑。") : canEdit ? "" : L("这本书正在下载，暂停后可保存校对。")) .font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button(L("关闭")) { if changed { confirmDiscard = true } else { dismiss() } }.keyboardShortcut(.cancelAction).disabled(saving)
                if showOriginal {
                Button(saving ? L("正在保存…") : L("保存并标记已校对")) {
                    saving = true
                    Task {
                        if await model.saveChapter(chapter, text: text) { dismiss() }
                        else { saveError = model.notice ?? L("保存未完成，请重试。") }
                        saving = false
                    }
                }.buttonStyle(.borderedProminent).keyboardShortcut("s", modifiers: .command)
                    .disabled(saving || !canEdit || text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || (chapter.text ?? "").isEmpty)
                }
            }
        }.padding(24).frame(width: 690, height: 540)
        .environment(\.locale, localization.locale)
        .onDisappear { AppDelegate.editorHasUnsavedChanges = false }
        .interactiveDismissDisabled(saving || changed)
        .alert(L("放弃未保存的修改？"), isPresented: $confirmDiscard) {
            Button(L("继续编辑"), role: .cancel) {}
            Button(L("放弃修改"), role: .destructive) { dismiss() }
        }
    }
    private var translationReader: some View {
        VStack(alignment: .leading, spacing: 10) {
            if chapter.translation?.status == "stale" {
                Label(L("译文需更新"), systemImage: "arrow.triangle.2.circlepath").font(.caption).foregroundStyle(.orange)
            }
            if let error = chapter.translation?.error, !error.isEmpty { Text(LM(error)).font(.caption).foregroundStyle(.secondary) }
            if let issues = chapter.translation?.quality_issues, !issues.isEmpty {
                DisclosureGroup(L("翻译提示（\(issues.count)）")) {
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 10) {
                            ForEach(Array(issues.enumerated()), id: \.offset) { _, issue in
                                VStack(alignment: .leading, spacing: 4) {
                                    if let message = issue.message { Text(LM(message)).font(.caption) }
                                    if let source = issue.source {
                                        Text(source + (issue.target.map { " → " + $0 } ?? "")).font(.caption.monospaced()).textSelection(.enabled)
                                    }
                                }.frame(maxWidth: .infinity, alignment: .leading)
                            }
                        }.padding(.top, 6)
                    }.frame(maxHeight: 120)
                }.font(.caption).foregroundStyle(.secondary)
            }
            if let translated = chapter.translation?.text, !translated.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                ScrollView {
                    Text(translated).font(.system(size: 15)).lineSpacing(6).textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading).padding(16)
                }.background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 8))
                    .accessibilityLabel(L("章节译文"))
            } else {
                ContentUnavailableView(L("本章还没有译文"), systemImage: "character.bubble", description: Text(model.translating(chapter.book_id) ? L("翻译正在进行，已完成的译文会自动显示。") : L("在小说详情中开始或继续翻译，也可以切换查看原文。")))
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }.frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

@main
struct NovelistApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    @StateObject private var model = Model()
    @ObservedObject private var localization = Localization.shared
    var body: some Scene {
        Window(L("拾页"), id: "main") { LibraryRootView(model: model).environment(\.locale, localization.locale) }
            .defaultSize(width: 1020, height: 700)
            .windowResizability(.contentMinSize)
            .commands {
                CommandGroup(replacing: .appInfo) {
                    Button(L("关于拾页")) {
                        var options: [NSApplication.AboutPanelOptionKey: Any] = [.applicationName: L("拾页")]
                        if let url = Bundle.main.url(forResource: "AppIcon-v04", withExtension: "icns"), let icon = NSImage(contentsOf: url) {
                            options[.applicationIcon] = icon
                        }
                        NSApp.orderFrontStandardAboutPanel(options: options)
                    }
                }
                CommandGroup(replacing: .newItem) {}
                CommandGroup(after: .appSettings) {
                    Menu(L("界面语言")) {
                        ForEach(InterfaceLanguage.allCases) { language in
                            Button {
                                localization.language = language
                            } label: {
                                if localization.language == language { Label(language.label, systemImage: "checkmark") }
                                else { Text(language.label) }
                            }
                        }
                    }
                }
            }
    }
}
