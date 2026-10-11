import SwiftUI

struct BookVersionSummary: Decodable, Identifiable {
    var id: String
    var created_at: String
    var source: String
    var saved: Int
    var total: Int
    var refresh_failed: Int?

    private var date: Date? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.date(from: created_at) ?? ISO8601DateFormatter().date(from: created_at)
    }

    var label: String {
        let time = date?.formatted(Date.FormatStyle(date: .numeric, time: .standard).locale(Localization.shared.locale)) ?? created_at
        return time + " · " + acquisitionLabel(source)
    }

    var fileLabel: String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd_HH-mm-ss"
        return (date.map(formatter.string(from:)) ?? String(created_at.prefix(10))) + " " + acquisitionLabel(source)
    }
}

struct BookVersionList: Decodable { var versions: [BookVersionSummary] }

struct BookVersionsView: View {
    @ObservedObject var model: Model
    let book: Book
    @Environment(\.dismiss) private var dismiss
    @State private var versions: [BookVersionSummary] = []
    @State private var selectedID = ""
    @State private var edition: Book?
    @State private var chapterID: String?
    @State private var reading: Chapter?
    @State private var loading = true
    @State private var loadingChapter = false
    @State private var deleting = false
    @State private var confirmingDelete = false
    @State private var showingExport = false
    @State private var error: String?

    private var selected: BookVersionSummary? { versions.first { $0.id == selectedID } }
    private var selectedChapter: Chapter? { edition?.chapters.first { $0.id == chapterID } }
    private var query: String { "book_id=\(book.id)&version_id=\(selectedID)" }

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 5) {
                    Text(L("下载版本")).font(.headline)
                    Text(book.title).font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
                }
                Spacer()
                Button(L("完成")) { dismiss() }.keyboardShortcut(.cancelAction)
            }.padding(22)
            Divider()
            if !versions.isEmpty {
                HStack(spacing: 14) {
                    Picker(L("查看版本"), selection: $selectedID) {
                        ForEach(versions) { version in Text(version.label).tag(version.id) }
                    }.labelsHidden().frame(maxWidth: .infinity)
                    Button(L("导出…")) { showingExport = true }
                        .disabled(model.sending || loading || (selected?.saved ?? 0) == 0)
                    Button(role: .destructive) { confirmingDelete = true } label: {
                        Image(systemName: "trash")
                    }.help(L("删除版本")).accessibilityLabel(L("删除版本"))
                        .disabled(deleting || selectedID.isEmpty)
                }.padding(.horizontal, 22).padding(.vertical, 14)
                if let selected {
                    HStack {
                        Text(L("\(selected.saved) / \(selected.total) 章"))
                        Spacer()
                        if let failed = selected.refresh_failed, failed > 0 {
                            Text(L("\(failed) 章更新失败，保留原文"))
                        }
                    }.font(.caption).foregroundStyle(.secondary).padding(.horizontal, 22).padding(.bottom, 14)
                }
                Divider()
            }
            if let error {
                VStack(spacing: 12) {
                    Text(error).foregroundStyle(.secondary)
                    Button(L("重试")) { Task { await loadVersions() } }
                }.frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if loading {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if versions.isEmpty {
                ContentUnavailableView(L("暂无下载版本"), systemImage: "books.vertical")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let edition {
                Table(edition.chapters, selection: $chapterID) {
                    TableColumn(L("章节")) { chapter in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(chapter.title).lineLimit(1)
                            if let summary = chapter.summary, !summary.isEmpty {
                                Text(summary).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                            }
                        }.padding(.vertical, 5)
                    }
                    TableColumn(L("来源")) { chapter in Text(acquisitionLabel(chapter.method)).foregroundStyle(.secondary) }.width(100)
                    TableColumn(L("状态")) { chapter in
                        Text(chapter.readable_locally == true ? L("已保存") : L("未下载")).foregroundStyle(.secondary)
                    }.width(80)
                }
                Divider()
                HStack {
                    Spacer()
                    if loadingChapter { ProgressView().controlSize(.small) }
                    Button(L("阅读本章")) { Task { await readChapter() } }
                        .disabled(selectedChapter?.readable_locally != true || loadingChapter)
                }.padding(16)
            }
        }.frame(minWidth: 720, idealWidth: 820, minHeight: 510, idealHeight: 620)
            .task { await loadVersions() }
            .task(id: selectedID) { await loadEdition() }
            .confirmationDialog(L("删除所选版本？"), isPresented: $confirmingDelete) {
                Button(L("删除版本"), role: .destructive) { Task { await deleteVersion() } }
            } message: { Text(L("仅删除这份存档，当前正文和其他版本会保留。")) }
            .sheet(item: $reading) { chapter in BookVersionReadingView(chapter: chapter, version: selected?.label ?? "") }
            .sheet(isPresented: $showingExport) { BookExportView(model: model, book: book, initialVersionID: selectedID) }
    }

    @MainActor private func loadVersions() async {
        loading = true
        error = nil
        do {
            let result: BookVersionList = try await model.request("/api/book-versions?book_id=" + book.id)
            guard !Task.isCancelled else { return }
            versions = result.versions
            let nextID = versions.first?.id ?? ""
            if selectedID == nextID, !nextID.isEmpty { await loadEdition() }
            selectedID = nextID
            if selectedID.isEmpty { loading = false }
        } catch {
            guard !Task.isCancelled else { return }
            self.error = localizedError(error)
            loading = false
        }
    }

    @MainActor private func loadEdition() async {
        guard !selectedID.isEmpty else { return }
        let identifier = selectedID
        loading = true
        chapterID = nil
        error = nil
        do {
            let result: Book = try await model.request("/api/book-version?" + query)
            guard !Task.isCancelled, selectedID == identifier else { return }
            edition = result
        } catch {
            guard !Task.isCancelled, selectedID == identifier else { return }
            self.error = localizedError(error)
        }
        loading = false
    }

    @MainActor private func readChapter() async {
        guard let chapterID else { return }
        let identifier = selectedID
        loadingChapter = true
        defer { loadingChapter = false }
        do {
            let result: Chapter = try await model.request("/api/book-version-chapter?" + query + "&chapter_id=" + chapterID)
            if selectedID == identifier { reading = result; model.recordReading(bookID: book.id) }
        } catch { self.error = localizedError(error) }
    }

    @MainActor private func deleteVersion() async {
        deleting = true
        defer { deleting = false }
        do {
            let result: BookVersionList = try await model.request("/api/book-version-delete", data: ["book_id": book.id, "version_id": selectedID])
            versions = result.versions
            selectedID = versions.first?.id ?? ""
            edition = nil
            error = nil
            if selectedID.isEmpty { loading = false }
        } catch { self.error = localizedError(error) }
    }
}

private struct BookVersionReadingView: View {
    let chapter: Chapter
    let version: String
    @Environment(\.dismiss) private var dismiss
    @State private var showTranslation = true
    private var translated: String? { chapter.translation?.text }
    var body: some View {
        VStack(spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 5) {
                    Text(chapter.title).font(.headline)
                    Text(version).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                if translated?.isEmpty == false {
                    Toggle(L("显示译文"), isOn: $showTranslation).toggleStyle(.switch).controlSize(.small)
                }
                Button(L("完成")) { dismiss() }.keyboardShortcut(.cancelAction)
            }.padding(22)
            Divider()
            ScrollView {
                Text(showTranslation && translated?.isEmpty == false ? translated! : chapter.text ?? "")
                    .font(.system(size: 17)).lineSpacing(9).textSelection(.enabled)
                    .frame(maxWidth: 680, alignment: .leading).frame(maxWidth: .infinity).padding(30)
            }
        }.frame(minWidth: 700, idealWidth: 800, minHeight: 520, idealHeight: 650)
    }
}
