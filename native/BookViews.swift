import SwiftUI
import AppKit

struct BookMetadata: Decodable {
    var description: String?
    var genre: String?
    var status: String?
    var word_count: String?
    var protagonists: String?
    var supporting: String?
    var perspective: String?
    var tagline: String?
    var theme: String?
    var tags: [String]?
    var characters: [BookCharacter]?
    var relationships: [BookRelationship]?
}
struct BookCharacter: Decodable, Identifiable { var id: String; var name: String; var picture: String? }
struct BookRelationship: Decodable { var start: String; var end: String; var direction: String }
struct CharacterCardsView: View {
    let info: BookMetadata
    private var linkedIDs: Set<String> { Set((info.relationships ?? []).flatMap { [$0.start, $0.end] }) }
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(L("人物与关系")).font(.headline)
            ForEach(Array((info.relationships ?? []).enumerated()), id: \.offset) { _, relation in
                if let start = info.characters?.first(where: { $0.id == relation.start }), let end = info.characters?.first(where: { $0.id == relation.end }) {
                    HStack(alignment: .center, spacing: 16) {
                        card(start)
                        Image(systemName: relation.direction == "right_to_left" ? "arrow.left" : relation.direction == "left_to_right" ? "arrow.right" : "arrow.left.arrow.right")
                            .font(.system(size: 28, weight: .semibold)).foregroundStyle(Color.accentColor).frame(width: 58)
                            .accessibilityLabel(start.name + (relation.direction == "right_to_left" ? " ← " : relation.direction == "left_to_right" ? " → " : " ↔ ") + end.name)
                        card(end)
                    }
                }
            }
            if !(info.characters ?? []).filter({ !linkedIDs.contains($0.id) }).isEmpty {
                ScrollView(.horizontal) {
                    HStack(alignment: .top, spacing: 16) {
                        ForEach((info.characters ?? []).filter { !linkedIDs.contains($0.id) }) { card($0) }
                    }
                }
            }
        }
    }
    private func card(_ character: BookCharacter) -> some View {
        VStack(spacing: 7) {
            AsyncImage(url: character.picture.flatMap(URL.init(string:))) { image in image.resizable().scaledToFill() }
            placeholder: { ZStack { Color.primary.opacity(0.05); Image(systemName: "person.crop.square").font(.largeTitle).foregroundStyle(.secondary) } }
            .frame(width: 80, height: 100).clipShape(RoundedRectangle(cornerRadius: 7))
            Text(character.name).font(.caption).lineLimit(2).frame(width: 90)
        }
    }
}
struct OnlineChapter: Decodable, Identifiable {
    var id: String
    var book_id: String
    var title: String
    var text: String
}
struct BookCoverView: View {
    let source: String?
    let title: String
    var body: some View {
        AsyncImage(url: source.flatMap(URL.init(string:))) { image in image.resizable().scaledToFill() }
        placeholder: { ZStack { Color.primary.opacity(0.05); Image(systemName: "book.closed").font(.largeTitle).foregroundStyle(.secondary) } }
        .frame(width: 96, height: 132).clipShape(RoundedRectangle(cornerRadius: 7))
        .overlay(RoundedRectangle(cornerRadius: 7).stroke(Color.primary.opacity(0.08)))
        .accessibilityLabel(title)
    }
}
struct OnlineReadingView: View {
    @ObservedObject var model: Model
    let chapter: OnlineChapter
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text(chapter.title).font(.headline)
                Spacer()
                Button(L("完成")) { dismiss() }.keyboardShortcut(.cancelAction)
            }.padding(22)
            Divider()
            ScrollView {
                Text(chapter.text).font(.system(size: 17)).lineSpacing(9).textSelection(.enabled)
                    .frame(maxWidth: 680, alignment: .leading).frame(maxWidth: .infinity, alignment: .center).padding(30)
            }
        }.frame(minWidth: 700, idealWidth: 800, minHeight: 520, idealHeight: 650)
    }
}
