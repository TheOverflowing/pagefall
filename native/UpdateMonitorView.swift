import SwiftUI
import AppKit

struct UpdateMonitorBook: Decodable, Equatable, Identifiable {
    var id: String
    var title: String
    var auto_subscribed: Bool
    var last_checked_at: String?
    var changed_count: Int
    var error: String?
}

struct UpdateMonitorState: Decodable, Equatable {
    var enabled: Bool
    var checking: Bool
    var last_checked_at: String?
    var error: String?
    var subscribed_count: Int
    var changed_count: Int
    var books: [UpdateMonitorBook]
    var revision: Int?
    static let empty = UpdateMonitorState(enabled: true, checking: false, subscribed_count: 0, changed_count: 0, books: [])
    var lastCheckedLabel: String? {
        guard let raw = last_checked_at else { return nil }
        let format = ISO8601DateFormatter()
        format.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let date = format.date(from: raw) ?? ISO8601DateFormatter().date(from: raw)
        return date?.formatted(Date.FormatStyle(date: .numeric, time: .shortened).locale(Localization.shared.locale))
    }
}

struct UpdateMonitorView: View {
    @ObservedObject var model: Model
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text(L("自动追更")).font(.headline)
                Spacer()
                Button(L("完成")) { dismiss() }.keyboardShortcut(.cancelAction)
            }.padding(22)
            Divider()
            VStack(alignment: .leading, spacing: 12) {
                Toggle(L("自动保存章节更新"), isOn: Binding(get: { model.updates.enabled }, set: { model.setAutomaticUpdates($0) }))
                    .toggleStyle(.switch).disabled(model.updatesSending || !model.connected)
                Text(L("自动订阅作品每分钟检查，其余已保存作品每 15 分钟检查。"))
                    .font(.caption).foregroundStyle(.secondary)
                HStack {
                    if model.updates.checking {
                        ProgressView().controlSize(.small)
                        Text(L("正在检查更新…")).font(.caption).foregroundStyle(.secondary)
                    } else if let checked = model.updates.lastCheckedLabel {
                        Text(L("上次检查：\(checked)")).font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button(L("立即检查")) { model.checkForUpdates() }
                        .disabled(!model.connected || model.updates.checking || model.updatesSending)
                }
                if let error = model.updatesError ?? model.updates.error, !error.isEmpty {
                    Text(LM(error)).font(.caption).foregroundStyle(.red).textSelection(.enabled)
                }
            }.padding(22)
            Divider()
            if model.updates.books.isEmpty {
                ContentUnavailableView(L("暂无追更作品"), systemImage: "book.closed",
                    description: Text(L("保存章节或在晋江开启自动订阅后，作品会出现在这里。")))
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                List {
                    let subscribed = model.updates.books.filter(\.auto_subscribed)
                    let saved = model.updates.books.filter { !$0.auto_subscribed }
                    if !subscribed.isEmpty {
                        Section(L("自动订阅 · \(subscribed.count) 本")) {
                            ForEach(subscribed) { book in bookRow(book) }
                        }
                    }
                    if !saved.isEmpty {
                        Section(L("其他已保存作品")) {
                            ForEach(saved) { book in bookRow(book) }
                        }
                    }
                }.listStyle(.inset)
            }
        }.frame(width: 620, height: 560)
        .task { await model.refreshUpdates() }
    }

    private func bookRow(_ book: UpdateMonitorBook) -> some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 5) {
                Text(book.title).lineLimit(1)
                if let error = book.error, !error.isEmpty { Text(LM(error)).font(.caption).foregroundStyle(.secondary).lineLimit(2) }
            }
            Spacer()
            if book.changed_count > 0 { Text(L("\(book.changed_count) 章有更新")).font(.caption).foregroundStyle(Color.accentColor) }
            Button(L("查看目录")) { dismiss(); model.openBook(id: book.id) }
        }.padding(.vertical, 5)
    }
}

struct UpdateMenuContent: View {
    @ObservedObject var model: Model
    @Environment(\.openWindow) private var openWindow
    var body: some View {
        Button(L("打开拾页")) {
            NSApp.setActivationPolicy(.regular)
            openWindow(id: "main")
            NSApp.activate(ignoringOtherApps: true)
        }
        Divider()
        Text(model.updates.checking ? L("正在检查更新…") : model.updates.enabled ? L("自动追更已开启") : L("自动追更已暂停"))
        if model.updates.changed_count > 0 { Text(L("\(model.updates.changed_count) 章有更新")) }
        Button(L("立即检查")) { model.checkForUpdates() }.disabled(!model.connected || model.updates.checking || model.updatesSending)
        Button(model.updates.enabled ? L("暂停自动追更") : L("恢复自动追更")) { model.setAutomaticUpdates(!model.updates.enabled) }
            .disabled(!model.connected || model.updatesSending)
        Divider()
        Button(L("退出拾页")) { NSApp.terminate(nil) }.keyboardShortcut("q")
    }
}
