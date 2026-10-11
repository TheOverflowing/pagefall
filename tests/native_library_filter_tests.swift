import Foundation

@main
struct NativeLibraryFilterTests {
    static var assertions = 0

    static func expect(_ condition: @autoclosure () -> Bool, _ message: String) {
        assertions += 1
        guard condition() else {
            fputs("FAIL: \(message)\n", stderr)
            exit(1)
        }
    }

    static func decode(_ json: String) throws -> BookClassification {
        try JSONDecoder().decode(BookClassification.self, from: Data(json.utf8))
    }

    static func instant(_ value: String) -> Date {
        ISO8601DateFormatter().date(from: value)!
    }

    static func dated(_ value: String) throws -> BookClassification {
        try decode("{\"published_at\":\"\(value)\"}")
    }

    static func main() throws {
        let lily = try decode(#"{"orientation":"百合","perspective":"主受","originality":"原创","era":"近代现代","genre":"爱情","status":"连载","word_count":86226}"#)
        let romance = try decode(#"{"orientation":"言情","perspective":"女主","originality":"原创","era":"古色古香","genre":"爱情","status":"完结","word_count":320000}"#)
        let pureLove = try decode(#"{"orientation":"纯爱","perspective":"主攻","originality":"衍生","era":"幻想未来","genre":"科幻","status":"完结","word_count":1200000}"#)

        expect(lily.orientation == "百合", "JSON decodes the book orientation")
        expect(lily.perspective == "主受", "JSON decodes the book perspective")
        expect(lily.originality == "原创" && lily.era == "近代现代", "JSON decodes originality and era")
        expect(lily.genre == "爱情" && lily.status == "连载", "JSON decodes genre and status")
        expect(lily.word_count == 86_226, "JSON decodes the integer word count")
        for facet in LibraryFacet.allCases {
            expect(lily.value(for: facet) != nil, "Decoded classification exposes \(facet.rawValue)")
        }

        var selection = LibraryFilterSelection()
        expect(selection.isEmpty && selection.count == 0, "A new selection is empty")
        expect(selection.matches(classification: nil, tags: []), "Unfiltered books with missing metadata remain visible")
        selection.toggle("百合", in: .orientation)
        selection.toggle("言情", in: .orientation)
        expect(selection.matches(classification: lily, tags: []), "An orientation group matches its first choice")
        expect(selection.matches(classification: romance, tags: []), "An orientation group matches its second choice with OR")
        expect(!selection.matches(classification: pureLove, tags: []), "An orientation group rejects an unselected choice")
        selection.toggle("主受", in: .perspective)
        expect(selection.matches(classification: lily, tags: []), "A book matching both selected facets is included")
        expect(!selection.matches(classification: romance, tags: []), "Different facet groups combine with AND")
        expect(!selection.matches(classification: nil, tags: []), "Missing classification does not match a concrete facet")
        let unknownPerspective = try decode(#"{"orientation":"百合","word_count":86226}"#)
        expect(!selection.matches(classification: unknownPerspective, tags: []), "A missing perspective does not match a selected perspective")
        let unknownValue = try decode(#"{"orientation":"未知","perspective":"主受"}"#)
        expect(!selection.matches(classification: unknownValue, tags: []), "An unknown value does not match a different concrete choice")

        selection = LibraryFilterSelection()
        selection.tags = ["甜文", "快穿"]
        expect(selection.matches(classification: nil, tags: ["甜文", "快穿", "系统"]), "All-tags mode accepts books containing every selected tag")
        expect(!selection.matches(classification: nil, tags: ["甜文"]), "All-tags mode rejects a partial tag match")
        expect(!selection.matches(classification: nil, tags: []), "All-tags mode rejects books with no tags")
        selection.matchAllTags = false
        expect(selection.matches(classification: nil, tags: ["快穿"]), "Any-tag mode accepts one selected tag")
        expect(!selection.matches(classification: nil, tags: ["悬疑"]), "Any-tag mode rejects disjoint tags")
        selection.toggle("百合", in: .orientation)
        expect(!selection.matches(classification: romance, tags: ["快穿"]), "A tag match cannot override a nonmatching facet")
        expect(selection.matches(classification: lily, tags: ["甜文"]), "Tag and facet groups combine with AND")

        let boundaries: [(Int, LibraryWordRange)] = [
            (0, .under100k), (99_999, .under100k),
            (100_000, .from100kTo300k), (299_999, .from100kTo300k),
            (300_000, .from300kTo500k), (499_999, .from300kTo500k),
            (500_000, .from500kTo1m), (999_999, .from500kTo1m),
            (1_000_000, .over1m), (1_000_001, .over1m)
        ]
        for (count, expectedRange) in boundaries {
            let classification = try decode("{\"word_count\":\(count)}")
            for range in LibraryWordRange.allCases {
                selection = LibraryFilterSelection()
                selection.wordRanges = [range]
                expect(selection.matches(classification: classification, tags: []) == (range == expectedRange), "Word count \(count) belongs only to \(expectedRange.rawValue), checked \(range.rawValue)")
            }
        }

        selection = LibraryFilterSelection()
        selection.wordRanges = [.under100k, .over1m]
        expect(selection.matches(classification: lily, tags: []), "Multiple word ranges accept a book in the first range")
        expect(selection.matches(classification: pureLove, tags: []), "Multiple word ranges accept a book in the other range with OR")
        expect(!selection.matches(classification: romance, tags: []), "Multiple word ranges leave unselected intervals excluded")
        expect(!selection.matches(classification: nil, tags: []), "Unknown word count does not match a concrete range")
        let invalidWordCount = try decode(#"{"word_count":-1}"#)
        expect(!selection.matches(classification: invalidWordCount, tags: []), "Negative word count does not match a concrete range")
        selection.toggle("百合", in: .orientation)
        expect(!selection.matches(classification: pureLove, tags: []), "Word ranges and facets combine with AND")

        selection = LibraryFilterSelection()
        let initialIdentity = selection.identity
        selection.toggle("百合", in: .orientation)
        expect(selection.count == 1 && !selection.isEmpty, "Toggling a choice adds one filter")
        selection.toggle("百合", in: .orientation)
        expect(selection.count == 0 && selection.isEmpty, "Toggling the same choice removes the filter")
        expect(selection.identity == initialIdentity, "Removing the last choice restores the empty selection identity")
        selection.toggle("主受", in: .perspective)
        selection.wordRanges = [.under100k, .over1m]
        selection.tags = ["甜文", "快穿"]
        selection.matchAllTags = false
        expect(selection.count == 5, "The active filter count includes facets, word ranges, and tags")
        selection = LibraryFilterSelection()
        expect(selection.facets.isEmpty && selection.wordRanges.isEmpty && selection.tags.isEmpty, "Clearing removes every filter category")
        expect(selection.isEmpty && selection.matchAllTags, "Clearing restores the default tag mode")
        expect(selection.matches(classification: nil, tags: []), "Clearing makes books with unknown metadata visible again")

        let datedLily = try decode(#"{"orientation":"百合","perspective":"主受","originality":"原创","era":"近代现代","genre":"爱情","status":"连载","word_count":86226,"published_at":"2026-10-08","favorite_count":4340}"#)
        expect(datedLily.published_at == "2026-10-08", "JSON decodes publication date")
        expect(datedLily.favorite_count == 4_340, "JSON decodes favorite count")
        expect(datedLily.publishedDate == instant("2026-10-08T00:00:00+08:00"), "Publication dates resolve to midnight in Shanghai")
        expect(BookClassification.publicationCalendar.timeZone.identifier == "Asia/Shanghai", "Publication filtering uses the Shanghai time zone")

        let monthEndNow = instant("2026-10-31T12:00:00+08:00")
        let publicationBoundaries: [(LibraryPublicationPeriod, String, String, String)] = [
            (.last2Months, "2026-08-30", "2026-08-31", "2026-09-01"),
            (.last3Months, "2026-07-30", "2026-07-31", "2026-08-01"),
            (.last6Months, "2026-04-29", "2026-04-30", "2026-05-01"),
            (.lastYear, "2025-10-30", "2025-10-31", "2025-11-01"),
            (.last2Years, "2024-10-30", "2024-10-31", "2024-11-01")
        ]
        for (period, before, boundary, after) in publicationBoundaries {
            selection = LibraryFilterSelection()
            selection.publicationPeriods = [period]
            let beforeBook = try dated(before)
            let boundaryBook = try dated(boundary)
            let afterBook = try dated(after)
            let todayBook = try dated("2026-10-31")
            let futureBook = try dated("2026-11-01")
            expect(!selection.matches(classification: beforeBook, tags: [], now: monthEndNow), "\(period.rawValue) excludes the day before its boundary")
            expect(selection.matches(classification: boundaryBook, tags: [], now: monthEndNow), "\(period.rawValue) includes its boundary at a month end")
            expect(selection.matches(classification: afterBook, tags: [], now: monthEndNow), "\(period.rawValue) includes the day after its boundary")
            expect(selection.matches(classification: todayBook, tags: [], now: monthEndNow), "\(period.rawValue) includes today")
            expect(!selection.matches(classification: futureBook, tags: [], now: monthEndNow), "\(period.rawValue) excludes a future date")
        }

        let leapCases: [(LibraryPublicationPeriod, String, String, String)] = [
            (.lastYear, "2024-02-29T12:00:00+08:00", "2023-02-27", "2023-02-28"),
            (.last2Years, "2024-02-29T12:00:00+08:00", "2022-02-27", "2022-02-28"),
            (.last6Months, "2024-08-31T12:00:00+08:00", "2024-02-28", "2024-02-29")
        ]
        for (period, nowString, before, boundary) in leapCases {
            selection = LibraryFilterSelection()
            selection.publicationPeriods = [period]
            let beforeBook = try dated(before)
            let boundaryBook = try dated(boundary)
            expect(!selection.matches(classification: beforeBook, tags: [], now: instant(nowString)), "\(period.rawValue) excludes the day before a leap-year boundary")
            expect(selection.matches(classification: boundaryBook, tags: [], now: instant(nowString)), "\(period.rawValue) includes a leap-year boundary")
        }

        let shanghaiNow = instant("2026-10-09T00:30:00+08:00")
        selection = LibraryFilterSelection()
        selection.publicationPeriods = [.last2Months]
        let shanghaiToday = try dated("2026-10-09")
        let shanghaiTomorrow = try dated("2026-10-10")
        expect(selection.matches(classification: shanghaiToday, tags: [], now: shanghaiNow), "The Shanghai date is included even when UTC is still yesterday")
        expect(!selection.matches(classification: shanghaiTomorrow, tags: [], now: shanghaiNow), "Tomorrow in Shanghai is excluded")

        for invalid in ["2026-02-30", "2026-13-01", "2026-00-01", "2026-10-00", "0000-01-01", "2026-10", "unknown"] {
            let invalidBook = try dated(invalid)
            expect(invalidBook.publishedDate == nil, "Invalid publication date \(invalid) does not resolve")
            expect(!selection.matches(classification: invalidBook, tags: [], now: shanghaiNow), "Invalid publication date \(invalid) cannot match a period")
        }
        let unknownPublication = try decode(#"{"published_at":null}"#)
        expect(!selection.matches(classification: unknownPublication, tags: [], now: shanghaiNow), "Unknown publication date cannot match a selected period")
        expect(!selection.matches(classification: nil, tags: [], now: shanghaiNow), "Missing classification cannot match a selected period")

        selection = LibraryFilterSelection()
        selection.publicationPeriods = [.last2Months, .last6Months]
        let widerPeriodBook = try dated("2026-05-31")
        let outsidePeriodsBook = try dated("2026-02-01")
        expect(selection.matches(classification: widerPeriodBook, tags: [], now: monthEndNow), "Publication period choices combine with OR")
        expect(!selection.matches(classification: outsidePeriodsBook, tags: [], now: monthEndNow), "A book outside every selected period is excluded")
        selection.publicationPeriods = [.last2Months]
        selection.publicationYears = [2024]
        let yearOnlyBook = try dated("2024-06-01")
        let periodOnlyBook = try dated("2026-09-01")
        let neitherPublicationBook = try dated("2025-06-01")
        expect(selection.matches(classification: yearOnlyBook, tags: [], now: monthEndNow), "A selected year can match outside selected periods")
        expect(selection.matches(classification: periodOnlyBook, tags: [], now: monthEndNow), "A selected period can match outside selected years")
        expect(!selection.matches(classification: neitherPublicationBook, tags: [], now: monthEndNow), "Publication years and periods reject a book matching neither")
        selection.publicationPeriods = []
        selection.publicationYears = [2024, 2025]
        for value in ["2024-01-01", "2024-12-31", "2025-01-01", "2025-12-31"] {
            let book = try dated(value)
            expect(selection.matches(classification: book, tags: [], now: monthEndNow), "Selected years include \(value)")
        }
        for value in ["2023-12-31", "2026-01-01"] {
            let book = try dated(value)
            expect(!selection.matches(classification: book, tags: [], now: monthEndNow), "Selected years exclude \(value)")
        }
        expect(!selection.matches(classification: unknownPublication, tags: [], now: monthEndNow), "Unknown publication date cannot match a selected year")
        selection.publicationYears = [2027]
        let futureYearBook = try dated("2027-01-01")
        expect(!selection.matches(classification: futureYearBook, tags: [], now: monthEndNow), "Selecting a year does not admit future publication dates")

        let favoriteBoundaries: [(Int, LibraryFavoriteRange)] = [
            (0, .under1k), (999, .under1k),
            (1_000, .from1kTo5k), (4_999, .from1kTo5k),
            (5_000, .from5kTo10k), (9_999, .from5kTo10k),
            (10_000, .from10kTo50k), (49_999, .from10kTo50k),
            (50_000, .from50kTo100k), (99_999, .from50kTo100k),
            (100_000, .over100k), (100_001, .over100k)
        ]
        for (count, expectedRange) in favoriteBoundaries {
            let classification = try decode("{\"favorite_count\":\(count)}")
            for range in LibraryFavoriteRange.allCases {
                selection = LibraryFilterSelection()
                selection.favoriteRanges = [range]
                expect(selection.matches(classification: classification, tags: []) == (range == expectedRange), "Favorite count \(count) belongs only to \(expectedRange.rawValue), checked \(range.rawValue)")
            }
        }
        selection = LibraryFilterSelection()
        selection.favoriteRanges = [.under1k, .over100k]
        let fewFavorites = try decode(#"{"favorite_count":0}"#)
        let manyFavorites = try decode(#"{"favorite_count":100000}"#)
        expect(selection.matches(classification: fewFavorites, tags: []), "Multiple favorite ranges accept the lower range")
        expect(selection.matches(classification: manyFavorites, tags: []), "Multiple favorite ranges accept the upper range with OR")
        expect(!selection.matches(classification: datedLily, tags: []), "Multiple favorite ranges exclude an unselected interval")
        let unknownFavorites = try decode(#"{"favorite_count":null}"#)
        expect(!selection.matches(classification: unknownFavorites, tags: []), "Unknown favorite count cannot match a selected range")
        expect(!selection.matches(classification: nil, tags: []), "Missing classification cannot match a favorite range")
        let negativeFavorites = try decode(#"{"favorite_count":-1}"#)
        selection.favoriteRanges = Set(LibraryFavoriteRange.allCases)
        expect(!selection.matches(classification: negativeFavorites, tags: []), "Negative favorite counts match no range")

        selection = LibraryFilterSelection()
        selection.toggle("百合", in: .orientation)
        selection.wordRanges = [.under100k]
        selection.publicationPeriods = [.last3Months]
        selection.favoriteRanges = [.from1kTo5k]
        selection.tags = ["甜文"]
        expect(selection.matches(classification: datedLily, tags: ["甜文"], now: shanghaiNow), "A book matching every classification, date, count, and tag group is included")
        var changed = datedLily
        changed.orientation = "言情"
        expect(!selection.matches(classification: changed, tags: ["甜文"], now: shanghaiNow), "Publication and favorite matches do not override orientation")
        changed = datedLily
        changed.published_at = "2025-01-01"
        expect(!selection.matches(classification: changed, tags: ["甜文"], now: shanghaiNow), "Other filter groups do not override a publication mismatch")
        changed = datedLily
        changed.favorite_count = 50_000
        expect(!selection.matches(classification: changed, tags: ["甜文"], now: shanghaiNow), "Other filter groups do not override a favorite mismatch")
        expect(!selection.matches(classification: datedLily, tags: [], now: shanghaiNow), "Publication and favorite filters combine with tags using AND")

        selection.publicationPeriods = [.last2Months, .last6Months]
        selection.publicationYears = [2024, 2025]
        selection.favoriteRanges = [.under1k, .over100k]
        expect(selection.count == 9, "Active count includes every selected period, year, favorite range, and existing filter")
        let fullIdentity = selection.identity
        var reordered = LibraryFilterSelection()
        reordered.tags = ["甜文"]
        reordered.favoriteRanges = [.over100k, .under1k]
        reordered.publicationYears = [2025, 2024]
        reordered.publicationPeriods = [.last6Months, .last2Months]
        reordered.wordRanges = [.under100k]
        reordered.toggle("百合", in: .orientation)
        expect(reordered.identity == fullIdentity, "Selection identity is independent of insertion order")
        reordered.publicationYears.remove(2025)
        expect(reordered.identity != fullIdentity, "Selection identity includes publication years")
        reordered = selection
        reordered.publicationPeriods.remove(.last2Months)
        expect(reordered.identity != fullIdentity, "Selection identity includes publication periods")
        reordered = selection
        reordered.favoriteRanges.remove(.under1k)
        expect(reordered.identity != fullIdentity, "Selection identity includes favorite ranges")
        selection = LibraryFilterSelection()
        expect(selection.publicationPeriods.isEmpty && selection.publicationYears.isEmpty && selection.favoriteRanges.isEmpty, "Clearing removes publication and favorite filters")
        expect(selection.count == 0 && selection.isEmpty && selection.identity == initialIdentity, "Clearing restores the full empty selection state and identity")
        expect(selection.matches(classification: nil, tags: [], now: shanghaiNow), "Unfiltered books with unknown dates and counts remain visible after clearing")

        let indexedBooks = [
            LibraryBook(id: "lily", title: "雨中的故事", author: "Lin", url: "", tags: ["甜文", "快穿", "甜文"], classification: datedLily),
            LibraryBook(id: "romance", title: "Sunny Days", author: "雨林", url: "", tags: ["甜文"], classification: romance),
            LibraryBook(id: "pure", title: "星际", author: "Ada", url: "", tags: ["科幻"], classification: pureLove),
            LibraryBook(id: "unknown", title: "未知", author: "", url: ""),
            LibraryBook(id: "future", title: "Future", author: "", url: "", classification: try dated("2026-10-10")),
            LibraryBook(id: "invalid", title: "Invalid", author: "", url: "", classification: try dated("2026-02-30"))
        ]
        let index = LibraryFilterIndex(books: indexedBooks)
        expect(index.tags.first?.name == "甜文" && index.tags.first?.count == 2, "Indexed tag counts count each tag only once per book")
        expect(index.facetOptions[.orientation] == LibraryFacet.orientation.preferredOrder, "Indexed facets preserve their preferred order")
        expect(index.publicationYears == [2026], "Indexed publication years include only valid dates")
        var selections: [LibraryFilterSelection] = [LibraryFilterSelection()]
        for facet in LibraryFacet.allCases {
            for option in facet.preferredOrder {
                var item = LibraryFilterSelection(); item.toggle(option, in: facet); selections.append(item)
            }
        }
        for period in LibraryPublicationPeriod.allCases {
            var item = LibraryFilterSelection(); item.publicationPeriods = [period]; selections.append(item)
        }
        for range in LibraryWordRange.allCases {
            var item = LibraryFilterSelection(); item.wordRanges = [range]; selections.append(item)
        }
        for range in LibraryFavoriteRange.allCases {
            var item = LibraryFilterSelection(); item.favoriteRanges = [range]; selections.append(item)
        }
        var combined = LibraryFilterSelection()
        combined.toggle("百合", in: .orientation); combined.toggle("主受", in: .perspective)
        combined.publicationPeriods = [.last2Months, .lastYear]; combined.publicationYears = [2024]
        combined.favoriteRanges = [.from1kTo5k]; combined.wordRanges = [.under100k]
        combined.tags = ["甜文", "快穿"]; selections.append(combined)
        combined.matchAllTags = false; selections.append(combined)
        combined = LibraryFilterSelection(); combined.tags = ["甜文", "快穿"]; selections.append(combined)
        combined.matchAllTags = false; selections.append(combined)
        combined = LibraryFilterSelection(); combined.publicationYears = [2026]; selections.append(combined)
        for item in selections {
            for query in ["", "雨", "sunny", "LIN", "没有这本书"] {
                let expected = indexedBooks.filter { book in
                    (query.isEmpty || book.title.localizedCaseInsensitiveContains(query) || book.author.localizedCaseInsensitiveContains(query))
                        && item.matches(classification: book.classification, tags: book.tags ?? [], now: shanghaiNow)
                }.map(\.id)
                expect(index.matchingBooks(item, query: query, now: shanghaiNow).map(\.id) == expected, "Indexed results preserve every filter group, text search, and ordering")
            }
        }
        combined = LibraryFilterSelection(); combined.publicationYears = [2026]
        expect(!index.matchingBooks(combined, now: shanghaiNow).contains { $0.id == "future" }, "Cached results exclude tomorrow")
        expect(index.matchingBooks(combined, now: instant("2026-10-10T00:30:00+08:00")).contains { $0.id == "future" }, "Date filtering cache refreshes across Shanghai midnight")
        var enriched = indexedBooks
        enriched[3].classification = datedLily
        enriched[3].tags = ["新标签"]
        let updatedIndex = LibraryFilterIndex(books: enriched)
        expect(updatedIndex.tags.contains { $0.name == "新标签" && $0.count == 1 }, "A newly enriched shelf rebuilds the tag candidates")
        combined = LibraryFilterSelection(); combined.toggle("百合", in: .orientation)
        expect(updatedIndex.matchingBooks(combined, now: shanghaiNow).map(\.id) == ["lily", "unknown"], "Newly enriched books appear in an existing filter in shelf order")
        expect(LibraryFilterIndex(books: []).matchingBooks(combined, now: shanghaiNow).isEmpty, "An empty index remains empty")

        let cache = FileManager.default.temporaryDirectory.appendingPathComponent("pagefall-library-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: cache, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: cache) }
        try Data(#"{"books":[{"id":"ready","title":"春日","author":"作者","url":"https://example.test/1"},{"id":"basic","title":"冬日","author":"作者","url":"https://example.test/2"},{"id":"unknown","title":"预约","author":"另一作者","url":"https://example.test/3"}],"account":{"state":"connected","nickname":"Reader"},"expected_count":3,"complete":true}"#.utf8).write(to: cache.appendingPathComponent("library.json"))
        try Data(#"{"ready":["甜文","快穿"],"basic":["剧情"],"unknown":[]}"#.utf8).write(to: cache.appendingPathComponent("library-tags.json"))
        try Data(#"{"ready":{"orientation":"百合","perspective":"主受","favorite_count":4000,"published_at":"2026-09-01","_checked_version":2},"basic":{"orientation":"言情","favorite_count":200,"_basic_checked":true},"unknown":{"published_at":null,"_checked_version":2}}"#.utf8).write(to: cache.appendingPathComponent("library-classifications.json"))
        let cachedShelf = try LibraryState.cached(in: cache)
        expect(cachedShelf.books.map(\.id) == ["ready", "basic", "unknown"], "Startup cache retains shelf order")
        expect(cachedShelf.account.nickname == "Reader" && cachedShelf.expected_count == 3 && cachedShelf.complete == true, "Startup cache retains shelf and account information")
        expect(cachedShelf.classification_count == 2 && cachedShelf.classification_basic_count == 3, "Startup cache retains both enrichment stages, including checked books with no date")
        expect(cachedShelf.syncing == false && cachedShelf.classification_loading == false, "Startup cache does not revive saved in-flight activity")
        expect(cachedShelf.books[0].tags == ["甜文", "快穿"], "Startup cache merges saved tags")
        expect(cachedShelf.books[0].classification?.favorite_count == 4000, "Startup cache merges saved classification")
        expect(cachedShelf.books[2].classification?.publishedDate == nil, "Startup cache keeps unknown publication dates unknown")
        combined = LibraryFilterSelection(); combined.toggle("百合", in: .orientation); combined.toggle("主受", in: .perspective)
        combined.favoriteRanges = [.from1kTo5k]; combined.publicationPeriods = [.last2Months]; combined.tags = ["甜文"]
        expect(LibraryFilterIndex(books: cachedShelf.books).matchingBooks(combined, now: shanghaiNow).map(\.id) == ["ready"], "Combined filters work before a backend response")
        let reloadedShelf = try LibraryState.cached(in: cache)
        expect(reloadedShelf == cachedShelf, "Reopening the same disk cache preserves the complete snapshot")
        try FileManager.default.removeItem(at: cache.appendingPathComponent("library-tags.json"))
        try FileManager.default.removeItem(at: cache.appendingPathComponent("library-classifications.json"))
        let unenrichedShelf = try LibraryState.cached(in: cache)
        expect(unenrichedShelf.books.count == 3, "A shelf with no enrichment cache remains immediately readable")
        expect(unenrichedShelf.classification_count == 0 && unenrichedShelf.classification_basic_count == 0, "Missing enrichment caches do not appear complete")

        let defaultsSuite = "pagefall-filter-tests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: defaultsSuite)!
        defer { defaults.removePersistentDomain(forName: defaultsSuite) }
        expect(LibraryFilterSelection.load(from: defaults).isEmpty, "The first launch starts without filters")
        var applied = LibraryFilterSelection()
        applied.facets = [.orientation: ["百合"], .perspective: ["主受"], .originality: ["原创"], .era: ["近代现代"], .genre: ["爱情"], .status: ["连载"]]
        applied.wordRanges = [.under100k]
        applied.publicationPeriods = [.last2Months]
        applied.publicationYears = [2024]
        applied.favoriteRanges = [.from1kTo5k]
        applied.tags = ["甜文", "快穿"]
        applied.matchAllTags = false
        applied.save(to: defaults)
        let reopenedDefaults = UserDefaults(suiteName: defaultsSuite)!
        let restored = LibraryFilterSelection.load(from: reopenedDefaults)
        expect(restored == applied, "Reopening preferences preserves every applied filter and tag mode")
        expect(index.matchingBooks(restored, now: shanghaiNow).map(\.id) == ["lily"], "Restored combined filters produce the same matching books")
        applied.tags.remove("快穿")
        applied.publicationYears.remove(2024)
        applied.save(to: defaults)
        expect(LibraryFilterSelection.load(from: reopenedDefaults) == applied, "Removing applied choices persists the remaining filters")
        LibraryFilterSelection().save(to: defaults)
        let cleared = LibraryFilterSelection.load(from: reopenedDefaults)
        expect(cleared.isEmpty && cleared.matchAllTags, "Clearing filters remains cleared after reopening preferences")

        let filterDecoder = JSONDecoder()
        let olderSelection = try filterDecoder.decode(LibraryFilterSelection.self, from: Data(#"{"facets":{"orientation":["百合"]},"tags":["甜文"]}"#.utf8))
        expect(olderSelection.facets[.orientation] == ["百合"] && olderSelection.tags == ["甜文"], "Older saved filters retain their existing choices")
        expect(olderSelection.publicationPeriods.isEmpty && olderSelection.favoriteRanges.isEmpty && olderSelection.matchAllTags, "Missing saved fields use the original defaults")
        let futureSelection = try filterDecoder.decode(LibraryFilterSelection.self, from: Data(#"{"facets":{"orientation":["百合"],"futureFacet":["unknown"]},"wordRanges":["under100k","futureRange"],"publicationPeriods":["last2Months","futurePeriod"],"favoriteRanges":["from1kTo5k","futureRange"],"futureSetting":true}"#.utf8))
        expect(futureSelection.facets == [.orientation: ["百合"]], "Unknown facet names do not discard known selections")
        expect(futureSelection.wordRanges == [.under100k] && futureSelection.publicationPeriods == [.last2Months] && futureSelection.favoriteRanges == [.from1kTo5k], "Unknown range choices do not discard known range choices")
        defaults.set(Data("invalid JSON".utf8), forKey: "library.appliedFilters")
        expect(LibraryFilterSelection.load(from: defaults).isEmpty, "An unreadable saved selection leaves the shelf usable without filters")

        print("PASS: \(assertions) native library filter assertions")
    }
}
