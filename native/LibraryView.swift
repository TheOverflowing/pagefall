import SwiftUI
import AppKit

struct LibraryRootView: View {
    @ObservedObject var model: Model
    @ObservedObject private var localization = Localization.shared
    private enum Page: String, CaseIterable { case library = "我的书架", downloads = "下载", translation = "翻译", account = "晋江账号" }
    @State private var page: Page? = .library
    @AppStorage("library.layout") private var layout = "list"
    @AppStorage("library.sort") private var sortRaw = "platform"
    @AppStorage("library.sortAscending") private var sortAscending = true
    @AppStorage("library.selectedFolder") private var selectedFolderID = ""
    @AppStorage("library.foldersExpanded") private var foldersExpanded = true
    @State private var managingBooks = false
    @State private var selectedBookIDs: Set<String> = []
    @State private var movingBooks = false
    @State private var batchMessage: String?
    @State private var folderEditor: LibraryFolderEditorRequest?
    @State private var deletingFolder: LibraryFolder?
    @State private var search = ""
    @State private var filters = LibraryFilterSelection.load()
    @State private var showingFilters = false
    @State private var adding = false
    @State private var chromeSetup = false
    @State private var newURL = ""
    @State private var showingPlatforms = false
    @State private var showingUpdates = false
    @State private var platformSearch = ""
    @State private var chapterSelection: String?
    @State private var requestedDirectoryID: String?
    private struct HistorySelection: Identifiable {
        let bookID: String
        let chapter: Chapter
        var id: String { bookID + "/" + chapter.id }
    }
    @State private var historySelection: HistorySelection?
    @State private var versionsBook: Book?
    @State private var exportBook: Book?
    @State private var translationBook: Book?
    @State private var glossaryBook: Book?
    @State private var translationSettings = false
    @State private var jinjiangSettings = false
    @State private var detailTab = 0
    @State private var draftTitle = ""
    @State private var draftText = ""
    @State private var creatingDraft = false

    private var accountConnected: Bool { model.libraryState.account.state == "connected" }
    private var accountNickname: String? {
        guard let value = model.libraryState.account.nickname?.trimmingCharacters(in: .whitespacesAndNewlines), !value.isEmpty else { return nil }
        return value
    }
    private var awaitingLogin: Bool { model.state.account_waiting == true || ["awaiting_login", "login_pending"].contains(model.libraryState.account.state) }
    private var syncing: Bool { model.libraryState.syncing == true }
    private var accountStatus: String {
        if !model.connected { return L("正在连接") }
        switch model.libraryState.account.state {
        case "connected": return L("已连接")
        case "expired", "needs_login", "login_required": return L("需要重新登录")
        case "connecting", "checking": return L("正在检查连接")
        case "awaiting_login", "login_pending": return L("等待完成登录")
        case "error": return L("暂时无法检查连接")
        default: return L("尚未连接")
        }
    }
    private var visibleBooks: [LibraryBook] {
        let query = search.trimmingCharacters(in: .whitespacesAndNewlines)
        return model.libraryFilterIndex.matchingBooks(filters, query: query, sort: sortOrder, ascending: sortAscending, folderID: selectedFolderID.isEmpty ? nil : selectedFolderID)
    }
    private var sortOrder: LibrarySortOrder { LibrarySortOrder(rawValue: sortRaw) ?? .platform }
    private var visibleBookIDs: Set<String> { Set(visibleBooks.map(\.id)) }
    private var allBooksSelected: Bool { !visibleBookIDs.isEmpty && visibleBookIDs.isSubset(of: selectedBookIDs) }
    private var folders: [LibraryFolder] { (model.libraryState.folders ?? []).sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending } }
    private var folderTitle: String {
        if selectedFolderID == LibraryFilterIndex.unfiledFolderID { return L("未分类") }
        return folders.first { $0.id == selectedFolderID }?.name ?? L("我的书架")
    }
    private var sidebarSelection: Binding<String?> {
        Binding(get: {
            switch page ?? .library {
            case .library: return selectedFolderID.isEmpty ? "library" : "folder:" + selectedFolderID
            case .downloads: return "downloads"
            case .translation: return "translation"
            case .account: return nil
            }
        }, set: { value in
            guard let value else { return }
            if value == "downloads" { page = .downloads }
            else if value == "translation" { page = .translation }
            else {
                selectedFolderID = value.hasPrefix("folder:") ? String(value.dropFirst(7)) : ""
                model.closeBook()
                page = .library
            }
        })
    }
    private var filtering: Bool { !search.isEmpty || !filters.isEmpty }
    private var downloadBooks: [Book] {
        var books = model.downloads
        if let book = model.state.book, !books.contains(where: { $0.id == book.id }) { books.insert(book, at: 0) }
        return books
    }
    private func progress(for book: Book) -> ProgressInfo {
        if model.state.book?.id == book.id { return model.state.progress }
        if let progress = book.progress { return progress }
        let done = book.chapters.filter { ["done", "review", "reviewed"].contains($0.state) }.count
        return ProgressInfo(done: done, total: book.chapters.count, missing: max(0, book.chapters.count - done), review: book.chapters.filter { $0.state == "review" }.count)
    }
    private func isActive(_ book: Book) -> Bool { model.downloading && model.state.book?.id == book.id }
    private func isProcessing(_ book: Book) -> Bool { isActive(book) || model.proofreading(book.id) }
    private func isQueued(_ book: Book) -> Bool { model.queued.contains { $0.id == book.id } }
    private func savedAll(_ book: Book) -> Bool { let value = progress(for: book); return value.total > 0 && value.done >= value.total }
    private var contentIdentity: String {
        switch page ?? .library {
        case .library: return "library:\(model.detail?.id ?? (model.loadingBook ? "loading" : "shelf"))"
        case .translation: return "translation"
        case .downloads: return "downloads"
        case .account: return "account"
        }
    }

    var body: some View {
        NavigationSplitView {
            VStack(spacing: 0) {
                List(selection: sidebarSelection) {
                    Label(L("我的书架"), systemImage: "books.vertical").tag("library")
                    Label(L("下载"), systemImage: "arrow.down.circle").tag("downloads")
                    Label(L("翻译"), systemImage: "character.bubble").tag("translation")
                    Section(isExpanded: $foldersExpanded) {
                        folderRow(id: LibraryFilterIndex.unfiledFolderID, name: L("未分类"), count: model.libraryFilterIndex.unfiledCount)
                        ForEach(folders) { folder in
                            folderRow(id: folder.id, name: folder.name, count: model.libraryFilterIndex.folderCounts[folder.id] ?? 0)
                                .contextMenu {
                                    Button(L("重命名…")) { folderEditor = LibraryFolderEditorRequest(folder: folder) }
                                    Button(L("删除文件夹…"), role: .destructive) { deletingFolder = folder }
                                }
                        }
                        Button { folderEditor = LibraryFolderEditorRequest(folder: nil) } label: {
                            Label(L("新建文件夹…"), systemImage: "folder.badge.plus")
                        }.buttonStyle(.plain).disabled(!model.connected)
                    } header: { Text(L("文件夹")) }
                }.listStyle(.sidebar)
                if model.downloading || model.state.proofreading?.running == true || model.state.translation?.running == true { activity.padding(.horizontal, 14).padding(.bottom, 16) }
                Divider()
                Button { page = .account } label: {
                    HStack(spacing: 10) {
                        Image(systemName: "person.crop.circle").font(.system(size: 22)).foregroundStyle(.secondary)
                        VStack(alignment: .leading, spacing: 3) {
                            Text(accountNickname ?? L("晋江账号")).font(.system(size: 13, weight: .medium)).lineLimit(1)
                            Text(accountStatus).font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                    }.padding(12).contentShape(Rectangle())
                }.buttonStyle(.plain)
                    .background(page == .account ? Color.primary.opacity(0.06) : .clear)
                    .help(accountNickname.map { L("\($0) · 晋江账号") } ?? L("晋江账号"))
                    .accessibilityLabel(L("\(accountNickname ?? L("晋江账号"))，\(accountStatus)，管理账号"))
            }
            .navigationSplitViewColumnWidth(min: 180, ideal: 200, max: 240)
        } detail: {
            VStack(spacing: 0) {
                contentHeader.fixedSize(horizontal: false, vertical: true).layoutPriority(1)
                Divider()
                GeometryReader { bounds in
                    Group {
                    switch page ?? .library {
                    case .library:
                        if let book = model.detail { bookDetail(book) }
                        else if model.loadingBook {
                            VStack(spacing: 12) { ProgressView(); Text(L("正在读取小说…")).font(.subheadline).foregroundStyle(.secondary) }
                                .frame(maxWidth: .infinity, maxHeight: .infinity)
                        } else { library }
                    case .translation: translationWorkspace
                    case .downloads: downloads
                    case .account: account
                    }
                    }
                    // AppKit-backed Table must receive a fresh host when replacing
                    // a scroll view, and finite bounds from the detail column.
                    .id(contentIdentity)
                    .frame(width: max(0, bounds.size.width), height: max(0, bounds.size.height), alignment: .topLeading)
                    .clipped()
                }.frame(minWidth: 0, maxWidth: .infinity, minHeight: 0, maxHeight: .infinity)
                if let notice = model.notice, !notice.isEmpty { noticeBar(notice) }
            }.background(Color(nsColor: .windowBackgroundColor))
        }
        .navigationSplitViewStyle(.balanced)
        .frame(minWidth: 820, minHeight: 590)
        .sheet(isPresented: $adding) { addSheet }
        .sheet(isPresented: $chromeSetup) { chromeSheet }
        .sheet(isPresented: $showingPlatforms) { platformsSheet }
        .sheet(isPresented: $showingUpdates) { UpdateMonitorView(model: model) }
        .sheet(isPresented: $showingFilters) {
            LibraryFiltersView(index: model.libraryFilterIndex, query: search.trimmingCharacters(in: .whitespacesAndNewlines), initial: filters,
                classificationLoading: model.libraryState.classification_loading == true,
                classificationCount: model.libraryState.classification_count ?? 0,
                basicClassificationCount: model.libraryState.classification_basic_count ?? model.libraryState.classification_count ?? 0,
                folderID: selectedFolderID.isEmpty ? nil : selectedFolderID) { filters = $0 }
        }
        .sheet(item: $model.reading) { chapter in OnlineReadingView(model: model, chapter: chapter) }
        .sheet(item: $model.editing) { chapter in ChapterView(model: model, chapter: chapter) }
        .sheet(item: $historySelection) { selection in
            ChapterHistoryView(model: model, bookID: selection.bookID, chapterID: selection.chapter.id, title: selection.chapter.title)
        }
        .sheet(item: $translationBook) { book in TranslationSettingsView(model: model, book: book) }
        .sheet(item: $versionsBook) { book in BookVersionsView(model: model, book: book) }
        .sheet(item: $exportBook) { book in BookExportView(model: model, book: book) }
        .sheet(item: $folderEditor) { request in
            LibraryFolderEditor(folder: request.folder) { name in
                if let id = try await model.saveLibraryFolder(id: request.folder?.id, name: name) {
                    selectedFolderID = id
                    page = .library
                    model.closeBook()
                }
            }
        }
        .confirmationDialog(L("删除文件夹？"), isPresented: Binding(get: { deletingFolder != nil }, set: { if !$0 { deletingFolder = nil } }), presenting: deletingFolder) { folder in
            Button(L("删除“\(folder.name)”"), role: .destructive) {
                Task {
                    do {
                        try await model.deleteLibraryFolder(folder.id)
                        if selectedFolderID == folder.id { selectedFolderID = LibraryFilterIndex.unfiledFolderID }
                    } catch { model.notice = localizedError(error) }
                }
            }
        } message: { _ in Text(L("文件夹中的书籍会保留，并移到未分类。")) }
        .sheet(item: $glossaryBook) { book in GlossaryView(model: model, book: book) }
        .sheet(isPresented: $translationSettings) { TranslationSettingsView(model: model, book: nil) }
        .sheet(isPresented: $jinjiangSettings) { JinjiangSettingsView(model: model) }
        .onChange(of: contentIdentity) { _, _ in
            chapterSelection = nil
            if let identifier = requestedDirectoryID, model.detail?.id == identifier {
                detailTab = 1
                requestedDirectoryID = nil
            } else { detailTab = 0 }
        }
        .onChange(of: filters) { _, selection in selection.save() }
        .onChange(of: sortRaw) { _, _ in sortAscending = sortOrder.defaultAscending }
        .onChange(of: visibleBookIDs) { _, ids in selectedBookIDs.formIntersection(ids) }
        .onChange(of: page) { _, _ in finishManagingBooks() }
    }

    private func folderRow(id: String, name: String, count: Int) -> some View {
        HStack {
            Label(name, systemImage: id == LibraryFilterIndex.unfiledFolderID ? "tray" : "folder")
                .lineLimit(1)
            Spacer(minLength: 4)
            Text(count.formatted()).font(.caption).foregroundStyle(.secondary).monospacedDigit()
        }
        .tag("folder:" + id)
        .contentShape(Rectangle())
        .dropDestination(for: String.self) { items, _ in
            let known = Set(model.libraryState.books.map(\.id))
            let books = items.filter { known.contains($0) }
            guard model.connected, !books.isEmpty else { return false }
            model.moveLibraryBooks(books, to: id == LibraryFilterIndex.unfiledFolderID ? nil : id)
            return true
        }
    }

    private var shelfToolbar: some View {
        HStack(spacing: 12) {
            if managingBooks {
                Button(allBooksSelected ? L("取消全选") : L("全选当前结果")) {
                    selectedBookIDs = allBooksSelected ? [] : visibleBookIDs
                    batchMessage = nil
                }.disabled(visibleBooks.isEmpty || movingBooks)
                Text(batchMessage ?? L("已选 \(selectedBookIDs.count) 本"))
                    .font(.caption).foregroundStyle(.secondary).lineLimit(1).monospacedDigit()
                if movingBooks { ProgressView().controlSize(.small).accessibilityLabel(L("正在移动书籍…")) }
                Menu {
                    Button(L("未分类")) { moveSelectedBooks(to: nil) }
                    ForEach(folders) { folder in
                        Button(folder.name) { moveSelectedBooks(to: folder.id) }
                    }
                } label: { Label(L("移到文件夹"), systemImage: "folder") }
                    .disabled(selectedBookIDs.isEmpty || movingBooks || !model.connected)
                Spacer(minLength: 4)
                Button(L("完成")) { finishManagingBooks() }.disabled(movingBooks)
            } else {
                shelfSortMenu
                Spacer()
                Button { managingBooks = true; batchMessage = nil } label: {
                    Label(L("批量管理"), systemImage: "checkmark.circle")
                }.labelStyle(.iconOnly).help(L("批量管理"))
                    .accessibilityLabel(L("批量管理")).disabled(visibleBooks.isEmpty)
            }
            Picker(L("书架显示方式"), selection: $layout) {
                Image(systemName: "list.bullet").tag("list").help(L("列表模式")).accessibilityLabel(L("列表模式"))
                Image(systemName: "square.grid.2x2").tag("covers").help(L("封面模式")).accessibilityLabel(L("封面模式"))
            }.pickerStyle(.segmented).labelsHidden().frame(width: 74)
        }.controlSize(.small).padding(.horizontal, 24).padding(.vertical, 10)
    }

    private var shelfSortMenu: some View {
        Menu {
                Picker(L("排序方式"), selection: $sortRaw) {
                    ForEach(LibrarySortOrder.allCases) { order in Text(LS(order.title)).tag(order.rawValue) }
                }.pickerStyle(.inline)
                if sortOrder != .platform {
                    Divider()
                    Picker(L("排序方向"), selection: $sortAscending) {
                        Text(sortOrder == .updated || sortOrder == .recentReading ? L("从新到旧") : L("降序")).tag(false)
                        Text(sortOrder == .updated || sortOrder == .recentReading ? L("从旧到新") : L("升序")).tag(true)
                    }.pickerStyle(.inline)
                }
            } label: {
                Label(LS(sortOrder.title), systemImage: "arrow.up.arrow.down")
        }.fixedSize().accessibilityLabel(L("书架排序"))
    }

    private func finishManagingBooks() {
        managingBooks = false
        selectedBookIDs = []
        batchMessage = nil
    }

    private func activateBook(_ book: LibraryBook) {
        if managingBooks {
            if selectedBookIDs.contains(book.id) { selectedBookIDs.remove(book.id) }
            else { selectedBookIDs.insert(book.id) }
            batchMessage = nil
        } else { model.openBook(id: book.id) }
    }

    private func moveSelectedBooks(to folderID: String?) {
        let ids = visibleBooks.map(\.id).filter { selectedBookIDs.contains($0) }
        guard !ids.isEmpty, !movingBooks else { return }
        movingBooks = true
        batchMessage = nil
        Task {
            defer { movingBooks = false }
            do {
                try await model.assignLibraryBooks(ids, to: folderID)
                selectedBookIDs.subtract(ids)
                if managingBooks { batchMessage = L("已移动 \(ids.count) 本小说") }
            } catch { model.notice = localizedError(error) }
        }
    }

    @ViewBuilder private func shelfBookInteractions<Content: View>(_ book: LibraryBook, @ViewBuilder content: () -> Content) -> some View {
        if managingBooks { content().disabled(movingBooks) }
        else { content().contextMenu { bookFolderMenu(book) }.draggable(book.id) }
    }

    @ViewBuilder private func bookFolderMenu(_ book: LibraryBook) -> some View {
        Button(L("查看小说详情")) { model.openBook(id: book.id) }
        if model.queued.contains(where: { $0.id == book.id }) || (model.downloading && model.state.book?.id == book.id) {
            Button(L("查看进度")) { page = .downloads }
        } else {
            Button(L("下载整本")) { model.requestDownload(url: book.url) }.disabled(!model.connected || model.sending)
        }
        Divider()
        Menu(L("移到文件夹")) {
            Button { model.moveLibraryBooks([book.id], to: nil) } label: {
                if (book.folder_ids ?? []).isEmpty { Label(L("未分类"), systemImage: "checkmark") }
                else { Text(L("未分类")) }
            }
            ForEach(folders) { folder in
                Button { model.moveLibraryBooks([book.id], to: folder.id) } label: {
                    if book.folder_ids?.contains(folder.id) == true { Label(folder.name, systemImage: "checkmark") }
                    else { Text(folder.name) }
                }
            }
        }.disabled(!model.connected)
    }

    private func sortDetail(_ book: LibraryBook) -> String {
        switch sortOrder {
        case .updated:
            guard let date = LibraryOrganizationCache.date(book.updated_at) else { return "" }
            return L("更新于 \(date.formatted(.dateTime.locale(localization.locale).year().month().day()))")
        case .recentReading:
            guard let timestamp = book.last_read_at else { return "" }
            return L("读于 \(Date(timeIntervalSince1970: timestamp).formatted(.dateTime.locale(localization.locale).month().day().hour().minute()))")
        case .wordCount:
            return book.classification?.word_count.map { L("\($0.formatted()) 字") } ?? ""
        case .favoriteCount:
            return book.classification?.favorite_count.map { L("\($0.formatted()) 收藏") } ?? ""
        default: return ""
        }
    }

    private var contentHeader: some View {
        HStack(spacing: 12) {
            if page == .library, model.detail != nil {
                Button { model.closeBook(); chapterSelection = nil } label: { Image(systemName: "chevron.left") }
                    .help(L("返回书架"))
                Text(L("小说详情")).font(.headline)
            } else {
                Text(page == .library ? folderTitle : LS((page ?? .library).rawValue)).font(.title2.weight(.semibold)).lineLimit(1)
            }
            Spacer(minLength: 16)
            if page == .library, model.detail == nil {
                HStack(spacing: 6) {
                    Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
                    TextField(L("搜索书名或作者"), text: $search).textFieldStyle(.plain)
                        .accessibilityLabel(L("搜索书架"))
                }.padding(.horizontal, 9).padding(.vertical, 6)
                    .background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 6))
                    .frame(maxWidth: 220)
                Button { showingFilters = true } label: {
                    Label(filters.isEmpty ? L("筛选") : L("筛选 · \(filters.count)"), systemImage: "line.3.horizontal.decrease")
                }.fixedSize().accessibilityLabel(L("筛选书架"))
                    .tint(filters.isEmpty ? nil : Color.accentColor)
                Button { model.refreshLibrary() } label: { Image(systemName: "arrow.clockwise") }
                    .help(L("刷新收藏")).accessibilityLabel(L("刷新收藏")).disabled(!model.connected || syncing)
                Button { newURL = ""; adding = true } label: { Label(L("添加链接…"), systemImage: "plus") }
                    .keyboardShortcut("n", modifiers: .command)
            } else if page == .library, let book = model.detail {
                if model.loadingBook { ProgressView().controlSize(.small).accessibilityLabel(L("正在更新详情…")) }
                Button { model.openBook(id: book.id, refresh: true) } label: { Image(systemName: "arrow.clockwise") }
                    .help(L("刷新详情")).accessibilityLabel(L("刷新详情")).disabled(model.loadingBook || !model.connected)
            } else if page == .translation {
                Button(L("AI 翻译设置…")) { translationSettings = true }.disabled(!model.connected)
            } else if page == .downloads {
                Button(L("打开文件夹")) { model.revealExports() }
            }
            Button { showingUpdates = true } label: {
                Image(systemName: model.updates.changed_count > 0 ? "arrow.triangle.2.circlepath.circle.fill" : "arrow.triangle.2.circlepath.circle")
            }.help(L("自动追更")).accessibilityLabel(L("自动追更"))
        }.padding(.horizontal, 24).frame(height: 68)
    }

    private var selectedFilterBar: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                ForEach(LibraryPublicationPeriod.allCases.filter { filters.publicationPeriods.contains($0) }) { period in
                    selectedFilterChip(L("发表：\(LS(period.title))")) { filters.publicationPeriods.remove(period) }
                }
                ForEach(filters.publicationYears.sorted(by: >), id: \.self) { year in
                    selectedFilterChip(L("发表：\(L("\(year) 年"))")) { filters.publicationYears.remove(year) }
                }
                ForEach(LibraryFavoriteRange.allCases.filter { filters.favoriteRanges.contains($0) }) { range in
                    selectedFilterChip(L("收藏：\(LS(range.title))")) { filters.favoriteRanges.remove(range) }
                }
                ForEach(LibraryFacet.allCases) { facet in
                    ForEach((filters.facets[facet] ?? []).sorted(), id: \.self) { value in
                        selectedFilterChip(LS(value)) { filters.toggle(value, in: facet) }
                    }
                }
                ForEach(LibraryWordRange.allCases.filter { filters.wordRanges.contains($0) }) { range in
                    selectedFilterChip(LS(range.title)) { filters.wordRanges.remove(range) }
                }
                ForEach(filters.tags.sorted(), id: \.self) { tag in
                    selectedFilterChip(tag) { filters.tags.remove(tag) }
                }
                Button(L("清除筛选")) { filters = LibraryFilterSelection() }.buttonStyle(.link).font(.caption)
            }.padding(.horizontal, 24)
        }.padding(.top, 12)
    }

    private func selectedFilterChip(_ title: String, remove: @escaping () -> Void) -> some View {
        Button(action: remove) {
            HStack(spacing: 4) { Text(title); Image(systemName: "xmark").font(.caption2.weight(.semibold)) }
                .font(.caption).padding(.horizontal, 8).padding(.vertical, 4)
                .background(Color.accentColor.opacity(0.14), in: Capsule())
        }.buttonStyle(.plain).accessibilityLabel(L("移除筛选 \(title)"))
    }

    private var library: some View {
        VStack(spacing: 0) {
            shelfToolbar
            if !filters.isEmpty { selectedFilterBar }
            if model.libraryState.classification_loading == true && !filters.isEmpty {
                HStack(spacing: 6) {
                    ProgressView().controlSize(.small)
                    Text(filters.publicationPeriods.isEmpty && filters.publicationYears.isEmpty
                         ? L("正在补齐资料 · \(model.libraryState.classification_basic_count ?? model.libraryState.classification_count ?? 0) / \(model.libraryState.books.count)")
                         : L("正在补齐发表时间 · \(model.libraryState.classification_count ?? 0) / \(model.libraryState.books.count)"))
                        .font(.caption).foregroundStyle(.secondary)
                    Spacer()
                }.padding(.horizontal, 24).padding(.top, 10)
            }
            if syncing && !model.libraryState.books.isEmpty {
                HStack(spacing: 8) { ProgressView().controlSize(.small); Text(L("正在刷新收藏…")).font(.caption).foregroundStyle(.secondary); Spacer() }
                    .padding(.horizontal, 24).padding(.top, 12)
            }
            if !model.connected && model.libraryState.books.isEmpty {
                VStack(spacing: 14) {
                    ProgressView().controlSize(.regular)
                    Text(L("正在打开书架")).font(.headline)
                }.frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if syncing && model.libraryState.books.isEmpty {
                VStack(spacing: 14) {
                    ProgressView().controlSize(.regular)
                    Text(L("正在读取你的收藏")).font(.headline)
                }.frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if visibleBooks.isEmpty {
                VStack(spacing: 14) {
                    Image(systemName: filtering ? "magnifyingglass" : "books.vertical").font(.system(size: 34, weight: .light)).foregroundStyle(.tertiary)
                    Text(filtering ? L("未找到匹配的小说") : !selectedFolderID.isEmpty ? L("文件夹里还没有小说") : L("你的故事，都在这里")).font(.title3.weight(.medium))
                    Text(emptyLibraryMessage)
                        .foregroundStyle(.secondary).multilineTextAlignment(.center)
                    if !filters.isEmpty {
                        Button(L("调整筛选")) { showingFilters = true }
                        Button(L("清除筛选")) { filters = LibraryFilterSelection() }.buttonStyle(.link)
                    }
                    if !filtering && selectedFolderID.isEmpty {
                        HStack(spacing: 12) {
                            Button(accountConnected ? L("刷新书架") : L("连接账号")) { if accountConnected { model.refreshLibrary() } else { page = .account } }
                                .buttonStyle(.borderedProminent).disabled(!model.connected || syncing)
                            Button(L("添加链接…")) { newURL = ""; adding = true }
                        }
                    }
                }.padding(32).frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    if layout == "covers" {
                        LazyVGrid(columns: [GridItem(.adaptive(minimum: 138, maximum: 172), spacing: 24, alignment: .top)], alignment: .leading, spacing: 24) {
                            ForEach(visibleBooks) { book in
                                shelfBookInteractions(book) {
                                    LibraryCoverCard(book: book, detail: sortDetail(book), selecting: managingBooks, selected: selectedBookIDs.contains(book.id), open: { activateBook(book) })
                                }
                            }
                        }.padding(24)
                    } else {
                      LazyVStack(spacing: 0) {
                        ForEach(visibleBooks) { book in
                          shelfBookInteractions(book) {
                            LibraryBookRow(book: book, detail: sortDetail(book), selecting: managingBooks, selected: selectedBookIDs.contains(book.id), queued: model.queued.contains { $0.id == book.id },
                                downloading: model.downloading && model.state.book?.id == book.id,
                                canDownload: model.connected && !model.sending, language: localization.identifier,
                                open: { activateBook(book) },
                                download: { model.requestDownload(url: book.url) },
                                showDownloads: { page = .downloads })
                                .equatable()
                          }
                        }
                    }.padding(.horizontal, 24).padding(.top, 5)
                    }
                }
                // Reset only the results scroller; shared covers and editor state survive.
                .id(search + "|" + filters.identity + "|" + selectedFolderID + "|" + sortRaw + "|" + String(sortAscending) + "|" + layout)
                .transaction { $0.animation = nil }
                HStack {
                    Text(filtering ? L("找到 \(visibleBooks.count) 本小说") : L("\(visibleBooks.count) 本小说"))
                    if model.libraryState.complete == false { Text(L("· 收藏尚未全部同步")) }
                    Spacer()
                }.font(.caption).foregroundStyle(.secondary).padding(.horizontal, 24).padding(.vertical, 12)
            }
        }
    }

    private var emptyLibraryMessage: String {
        if !filters.isEmpty { return L("试试其他筛选条件。") }
        if !search.isEmpty { return L("试试其他书名或作者。") }
        if !selectedFolderID.isEmpty { return L("将书籍拖到左侧文件夹，或右键选择“移到文件夹”。") }
        if let message = model.libraryState.message, !message.isEmpty, model.libraryState.account.state == "error" { return LM(message) }
        return accountConnected ? L("刷新晋江收藏，或添加一本小说的链接。") : L("连接晋江账号后，你收藏的小说会显示在这里。")
    }

    private var downloads: some View {
        DownloadsView(model: model, openBook: { book in
            requestedDirectoryID = book.id
            page = .library
            model.openBook(id: book.id)
        }, openVersions: { versionsBook = $0 }, exportControl: { book in exportMenu(book, compact: true) })
    }

    private var translationWorkspace: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                Text(L("把故事带到另一种语言")).font(.title2.weight(.semibold))
                GroupBox {
                    VStack(alignment: .leading, spacing: 12) {
                        TextField(L("译稿标题（可选）"), text: $draftTitle).textFieldStyle(.roundedBorder)
                        TextEditor(text: $draftText).font(.body).frame(minHeight: 160).accessibilityLabel(L("粘贴待翻译的原文"))
                            .overlay(RoundedRectangle(cornerRadius: 5).stroke(Color.primary.opacity(0.1)))
                        HStack {
                            Text(L("\(draftText.count) / 20000 字")).font(.caption).foregroundStyle(.secondary)
                            Spacer()
                            Button(creatingDraft ? L("正在创建…") : L("创建译稿并选择语言…")) {
                                creatingDraft = true
                                Task {
                                    defer { creatingDraft = false }
                                    do {
                                        let book: Book = try await model.request("/api/translation/drafts", data: ["title": draftTitle, "text": draftText])
                                        draftTitle = ""; draftText = ""; translationBook = book; await model.refreshState()
                                    } catch { model.notice = localizedError(error) }
                                }
                            }.buttonStyle(.borderedProminent).disabled(creatingDraft || !model.connected || draftText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || draftText.count > 20000)
                        }

                    }.padding(10)
                }
                Text(L("小说与译稿")).font(.headline)
                if downloadBooks.filter({ progress(for: $0).done > 0 }).isEmpty {
                    Text(L("暂无可翻译的已保存内容。可以先粘贴原文，或从书架下载小说。")) .foregroundStyle(.secondary)
                }
                ForEach(downloadBooks.filter { progress(for: $0).done > 0 }) { book in
                    VStack(alignment: .leading, spacing: 12) {
                        HStack {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(book.title).font(.headline)
                                Text(L("已保存 \(progress(for: book).done) 章原文")).font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Button(L("查看章节")) { page = .library; model.openBook(id: book.id) }
                        }
                        translationControls(book, compact: false)
                    }.padding(16).background(Color.primary.opacity(0.035), in: RoundedRectangle(cornerRadius: 10))
                }
            }.padding(24).frame(maxWidth: 850, alignment: .leading).frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func bookDetail(_ book: Book) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .top, spacing: 20) {
                BookCoverView(source: book.cover, title: book.title)
                VStack(alignment: .leading, spacing: 8) {
                    Text(book.title).font(.title2.weight(.semibold)).lineLimit(2)
                    Text(book.author.isEmpty ? L("作者未提供") : book.author).font(.subheadline).foregroundStyle(.secondary)
                    if let info = book.metadata {
                        Text([info.genre, info.perspective, info.status].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · "))
                            .font(.caption).foregroundStyle(.secondary).lineLimit(2)
                        if let count = info.word_count, !count.isEmpty {
                            Text(model.loadingBook && book.chapters.isEmpty ? L("\(count) 字") : L("\(count) 字 · \(book.chapters.count) 章"))
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    HStack {
                        Button(L("开始阅读")) { if let first = book.chapters.first { model.readChapter(book, first) } }
                            .buttonStyle(.borderedProminent).disabled(!model.canReadChapter(book.chapters.first))
                        if savedAll(book) {
                            Menu(L("下载")) {
                                Button(L("重新下载")) { model.requestDownload(url: book.url, refresh: true) }
                                    .disabled(isQueued(book) || isActive(book) || model.sending)
                                Button(L("下载版本…")) { versionsBook = book }
                            }
                        } else {
                            Button(isQueued(book) || isActive(book) ? L("查看下载") : L("下载整本")) {
                                if isQueued(book) || isActive(book) { page = .downloads } else { model.requestDownload(url: book.url) }
                            }.disabled(!model.connected || model.sending)
                        }
                        exportMenu(book)
                        if model.readingChapter { ProgressView().controlSize(.small).accessibilityLabel(L("正在读取本章…")) }
                    }.padding(.top, 4)
                }
                Spacer(minLength: 0)
            }.padding(24).fixedSize(horizontal: false, vertical: true)
            Picker(L("书籍内容"), selection: $detailTab) {
                Text(L("简介")).tag(0); Text(L("目录")).tag(1)
            }.pickerStyle(.segmented).frame(width: 200).padding(.horizontal, 24).padding(.bottom, 16)
            Divider()
            if detailTab == 0 {
                ScrollView {
                    VStack(alignment: .leading, spacing: 22) {
                        if let info = book.metadata {
                            if let tagline = info.tagline, !tagline.isEmpty { Text(tagline).font(.headline) }
                            if let names = info.protagonists, !names.isEmpty { metadataRow(L("主角"), names) }
                            if let supporting = info.supporting, !supporting.isEmpty { metadataRow(L("配角"), supporting) }
                            if let perspective = info.perspective, !perspective.isEmpty { metadataRow(L("作品视角"), perspective) }
                            if let tags = info.tags, !tags.isEmpty { metadataRow(L("标签"), tags.joined(separator: " · ")) }
                            if !(info.characters ?? []).isEmpty { CharacterCardsView(info: info) }
                            if info.description?.isEmpty == false || !model.loadingBook { VStack(alignment: .leading, spacing: 10) {
                                Text(L("文案")).font(.headline)
                                Text(info.description?.isEmpty == false ? info.description! : L("暂无文案"))
                                    .font(.body).lineSpacing(7).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                            } }
                            if let theme = info.theme, !theme.isEmpty { metadataRow(L("立意"), theme) }
                        } else if !model.loadingBook { Text(L("暂无作品简介")) .foregroundStyle(.secondary) }
                        if progress(for: book).done > 0 {
                            Divider(); proofreadingControls(book, compact: false); translationControls(book, compact: false)
                        }
                    }.padding(24).frame(maxWidth: 850, alignment: .leading).frame(maxWidth: .infinity, alignment: .leading)
                }
            } else { chapterDirectory(book) }
        }.frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }

    private func metadataRow(_ label: String, _ value: String) -> some View {
        HStack(alignment: .top, spacing: 16) {
            Text(label).foregroundStyle(.secondary).frame(width: 70, alignment: .leading)
            Text(value).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
        }.font(.subheadline)
    }

    private func chapterDirectory(_ book: Book) -> some View {
        VStack(spacing: 0) {
            if model.readingChapter { HStack { ProgressView().controlSize(.small); Text(L("正在读取本章…")) }.padding(12) }
            if book.chapters.isEmpty {
                ContentUnavailableView(L("暂未读取到目录"), systemImage: "list.bullet", description: Text(L("请稍后重新打开书籍详情。")))
            } else {
                Table(book.chapters, selection: $chapterSelection) {
                    TableColumn(L("章")) { chapter in Text(chapter.ordinal.map(String.init) ?? String((book.chapters.firstIndex(where: { $0.id == chapter.id }) ?? 0) + 1)).foregroundStyle(.secondary) }.width(40)
                    TableColumn(L("章节与提要")) { chapter in
                        VStack(alignment: .leading, spacing: 4) {
                            HStack(spacing: 6) {
                                Text(chapter.title).font(.body.weight(.medium)).lineLimit(1)
                                if let status = chapter.update_status {
                                    Text(status == "new" ? L("新章") : L("有修改"))
                                        .font(.caption2.weight(.medium)).foregroundStyle(Color.accentColor)
                                        .padding(.horizontal, 5).padding(.vertical, 2)
                                        .background(Color.accentColor.opacity(0.1), in: RoundedRectangle(cornerRadius: 4))
                                }
                            }
                            if let summary = chapter.summary, !summary.isEmpty { Text(summary).font(.caption).foregroundStyle(.secondary).lineLimit(2) }
                        }.padding(.vertical, 5)
                    }
                    TableColumn(L("更新时间")) { chapter in
                        let updated = chapter.source_updated_at ?? ""
                        Text(updated.isEmpty ? "—" : updated).font(.caption).monospacedDigit().foregroundStyle(.secondary)
                    }.width(min: 145, ideal: 155)
                    TableColumn(L("状态")) { chapter in
                        let saved = ["done", "review", "reviewed"].contains(chapter.state)
                        Text(saved ? chapter.label : chapter.readable_locally == true ? L("本机副本") : L("在线读取"))
                            .foregroundStyle(!saved && chapter.readable_locally == true ? .secondary : chapter.color)
                    }.width(min: 100, ideal: 125)
                    TableColumn(L("译文")) { chapter in Text(chapter.translation?.label ?? "—").foregroundStyle(.secondary) }.width(100)
                }.contextMenu(forSelectionType: String.self) { selected in
                    Button(L("阅读本章")) { if let id = selected.first, let chapter = book.chapters.first(where: { $0.id == id }) { model.readChapter(book, chapter) } }
                        .disabled(!model.canReadChapter(book.chapters.first { selected.contains($0.id) }))
                    Button(L("历史版本…")) { if let id = selected.first, let chapter = book.chapters.first(where: { $0.id == id }) { historySelection = HistorySelection(bookID: book.id, chapter: chapter) } }
                        .disabled(selected.isEmpty)
                    Button(L("标记更新已读")) { if let id = selected.first { model.markChapterUpdateRead(bookID: book.id, chapterID: id) } }
                        .disabled(!book.chapters.contains { selected.contains($0.id) && $0.update_status != nil })
                } primaryAction: { selected in
                    if let id = selected.first, let chapter = book.chapters.first(where: { $0.id == id }) { model.readChapter(book, chapter) }
                }
                HStack {
                    if let chapter = book.chapters.first(where: { $0.id == chapterSelection }), chapter.update_status != nil {
                        Button(L("标记更新已读")) { model.markChapterUpdateRead(bookID: book.id, chapterID: chapter.id) }
                    }
                    Spacer()
                    Button(L("历史版本…")) { if let chapter = book.chapters.first(where: { $0.id == chapterSelection }) { historySelection = HistorySelection(bookID: book.id, chapter: chapter) } }
                        .disabled(chapterSelection == nil)
                    Button(L("阅读本章")) { if let chapter = book.chapters.first(where: { $0.id == chapterSelection }) { model.readChapter(book, chapter) } }
                        .disabled(!model.canReadChapter(book.chapters.first { $0.id == chapterSelection }))
                }.padding(16)
            }
        }
    }

    private func exportMenu(_ book: Book, compact: Bool = false) -> some View {
        let value = progress(for: book)
        return Button(!compact && value.done > 0 && value.done < value.total ? L("导出已保存章节…") : L("导出…")) {
            exportBook = book
        }.fixedSize().disabled(value.done == 0 || model.sending)
    }

    @ViewBuilder
    private func translationControls(_ book: Book, compact: Bool) -> some View {
        VStack(alignment: .leading, spacing: 9) {
            HStack(spacing: 12) {
                Label(L("AI 翻译"), systemImage: "character.bubble").font(.subheadline.weight(.medium))
                if let target = book.translation?.target_language ?? model.state.translation?.target_language, book.translation != nil || model.translating(book.id) {
                    Text(TranslationLanguage.label(target)).font(.caption).foregroundStyle(.secondary)
                }
                Spacer(minLength: 8)
                if model.translating(book.id) {
                    Button(L("暂停翻译")) { model.action("translate/pause", payload: ["book_id": book.id]) }.disabled(model.sending)
                } else {
                    if let translated = book.translation, (translated.done ?? 0) < (translated.total ?? 0) || (translated.stale ?? 0) > 0 || (translated.failed ?? 0) > 0 || translated.status == "stale" {
                        Button((translated.stale ?? 0) > 0 || translated.status == "stale" ? L("更新译文") : L("继续翻译")) { model.action("translate", payload: ["book_id": book.id, "scope": "all", "source_language": translated.source_language ?? "auto", "target_language": translated.target_language ?? "zh-Hans"]) }
                            .disabled(!model.connected || model.working)
                    }
                    Button(book.translation == nil ? L("翻译…") : L("翻译设置…")) { translationBook = book }.disabled(!model.connected || model.working)
                }
                Button(L("术语表…")) { glossaryBook = book }.disabled(!model.connected)
            }
            if model.translating(book.id), let translation = model.state.translation {
                ProgressView(value: Double(translation.done ?? 0), total: Double(max(1, translation.total ?? 0)))
                Text(L("已翻译 \(translation.done ?? 0) / \(translation.total ?? 0) 章"))
                    .font(.caption).foregroundStyle(.secondary).monospacedDigit()
                if let message = translation.message, !message.isEmpty { Text(LM(message)).font(.caption).foregroundStyle(.secondary).lineLimit(2) }
            } else if let translation = book.translation {
                HStack {
                    Text(L("已翻译 \(translation.done ?? 0) / \(translation.total ?? 0) 章"))
                    if (translation.failed ?? 0) > 0 { Text(L("· \(translation.failed ?? 0) 章需重试")) }
                    if (translation.stale ?? 0) > 0 || translation.status == "stale" { Text(L("· 译文需更新")) }
                }.font(.caption).foregroundStyle(.secondary).monospacedDigit()
            }
        }
    }

    @ViewBuilder
    private func proofreadingControls(_ book: Book, compact: Bool) -> some View {
        if model.proofreading(book.id), let proof = model.state.proofreading {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Label(L("正在自动校对"), systemImage: "text.badge.checkmark").font(.subheadline.weight(.medium))
                    Spacer()
                    Button(L("暂停校对")) { model.action("proofread/pause", payload: ["book_id": book.id]) }.disabled(model.sending)
                }
                ProgressView(value: Double(proof.done ?? 0), total: Double(max(1, proof.total ?? 0)))
                Text(L("已检查 \(proof.done ?? 0) / \(proof.total ?? 0) 章 · 已整理 \(proof.changed ?? 0) 章 · \(proof.issues ?? 0) 处需注意"))
                    .font(.caption).foregroundStyle(.secondary).monospacedDigit()
            }
        } else if progress(for: book).done > 0 {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 12) {
                    Button { model.proofread(book.id) } label: { Label(L("自动校对"), systemImage: "text.badge.checkmark") }
                        .disabled(!model.connected || model.working || isQueued(book))

                    if book.proofreading?.can_undo == true {
                        Button(L("撤销自动校对")) { model.proofread(book.id, undo: true) }
                            .disabled(!model.connected || model.working)
                            .help(L("恢复自动校对前的正文，保留之后的人工修改。"))
                    }
                    Spacer(minLength: 0)
                }
                if let proof = book.proofreading, proof.can_undo == true {
                    Text(L("已检查 \(proof.done ?? 0) / \(proof.total ?? 0) 章 · 已整理 \(proof.changed ?? 0) 章 · \(proof.issues ?? 0) 处需注意"))
                        .font(.caption).foregroundStyle(.secondary).monospacedDigit()
                }
            }
        }
    }

    private var account: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                HStack(spacing: 16) {
                    Image(systemName: "person.crop.circle.fill")
                        .font(.system(size: 42, weight: .light)).foregroundStyle(.secondary).accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 7) {
                        Text(accountNickname ?? L("晋江文学城")).font(.title2.weight(.semibold)).lineLimit(2)
                        if accountNickname != nil { Text(L("晋江文学城")).font(.subheadline).foregroundStyle(.secondary) }
                        Label(accountStatus, systemImage: accountConnected && model.connected ? "checkmark.circle.fill" : "circle")
                            .font(.subheadline).foregroundStyle(accountConnected && model.connected ? Color.green : Color.secondary)
                    }
                }
                if !accountConnected, let message = model.libraryState.account.message, !message.isEmpty { Text(LM(message)).font(.subheadline).foregroundStyle(.secondary).textSelection(.enabled) }
                if !model.connected {
                    HStack(spacing: 10) { ProgressView().controlSize(.small); Text(L("正在读取账号信息…")).font(.subheadline).foregroundStyle(.secondary) }
                    Button(L("允许访问本机书库…")) { model.authorizeLibraryFolder() }
                        .disabled(model.choosingLibraryFolder)
                } else if accountConnected {
                    VStack(alignment: .leading, spacing: 14) {
                        HStack(spacing: 12) {
                            Button(syncing ? L("正在刷新…") : L("刷新收藏书架")) { model.refreshLibrary() }
                                .buttonStyle(.borderedProminent).disabled(!model.connected || syncing)
                            Text(L("已同步 \(model.libraryState.books.count) 本小说")).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    Divider()
                    DisclosureGroup(L("连接设置")) {
                        VStack(alignment: .leading, spacing: 18) {
                            Button(L("重新连接 Chrome…")) { chromeSetup = true }.disabled(!model.connected)
                            independentLogin
                        }.padding(.top, 12)
                    }
                } else {
                    VStack(alignment: .leading, spacing: 12) {
                        Text(L("使用 Chrome 中的晋江账号")).font(.headline)

                        Button(L("连接 Chrome…")) { chromeSetup = true }.buttonStyle(.borderedProminent).disabled(!model.connected)
                    }
                    if awaitingLogin {
                        HStack {
                            Button(L("已完成登录，读取书架")) { model.finishConnection() }
                                .buttonStyle(.borderedProminent).disabled(!model.connected || model.sending || model.state.busy)
                            if model.state.account_waiting == true {
                                Button(L("取消连接")) { model.cancelConnection() }.disabled(model.sending)
                            }
                        }
                    }
                    DisclosureGroup(L("其他登录方式")) { independentLogin.padding(.top, 12) }
                }
                Divider()
                LanguagePicker().frame(maxWidth: 360, alignment: .leading)
                Button { translationSettings = true } label: { Label(L("AI 翻译设置…"), systemImage: "character.bubble") }.disabled(!model.connected)
                Button { jinjiangSettings = true } label: { Label(L("晋江下载设置…"), systemImage: "arrow.down.doc") }.disabled(!model.connected)
                DisclosureGroup(L("下载偏好")) {
                    VStack(alignment: .leading, spacing: 12) {
                        Picker(L("文字识别"), selection: $model.mode) {
                            Text(L("自动选择")).tag("auto")
                            Text(L("始终使用本地 OCR")).tag("ocr")
                        }.frame(maxWidth: 360, alignment: .leading)
                    }.padding(.top, 12)
                }
            }.padding(32).frame(maxWidth: 620, alignment: .leading).frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var independentLogin: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Button(L("打开登录窗口")) { model.connectAccount() }.disabled(!model.connected || model.sending || model.state.busy)
                Button(L("已完成登录")) { model.finishConnection() }.disabled(!model.connected || model.sending || model.state.busy)
                if model.state.account_waiting == true {
                    Button(L("取消连接")) { model.cancelConnection() }.disabled(model.sending)
                }
            }
            if model.state.busy {
                Text(L("正在下载。请先暂停，并等当前章节保存后再更换登录。"))
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private var activity: some View {
        Button { page = .downloads } label: {
            VStack(alignment: .leading, spacing: 7) {
                if let translation = model.state.translation, translation.running == true {
                    Text(L("正在翻译")).font(.caption.weight(.medium))
                    Text(downloadBooks.first(where: { $0.id == translation.book_id })?.title ?? "").font(.caption).lineLimit(1)
                    ProgressView(value: Double(translation.done ?? 0), total: Double(max(1, translation.total ?? 0)))
                    Text(L("\(translation.done ?? 0) / \(translation.total ?? 0) 章")).font(.caption2).foregroundStyle(.secondary).monospacedDigit()
                } else if let proof = model.state.proofreading, proof.running == true {
                    Text(L("正在自动校对")).font(.caption.weight(.medium))
                    Text(downloadBooks.first(where: { $0.id == proof.book_id })?.title ?? "").font(.caption).lineLimit(1)
                    ProgressView(value: Double(proof.done ?? 0), total: Double(max(1, proof.total ?? 0)))
                    Text(L("\(proof.done ?? 0) / \(proof.total ?? 0) 章")).font(.caption2).foregroundStyle(.secondary).monospacedDigit()
                } else {
                    Text(model.state.book?.title ?? L("正在准备下载")).font(.caption.weight(.medium)).lineLimit(1)
                    ProgressView(value: Double(model.state.progress.done), total: Double(max(1, model.state.progress.total)))
                    Text(L("\(model.state.progress.done) / \(model.state.progress.total) 章")).font(.caption2).foregroundStyle(.secondary).monospacedDigit()
                }
            }.contentShape(Rectangle())
        }.buttonStyle(.plain).help(L("查看下载进度"))
    }

    private func noticeBar(_ notice: String) -> some View {
        HStack(spacing: 10) {
            Text(LM(notice)).font(.caption).foregroundStyle(.secondary).lineLimit(3).textSelection(.enabled)
            Spacer(minLength: 6)
            if let exported = model.exported { Button(L("在 Finder 中显示")) { NSWorkspace.shared.activateFileViewerSelecting([exported]) }.font(.caption) }
            Button { model.notice = nil } label: { Image(systemName: "xmark").font(.caption) }.buttonStyle(.plain).help(L("关闭提示")).accessibilityLabel(L("关闭提示"))
        }.padding(.horizontal, 24).padding(.vertical, 12).background(Color.primary.opacity(0.025))
    }

    private var addSheet: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text(L("添加小说")).font(.title2.weight(.semibold))
            TextField(L("粘贴小说作品主页链接"), text: $newURL).textFieldStyle(.roundedBorder)
                .onSubmit { addNewBook() }.accessibilityLabel(L("小说作品主页链接"))
            HStack(spacing: 6) {
                Text(L("晋江、长佩、番茄及更多网站")).font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button(L("平台列表")) { adding = false; showingPlatforms = true }.buttonStyle(.link).font(.caption)
            }
            if model.state.busy { Text(L("当前小说会继续下载，新小说将加入等待队列。")).font(.caption).foregroundStyle(.secondary) }
            HStack { Spacer(); Button(L("取消")) { adding = false }.keyboardShortcut(.cancelAction); Button(L("添加")) { addNewBook() }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction).disabled(newURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !model.connected) }
        }.padding(26).frame(width: 460)
    }
    private var platformsSheet: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Text(L("支持的平台")).font(.title2.weight(.semibold))
                Spacer()
                Text(L("\(model.platforms.count) 个")).font(.subheadline).foregroundStyle(.secondary)
            }

            TextField(L("搜索平台或网址"), text: $platformSearch).textFieldStyle(.roundedBorder).accessibilityLabel(L("搜索平台"))
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    ForEach(model.platforms.filter { platformSearch.isEmpty || $0.name.localizedCaseInsensitiveContains(platformSearch) || $0.hosts.contains(where: { $0.localizedCaseInsensitiveContains(platformSearch) }) }) { platform in
                        VStack(alignment: .leading, spacing: 5) {
                            HStack {
                                Text(LS(platform.name)).font(.subheadline.weight(.medium))
                                Spacer()
                            }
                            Text(platform.hosts.first ?? "").font(.caption).foregroundStyle(.secondary)
                        }.padding(.vertical, 12)
                        Divider()
                    }
                }
            }.frame(height: 310)
            HStack {
                Spacer()
                Button(L("完成")) { showingPlatforms = false; adding = true }.keyboardShortcut(.defaultAction)
            }
        }.padding(26).frame(width: 540)
    }
    private func addNewBook() {
        let value = newURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty && model.connected else { return }
        model.addBook(url: value); adding = false; page = .library
    }

    private var chromeSheet: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text(L("连接你的 Chrome")).font(.title2.weight(.semibold))

            setupStep("1", title: L("安装本机连接"), detail: "") {
                Button(L("安装连接并打开扩展文件夹")) { model.installChromeBridge() }
            }
            setupStep("2", title: L("将扩展添加到 Chrome"), detail: L("打开扩展管理页，开启“开发者模式”，点击“加载已解压的扩展程序”，选择刚打开的 chrome-extension 文件夹。")) {
                Button(L("打开 Chrome 扩展管理")) {
                    if let chrome = NSWorkspace.shared.urlForApplication(withBundleIdentifier: "com.google.Chrome"), let url = URL(string: "chrome://extensions") {
                        NSWorkspace.shared.open([url], withApplicationAt: chrome, configuration: NSWorkspace.OpenConfiguration())
                    }
                }
            }
            setupStep("3", title: L("连接晋江账号"), detail: L("在晋江登录后，打开“拾页 · 晋江连接”扩展，点击“连接拾页”。")) { EmptyView() }
            HStack {
                Spacer()
                Button(L("稍后设置")) { chromeSetup = false }.keyboardShortcut(.cancelAction)
                Button(L("检查连接")) { chromeSetup = false; model.finishConnection() }
                    .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                    .disabled(!model.connected || model.sending || model.state.busy)
            }
        }.padding(28).frame(width: 540)
    }
    private func setupStep<Content: View>(_ number: String, title: String, detail: String, @ViewBuilder content: () -> Content) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Text(number).font(.system(size: 12, weight: .medium)).foregroundStyle(.secondary).frame(width: 22, height: 22).background(Color.primary.opacity(0.06), in: Circle())
            VStack(alignment: .leading, spacing: 8) { Text(title).font(.headline); if !detail.isEmpty { Text(detail).font(.subheadline).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true) }; content() }
        }
    }
}

private struct LibraryBookRow: View, Equatable {
    let book: LibraryBook
    let detail: String
    let selecting: Bool
    let selected: Bool
    let queued: Bool
    let downloading: Bool
    let canDownload: Bool
    let language: String
    let open: () -> Void
    let download: () -> Void
    let showDownloads: () -> Void

    static func == (lhs: Self, rhs: Self) -> Bool {
        lhs.book.id == rhs.book.id && lhs.book.title == rhs.book.title && lhs.book.author == rhs.book.author
            && lhs.book.url == rhs.book.url && lhs.book.cover == rhs.book.cover
            && lhs.queued == rhs.queued && lhs.downloading == rhs.downloading
            && lhs.canDownload == rhs.canDownload && lhs.language == rhs.language && lhs.detail == rhs.detail
            && lhs.selecting == rhs.selecting && lhs.selected == rhs.selected
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 14) {
                if selecting {
                    Toggle(L("选择\(book.title)"), isOn: Binding(get: { selected }, set: { _ in open() }))
                        .toggleStyle(.checkbox).labelsHidden()
                }
                Button(action: open) { CoverImageView(source: book.cover, title: book.title, reloadMenu: false) }
                    .buttonStyle(.plain).accessibilityLabel(selecting ? L("选择\(book.title)") : L("\(book.title)，查看小说详情"))
                    .accessibilityValue(selecting ? (selected ? L("已选择") : L("未选择")) : "")
                Button(action: open) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(book.title).font(.system(size: 14, weight: .medium)).foregroundStyle(.primary).lineLimit(2)
                        Text(book.author.isEmpty ? L("作者未提供") : book.author).font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
                        if !detail.isEmpty { Text(detail).font(.caption).foregroundStyle(.secondary).lineLimit(1) }
                    }.frame(maxWidth: .infinity, alignment: .leading).contentShape(Rectangle())
                }.buttonStyle(.plain).accessibilityLabel(selecting ? L("选择\(book.title)") : L("\(book.title)，\(book.author.isEmpty ? L("作者未提供") : book.author)，查看小说"))
                    .accessibilityValue(selecting ? (selected ? L("已选择") : L("未选择")) : "")
                if !selecting {
                  if queued {
                    Text(L("已加入队列")).font(.caption).foregroundStyle(.secondary)
                } else if downloading {
                    Button(L("查看进度"), action: showDownloads).accessibilityLabel(L("查看\(book.title)的下载进度"))
                } else {
                    Button(L("下载整本"), action: download).disabled(!canDownload).accessibilityLabel(L("下载\(book.title)"))
                  }
                }
            }.padding(.vertical, 14)
                .background(selecting && selected ? Color.accentColor.opacity(0.08) : .clear, in: RoundedRectangle(cornerRadius: 6))
            Divider()
        }
    }
}

private enum CoverResult {
    case image(NSImage), missing, failed
}

/// Rows request covers only when visible. A shared flight avoids duplicate work when
/// SwiftUI recreates a row; failed requests never enter the permanent image cache.
@MainActor
private final class CoverStore {
    static let shared = CoverStore()
    // Cover misses may wait on the source site; keep them out of the API connection pool.
    private static let imageSession: URLSession = {
        let configuration = URLSessionConfiguration.default
        configuration.httpMaximumConnectionsPerHost = 4
        return URLSession(configuration: configuration)
    }()
    private var images = NSCache<NSURL, NSImage>()
    private var terminal: [URL: (result: CoverResult, date: Date)] = [:]
    private var flights: [URL: Task<CoverResult, Never>] = [:]

    private init() { images.countLimit = 300 }

    func image(for url: URL, retry: Bool) async -> CoverResult {
        let previous = images.object(forKey: url as NSURL)
        if !retry, let previous { return .image(previous) }
        if let task = flights[url] { return await task.value }
        if !retry, let cached = terminal[url], Date().timeIntervalSince(cached.date) < 30 { return cached.result }
        let task = Task { await Self.fetch(url, retry: retry) }
        flights[url] = task
        var result = await task.value
        if case .failed = result, let previous { result = .image(previous) }
        flights[url] = nil
        switch result {
        case .image(let image): images.setObject(image, forKey: url as NSURL); terminal[url] = nil
        default: terminal[url] = (result, Date())
        }
        return result
    }

    private static func fetch(_ source: URL, retry: Bool) async -> CoverResult {
        var url = source
        if retry, source.host == "127.0.0.1", source.path == "/api/cover", var parts = URLComponents(url: source, resolvingAgainstBaseURL: false) {
            parts.queryItems = (parts.queryItems ?? []).filter { $0.name != "retry" } + [URLQueryItem(name: "retry", value: "1")]
            url = parts.url ?? source
        }
        for attempt in 0...2 {
            if Task.isCancelled { return .failed }
            var request = URLRequest(url: url, cachePolicy: retry ? .reloadIgnoringLocalCacheData : .useProtocolCachePolicy, timeoutInterval: 18)
            request.setValue("image/*", forHTTPHeaderField: "Accept")
            do {
                let (data, response) = try await imageSession.data(for: request)
                guard let http = response as? HTTPURLResponse else { return .failed }
                if http.statusCode == 404 { return .missing }
                if (200..<300).contains(http.statusCode), let image = NSImage(data: data) { return .image(image) }
                if http.statusCode == 503, attempt < 2 {
                    let delay = min(30, max(1, Double(http.value(forHTTPHeaderField: "Retry-After") ?? "") ?? Double(attempt + 1)))
                    do { try await Task.sleep(for: .seconds(delay)) } catch { return .failed }
                    continue
                }
                return .failed
            } catch {
                let transient: Set<URLError.Code> = [.timedOut, .networkConnectionLost, .cannotConnectToHost, .cannotFindHost, .notConnectedToInternet, .dnsLookupFailed]
                guard attempt < 2, let error = error as? URLError, transient.contains(error.code) else { return .failed }
                do { try await Task.sleep(for: .seconds(attempt + 1)) } catch { return .failed }
            }
        }
        return .failed
    }
}

struct CoverImageView: View {
    let source: String?
    let title: String
    var width: CGFloat = 46
    var height: CGFloat = 64
    var reloadMenu = true
    @State private var result: CoverResult?
    @State private var loading = true
    @ObservedObject private var localization = Localization.shared

    private var failed: Bool { if case .failed = result { return true }; return false }
    private var help: String {
        if loading { return L("正在读取封面") }
        return failed ? L("封面暂时无法载入，点击重试。正文下载不受影响。") : L("网站尚未提供封面，以书名代替。")
    }

    var body: some View {
        Group {
            if reloadMenu {
                cover.contextMenu {
                    Button(L("重新载入封面")) { Task { await load(retry: true) } }.disabled(loading || source == nil)
                }
            } else { cover }
        }
    }

    private var cover: some View {
        Group {
            if case .image(let image) = result {
                Image(nsImage: image).resizable().scaledToFill().accessibilityHidden(true)
            } else {
                ZStack(alignment: .bottomTrailing) {
                    Rectangle().fill(Color(nsColor: .textBackgroundColor))
                    VStack(spacing: 5) {
                        Text(String(title.prefix(2))).font(.system(size: max(14, width * 0.16), weight: .medium, design: .serif)).lineLimit(2).foregroundStyle(.secondary)
                        Rectangle().fill(Color.primary.opacity(0.12)).frame(width: width / 3, height: 1)
                    }.padding(6).frame(maxWidth: .infinity, maxHeight: .infinity)
                    if loading { ProgressView().controlSize(.mini).padding(3) }
                    else if failed {
                        Button { Task { await load(retry: true) } } label: {
                            Image(systemName: "arrow.clockwise").font(.system(size: 10, weight: .medium)).padding(4)
                        }.buttonStyle(.plain).background(.regularMaterial, in: Circle()).padding(2)
                            .accessibilityLabel(L("重新载入\(title)的封面"))
                    }
                }.help(help)
            }
        }.frame(width: width, height: height)
            .clipped().clipShape(RoundedRectangle(cornerRadius: width > 80 ? 6 : 3))
            .overlay { RoundedRectangle(cornerRadius: width > 80 ? 6 : 3).strokeBorder(Color.primary.opacity(0.09), lineWidth: 0.5) }
            .shadow(color: .black.opacity(0.05), radius: 2, x: 0, y: 1)
            .task(id: source) { await load(retry: false) }
    }

    private func load(retry: Bool) async {
        loading = true
        guard let source, let url = URL(string: source), ["http", "https"].contains(url.scheme?.lowercased() ?? "") else { result = .missing; loading = false; return }
        let fetched = await CoverStore.shared.image(for: url, retry: retry)
        guard !Task.isCancelled else { return }
        result = fetched
        loading = false
    }
}
