import SwiftUI

struct ChapterVersionSummary: Decodable, Identifiable {
    var id: String
    var title: String
    var recorded_at: String
    var source_updated_at: String?
    var kind: String
    var characters: Int

    var kindLabel: String {
        switch kind {
        case "source": return L("读取正文")
        case "edit": return L("手动校对")
        case "proofread": return L("自动校对")
        case "proofread_undo": return L("撤销校对")
        default: return L("已有副本")
        }
    }
    var savedTime: String {
        guard let date = Self.fractionalDate.date(from: recorded_at) ?? Self.isoDate.date(from: recorded_at) else { return recorded_at }
        return date.formatted(Date.FormatStyle(date: .numeric, time: .standard).locale(Localization.shared.locale))
    }
    private static let isoDate = ISO8601DateFormatter()
    private static let fractionalDate: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()
}

struct ChapterVersionList: Decodable { var versions: [ChapterVersionSummary] }
struct ChapterVersionText: Decodable { var text: String }
struct ChapterVersionDiff: Decodable {
    struct Row: Decodable { var kind: String; var text: String }
    var rows: [Row]
    var added: Int
    var deleted: Int
}

struct ChapterHistoryView: View {
    @ObservedObject var model: Model
    let bookID: String
    let chapterID: String
    let title: String
    @Environment(\.dismiss) private var dismiss
    @State private var versions: [ChapterVersionSummary] = []
    @State private var selectedID = ""
    @State private var previousID = ""
    @State private var comparing = false
    @State private var loadingList = true
    @State private var loadingContent = false
    @State private var text = ""
    @State private var difference: ChapterVersionDiff?
    @State private var error: String?

    private var selectedVersion: ChapterVersionSummary? { versions.first { $0.id == selectedID } }
    private var selectionKey: String { "\(comparing)-\(previousID)-\(selectedID)" }
    private var query: String {
        var components = URLComponents()
        components.queryItems = [URLQueryItem(name: "book_id", value: bookID), URLQueryItem(name: "chapter_id", value: chapterID)]
        return components.percentEncodedQuery ?? ""
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 16) {
                VStack(alignment: .leading, spacing: 5) {
                    Text(L("历史版本")).font(.headline)
                    Text(title).font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
                }
                Spacer()
                if versions.count > 1 {
                    Picker(L("显示方式"), selection: $comparing) {
                        Text(L("查看正文")).tag(false)
                        Text(L("差异对比")).tag(true)
                    }.pickerStyle(.segmented).labelsHidden().frame(width: 230)
                }
                Button(L("完成")) { dismiss() }.keyboardShortcut(.cancelAction)
            }.padding(22)
            Divider()
            if !versions.isEmpty {
                versionControls
                Divider()
            }
            if let error {
                VStack(spacing: 12) {
                    Text(error).foregroundStyle(.secondary).multilineTextAlignment(.center)
                    Button(L("重试")) {
                        Task { if versions.isEmpty { await loadVersions() } else { await loadContent() } }
                    }
                }.padding(32).frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if loadingList || loadingContent {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if versions.isEmpty {
                ContentUnavailableView(L("暂无历史版本"), systemImage: "clock.arrow.circlepath")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if comparing, let difference {
                differenceContent(difference)
            } else {
                ScrollView {
                    Text(text).font(.system(size: 16)).lineSpacing(8).textSelection(.enabled)
                        .frame(maxWidth: 690, alignment: .leading).frame(maxWidth: .infinity).padding(28)
                }.accessibilityLabel(L("章节正文"))
            }
        }
        .frame(minWidth: 760, idealWidth: 860, minHeight: 540, idealHeight: 680)
        .task { await loadVersions() }
        .task(id: selectionKey) { await loadContent() }
    }

    private var versionControls: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 14) {
                if comparing {
                    versionPicker(L("对比版本"), selection: $previousID)
                    Image(systemName: "arrow.right").foregroundStyle(.secondary).accessibilityHidden(true)
                }
                versionPicker(L("查看版本"), selection: $selectedID)
            }
            if comparing, let difference, !loadingContent {
                HStack(spacing: 16) {
                    Label(L("新增 \(difference.added) 段"), systemImage: "plus").foregroundStyle(.green)
                    Label(L("删除 \(difference.deleted) 段"), systemImage: "minus").foregroundStyle(.red)
                    if difference.added == 0 && difference.deleted == 0 { Text(L("正文无差异")).foregroundStyle(.secondary) }
                }.font(.caption)
            } else if !comparing, let version = selectedVersion {
                HStack(spacing: 14) {
                    Text(L("\(version.characters) 字"))
                    if let updated = version.source_updated_at, !updated.isEmpty {
                        Text(L("章节更新时间：\(updated)"))
                    }
                }.font(.caption).foregroundStyle(.secondary)
            }
        }.padding(.horizontal, 22).padding(.vertical, 15)
    }

    private func versionPicker(_ label: String, selection: Binding<String>) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label).font(.caption).foregroundStyle(.secondary)
            Picker(label, selection: selection) {
                ForEach(versions) { version in
                    Text(version.savedTime + " · " + version.kindLabel).tag(version.id)
                }
            }.labelsHidden().frame(maxWidth: .infinity)
        }.frame(maxWidth: .infinity, alignment: .leading)
    }

    private func differenceContent(_ difference: ChapterVersionDiff) -> some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 0) {
                ForEach(Array(difference.rows.enumerated()), id: \.offset) { _, row in
                    let inserted = row.kind == "insert"
                    let deleted = row.kind == "delete"
                    HStack(alignment: .top, spacing: 12) {
                        Text(inserted ? "+" : deleted ? "−" : " ")
                            .font(.system(size: 16, weight: .semibold, design: .monospaced))
                            .foregroundStyle(inserted ? Color.green : deleted ? Color.red : Color.secondary)
                            .frame(width: 15).accessibilityHidden(true)
                        Text(row.text.isEmpty ? " " : row.text)
                            .font(.system(size: 16)).lineSpacing(8).textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .padding(.horizontal, 18).padding(.vertical, 8)
                    .background(inserted ? Color.green.opacity(0.09) : deleted ? Color.red.opacity(0.09) : Color.clear)
                    .accessibilityElement(children: .combine)
                    .accessibilityLabel((inserted ? L("新增") + " " : deleted ? L("删除") + " " : "") + row.text)
                }
            }.frame(maxWidth: 740).frame(maxWidth: .infinity).padding(.vertical, 22).padding(.horizontal, 16)
        }.accessibilityLabel(L("差异对比"))
    }

    @MainActor private func loadVersions() async {
        loadingList = true
        error = nil
        do {
            let response: ChapterVersionList = try await model.request("/api/chapter-versions?" + query)
            guard !Task.isCancelled else { return }
            versions = response.versions
            selectedID = versions.first?.id ?? ""
            previousID = versions.dropFirst().first?.id ?? selectedID
            comparing = versions.count > 1
        } catch {
            guard !Task.isCancelled else { return }
            self.error = localizedError(error)
        }
        loadingList = false
    }

    @MainActor private func loadContent() async {
        guard !selectedID.isEmpty else { return }
        let requestedSelection = selectionKey
        loadingContent = true
        error = nil
        do {
            if comparing {
                let response: ChapterVersionDiff = try await model.request("/api/chapter-diff?" + query + "&from_id=" + previousID + "&to_id=" + selectedID)
                guard !Task.isCancelled, requestedSelection == selectionKey else { return }
                difference = response
            } else {
                let response: ChapterVersionText = try await model.request("/api/chapter-version?" + query + "&version_id=" + selectedID)
                guard !Task.isCancelled, requestedSelection == selectionKey else { return }
                text = response.text
            }
        } catch {
            guard !Task.isCancelled, requestedSelection == selectionKey else { return }
            self.error = localizedError(error)
        }
        loadingContent = false
    }
}
