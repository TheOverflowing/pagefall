import Foundation

@main
struct NativeLibraryFilterPerformance {
    static func measure(runs: Int, _ work: () -> Int) -> (milliseconds: Double, checksum: Int) {
        var checksum = 0
        let start = DispatchTime.now().uptimeNanoseconds
        for _ in 0..<runs { checksum += work() }
        return (Double(DispatchTime.now().uptimeNanoseconds - start) / 1_000_000 / Double(runs), checksum)
    }

    static func fixture() -> [LibraryBook] {
        (0..<1094).map { ordinal in
            let classification = BookClassification(
                orientation: LibraryFacet.orientation.preferredOrder[ordinal % 5],
                perspective: LibraryFacet.perspective.preferredOrder[ordinal % 6],
                originality: ordinal % 2 == 0 ? "原创" : "衍生", era: "近代现代", genre: "爱情",
                status: ordinal % 3 == 0 ? "连载" : "完结", word_count: ordinal * 2000,
                published_at: "\(2020 + ordinal % 7)-\(String(format: "%02d", ordinal % 12 + 1))-\(String(format: "%02d", ordinal % 28 + 1))",
                favorite_count: ordinal * 120)
            return LibraryBook(id: String(ordinal), title: "故事 \(ordinal)", author: "作者 \(ordinal % 80)", url: "",
                tags: ["甜文", "标签 \(ordinal % 80)", "标签 \(ordinal % 25)"], classification: classification)
        }
    }

    static func main() throws {
        let books: [LibraryBook]
        if CommandLine.arguments.count > 1 {
            struct Shelf: Decodable { let books: [LibraryBook] }
            let data = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))
            books = try JSONDecoder().decode(Shelf.self, from: data).books
        } else { books = fixture() }
        let now = ISO8601DateFormatter().date(from: "2026-10-09T00:30:00+08:00")!
        let index = LibraryFilterIndex(books: books)
        var selection = LibraryFilterSelection()
        selection.publicationPeriods = [.lastYear]
        let build = measure(runs: 100) { LibraryFilterIndex(books: books).tags.count }
        var step = 0
        let legacyMatch = measure(runs: 300) {
            selection.publicationPeriods = step % 2 == 0 ? [.lastYear] : [.last2Years]; step += 1
            return books.filter { selection.matches(classification: $0.classification, tags: $0.tags ?? [], now: now) }.count
        }
        step = 0
        let indexedMatch = measure(runs: 300) {
            selection.publicationPeriods = step % 2 == 0 ? [.lastYear] : [.last2Years]; step += 1
            return index.matchingBooks(selection, now: now).count
        }
        guard legacyMatch.checksum == indexedMatch.checksum else { fatalError("Filter results differ") }
        selection.publicationPeriods = [.lastYear]
        let legacyRedraw = measure(runs: 100) {
            var total = 0
            for _ in 0..<5 {
                var counts: [String: Int] = [:]
                for book in books { for tag in Set(book.tags ?? []) { counts[tag, default: 0] += 1 } }
                let tags: [(name: String, count: Int)] = counts.map { (name: $0.key, count: $0.value) }
                total += tags.sorted { $0.count != $1.count ? $0.count > $1.count : $0.name.localizedStandardCompare($1.name) == .orderedAscending }.count
            }
            let calendar = BookClassification.publicationCalendar
            for _ in 0..<3 { total += Set(books.compactMap { $0.classification?.publishedDate }.map { calendar.component(.year, from: $0) }).count }
            for facet in LibraryFacet.allCases {
                let available = Set(books.compactMap { $0.classification?.value(for: facet) }.filter { !$0.isEmpty })
                total += (facet.preferredOrder + available.subtracting(facet.preferredOrder).sorted()).count
            }
            total += books.filter { selection.matches(classification: $0.classification, tags: $0.tags ?? [], now: now) }.count
            return total
        }
        let indexedRedraw = measure(runs: 100) {
            var total = 0
            for _ in 0..<5 { total += index.tags.count }
            for _ in 0..<3 { total += index.publicationYears.count }
            for facet in LibraryFacet.allCases { total += index.facetOptions[facet]?.count ?? 0 }
            return total + index.matchingBooks(selection, now: now).count
        }
        guard legacyRedraw.checksum == indexedRedraw.checksum else { fatalError("Filter candidates differ") }
        var combinations: [LibraryFilterSelection] = []
        for book in books.prefix(100) {
            var item = LibraryFilterSelection()
            if let value = book.classification?.orientation { item.toggle(value, in: .orientation) }
            if let value = book.classification?.perspective { item.toggle(value, in: .perspective) }
            item.publicationPeriods = [.lastYear, .last2Years]
            item.favoriteRanges = [.under1k, .from1kTo5k, .from5kTo10k, .from10kTo50k, .from50kTo100k, .over100k]
            item.tags = Set((book.tags ?? []).prefix(1))
            combinations.append(item)
        }
        for item in combinations {
            let expected = books.filter { item.matches(classification: $0.classification, tags: $0.tags ?? [], now: now) }.map(\.id)
            guard index.matchingBooks(item, now: now).map(\.id) == expected else { fatalError("Combined filter results differ") }
        }
        var combination = 0
        let combined = measure(runs: 500) {
            defer { combination += 1 }
            return index.matchingBooks(combinations[combination % combinations.count], now: now).count
        }
        print(String(format: "books=%d build_index_ms=%.4f", books.count, build.milliseconds))
        print(String(format: "changing_selection_ms legacy=%.4f indexed=%.4f", legacyMatch.milliseconds, indexedMatch.milliseconds))
        print(String(format: "redraw_calculations_ms legacy=%.4f indexed=%.4f", legacyRedraw.milliseconds, indexedRedraw.milliseconds))
        print(String(format: "combined_selection_ms=%.4f verified_combinations=%d", combined.milliseconds, combinations.count))
        if CommandLine.arguments.count > 2 {
            let directory = URL(fileURLWithPath: CommandLine.arguments[2], isDirectory: true)
            let cached = try LibraryState.cached(in: directory)
            let diskRead = measure(runs: 30) { (try! LibraryState.cached(in: directory)).books.count }
            let startup = measure(runs: 30) { LibraryFilterIndex(books: (try! LibraryState.cached(in: directory)).books).books.count }
            print(String(format: "cached_books=%d cached_checked=%d cached_basic=%d disk_load_ms=%.4f disk_and_index_ms=%.4f", cached.books.count, cached.classification_count ?? 0, cached.classification_basic_count ?? 0, diskRead.milliseconds, startup.milliseconds))
        }
    }
}
