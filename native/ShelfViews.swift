import SwiftUI

struct LibraryCoverCard: View {
    let book: LibraryBook
    let detail: String
    var selecting = false
    var selected = false
    let open: () -> Void

    var body: some View {
        Button(action: open) {
            VStack(alignment: .leading, spacing: 7) {
                CoverImageView(source: book.cover, title: book.title, width: 138, height: 192, reloadMenu: false)
                    .overlay {
                        if selecting && selected { RoundedRectangle(cornerRadius: 6).strokeBorder(Color.accentColor, lineWidth: 3) }
                    }
                    .overlay(alignment: .topTrailing) {
                        if selecting {
                            Image(systemName: selected ? "checkmark.circle.fill" : "circle")
                                .font(.system(size: 22, weight: .medium))
                                .foregroundStyle(selected ? Color.accentColor : Color.primary)
                                .background(.regularMaterial, in: Circle()).padding(8)
                                .accessibilityHidden(true)
                        }
                    }
                    .padding(.bottom, 3)
                Text(book.title).font(.system(size: 13, weight: .medium)).foregroundStyle(.primary)
                    .lineLimit(2, reservesSpace: true)
                Text(book.author.isEmpty ? L("作者未提供") : book.author)
                    .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                if !detail.isEmpty { Text(detail).font(.caption2).foregroundStyle(.secondary).lineLimit(1) }
            }.frame(width: 138, alignment: .leading).contentShape(Rectangle())
        }.buttonStyle(.plain).accessibilityLabel(selecting ? L("选择\(book.title)") : L("\(book.title)，\(book.author)，查看小说详情"))
            .accessibilityValue(selecting ? (selected ? L("已选择") : L("未选择")) : "")
    }
}

struct LibraryFolderEditorRequest: Identifiable {
    let id = UUID()
    let folder: LibraryFolder?
}

struct LibraryFolderEditor: View {
    let folder: LibraryFolder?
    let save: (String) async throws -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var name: String
    @State private var saving = false
    @State private var error: String?
    @FocusState private var nameFocused: Bool

    init(folder: LibraryFolder?, save: @escaping (String) async throws -> Void) {
        self.folder = folder
        self.save = save
        _name = State(initialValue: folder?.name ?? "")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text(folder == nil ? L("新建文件夹") : L("重命名文件夹")).font(.headline)
            TextField(L("文件夹名称"), text: $name).textFieldStyle(.roundedBorder)
                .focused($nameFocused).disabled(saving)
            if let error { Text(error).font(.callout).foregroundStyle(.red).fixedSize(horizontal: false, vertical: true) }
            HStack {
                if saving { ProgressView().controlSize(.small) }
                Spacer()
                Button(L("取消")) { dismiss() }.keyboardShortcut(.cancelAction).disabled(saving)
                Button(folder == nil ? L("创建") : L("保存")) {
                    saving = true
                    error = nil
                    Task {
                        do { try await save(name.trimmingCharacters(in: .whitespacesAndNewlines)); dismiss() }
                        catch { self.error = localizedError(error); saving = false }
                    }
                }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                    .disabled(saving || name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }.padding(24).frame(width: 360).onAppear { nameFocused = true }
    }
}
