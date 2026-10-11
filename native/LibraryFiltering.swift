import Foundation

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
    var folders: [LibraryFolder]?
    var folder_id: String?
    static let empty = LibraryState(books: [], account: AccountInfo(state: "unknown"))

    static func cached(in directory: URL) throws -> LibraryState {
        let decoder = JSONDecoder()
        var snapshot = try decoder.decode(Self.self, from: Data(contentsOf: directory.appendingPathComponent("library.json")))
        let tags = (try? decoder.decode([String: [String]].self, from: Data(contentsOf: directory.appendingPathComponent("library-tags.json")))) ?? [:]
        let classifications = (try? decoder.decode([String: CachedClassification].self, from: Data(contentsOf: directory.appendingPathComponent("library-classifications.json")))) ?? [:]
        snapshot.books = snapshot.books.map { source in
            var book = source
            book.tags = tags[book.id] ?? book.tags
            book.classification = classifications[book.id]?.value ?? book.classification
            return book
        }
        if let organization = try? decoder.decode(LibraryOrganizationCache.self, from: Data(contentsOf: directory.appendingPathComponent("library-organization.json"))) {
            organization.apply(to: &snapshot)
        } else if let folders = snapshot.folders {
            LibraryOrganizationCache(folders: folders).apply(to: &snapshot)
        }
        snapshot.syncing = false
        snapshot.classification_loading = false
        snapshot.classification_count = snapshot.books.filter { classifications[$0.id]?.complete == true }.count
        snapshot.classification_basic_count = snapshot.books.filter { classifications[$0.id]?.basicComplete == true }.count
        return snapshot
    }

    private struct CachedClassification: Decodable {
        let value: BookClassification
        let complete: Bool
        let basicComplete: Bool
        private enum CodingKeys: String, CodingKey { case checkedVersion = "_checked_version", basicChecked = "_basic_checked" }

        init(from decoder: Decoder) throws {
            value = try BookClassification(from: decoder)
            let markers = try decoder.container(keyedBy: CodingKeys.self)
            complete = try markers.decodeIfPresent(Int.self, forKey: .checkedVersion) == 2
            basicComplete = try markers.decodeIfPresent(Bool.self, forKey: .basicChecked) == true || complete
        }
    }
}

struct LibraryBook: Decodable, Identifiable, Hashable {
    var id: String
    var title: String
    var author: String
    var url: String
    var cover: String?
    var tags: [String]?
    var classification: BookClassification?
    var folder_ids: [String]?
    var source_folder_ids: [String]?
    var last_read_at: Double?
    var updated_at: String?
}

struct BookClassification: Decodable, Hashable {
    var orientation: String?
    var perspective: String?
    var originality: String?
    var era: String?
    var genre: String?
    var status: String?
    var word_count: Int?
    var published_at: String?
    var favorite_count: Int?

    static let publicationCalendar: Calendar = {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "Asia/Shanghai")!
        return calendar
    }()
    var publishedDate: Date? {
        guard let raw = published_at else { return nil }
        let parts = raw.split(separator: "-")
        guard parts.count == 3, let year = Int(parts[0]), let month = Int(parts[1]), let day = Int(parts[2]), year > 0 else { return nil }
        let calendar = Self.publicationCalendar
        let components = DateComponents(year: year, month: month, day: day)
        guard let date = calendar.date(from: components), calendar.dateComponents([.year, .month, .day], from: date) == components else { return nil }
        return date
    }

    func value(for facet: LibraryFacet) -> String? {
        switch facet {
        case .orientation: return orientation
        case .perspective: return perspective
        case .originality: return originality
        case .era: return era
        case .genre: return genre
        case .status: return status
        }
    }
}

enum LibraryFacet: String, CaseIterable, Identifiable {
    case orientation, perspective, originality, era, genre, status
    var id: String { rawValue }
    var title: String {
        switch self {
        case .orientation: return "作品种类"
        case .perspective: return "作品视角"
        case .originality: return "原创性"
        case .era: return "时代"
        case .genre: return "题材"
        case .status: return "作品进度"
        }
    }
    var preferredOrder: [String] {
        switch self {
        case .orientation: return ["言情", "纯爱", "百合", "无CP", "多元"]
        case .perspective: return ["女主", "男主", "主受", "主攻", "互攻", "不明"]
        case .originality: return ["原创", "衍生"]
        case .era: return ["近代现代", "古色古香", "架空历史", "幻想未来"]
        case .genre: return ["爱情", "剧情", "奇幻", "仙侠", "武侠", "科幻", "游戏", "悬疑", "童话", "传奇", "轻小说"]
        case .status: return ["连载", "完结", "暂停"]
        }
    }
}

enum LibraryWordRange: String, CaseIterable, Identifiable {
    case under100k, from100kTo300k, from300kTo500k, from500kTo1m, over1m
    var id: String { rawValue }
    var title: String {
        switch self {
        case .under100k: return "10 万字以下"
        case .from100kTo300k: return "10–30 万字"
        case .from300kTo500k: return "30–50 万字"
        case .from500kTo1m: return "50–100 万字"
        case .over1m: return "100 万字以上"
        }
    }
    func contains(_ count: Int) -> Bool {
        switch self {
        case .under100k: return count < 100_000
        case .from100kTo300k: return (100_000..<300_000).contains(count)
        case .from300kTo500k: return (300_000..<500_000).contains(count)
        case .from500kTo1m: return (500_000..<1_000_000).contains(count)
        case .over1m: return count >= 1_000_000
        }
    }
}

enum LibraryPublicationPeriod: String, CaseIterable, Identifiable {
    case last2Months, last3Months, last6Months, lastYear, last2Years
    var id: String { rawValue }
    var title: String {
        switch self {
        case .last2Months: return "两个月内"
        case .last3Months: return "一季度内"
        case .last6Months: return "半年内"
        case .lastYear: return "一年内"
        case .last2Years: return "两年内"
        }
    }
    private var months: Int {
        switch self {
        case .last2Months: return 2
        case .last3Months: return 3
        case .last6Months: return 6
        case .lastYear: return 12
        case .last2Years: return 24
        }
    }
    func startDate(today: Date, calendar: Calendar) -> Date? {
        calendar.date(byAdding: .month, value: -months, to: today)
    }
    func contains(_ date: Date, now: Date, calendar: Calendar) -> Bool {
        let today = calendar.startOfDay(for: now)
        guard let start = startDate(today: today, calendar: calendar) else { return false }
        return date >= start && date <= today
    }
}

enum LibraryFavoriteRange: String, CaseIterable, Identifiable {
    case under1k, from1kTo5k, from5kTo10k, from10kTo50k, from50kTo100k, over100k
    var id: String { rawValue }
    var title: String {
        switch self {
        case .under1k: return "1 千以下"
        case .from1kTo5k: return "1 千–5 千"
        case .from5kTo10k: return "5 千–1 万"
        case .from10kTo50k: return "1 万–5 万"
        case .from50kTo100k: return "5 万–10 万"
        case .over100k: return "10 万以上"
        }
    }
    func contains(_ count: Int) -> Bool {
        switch self {
        case .under1k: return (0..<1_000).contains(count)
        case .from1kTo5k: return (1_000..<5_000).contains(count)
        case .from5kTo10k: return (5_000..<10_000).contains(count)
        case .from10kTo50k: return (10_000..<50_000).contains(count)
        case .from50kTo100k: return (50_000..<100_000).contains(count)
        case .over100k: return count >= 100_000
        }
    }
}

struct LibraryFilterSelection: Equatable, Codable {
    var facets: [LibraryFacet: Set<String>] = [:]
    var wordRanges: Set<LibraryWordRange> = []
    var publicationPeriods: Set<LibraryPublicationPeriod> = []
    var publicationYears: Set<Int> = []
    var favoriteRanges: Set<LibraryFavoriteRange> = []
    var tags: Set<String> = []
    var matchAllTags = true

    var count: Int { facets.values.reduce(0) { $0 + $1.count } + wordRanges.count + publicationPeriods.count + publicationYears.count + favoriteRanges.count + tags.count }
    var isEmpty: Bool { count == 0 }
    var identity: String {
        let facetKeys: [String] = LibraryFacet.allCases.map { facet in
            let values = (facets[facet] ?? []).sorted().joined(separator: ",")
            return facet.rawValue + ":" + values
        }
        let parts: [String] = [
            facetKeys.joined(separator: "|"),
            wordRanges.map(\.rawValue).sorted().joined(separator: ","),
            publicationPeriods.map(\.rawValue).sorted().joined(separator: ","),
            publicationYears.sorted().map(String.init).joined(separator: ","),
            favoriteRanges.map(\.rawValue).sorted().joined(separator: ","),
            tags.sorted().joined(separator: ","), String(matchAllTags)
        ]
        return parts.joined(separator: "|")
    }

    mutating func toggle(_ value: String, in facet: LibraryFacet) {
        if facets[facet, default: []].contains(value) { facets[facet]?.remove(value) }
        else { facets[facet, default: []].insert(value) }
    }

    func matches(classification: BookClassification?, tags bookTags: [String], now: Date = Date()) -> Bool {
        for facet in LibraryFacet.allCases {
            let selected = facets[facet] ?? []
            if !selected.isEmpty {
                guard let value = classification?.value(for: facet), selected.contains(value) else { return false }
            }
        }
        if !wordRanges.isEmpty {
            guard let count = classification?.word_count, count >= 0, wordRanges.contains(where: { $0.contains(count) }) else { return false }
        }
        if !publicationPeriods.isEmpty || !publicationYears.isEmpty {
            let calendar = BookClassification.publicationCalendar
            guard let date = classification?.publishedDate, date <= calendar.startOfDay(for: now) else { return false }
            let matchesYear = publicationYears.contains(calendar.component(.year, from: date))
            if !matchesYear && !publicationPeriods.contains(where: { $0.contains(date, now: now, calendar: calendar) }) { return false }
        }
        if !favoriteRanges.isEmpty {
            guard let count = classification?.favorite_count, favoriteRanges.contains(where: { $0.contains(count) }) else { return false }
        }
        if !tags.isEmpty {
            let available = Set(bookTags)
            return matchAllTags ? tags.isSubset(of: available) : !tags.isDisjoint(with: available)
        }
        return true
    }
}

extension LibraryFilterSelection {
    private static let defaultsKey = "library.appliedFilters"
    private enum CodingKeys: String, CodingKey {
        case facets, wordRanges, publicationPeriods, publicationYears, favoriteRanges, tags, matchAllTags
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        let storedFacets = try values.decodeIfPresent([String: Set<String>].self, forKey: .facets) ?? [:]
        facets = Dictionary(uniqueKeysWithValues: storedFacets.compactMap { key, choices in
            LibraryFacet(rawValue: key).map { ($0, choices) }
        })
        wordRanges = Set((try values.decodeIfPresent([String].self, forKey: .wordRanges) ?? []).compactMap(LibraryWordRange.init(rawValue:)))
        publicationPeriods = Set((try values.decodeIfPresent([String].self, forKey: .publicationPeriods) ?? []).compactMap(LibraryPublicationPeriod.init(rawValue:)))
        publicationYears = try values.decodeIfPresent(Set<Int>.self, forKey: .publicationYears) ?? []
        favoriteRanges = Set((try values.decodeIfPresent([String].self, forKey: .favoriteRanges) ?? []).compactMap(LibraryFavoriteRange.init(rawValue:)))
        tags = try values.decodeIfPresent(Set<String>.self, forKey: .tags) ?? []
        matchAllTags = try values.decodeIfPresent(Bool.self, forKey: .matchAllTags) ?? true
    }

    func encode(to encoder: Encoder) throws {
        var values = encoder.container(keyedBy: CodingKeys.self)
        try values.encode(Dictionary(uniqueKeysWithValues: facets.map { ($0.key.rawValue, $0.value) }), forKey: .facets)
        try values.encode(wordRanges.map(\.rawValue), forKey: .wordRanges)
        try values.encode(publicationPeriods.map(\.rawValue), forKey: .publicationPeriods)
        try values.encode(publicationYears, forKey: .publicationYears)
        try values.encode(favoriteRanges.map(\.rawValue), forKey: .favoriteRanges)
        try values.encode(tags, forKey: .tags)
        try values.encode(matchAllTags, forKey: .matchAllTags)
    }

    static func load(from defaults: UserDefaults = .standard) -> Self {
        guard let data = defaults.data(forKey: defaultsKey),
              let selection = try? JSONDecoder().decode(Self.self, from: data) else { return Self() }
        return selection
    }

    func save(to defaults: UserDefaults = .standard) {
        guard let data = try? JSONEncoder().encode(self) else { return }
        defaults.set(data, forKey: Self.defaultsKey)
    }
}

final class LibraryFilterIndex {
    static let unfiledFolderID = "__unfiled"

    private struct SortKey: Hashable {
        let order: LibrarySortOrder
        let ascending: Bool
    }

    private struct Row {
        let book: LibraryBook
        let ordinal: Int
        let tags: Set<String>
        let folderIDs: Set<String>
        let publishedDate: Date?
        let publishedYear: Int?
        let updatedAt: Double?
    }

    let books: [LibraryBook]
    let tags: [(name: String, count: Int)]
    let facetOptions: [LibraryFacet: [String]]
    let publicationYears: [Int]
    let folderCounts: [String: Int]
    let unfiledCount: Int
    private let rows: [Row]
    private var orderedRows: [SortKey: [Row]] = [:]
    private var lastSelection: LibraryFilterSelection?
    private var lastQuery = ""
    private var lastDay: Date?
    private var lastSort: SortKey?
    private var lastFolderID: String?
    private var lastMatches: [LibraryBook] = []

    init(books: [LibraryBook]) {
        self.books = books
        let calendar = BookClassification.publicationCalendar
        var counts: [String: Int] = [:]
        var facets: [LibraryFacet: Set<String>] = [:]
        var years: Set<Int> = []
        var folders: [String: Int] = [:]
        var unfiled = 0
        var prepared: [Row] = []
        prepared.reserveCapacity(books.count)
        for (ordinal, book) in books.enumerated() {
            let tags = Set(book.tags ?? [])
            let folderIDs = Set(book.folder_ids ?? [])
            for folderID in folderIDs { folders[folderID, default: 0] += 1 }
            if folderIDs.isEmpty { unfiled += 1 }
            for tag in tags { counts[tag, default: 0] += 1 }
            for facet in LibraryFacet.allCases {
                if let value = book.classification?.value(for: facet), !value.isEmpty { facets[facet, default: []].insert(value) }
            }
            let date = book.classification?.publishedDate
            let year = date.map { calendar.component(.year, from: $0) }
            if let year { years.insert(year) }
            prepared.append(Row(book: book, ordinal: ordinal, tags: tags, folderIDs: folderIDs, publishedDate: date, publishedYear: year, updatedAt: LibraryOrganizationCache.date(book.updated_at)?.timeIntervalSince1970))
        }
        rows = prepared
        folderCounts = folders
        unfiledCount = unfiled
        let tagCounts: [(name: String, count: Int)] = counts.map { (name: $0.key, count: $0.value) }
        tags = tagCounts.sorted { $0.count != $1.count ? $0.count > $1.count : $0.name.localizedStandardCompare($1.name) == .orderedAscending }
        facetOptions = Dictionary(uniqueKeysWithValues: LibraryFacet.allCases.map { facet in
            (facet, facet.preferredOrder + (facets[facet] ?? []).subtracting(facet.preferredOrder).sorted())
        })
        publicationYears = years.sorted(by: >)
    }

    func matchingBooks(_ selection: LibraryFilterSelection, query: String = "", now: Date = Date(), sort: LibrarySortOrder = .platform, ascending: Bool = true, folderID: String? = nil) -> [LibraryBook] {
        let calendar = BookClassification.publicationCalendar
        let today = calendar.startOfDay(for: now)
        let sortKey = SortKey(order: sort, ascending: ascending)
        if selection == lastSelection, query == lastQuery, today == lastDay, sortKey == lastSort, folderID == lastFolderID { return lastMatches }
        let activeFacets = LibraryFacet.allCases.compactMap { facet -> (LibraryFacet, Set<String>)? in
            guard let values = selection.facets[facet], !values.isEmpty else { return nil }
            return (facet, values)
        }
        let publicationStart = selection.publicationPeriods.compactMap { $0.startDate(today: today, calendar: calendar) }.min()
        let filteringDate = publicationStart != nil || !selection.publicationYears.isEmpty
        let matches = sortedRows(for: sortKey).compactMap { row -> LibraryBook? in
            let book = row.book
            if let folderID {
                if folderID == Self.unfiledFolderID {
                    if !row.folderIDs.isEmpty { return nil }
                } else if !row.folderIDs.contains(folderID) { return nil }
            }
            if !query.isEmpty, !book.title.localizedCaseInsensitiveContains(query), !book.author.localizedCaseInsensitiveContains(query) { return nil }
            for (facet, values) in activeFacets {
                guard let value = book.classification?.value(for: facet), values.contains(value) else { return nil }
            }
            if !selection.wordRanges.isEmpty {
                guard let count = book.classification?.word_count, count >= 0, selection.wordRanges.contains(where: { $0.contains(count) }) else { return nil }
            }
            if filteringDate {
                guard let date = row.publishedDate, date <= today else { return nil }
                let yearMatches = row.publishedYear.map { selection.publicationYears.contains($0) } ?? false
                if !yearMatches && !(publicationStart.map { date >= $0 } ?? false) { return nil }
            }
            if !selection.favoriteRanges.isEmpty {
                guard let count = book.classification?.favorite_count, selection.favoriteRanges.contains(where: { $0.contains(count) }) else { return nil }
            }
            if !selection.tags.isEmpty {
                let tagsMatch = selection.matchAllTags ? selection.tags.isSubset(of: row.tags) : !selection.tags.isDisjoint(with: row.tags)
                if !tagsMatch { return nil }
            }
            return book
        }
        lastSelection = selection
        lastQuery = query
        lastDay = today
        lastSort = sortKey
        lastFolderID = folderID
        lastMatches = matches
        return matches
    }

    private func sortedRows(for key: SortKey) -> [Row] {
        if let cached = orderedRows[key] { return cached }
        let result: [Row]
        if key.order == .platform {
            result = key.ascending ? rows : rows.reversed()
        } else {
            result = rows.sorted { left, right in
                let comparison: ComparisonResult?
                switch key.order {
                case .title:
                    let leftTitle = left.book.title.trimmingCharacters(in: .whitespacesAndNewlines)
                    let rightTitle = right.book.title.trimmingCharacters(in: .whitespacesAndNewlines)
                    if leftTitle.isEmpty != rightTitle.isEmpty { return !leftTitle.isEmpty }
                    comparison = leftTitle.localizedStandardCompare(rightTitle)
                case .updated, .recentReading, .wordCount, .favoriteCount:
                    let leftValue = sortValue(left, order: key.order)
                    let rightValue = sortValue(right, order: key.order)
                    if (leftValue == nil) != (rightValue == nil) { return leftValue != nil }
                    if let leftValue, let rightValue, leftValue != rightValue {
                        comparison = leftValue < rightValue ? .orderedAscending : .orderedDescending
                    } else { comparison = nil }
                case .platform: comparison = nil
                }
                if let comparison, comparison != .orderedSame {
                    return key.ascending ? comparison == .orderedAscending : comparison == .orderedDescending
                }
                if left.ordinal != right.ordinal { return left.ordinal < right.ordinal }
                return left.book.id < right.book.id
            }
        }
        orderedRows[key] = result
        return result
    }

    private func sortValue(_ row: Row, order: LibrarySortOrder) -> Double? {
        switch order {
        case .updated: return row.updatedAt
        case .recentReading: return row.book.last_read_at.flatMap { $0 > 0 ? $0 : nil }
        case .wordCount: return row.book.classification?.word_count.flatMap { $0 >= 0 ? Double($0) : nil }
        case .favoriteCount: return row.book.classification?.favorite_count.flatMap { $0 >= 0 ? Double($0) : nil }
        case .title, .platform: return nil
        }
    }
}
