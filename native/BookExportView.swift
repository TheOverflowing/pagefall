import SwiftUI

struct BookExportView: View {
    @ObservedObject var model: Model
    let book: Book
    @Environment(\.dismiss) private var dismiss
    @State private var versions: [BookVersionSummary] = []
    @State private var selectedID: String
    @State private var archivedBook: Book?
    @State private var format = "epub"
    @State private var variant: String
    @State private var loadingList = true
    @State private var loadingVersion = false
    @State private var error: String?

    init(model: Model, book: Book, initialVersionID: String? = nil) {
        self.model = model
        self.book = book
        _selectedID = State(initialValue: initialVersionID ?? "")
        _variant = State(initialValue: (book.translation?.done ?? 0) > 0 ? "translation" : "source")
    }

    private var currentBook: Book {
        model.downloads.first { $0.id == book.id } ?? (model.state.book?.id == book.id ? model.state.book! : book)
    }
    private var selectedBook: Book? { selectedID.isEmpty ? currentBook : archivedBook }
    private var selectedVersion: BookVersionSummary? { versions.first { $0.id == selectedID } }
    private var translationAvailable: Bool {
        (selectedBook?.translation?.done ?? 0) > 0 || (selectedBook?.translation?.stale ?? 0) > 0
    }
    private var currentBusy: Bool {
        (model.state.busy && model.state.book?.id == book.id) || model.proofreading(book.id) || model.translating(book.id) || model.refreshingChapterID?.hasPrefix(book.id + "/") == true
    }
    private var canExport: Bool {
        !loadingList && !loadingVersion && !model.sending && selectedBook != nil &&
        (selectedID.isEmpty ? !currentBusy && (currentBook.progress?.done ?? 0) > 0 : (selectedVersion?.saved ?? 0) > 0) &&
        (variant != "translation" || translationAvailable)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            VStack(alignment: .leading, spacing: 6) {
                Text(L("导出小说")).font(.headline)
                Text(book.title).foregroundStyle(.secondary).lineLimit(2)
            }
            Grid(alignment: .leading, horizontalSpacing: 18, verticalSpacing: 16) {
                GridRow(alignment: .top) {
                    Text(L("版本")).foregroundStyle(.secondary).padding(.top, 4)
                    VStack(alignment: .leading, spacing: 7) {
                        Picker(L("导出版本"), selection: $selectedID) {
                            Text(L("当前版本")).tag("")
                            ForEach(versions) { version in Text(version.label).tag(version.id) }
                        }.labelsHidden().frame(maxWidth: .infinity, alignment: .leading)
                        if loadingList || loadingVersion {
                            ProgressView().controlSize(.small)
                        } else if let selectedVersion {
                            Text(L("\(selectedVersion.saved) / \(selectedVersion.total) 章")).font(.caption).foregroundStyle(.secondary)
                        } else if selectedID.isEmpty, let progress = currentBook.progress {
                            Text(L("\(progress.done) / \(progress.total) 章")).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
                GridRow {
                    Text(L("内容")).foregroundStyle(.secondary)
                    if translationAvailable {
                        Picker(L("导出内容"), selection: $variant) {
                            Text(L("原文")).tag("source")
                            Text(L("译文")).tag("translation")
                        }.pickerStyle(.segmented).labelsHidden()
                    } else { Text(L("原文")) }
                }
                GridRow {
                    Text(L("格式")).foregroundStyle(.secondary)
                    Picker(L("导出格式"), selection: $format) {
                        Text("EPUB").tag("epub")
                        Text("TXT").tag("txt")
                        Text("Markdown").tag("md")
                    }.pickerStyle(.segmented).labelsHidden()
                }
                GridRow {
                    Text("")
                    Toggle(L("纯净模式"), isOn: $model.cleanExport)
                        .help(L("关闭后附带导出与校对报告"))
                }
            }
            if selectedID.isEmpty && currentBusy {
                Text(L("暂停当前处理后可导出当前版本，也可选择已保存的下载版本。"))
                    .font(.caption).foregroundStyle(.secondary)
            }
            if let error {
                HStack {
                    Text(error).font(.caption).foregroundStyle(.red)
                    Spacer()
                    Button(L("重试")) { Task { await loadVersions(); await loadSelectedVersion() } }
                }
            }
            HStack {
                Spacer()
                Button(L("取消")) { dismiss() }.keyboardShortcut(.cancelAction)
                Button(L("导出…")) {
                    model.export(format, bookID: book.id, variant: variant,
                                 versionID: selectedID.isEmpty ? nil : selectedID,
                                 versionName: selectedVersion?.fileLabel)
                    dismiss()
                }.keyboardShortcut(.defaultAction).buttonStyle(.borderedProminent).disabled(!canExport)
            }
        }.padding(24).frame(width: 540)
            .task { await loadVersions() }
            .task(id: selectedID) { await loadSelectedVersion() }
    }

    @MainActor private func loadVersions() async {
        loadingList = true
        defer { loadingList = false }
        do {
            let result: BookVersionList = try await model.request("/api/book-versions?book_id=" + book.id)
            guard !Task.isCancelled else { return }
            versions = result.versions
            error = nil
        } catch {
            guard !Task.isCancelled else { return }
            self.error = localizedError(error)
        }
    }

    @MainActor private func loadSelectedVersion() async {
        archivedBook = nil
        error = nil
        if selectedID.isEmpty {
            loadingVersion = false
            if !translationAvailable { variant = "source" }
            return
        }
        let identifier = selectedID
        loadingVersion = true
        do {
            let result: Book = try await model.request("/api/book-version?book_id=" + book.id + "&version_id=" + identifier)
            guard !Task.isCancelled, identifier == selectedID else { return }
            archivedBook = result
            if !translationAvailable { variant = "source" }
        } catch {
            guard !Task.isCancelled, identifier == selectedID else { return }
            self.error = localizedError(error)
        }
        loadingVersion = false
    }
}
