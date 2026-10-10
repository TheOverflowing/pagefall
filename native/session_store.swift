import Foundation
import Security

let service = "local.novelist.jjwxc-session"
let account = "jinjiang"
let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: account]
func fail(_ status: OSStatus) -> Never {
    // Never print item data or authentication values.
    FileHandle.standardError.write(Data("Keychain operation failed (\(status)).\n".utf8))
    exit(1)
}
guard CommandLine.arguments.count == 2 else { exit(2) }
switch CommandLine.arguments[1] {
case "set":
    // Reject oversized input before buffering an unbounded pipe. Cookie values
    // never appear in diagnostics, even for malformed input.
    var data = Data()
    while data.count <= 1_048_576 {
        let part = FileHandle.standardInput.readData(ofLength: min(65536, 1_048_577 - data.count))
        if part.isEmpty { break }
        data.append(part)
    }
    guard data.count <= 1_048_576,
          let items = (try? JSONSerialization.jsonObject(with: data)) as? [[String: Any]],
          items.count <= 256 else { exit(2) }
    var status = SecItemUpdate(query as CFDictionary, [kSecValueData as String: data] as CFDictionary)
    if status == errSecItemNotFound {
        var item = query
        item[kSecValueData as String] = data
        item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        status = SecItemAdd(item as CFDictionary, nil)
    }
    guard status == errSecSuccess else { fail(status) }
case "get":
    var lookup = query
    lookup[kSecReturnData as String] = true
    lookup[kSecMatchLimit as String] = kSecMatchLimitOne
    var result: CFTypeRef?
    let status = SecItemCopyMatching(lookup as CFDictionary, &result)
    if status == errSecItemNotFound { FileHandle.standardOutput.write(Data("[]".utf8)); exit(0) }
    guard status == errSecSuccess, let data = result as? Data else { fail(status) }
    FileHandle.standardOutput.write(data)
case "delete":
    let status = SecItemDelete(query as CFDictionary)
    guard status == errSecSuccess || status == errSecItemNotFound else { fail(status) }
default: exit(2)
}
