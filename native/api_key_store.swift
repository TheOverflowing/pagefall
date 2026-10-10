import Foundation
import LocalAuthentication
import Security

// A separate service from Jinjiang sessions. No enumeration or access to keys
// owned by browsers, other apps, or other providers is supported.
guard CommandLine.arguments.count == 3 else { exit(2) }
let command = CommandLine.arguments[1]
let provider = CommandLine.arguments[2]
guard ["deepseek", "openrouter", "jjwxc"].contains(provider) else { exit(2) }
let service = provider == "jjwxc" ? "local.novelist.jinjiang-android-token" : "local.novelist.translation-api-keys"
let query: [String: Any] = [
    kSecClass as String: kSecClassGenericPassword,
    kSecAttrService as String: service,
    kSecAttrAccount as String: provider,
    kSecAttrSynchronizable as String: false
]

func fail(_ status: OSStatus) -> Never {
    FileHandle.standardError.write(Data("Keychain operation failed (\(status)).\n".utf8))
    exit(1)
}

switch command {
case "set":
    var payload = Data()
    while payload.count <= 16384 {
        let part = FileHandle.standardInput.readData(ofLength: min(4096, 16385 - payload.count))
        if part.isEmpty { break }
        payload.append(part)
    }
    guard payload.count <= 16384,
          let object = (try? JSONSerialization.jsonObject(with: payload)) as? [String: Any],
          object.keys.count == 1,
          let supplied = object["key"] as? String else { exit(2) }
    let key = supplied.trimmingCharacters(in: .whitespacesAndNewlines)
    guard (12...4096).contains(key.utf8.count),
          key.unicodeScalars.allSatisfy({ (33...126).contains(Int($0.value)) }) else { exit(2) }
    let data = Data(key.utf8)
    var status = SecItemUpdate(query as CFDictionary, [kSecValueData as String: data] as CFDictionary)
    if status == errSecItemNotFound {
        var item = query
        item[kSecValueData as String] = data
        item[kSecAttrLabel as String] = provider == "jjwxc" ? "拾页 · 晋江 Android Token" : "拾页 · \(provider == "deepseek" ? "DeepSeek" : "OpenRouter") API Key"
        item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        status = SecItemAdd(item as CFDictionary, nil)
    }
    guard status == errSecSuccess else { fail(status) }
case "has":
    var lookup = query
    lookup[kSecReturnAttributes as String] = true
    lookup[kSecMatchLimit as String] = kSecMatchLimitOne
    let authentication = LAContext()
    authentication.interactionNotAllowed = true
    lookup[kSecUseAuthenticationContext as String] = authentication
    var result: CFTypeRef?
    let status = SecItemCopyMatching(lookup as CFDictionary, &result)
    guard status == errSecSuccess || status == errSecItemNotFound else { fail(status) }
    let response = status == errSecSuccess ? "{\"has_key\":true}" : "{\"has_key\":false}"
    FileHandle.standardOutput.write(Data(response.utf8))
case "get":
    var lookup = query
    lookup[kSecReturnData as String] = true
    lookup[kSecMatchLimit as String] = kSecMatchLimitOne
    var result: CFTypeRef?
    let status = SecItemCopyMatching(lookup as CFDictionary, &result)
    if status == errSecItemNotFound { exit(0) }
    guard status == errSecSuccess, let data = result as? Data, data.count <= 4096 else { fail(status) }
    FileHandle.standardOutput.write(data)
case "delete":
    let status = SecItemDelete(query as CFDictionary)
    guard status == errSecSuccess || status == errSecItemNotFound else { fail(status) }
default:
    exit(2)
}
