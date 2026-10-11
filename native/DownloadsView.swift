import SwiftUI

struct DownloadsView<ExportControl: View>: View {
    @ObservedObject var model: Model
    let openBook: (Book) -> Void
    let openVersions: (Book) -> Void
    @ViewBuilder var exportControl: (Book) -> ExportControl
    @State private var search = ""
    @State private var filter: Filter = .all

    private enum Filter: String, CaseIterable, Identifiable {
        case all = "全部", active = "进行中", saved = "已保存", following = "自动追更"
        var id: String { rawValue }
    }
    private var books: [Book] {
        var result = model.downloads
        if let current = model.state.book {
            if let index = result.firstIndex(where: { $0.id == current.id }) { result[index] = current }
            else { result.insert(current, at: 0) }
        }
        for item in model.queued where !result.contains(where: { $0.id == item.id }) {
            let shelf = model.libraryState.books.first { $0.id == item.id }
            result.append(Book(id: item.id, title: shelf?.title ?? L("作品 \(item.id)"), author: shelf?.author ?? "", url: item.url, chapters: []))
        }
        let queuePositions = Dictionary(uniqueKeysWithValues: model.queued.enumerated().map { ($1.id, $0 + 1) })
        return result.enumerated().sorted { left, right in
            let l = active(left.element) ? 0 : queuePositions[left.element.id] ?? Int.max
            let r = active(right.element) ? 0 : queuePositions[right.element.id] ?? Int.max
            return l == r ? left.offset < right.offset : l < r
        }.map(\.element)
    }
    private func progress(_ book: Book) -> ProgressInfo {
        if model.state.book?.id == book.id { return model.state.progress }
        return book.progress ?? ProgressInfo(done: 0, total: book.chapters.count)
    }
    private func active(_ book: Book) -> Bool { model.downloading && model.state.book?.id == book.id }
    private func queued(_ book: Book) -> Bool { model.queued.contains { $0.id == book.id } }
    private func following(_ book: Book) -> Bool { model.updates.books.contains { $0.id == book.id && $0.auto_subscribed } }
    private func matches(_ book: Book, _ filter: Filter) -> Bool {
        switch filter {
        case .all: return true
        case .active: return active(book) || queued(book) || model.proofreading(book.id) || model.translating(book.id)
        case .saved: return progress(book).done > 0
        case .following: return following(book)
        }
    }
    private var visibleBooks: [Book] {
        let query = search.trimmingCharacters(in: .whitespacesAndNewlines)
        return books.filter { matches($0, filter) && (query.isEmpty || ($0.title + " " + $0.author).localizedStandardContains(query)) }
    }

    var body: some View {
        if books.isEmpty {
            ContentUnavailableView(L("还没有下载"), systemImage: "arrow.down.circle", description: Text(L("从书架选择一本小说，即可在这里查看进度。")))
        } else {
            VStack(spacing: 0) {
                HStack(spacing: 14) {
                    Picker(L("下载状态"), selection: $filter) {
                        ForEach(Filter.allCases) { item in
                            Text(LS(item.rawValue)).tag(item)
                        }
                    }.pickerStyle(.segmented).labelsHidden().frame(maxWidth: 340)
                    Spacer(minLength: 0)
                    HStack(spacing: 6) {
                        Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
                        TextField(L("搜索书名或作者"), text: $search).textFieldStyle(.plain)
                            .accessibilityLabel(L("搜索下载"))
                    }.padding(8).background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 6))
                        .frame(maxWidth: 220)
                }.padding(.horizontal, 24).padding(.vertical, 14)
                Divider()
                if visibleBooks.isEmpty {
                    ContentUnavailableView(L("没有匹配的下载"), systemImage: "magnifyingglass", description: Text(L("试试其他状态或书名。")))
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    ScrollView {
                        LazyVStack(spacing: 0) {
                            ForEach(visibleBooks) { book in
                                bookRow(book)
                                Divider().padding(.leading, 82)
                            }
                        }.padding(.horizontal, 24)
                    }.id(filter.rawValue + "|" + search)
                }
                Divider()
                HStack {
                    Text(L("\(visibleBooks.count) 本作品")).font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    if model.state.queue_paused == true && !model.queued.isEmpty {
                        Button(L("继续队列")) { model.action("queue/resume", payload: [:]) }.disabled(model.sending)
                    } else if !model.queued.isEmpty {
                        Text(L("\(model.queued.count) 本等待下载")).font(.caption).foregroundStyle(.secondary)
                    }
                }.padding(.horizontal, 24).padding(.vertical, 10)
            }
        }
    }

    private func bookRow(_ book: Book) -> some View {
        let saved = progress(book)
        let value = book.download_run.map { ProgressInfo(done: $0.done, total: $0.total) } ?? saved
        let running = active(book)
        return HStack(spacing: 14) {
            Button { openBook(book) } label: {
                CoverImageView(source: book.cover, title: book.title)
            }.buttonStyle(.plain).accessibilityLabel(L("\(book.title)，查看章节"))
            VStack(alignment: .leading, spacing: 6) {
                Button { openBook(book) } label: {
                    Text(book.title).font(.body.weight(.medium)).lineLimit(1).frame(maxWidth: .infinity, alignment: .leading)
                }.buttonStyle(.plain).accessibilityLabel(L("\(book.title)，查看章节"))
                HStack(spacing: 8) {
                    if !book.author.isEmpty { Text(book.author).lineLimit(1) }
                    if let platform = book.platform_name { Text(LS(platform)).lineLimit(1) }
                    if following(book) { Text(L("自动追更")).foregroundStyle(Color.accentColor) }
                }.font(.caption).foregroundStyle(.secondary)
                if running {
                    ProgressView(value: Double(value.done), total: Double(max(1, value.total))).controlSize(.small)
                    Text(LM(model.state.message)).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                } else if model.proofreading(book.id) {
                    Text(L("正在自动校对")).font(.caption).foregroundStyle(.secondary)
                } else if model.translating(book.id) {
                    Text(L("正在翻译")).font(.caption).foregroundStyle(.secondary)
                }
            }.frame(maxWidth: .infinity, alignment: .leading)
            VStack(alignment: .trailing, spacing: 5) {
                Text(value.total > 0 ? L("\(value.done) / \(value.total) 章") : L("等待目录"))
                    .font(.subheadline).monospacedDigit()
                Text(running ? L("下载中") : queued(book) ? model.state.queue_paused == true ? L("已暂停") : L("排队中") : value.total > 0 && value.done >= value.total ? L("已完成") : value.done > 0 ? L("已保存") : L("未下载"))
                    .font(.caption).foregroundStyle(running ? Color.accentColor : Color.secondary)
            }.frame(width: 100, alignment: .trailing)
            HStack(spacing: 8) {
                if running {
                    Button(L("暂停")) { model.action("pause", payload: [:]) }.disabled(model.sending)
                } else if saved.done > 0 {
                    exportControl(book)
                } else if !queued(book) {
                    Button(L("下载")) { model.requestDownload(url: book.url) }.disabled(!model.connected || model.sending)
                }
                Menu {
                    Button(L("查看章节")) { openBook(book) }
                    if saved.done > 0 || book.download_run != nil {
                        Button(L("下载版本…")) { openVersions(book) }
                        Button(L("重新下载")) { model.requestDownload(url: book.url, refresh: true) }
                            .disabled(running || queued(book) || !model.connected || model.sending)
                    }
                    if !running && !queued(book) && value.done < value.total {
                        Button(value.done > 0 ? L("继续下载") : L("下载整本")) { model.requestDownload(url: book.url) }
                            .disabled(!model.connected || model.sending)
                    }
                    if saved.done > 0 {
                        Divider()
                        if model.proofreading(book.id) {
                            Button(L("暂停校对")) { model.action("proofread/pause", payload: ["book_id": book.id]) }
                        } else {
                            Button(L("自动校对")) { model.proofread(book.id) }.disabled(!model.connected || model.working || queued(book))
                            if book.proofreading?.can_undo == true {
                                Button(L("撤销自动校对")) { model.proofread(book.id, undo: true) }.disabled(!model.connected || model.working)
                            }
                        }
                    }
                } label: { Image(systemName: "ellipsis") }
                    .menuStyle(.borderlessButton).menuIndicator(.hidden).fixedSize()
                    .accessibilityLabel(L("\(book.title)，更多操作"))
            }.frame(width: 118, alignment: .trailing)
        }.padding(.vertical, 14)
    }
}
