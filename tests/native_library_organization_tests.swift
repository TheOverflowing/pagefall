import Foundation

@main
struct NativeLibraryOrganizationTests {
    static var assertions = 0

    static func expect(_ condition: @autoclosure () -> Bool, _ message: String) {
        assertions += 1
        guard condition() else { fatalError(message) }
    }

    static func main() throws {
        let books = [
            LibraryBook(id: "z", title: "Book 2", author: "Ada", url: "", tags: ["甜文"], classification: BookClassification(word_count: 10, favorite_count: 100), folder_ids: ["a", "a"], last_read_at: 50, updated_at: "2026-10-10 08:00:00"),
            LibraryBook(id: "a", title: "Book 10", author: "Ada", url: "", tags: ["甜文"], classification: BookClassification(word_count: 30, favorite_count: 50), folder_ids: ["b"], last_read_at: 100, updated_at: "2026-10-11 08:00:00"),
            LibraryBook(id: "b", title: "Alpha", author: "Lin", url: "", tags: ["剧情"], classification: BookClassification(word_count: 30, favorite_count: 50), folder_ids: ["a"], last_read_at: 100, updated_at: "2026-10-11T00:00:00Z"),
            LibraryBook(id: "unknown", title: "Untitled", author: "", url: ""),
            LibraryBook(id: "invalid", title: " ", author: "", url: "", classification: BookClassification(word_count: -1, favorite_count: -1), last_read_at: 0, updated_at: "2026-02-30 08:00:00")
        ]
        let index = LibraryFilterIndex(books: books)
        let all = LibraryFilterSelection()
        func ids(_ order: LibrarySortOrder = .platform, ascending: Bool = true, folderID: String? = nil) -> [String] {
            index.matchingBooks(all, sort: order, ascending: ascending, folderID: folderID).map(\.id)
        }
        expect(ids() == ["z", "a", "b", "unknown", "invalid"], "Default order is the original shelf order")
        expect(ids(ascending: false) == ["invalid", "unknown", "b", "a", "z"], "Default order can be reversed")
        for order in [LibrarySortOrder.updated, .recentReading, .wordCount] {
            expect(ids(order, ascending: false) == ["a", "b", "z", "unknown", "invalid"], "Descending numeric sorts retain tie order and put unknowns last")
            expect(ids(order) == ["z", "a", "b", "unknown", "invalid"], "Ascending numeric sorts retain tie order and put unknowns last")
        }
        expect(ids(.favoriteCount, ascending: false) == ["z", "a", "b", "unknown", "invalid"], "Most collected books sort first")
        expect(ids(.favoriteCount) == ["a", "b", "z", "unknown", "invalid"], "Fewest collected books sort first without promoting unknowns")
        expect(ids(.title) == ["b", "z", "a", "unknown", "invalid"], "Title order uses natural number ordering")
        expect(ids(.title, ascending: false) == ["unknown", "a", "z", "b", "invalid"], "Title order can be reversed with empty titles still last")
        expect(LibrarySortOrder.title.defaultAscending && LibrarySortOrder.platform.defaultAscending, "Titles and shelf order start ascending")
        expect(!LibrarySortOrder.updated.defaultAscending && !LibrarySortOrder.recentReading.defaultAscending && !LibrarySortOrder.wordCount.defaultAscending && !LibrarySortOrder.favoriteCount.defaultAscending, "Latest, longest, and most collected start first")
        expect(index.folderCounts == ["a": 2, "b": 1], "Folder totals count each book once")
        expect(index.unfiledCount == 2, "Books with no folder are counted together")
        expect(ids(.updated, ascending: false, folderID: "a") == ["b", "z"], "Folder selection composes with sorting")
        expect(ids(folderID: LibraryFilterIndex.unfiledFolderID) == ["unknown", "invalid"], "Unfiled selection includes missing and empty assignments")
        expect(ids(folderID: "missing").isEmpty, "A missing folder does not display unrelated books")
        var sweet = LibraryFilterSelection()
        sweet.tags = ["甜文"]
        expect(index.matchingBooks(sweet, query: "book", sort: .wordCount, ascending: false, folderID: "a").map(\.id) == ["z"], "Folder, search, existing filters, and sort combine")
        expect(index.matchingBooks(sweet, query: "book", sort: .wordCount, ascending: false, folderID: "b").map(\.id) == ["a"], "Switching folders invalidates the result cache")
        expect(index.matchingBooks(sweet, query: "book", sort: .wordCount, ascending: false).map(\.id) == ["a", "z"], "Leaving a folder restores all matching books")
        expect(ids(.wordCount, ascending: false) == ["a", "b", "z", "unknown", "invalid"], "Clearing filters restores cached sorted order")
        var updated = books
        updated[0].last_read_at = 200
        let refreshed = LibraryFilterIndex(books: updated)
        expect(refreshed.matchingBooks(all, sort: .recentReading, ascending: false).first?.id == "z", "A newer reading timestamp updates the next index")

        for date in ["2026-10-11", "2026-10-11 08:30", "2026-10-11 08:30:00", "2026-10-11T08:30:00", "2026-10-11T00:30:00Z", "2026-10-11T00:30:00.000Z", "2026-10-11T08:30:00+08:00"] {
            expect(LibraryOrganizationCache.date(date) != nil, "Supported update date parses: \(date)")
        }
        for date in ["", "yesterday", "2026-02-30", "2026-10-11 25:30:00", "2026-10-11 10:99:00"] {
            expect(LibraryOrganizationCache.date(date) == nil, "Invalid update date remains unknown: \(date)")
        }
        expect(LibraryOrganizationCache.date("2026-10-11 08:30:00") == LibraryOrganizationCache.date("2026-10-11T00:30:00Z"), "Jinjiang wall-clock times use Shanghai time")

        let cache = FileManager.default.temporaryDirectory.appendingPathComponent("pagefall-organization-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: cache, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: cache) }
        try Data(#"{"books":[{"id":"site","title":"Site","author":"","url":"","source_folder_ids":["8"],"updated_at":"2026-10-12 08:00:00"},{"id":"moved","title":"Moved","author":"","url":"","source_folder_ids":["8"]},{"id":"unfiled","title":"Unfiled","author":"","url":"","source_folder_ids":["8"]},{"id":"deleted","title":"Deleted","author":"","url":"","source_folder_ids":["8"]},{"id":"updated","title":"Updated","author":"","url":"","source_folder_ids":["0"],"updated_at":"2026-10-10 08:00:00"},{"id":"new","title":"New","author":"","url":"","source_folder_ids":["20"]}],"folders":[{"id":"0","name":"尚未分类"},{"id":"-1","name":"推荐"},{"id":"8","name":"Site folder"},{"id":"20","name":"Deleted source folder"}],"account":{"state":"connected"},"complete":true}"#.utf8).write(to: cache.appendingPathComponent("library.json"))
        try Data(#"{"folders":{"jjwxc_8":{"id":"jjwxc_8","name":"Site folder","source":"jjwxc","source_id":"8"},"local_a":{"id":"local_a","name":"Local folder","source":"local"},"jjwxc_10":{"id":"jjwxc_10","name":"Empty folder","source":"jjwxc","source_id":"10"}},"assignments":{"moved":"local_a","unfiled":null,"deleted":"local_deleted"},"last_read":{"moved":1791766800},"updated_at":{"site":"2026-10-10 08:00:00","updated":"2026-10-12 08:00:00"},"ignored_source_ids":["20"]}"#.utf8).write(to: cache.appendingPathComponent("library-organization.json"))
        let shelf = try LibraryState.cached(in: cache)
        func book(_ id: String) -> LibraryBook { shelf.books.first { $0.id == id }! }
        expect(shelf.folders?.map(\.name) == ["Empty folder", "Local folder", "Site folder"], "Startup merges named folders including empty source folders")
        expect(book("site").folder_ids == ["jjwxc_8"], "Source folder is assigned when there is no local move")
        expect(book("moved").folder_ids == ["local_a"], "Local movement overrides imported source placement")
        expect(book("unfiled").folder_ids == [], "An explicit null assignment keeps the book unfiled after restart")
        expect(book("deleted").folder_ids == [], "Deleted local folders do not revive old source assignments")
        expect(book("updated").folder_ids == [], "Source unclassified group is represented by the unfiled collection")
        expect(book("new").folder_ids == [], "A deleted source folder is not reintroduced from the raw shelf snapshot")
        expect(book("moved").last_read_at == 1791766800, "Recent reading survives restart")
        expect(book("new").last_read_at == nil, "Never-read books remain unknown")
        expect(book("site").updated_at == "2026-10-12 08:00:00", "Newer site timestamps win over older local directory timestamps")
        expect(book("updated").updated_at == "2026-10-12 08:00:00", "Newer local directory timestamps win over older site timestamps")
        expect(book("new").updated_at == nil, "Missing update times stay unknown")
        let reopened = try LibraryState.cached(in: cache)
        expect(reopened == shelf, "Repeated startup restores the same organization state")
        let restored = LibraryFilterIndex(books: shelf.books)
        expect(restored.matchingBooks(all, sort: .recentReading, ascending: false).first?.id == "moved", "Recent-reading sorting works before the first backend response")
        expect(restored.matchingBooks(all, folderID: "jjwxc_8").map(\.id) == ["site"], "Folder filtering works before the first backend response")
        try FileManager.default.removeItem(at: cache.appendingPathComponent("library-organization.json"))
        let firstImport = try LibraryState.cached(in: cache)
        expect(firstImport.folders?.map(\.id) == ["jjwxc_20", "jjwxc_8"], "Raw source folders are readable before local organization is initialized")
        expect(firstImport.books.first { $0.id == "site" }?.folder_ids == ["jjwxc_8"], "Raw source membership gets its local ID before the backend response")
        expect(firstImport.books.first { $0.id == "updated" }?.folder_ids == [], "Source collection IDs never become custom folders")
        print("PASS: \(assertions) native library organization assertions")
    }
}
