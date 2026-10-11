import Foundation

struct LibraryBook: Decodable, Identifiable, Hashable {
    var id: String
    var title: String
    var author: String
    var url: String
    var cover: String?
    var tags: [String]?
    var classification: BookClassification?
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

struct LibraryFilterSelection: Equatable {
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

final class LibraryFilterIndex {
    private struct Row {
        let book: LibraryBook
        let tags: Set<String>
        let publishedDate: Date?
        let publishedYear: Int?
    }

    let books: [LibraryBook]
    let tags: [(name: String, count: Int)]
    let facetOptions: [LibraryFacet: [String]]
    let publicationYears: [Int]
    private let rows: [Row]
    private var lastSelection: LibraryFilterSelection?
    private var lastQuery = ""
    private var lastDay: Date?
    private var lastMatches: [LibraryBook] = []

    init(books: [LibraryBook]) {
        self.books = books
        let calendar = BookClassification.publicationCalendar
        var counts: [String: Int] = [:]
        var facets: [LibraryFacet: Set<String>] = [:]
        var years: Set<Int> = []
        var prepared: [Row] = []
        prepared.reserveCapacity(books.count)
        for book in books {
            let tags = Set(book.tags ?? [])
            for tag in tags { counts[tag, default: 0] += 1 }
            for facet in LibraryFacet.allCases {
                if let value = book.classification?.value(for: facet), !value.isEmpty { facets[facet, default: []].insert(value) }
            }
            let date = book.classification?.publishedDate
            let year = date.map { calendar.component(.year, from: $0) }
            if let year { years.insert(year) }
            prepared.append(Row(book: book, tags: tags, publishedDate: date, publishedYear: year))
        }
        rows = prepared
        let tagCounts: [(name: String, count: Int)] = counts.map { (name: $0.key, count: $0.value) }
        tags = tagCounts.sorted { $0.count != $1.count ? $0.count > $1.count : $0.name.localizedStandardCompare($1.name) == .orderedAscending }
        facetOptions = Dictionary(uniqueKeysWithValues: LibraryFacet.allCases.map { facet in
            (facet, facet.preferredOrder + (facets[facet] ?? []).subtracting(facet.preferredOrder).sorted())
        })
        publicationYears = years.sorted(by: >)
    }

    func matchingBooks(_ selection: LibraryFilterSelection, query: String = "", now: Date = Date()) -> [LibraryBook] {
        let calendar = BookClassification.publicationCalendar
        let today = calendar.startOfDay(for: now)
        if selection == lastSelection, query == lastQuery, today == lastDay { return lastMatches }
        let activeFacets = LibraryFacet.allCases.compactMap { facet -> (LibraryFacet, Set<String>)? in
            guard let values = selection.facets[facet], !values.isEmpty else { return nil }
            return (facet, values)
        }
        let publicationStart = selection.publicationPeriods.compactMap { $0.startDate(today: today, calendar: calendar) }.min()
        let filteringDate = publicationStart != nil || !selection.publicationYears.isEmpty
        let matches = rows.compactMap { row -> LibraryBook? in
            let book = row.book
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
        lastMatches = matches
        return matches
    }
}
