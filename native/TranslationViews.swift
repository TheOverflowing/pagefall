import SwiftUI
import AppKit

struct JinjiangSettings: Decodable { var has_token: Bool }
struct JinjiangConnectionResult: Decodable {
    var state: String
    var challenge_id: String?
    var delivery: String?
    var retry_after: Int?
}

struct JinjiangSettingsView: View {
    @ObservedObject var model: Model
    @Environment(\.dismiss) private var dismiss
    @State private var token = ""
    @State private var hasToken = false
    @State private var needsReconnect = false
    @State private var deleteToken = false
    @State private var loading = true
    @State private var saving = false
    @State private var error: String?
    @State private var loginShown = false
    @State private var advanced = false
    @State private var account = ""
    @State private var password = ""
    @State private var code = ""
    @State private var challengeID: String?
    @State private var delivery = "phone"
    @State private var retryAt = Date.distantPast
    @State private var codeSent = false
    @State private var codeRequested = false
    @FocusState private var focus: LoginField?
    private enum LoginField { case account, password, code }

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            VStack(alignment: .leading, spacing: 8) {
                Text(L("晋江下载设置")).font(.title2.weight(.semibold))

            }
            if loginShown {
                loginForm
            } else {
                HStack(spacing: 12) {
                    Image(systemName: hasToken && !needsReconnect ? "checkmark.circle.fill" : "person.crop.circle")
                        .font(.title2).foregroundStyle(hasToken && !needsReconnect ? Color.accentColor : Color.secondary)
                    VStack(alignment: .leading, spacing: 4) {
                        Text(L(needsReconnect ? "App 账号需要重新连接" : hasToken ? "App 账号已连接" : "连接晋江 App 账号")).font(.headline)
                    }
                    Spacer()
                    Button(L(hasToken ? "重新连接…" : "连接账号…")) {
                        loginShown = true; advanced = false; error = nil; focus = .account
                    }.disabled(loading || saving || model.working)
                }
            }
            if !loginShown {
                DisclosureGroup(L("高级设置"), isExpanded: $advanced) {
                    VStack(alignment: .leading, spacing: 10) {
                        Text(L("手动填写 Android Token")).font(.subheadline.weight(.medium))
                        SecureField(hasToken ? L("留空保留已保存的 Token") : L("输入晋江 Android Token"), text: $token)
                            .textFieldStyle(.roundedBorder).autocorrectionDisabled().disabled(loading || saving || deleteToken)
                            .accessibilityLabel(L("晋江 Android Token，仅写入"))
                        if hasToken { Toggle(L("移除已保存的 Token"), isOn: $deleteToken).toggleStyle(.checkbox).disabled(saving) }
                    }.padding(.top, 10)
                }.font(.subheadline)
            }
            if let error { Text(LM(error)).font(.caption).foregroundStyle(.red).fixedSize(horizontal: false, vertical: true) }
            HStack {
                Button(L(loginShown ? "取消" : "完成")) { close() }.keyboardShortcut(.cancelAction).disabled(saving)
                Spacer()
                if loading || saving { ProgressView().controlSize(.small).accessibilityLabel(L("正在连接晋江…")) }
                if loginShown {
                    Button(L(saving ? "正在连接晋江…" : challengeID == nil ? "登录并连接" : "验证并连接")) { connect() }
                        .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                        .disabled(loading || saving || model.working || account.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || password.isEmpty || (challengeID != nil && code.count < 4))
                } else if advanced {
                    Button(L(saving ? "正在保存…" : "保存设置")) { saveManual() }
                        .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                        .disabled(loading || saving || model.working)
                }
            }
        }.padding(26).frame(width: 560)
            .interactiveDismissDisabled(saving)
            .task {
                defer { loading = false }
                do {
                    let settings: JinjiangSettings = try await model.request("/api/jinjiang/settings")
                    let status: JinjiangConnectionResult = try await model.request("/api/jinjiang/connection")
                    hasToken = settings.has_token; needsReconnect = status.state == "needs_reconnect"
                } catch { self.error = localizedError(error) }
            }
            .onDisappear { clearInputs() }
    }

    private var loginForm: some View {
        VStack(alignment: .leading, spacing: 14) {
            if challengeID == nil {
                VStack(alignment: .leading, spacing: 6) {
                    Text(L("晋江账号")).font(.subheadline.weight(.medium))
                    TextField(L("手机号、邮箱或用户名"), text: $account).textFieldStyle(.roundedBorder)
                        .autocorrectionDisabled().focused($focus, equals: .account).disabled(saving)
                }
                VStack(alignment: .leading, spacing: 6) {
                    Text(L("密码")).font(.subheadline.weight(.medium))
                    SecureField(L("输入晋江密码"), text: $password).textFieldStyle(.roundedBorder)
                        .focused($focus, equals: .password).disabled(saving)
                }

            } else {
                Text(L(delivery == "email" ? "晋江需要验证邮箱" : "晋江需要验证手机")).font(.headline)
                Text(L("请先获取验证码，再填写收到的验证码完成连接。"))
                    .font(.subheadline).foregroundStyle(.secondary)
                HStack {
                    TextField(L("验证码"), text: $code).textFieldStyle(.roundedBorder)
                        .autocorrectionDisabled().focused($focus, equals: .code).disabled(saving)
                    TimelineView(.periodic(from: .now, by: 1)) { context in
                        let remaining = max(0, Int(ceil(retryAt.timeIntervalSince(context.date))))
                        Button(remaining > 0 ? L("\(remaining) 秒后重发") : L(codeRequested ? "重新发送" : "获取验证码")) { sendCode() }
                            .disabled(saving || remaining > 0)
                    }
                }
                if codeSent { Text(L("验证码已发送，请查看晋江绑定的手机或邮箱。")) .font(.caption).foregroundStyle(.secondary) }
                else if codeRequested { Text(L("验证码请求已提交。如已收到，请直接填写；暂未收到可稍后重发。")) .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true) }
                Button(L("换个账号")) { resetLogin() }.buttonStyle(.link).disabled(saving)
            }
        }
    }

    private func connect() {
        saving = true; error = nil
        Task {
            defer { saving = false }
            do {
                var data: [String: Any] = ["account": account, "password": password]
                if let challengeID { data["challenge_id"] = challengeID; data["code"] = code }
                let result: JinjiangConnectionResult = try await model.request("/api/jinjiang/connect", data: data)
                if result.state == "connected" {
                    hasToken = true; needsReconnect = false; loginShown = false; clearInputs()
                } else if result.state == "verification_required", let identifier = result.challenge_id {
                    challengeID = identifier; delivery = result.delivery ?? "phone"; focus = .code
                } else { error = L("操作未完成，请稍后重试。") }
            } catch {
                self.error = localizedError(error)
                if let api = error as? APIError, api.message == "本次验证已过期，请重新连接。" { resetLogin() }
            }
        }
    }

    private func sendCode() {
        guard let challengeID else { return }
        saving = true; error = nil
        Task {
            defer { saving = false }
            do {
                let result: JinjiangConnectionResult = try await model.request("/api/jinjiang/verification", data: ["challenge_id": challengeID])
                codeSent = result.state == "code_sent"; codeRequested = ["code_sent", "code_requested"].contains(result.state); retryAt = .now.addingTimeInterval(TimeInterval(result.retry_after ?? 60)); focus = .code
            } catch { self.error = localizedError(error); retryAt = .now.addingTimeInterval(60) }
        }
    }

    private func cancelChallenge() {
        guard let challengeID else { return }
        Task { let _: Acknowledgement? = try? await model.request("/api/jinjiang/cancel-connection", data: ["challenge_id": challengeID]) }
    }
    private func resetLogin() { cancelChallenge(); challengeID = nil; code = ""; password = ""; codeSent = false; codeRequested = false; retryAt = .distantPast; focus = .password }
    private func clearInputs() { token = ""; password = ""; account = ""; code = ""; challengeID = nil }
    private func close() { cancelChallenge(); clearInputs(); dismiss() }
    private func saveManual() {
        saving = true; error = nil
        Task {
            defer { saving = false }
            do {
                var data: [String: Any] = ["delete_token": deleteToken]
                if !token.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { data["token"] = token.trimmingCharacters(in: .whitespacesAndNewlines) }
                let _: Acknowledgement = try await model.request("/api/jinjiang/settings", data: data)
                clearInputs(); dismiss()
            } catch { self.error = localizedError(error) }
        }
    }
}

struct TranslationProvider: Decodable, Identifiable {
    var id: String
    var name: String
    var model: String?
    var key_configured: Bool?
}
struct TranslationSettings: Decodable {
    var provider: String
    var model: String
    var source_language: String
    var target_language: String
    var has_key: Bool?
    var providers: [TranslationProvider]
}
struct TranslationProgress: Decodable {
    var book_id: String?
    var running: Bool?
    var done: Int?
    var total: Int?
    var failed: Int?
    var message: String?
    var source_language: String?
    var target_language: String?
}
struct BookTranslation: Decodable {
    var status: String?
    var done: Int?
    var total: Int?
    var failed: Int?
    var stale: Int?
    var source_language: String?
    var target_language: String?
    var provider: String?
    var model: String?
    var glossary_count: Int?
}
struct ChapterTranslation: Decodable {
    var status: String?
    var text: String?
    var error: String?
    var source_language: String?
    var target_language: String?
    var updated_at: String?
    var translated_title: String?
    var quality_issues_count: Int?
    var quality_issues: [TranslationQualityIssue]?
    var label: String {
        switch status {
        case "done", "translated": return L("已翻译")
        case "stale": return L("译文需更新")
        case "translating", "running": return L("正在翻译")
        case "paused": return L("翻译已暂停")
        case "failed", "error": return L("翻译需重试")
        default: return L("尚未翻译")
        }
    }
}
struct TranslationQualityIssue: Decodable {
    var kind: String?
    var message: String?
    var source: String?
    var target: String?
    var segment_id: String?
}
struct GlossaryAlias: Codable { var source: String; var target: String }
struct GlossaryTerm: Codable, Identifiable {
    var id = UUID()
    var source: String
    var target: String
    var kind: String?
    var locked: Bool?
    var aliases: [GlossaryAlias]?
    var notes: String?
    enum CodingKeys: String, CodingKey { case source, target, kind, locked, aliases, notes }
}
struct GlossaryResponse: Decodable { var book_id: String; var terms: [GlossaryTerm] }

enum TranslationLanguage {
    static let sourceValues = ["zh", "en", "ja", "ko", "fr", "de", "es"]
    static let targetValues = ["zh-Hans", "zh-Hant", "en", "ja", "ko", "fr", "de", "es"]
    static func label(_ code: String) -> String {
        LS(["auto": "自动检测", "zh": "中文", "zh-Hans": "简体中文", "zh-Hant": "繁体中文", "en": "英语", "ja": "日语", "ko": "韩语", "fr": "法语", "de": "德语", "es": "西班牙语", "ru": "俄语"][code] ?? code)
    }
}

struct TranslationSettingsView: View {
    @ObservedObject var model: Model
    let book: Book?
    @ObservedObject private var localization = Localization.shared
    @Environment(\.dismiss) private var dismiss
    @State private var settings: TranslationSettings?
    @State private var provider = "deepseek"
    @State private var modelName = ""
    @State private var sourceLanguage = "auto"
    @State private var targetLanguage = "zh-Hans"
    @State private var apiKey = ""
    @State private var deleteKey = false
    @State private var loading = true
    @State private var saving = false
    @State private var error: String?
    private var providerName: String { settings?.providers.first(where: { $0.id == provider })?.name ?? provider }
    private var hasKey: Bool {
        settings?.providers.first(where: { $0.id == provider })?.key_configured ?? (settings?.provider == provider && settings?.has_key == true)
    }
    private var sameLanguage: Bool { sourceLanguage != "auto" && sourceLanguage.split(separator: "-").first?.lowercased() == targetLanguage.split(separator: "-").first?.lowercased() }
    private var canSave: Bool { !loading && !saving && !modelName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !sameLanguage }
    private var canTranslate: Bool { canSave && !model.working && !deleteKey && (hasKey || !apiKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty) }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                VStack(alignment: .leading, spacing: 5) {
                    Text(book == nil ? L("AI 翻译设置") : book?.platform == "local" ? L("翻译文本") : L("翻译小说")).font(.title2.weight(.semibold))
                    if let book { Text(book.title).font(.subheadline).foregroundStyle(.secondary).lineLimit(2) }
                }
                Spacer()
                if loading { ProgressView().controlSize(.small) }
            }
            if !loading, settings != nil {
                Form {
                    Picker(L("服务提供商"), selection: Binding(get: { provider }, set: { value in
                        guard value != provider else { return }
                        provider = value; apiKey = ""; deleteKey = false
                        modelName = settings?.providers.first(where: { $0.id == value })?.model ?? ""
                    })) {
                        ForEach(settings?.providers ?? []) { option in Text(option.name).tag(option.id) }
                    }
                    TextField(L("模型"), text: $modelName).textFieldStyle(.roundedBorder)
                        .autocorrectionDisabled().help(L("填写服务提供商支持的模型 ID。"))
                    VStack(alignment: .leading, spacing: 7) {
                        SecureField(hasKey ? L("留空保留已保存的 API Key") : L("输入 API Key"), text: $apiKey)
                            .textFieldStyle(.roundedBorder).autocorrectionDisabled().disabled(deleteKey)
                            .accessibilityLabel(L("API Key，仅写入"))
                        HStack {
                            Label(hasKey ? L("已保存 API Key") : L("尚未设置 API Key"), systemImage: hasKey ? "key.fill" : "key")
                                .font(.caption).foregroundStyle(.secondary)
                            Spacer()
                            if hasKey { Toggle(L("移除已保存的密钥"), isOn: $deleteKey).font(.caption).toggleStyle(.checkbox) }
                        }
                    }
                    Picker(L("原文语言"), selection: $sourceLanguage) {
                        Text(TranslationLanguage.label("auto")).tag("auto")
                        ForEach(TranslationLanguage.sourceValues, id: \.self) { Text(TranslationLanguage.label($0)).tag($0) }
                    }
                    Picker(L("译文语言"), selection: $targetLanguage) {
                        ForEach(TranslationLanguage.targetValues, id: \.self) { Text(TranslationLanguage.label($0)).tag($0) }
                    }
                }.formStyle(.grouped).fixedSize(horizontal: false, vertical: true)

                if sameLanguage { Text(L("请选择不同的原文与译文语言。")) .font(.caption).foregroundStyle(.orange) }
            }
            if let error { Text(LM(error)).font(.caption).foregroundStyle(.red).fixedSize(horizontal: false, vertical: true) }
            HStack(spacing: 12) {
                Button(L("取消")) { apiKey = ""; dismiss() }.keyboardShortcut(.cancelAction).disabled(saving)
                Spacer()
                if book != nil {
                    Button(L("仅保存设置")) { save(scope: nil) }.disabled(!canSave)
                    if book?.platform != "local" { Button(L("首章试译")) { save(scope: "first") }.disabled(!canTranslate) }
                    Button(saving ? L("正在保存…") : book?.platform == "local" ? L("开始翻译") : L("翻译全部已保存章节")) { save(scope: "all") }
                        .buttonStyle(.borderedProminent).disabled(!canTranslate)
                } else {
                    Button(saving ? L("正在保存…") : L("保存设置")) { save(scope: nil) }
                        .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction).disabled(!canSave)
                }
            }
        }.padding(26).frame(width: 650)
        .environment(\.locale, localization.locale)
        .interactiveDismissDisabled(saving)
        .task { await load() }
        .onDisappear { apiKey = "" }
    }

    private func load() async {
        do {
            let result: TranslationSettings = try await model.request("/api/translation/settings")
            settings = result
            provider = result.provider; modelName = result.model
            sourceLanguage = book?.translation?.source_language ?? result.source_language
            let target = book?.translation?.target_language ?? result.target_language
            targetLanguage = target == "zh" ? "zh-Hans" : target
            if let book, book.translation == nil, sourceLanguage == "auto" {
                let knownChinesePlatform = ["jjwxc", "gongzicp", "fanqie"].contains(book.platform ?? "")
                let legacyJinjiangBook = book.platform == nil && !book.id.isEmpty && book.id.allSatisfy(\.isNumber)
                if knownChinesePlatform || legacyJinjiangBook {
                    sourceLanguage = "zh"
                    if targetLanguage.split(separator: "-").first?.lowercased() == "zh" {
                        targetLanguage = "en"
                    }
                }
            }
        } catch { self.error = localizedError(error) }
        loading = false
    }
    private func save(scope: String?) {
        saving = true; error = nil
        Task {
            defer { saving = false }
            var payload: [String: Any] = ["provider": provider, "model": modelName.trimmingCharacters(in: .whitespacesAndNewlines), "source_language": sourceLanguage, "target_language": targetLanguage]
            if deleteKey { payload["delete_key"] = true }
            else if !apiKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { payload["api_key"] = apiKey.trimmingCharacters(in: .whitespacesAndNewlines) }
            do {
                let _: Acknowledgement = try await model.request("/api/translation/settings", data: payload)
                apiKey = ""
                settings = try await model.request("/api/translation/settings")
                if let scope, let book {
                    let _: Acknowledgement = try await model.request("/api/translate", data: ["book_id": book.id, "source_language": sourceLanguage, "target_language": targetLanguage, "scope": scope])
                }
                await model.refreshState()
                dismiss()
            } catch { self.error = localizedError(error) }
        }
    }
}

struct GlossaryView: View {
    @ObservedObject var model: Model
    let book: Book
    @ObservedObject private var localization = Localization.shared
    @Environment(\.dismiss) private var dismiss
    @State private var terms: [GlossaryTerm] = []
    @State private var loading = true
    @State private var loaded = false
    @State private var saving = false
    @State private var originalTerms = Data()
    @State private var confirmDiscard = false
    @State private var error: String?
    private var editable: Bool { loaded && !loading && !saving && !model.translating(book.id) }
    private var valid: Bool { terms.allSatisfy { !$0.source.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !$0.target.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty } }
    private var changed: Bool { !loading && encodedTerms != originalTerms }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                VStack(alignment: .leading, spacing: 5) { Text(L("人物与术语表")).font(.title2.weight(.semibold)); Text(book.title).font(.subheadline).foregroundStyle(.secondary).lineLimit(1) }
                Spacer()
                Button { terms.append(GlossaryTerm(source: "", target: "", kind: "term", locked: true)) } label: { Label(L("添加术语"), systemImage: "plus") }.disabled(!editable)
            }
            if loading { ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity) }
            else if terms.isEmpty {
                ContentUnavailableView(L("还没有术语"), systemImage: "character.book.closed", description: Text(L("翻译会逐步整理人物与术语，也可以先手动添加指定译名。")))
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    LazyVStack(spacing: 14) {
                        ForEach($terms) { $term in
                            VStack(alignment: .leading, spacing: 8) {
                                HStack(spacing: 10) {
                                    TextField(L("原文"), text: $term.source).accessibilityLabel(L("术语原文"))
                                    Image(systemName: "arrow.right").foregroundStyle(.tertiary)
                                    TextField(L("译名"), text: $term.target).accessibilityLabel(L("术语译名"))
                                        .onChange(of: term.target) { _, _ in term.locked = true }
                                    Toggle(L("锁定"), isOn: Binding(get: { term.locked ?? false }, set: { term.locked = $0 })).toggleStyle(.checkbox).fixedSize()
                                    Button { terms.removeAll { $0.id == term.id } } label: { Image(systemName: "minus.circle") }
                                        .buttonStyle(.plain).help(L("删除术语")).accessibilityLabel(L("删除术语"))
                                }
                                TextField(L("备注（可选）"), text: Binding(get: { term.notes ?? "" }, set: { term.notes = $0 }))
                                    .font(.caption).accessibilityLabel(L("术语备注"))
                                if let aliases = term.aliases, !aliases.isEmpty {
                                    Text(L("别名：\(aliases.map { $0.source + " → " + $0.target }.joined(separator: " · "))")).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                                }
                            }.textFieldStyle(.roundedBorder).disabled(!editable)
                            Divider()
                        }
                    }
                }
            }
            if model.translating(book.id) { Text(L("翻译暂停或完成后，可以编辑术语表。")) .font(.caption).foregroundStyle(.secondary) }
            if let error { Text(LM(error)).font(.caption).foregroundStyle(.red) }
            HStack {
                Text(L("术语数量：\(terms.count)")).font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button(L("关闭")) { if changed { confirmDiscard = true } else { dismiss() } }.keyboardShortcut(.cancelAction).disabled(saving)
                Button(saving ? L("正在保存…") : L("保存术语表")) { save() }.buttonStyle(.borderedProminent).keyboardShortcut("s", modifiers: .command)
                    .disabled(!editable || !changed || !valid)
            }
        }.padding(26).frame(width: 740, height: 540)
        .environment(\.locale, localization.locale)
        .interactiveDismissDisabled(changed || saving)
        .task { await load() }
        .onChange(of: changed) { _, value in AppDelegate.editorHasUnsavedChanges = value }
        .onDisappear { AppDelegate.editorHasUnsavedChanges = false }
        .alert(L("放弃未保存的术语修改？"), isPresented: $confirmDiscard) {
            Button(L("继续编辑"), role: .cancel) {}
            Button(L("放弃修改"), role: .destructive) { dismiss() }
        }
    }
    private var encodedTerms: Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = .sortedKeys
        return (try? encoder.encode(terms)) ?? Data()
    }
    private func load() async {
        do {
            let response: GlossaryResponse = try await model.request("/api/translation/glossary?book_id=" + book.id)
            terms = response.terms
            loaded = true
        } catch { self.error = localizedError(error) }
        originalTerms = encodedTerms; loading = false
    }
    private func save() {
        saving = true; error = nil
        Task {
            defer { saving = false }
            do {
                let termsData = try JSONSerialization.jsonObject(with: JSONEncoder().encode(terms))
                let _: Acknowledgement = try await model.request("/api/translation/glossary", data: ["book_id": book.id, "terms": termsData])
                await model.refreshState()
                if model.detail?.id == book.id { model.openBook(id: book.id) }
                originalTerms = encodedTerms; dismiss()
            } catch { self.error = localizedError(error) }
        }
    }
}
