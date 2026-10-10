import SwiftUI

struct LibraryFiltersView: View {
    let index: LibraryFilterIndex
    let query: String
    let classificationLoading: Bool
    let classificationCount: Int
    let basicClassificationCount: Int
    let apply: (LibraryFilterSelection) -> Void
    @Environment(\.dismiss) private var dismiss
    @ObservedObject private var localization = Localization.shared
    @State private var selection: LibraryFilterSelection
    @State private var tagSearch = ""
    @State private var expandedTags = false
    @State private var expandedYears = false

    init(index: LibraryFilterIndex, query: String, initial: LibraryFilterSelection, classificationLoading: Bool, classificationCount: Int, basicClassificationCount: Int, apply: @escaping (LibraryFilterSelection) -> Void) {
        self.index = index
        self.query = query
        self.classificationLoading = classificationLoading
        self.classificationCount = classificationCount
        self.basicClassificationCount = basicClassificationCount
        self.apply = apply
        _selection = State(initialValue: initial)
    }

    private var matchingCount: Int { index.matchingBooks(selection, query: query).count }
    private var tags: [(name: String, count: Int)] {
        let query = tagSearch.trimmingCharacters(in: .whitespacesAndNewlines)
        return index.tags.filter { query.isEmpty || $0.name.localizedCaseInsensitiveContains(query) }
    }
    private var publicationYears: [Int] {
        let calendar = BookClassification.publicationCalendar
        let current = calendar.component(.year, from: Date())
        let recent = Set([current, current - 1, current - 2])
        let available = Set(index.publicationYears)
        return recent.union(available).union(selection.publicationYears).filter { $0 > 0 && $0 <= current }.sorted(by: >)
    }
    private var visiblePublicationYears: [Int] {
        if expandedYears { return publicationYears }
        return Set(publicationYears.prefix(3)).union(selection.publicationYears).sorted(by: >)
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text(L("筛选书架")).font(.title2.weight(.semibold))
                Spacer()
                Button(L("清空")) { selection = LibraryFilterSelection(); tagSearch = "" }
                    .buttonStyle(.link).disabled(selection.isEmpty)
            }.padding(24)
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    LazyVGrid(columns: [GridItem(.flexible(), alignment: .top), GridItem(.flexible(), alignment: .top)], alignment: .leading, spacing: 20) {
                        publicationSection
                        favoritesSection
                        ForEach(LibraryFacet.allCases) { facet in facetSection(facet) }
                    }
                    VStack(alignment: .leading, spacing: 10) {
                        Text(L("字数")).font(.headline)
                        LazyVGrid(columns: [GridItem(.adaptive(minimum: 118))], spacing: 7) {
                            FilterChip(title: L("不限"), selected: selection.wordRanges.isEmpty) { selection.wordRanges = [] }
                            ForEach(LibraryWordRange.allCases) { range in
                                FilterChip(title: LS(range.title), selected: selection.wordRanges.contains(range)) {
                                    if selection.wordRanges.contains(range) { selection.wordRanges.remove(range) }
                                    else { selection.wordRanges.insert(range) }
                                }
                            }
                        }
                    }
                    Divider()
                    tagsSection
                }.padding(24)
            }
            Divider()
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(L("找到 \(matchingCount) 本小说")).font(.subheadline.weight(.medium))
                    if classificationLoading {
                        HStack(spacing: 5) {
                            ProgressView().controlSize(.mini)
                            Text(selection.publicationPeriods.isEmpty && selection.publicationYears.isEmpty
                                 ? L("正在补齐资料 · \(basicClassificationCount) / \(index.books.count)")
                                 : L("正在补齐发表时间 · \(classificationCount) / \(index.books.count)"))
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
                Spacer()
                Button(L("取消")) { dismiss() }.keyboardShortcut(.cancelAction)
                Button(L("应用筛选")) { apply(selection); dismiss() }
                    .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
            }.padding(.horizontal, 24).padding(.vertical, 18)
        }.frame(width: 640, height: 620)
    }

    private var publicationSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(L("发表时间")).font(.headline)
            LazyVGrid(columns: [GridItem(.adaptive(minimum: localization.identifier == "en" ? 114 : 80))], spacing: 7) {
                FilterChip(title: L("不限"), selected: selection.publicationPeriods.isEmpty && selection.publicationYears.isEmpty) {
                    selection.publicationPeriods = []; selection.publicationYears = []
                }
                ForEach(LibraryPublicationPeriod.allCases) { period in
                    FilterChip(title: LS(period.title), selected: selection.publicationPeriods.contains(period)) {
                        if selection.publicationPeriods.contains(period) { selection.publicationPeriods.remove(period) }
                        else { selection.publicationPeriods.insert(period) }
                    }
                }
                ForEach(visiblePublicationYears, id: \.self) { year in
                    FilterChip(title: L("\(year) 年"), selected: selection.publicationYears.contains(year)) {
                        if selection.publicationYears.contains(year) { selection.publicationYears.remove(year) }
                        else { selection.publicationYears.insert(year) }
                    }
                }
            }
            if publicationYears.count > 3 {
                Button(expandedYears ? L("收起年份") : L("更多年份")) { expandedYears.toggle() }
                    .buttonStyle(.link).font(.subheadline)
            }
        }.frame(maxWidth: .infinity, alignment: .topLeading)
    }

    private var favoritesSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(L("收藏数")).font(.headline)
            LazyVGrid(columns: [GridItem(.adaptive(minimum: localization.identifier == "en" ? 114 : 80))], spacing: 7) {
                FilterChip(title: L("不限"), selected: selection.favoriteRanges.isEmpty) { selection.favoriteRanges = [] }
                ForEach(LibraryFavoriteRange.allCases) { range in
                    FilterChip(title: LS(range.title), selected: selection.favoriteRanges.contains(range)) {
                        if selection.favoriteRanges.contains(range) { selection.favoriteRanges.remove(range) }
                        else { selection.favoriteRanges.insert(range) }
                    }
                }
            }
        }.frame(maxWidth: .infinity, alignment: .topLeading)
    }

    private func facetSection(_ facet: LibraryFacet) -> some View {
        let options = index.facetOptions[facet] ?? facet.preferredOrder
        return VStack(alignment: .leading, spacing: 10) {
            Text(LS(facet.title)).font(.headline)
            LazyVGrid(columns: [GridItem(.adaptive(minimum: localization.identifier == "en" ? 114 : 80))], spacing: 7) {
                FilterChip(title: L("不限"), selected: (selection.facets[facet] ?? []).isEmpty) { selection.facets[facet] = [] }
                ForEach(options, id: \.self) { option in
                    FilterChip(title: LS(option), selected: selection.facets[facet]?.contains(option) == true) { selection.toggle(option, in: facet) }
                }
            }
        }.frame(maxWidth: .infinity, alignment: .topLeading)
    }

    private var tagsSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(L("包含标签")).font(.headline)
                Spacer()
                Picker(L("标签匹配方式"), selection: $selection.matchAllTags) {
                    Text(L("全部匹配")).tag(true)
                    Text(L("任一匹配")).tag(false)
                }.pickerStyle(.segmented).labelsHidden().frame(width: 188).accessibilityLabel(L("标签匹配方式"))
            }
            HStack(spacing: 7) {
                Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
                TextField(L("搜索标签"), text: $tagSearch).textFieldStyle(.plain)
                if !tagSearch.isEmpty {
                    Button { tagSearch = "" } label: { Image(systemName: "xmark.circle.fill").foregroundStyle(.secondary) }
                        .buttonStyle(.plain).accessibilityLabel(L("清除标签搜索"))
                }
            }.padding(9).background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 7))
            if !selection.tags.isEmpty {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 100))], spacing: 7) {
                    ForEach(selection.tags.sorted(), id: \.self) { tag in
                        FilterChip(title: tag, selected: true, removable: true) { selection.tags.remove(tag) }
                    }
                }
            }
            if tags.isEmpty {
                Text(tagSearch.isEmpty ? L("暂无标签") : L("未找到标签")).font(.subheadline).foregroundStyle(.secondary).padding(.vertical, 8)
            } else if expandedTags || !tagSearch.isEmpty {
                ScrollView {
                    tagGrid(tags)
                }.frame(height: 190)
            } else {
                tagGrid(Array(tags.prefix(12)))
            }
            if tagSearch.isEmpty && tags.count > 12 {
                Button(expandedTags ? L("收起标签") : L("查看全部标签 · \(tags.count)")) { expandedTags.toggle() }
                    .buttonStyle(.link).font(.subheadline)
            }
        }
    }

    private func tagGrid(_ values: [(name: String, count: Int)]) -> some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 100))], spacing: 7) {
            ForEach(values, id: \.name) { tag in
                FilterChip(title: tag.name, selected: selection.tags.contains(tag.name), count: tag.count) {
                    if selection.tags.contains(tag.name) { selection.tags.remove(tag.name) }
                    else { selection.tags.insert(tag.name) }
                }
            }
        }
    }
}

private struct FilterChip: View {
    let title: String
    let selected: Bool
    var count: Int? = nil
    var removable = false
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            HStack(spacing: 4) {
                Text(title).lineLimit(1)
                if let count { Text(String(count)).font(.caption).foregroundStyle(.secondary) }
                if removable { Image(systemName: "xmark").font(.caption2) }
            }.font(.system(size: 12, weight: selected ? .semibold : .regular))
                .frame(maxWidth: .infinity).padding(.horizontal, 6).frame(height: 30)
                .foregroundStyle(selected ? Color.accentColor : Color.primary)
                .background(selected ? Color.accentColor.opacity(0.14) : Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 6))
                .overlay(RoundedRectangle(cornerRadius: 6).stroke(selected ? Color.accentColor.opacity(0.35) : Color.primary.opacity(0.08)))
                .contentShape(Rectangle())
        }.buttonStyle(.plain)
            .accessibilityLabel(title)
            .accessibilityValue(selected ? L("已选择") : L("未选择"))
    }
}
