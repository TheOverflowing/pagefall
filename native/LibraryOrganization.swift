import Foundation

struct LibraryFolder: Decodable, Identifiable, Hashable {
    var id: String
    var name: String
    var source: String
    var source_id: String?
}

extension LibraryFolder {
    private enum CodingKeys: String, CodingKey { case id, name, source, source_id }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        let savedID = try values.decode(String.self, forKey: .id)
        name = try values.decode(String.self, forKey: .name)
        if let savedSource = try values.decodeIfPresent(String.self, forKey: .source) {
            id = savedID
            source = savedSource
            source_id = try values.decodeIfPresent(String.self, forKey: .source_id)
        } else {
            // The shelf snapshot stores source IDs; the organization file stores local IDs.
            id = "jjwxc_" + savedID
            source = "jjwxc"
            source_id = savedID
        }
    }
}

enum LibrarySortOrder: String, CaseIterable, Identifiable {
    case platform, updated, recentReading, wordCount, favoriteCount, title
    var id: String { rawValue }
    var title: String {
        switch self {
        case .platform: return "默认排序"
        case .updated: return "更新时间"
        case .recentReading: return "最近阅读"
        case .wordCount: return "字数"
        case .favoriteCount: return "收藏数"
        case .title: return "书名"
        }
    }
    var defaultAscending: Bool { self == .platform || self == .title }
}

struct LibraryOrganizationCache: Decodable {
    var folders: [String: LibraryFolder] = [:]
    var assignments: [String: String?] = [:]
    var last_read: [String: Double] = [:]
    var updated_at: [String: String] = [:]

    private enum CodingKeys: String, CodingKey { case folders, assignments, last_read, updated_at }

    init(folders: [LibraryFolder]) {
        self.folders = Dictionary(uniqueKeysWithValues: folders.filter { !Self.isSourceCollection($0.id) }.map { ($0.id, $0) })
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        folders = (try values.decodeIfPresent([String: LibraryFolder].self, forKey: .folders) ?? [:]).filter { !Self.isSourceCollection($0.key) }
        assignments = try values.decodeIfPresent([String: String?].self, forKey: .assignments) ?? [:]
        last_read = try values.decodeIfPresent([String: Double].self, forKey: .last_read) ?? [:]
        updated_at = try values.decodeIfPresent([String: String].self, forKey: .updated_at) ?? [:]
    }

    private static func isSourceCollection(_ id: String) -> Bool { id == "jjwxc_0" || id == "jjwxc_-1" }

    func apply(to snapshot: inout LibraryState) {
        snapshot.folders = folders.values.sorted {
            let comparison = $0.name.localizedStandardCompare($1.name)
            return comparison == .orderedSame ? $0.id < $1.id : comparison == .orderedAscending
        }
        snapshot.books = snapshot.books.map { source in
            var book = source
            if let assigned = assignments[book.id] {
                book.folder_ids = assigned.flatMap { folders[$0] == nil ? nil : [$0] } ?? []
            } else {
                book.folder_ids = (book.source_folder_ids ?? []).map { "jjwxc_" + $0 }.filter { folders[$0] != nil }
            }
            book.last_read_at = last_read[book.id]
            if let stored = updated_at[book.id], let storedDate = Self.date(stored), Self.date(book.updated_at).map({ storedDate > $0 }) ?? true {
                book.updated_at = stored
            }
            return book
        }
    }

    static func date(_ value: String?) -> Date? {
        guard let value, !value.isEmpty else { return nil }
        if value.count == 10 || value.count == 16 || value.count == 19 {
            let parts = value.split(whereSeparator: { "- T:".contains($0) }).compactMap { Int($0) }
            guard parts.count == 3 || parts.count == 5 || parts.count == 6 else { return nil }
            let components = DateComponents(year: parts[0], month: parts[1], day: parts[2], hour: parts.count > 3 ? parts[3] : 0, minute: parts.count > 4 ? parts[4] : 0, second: parts.count > 5 ? parts[5] : 0)
            let calendar = BookClassification.publicationCalendar
            guard let date = calendar.date(from: components), calendar.dateComponents([.year, .month, .day, .hour, .minute, .second], from: date) == components else { return nil }
            return date
        }
        return isoDate.date(from: value) ?? fractionalISODate.date(from: value)
    }

    private static let isoDate = ISO8601DateFormatter()
    private static let fractionalISODate: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()
}
