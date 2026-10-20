// Fixed macOS CryptoKit bridge: no files, Keychain, network or process launch.
import Foundation
import CoreFoundation
import CryptoKit
import Darwin

let frameLimit = 2 * 1024 * 1024
let plainLimit = 1024 * 1024
let boxLimit = plainLimit + 1024

func fail() -> Never {
    FileHandle.standardError.write(Data("native_crypto_unavailable\n".utf8))
    exit(1)
}
func decode(_ object: [String: Any], _ name: String, _ cap: Int) -> Data? {
    guard let string = object[name] as? String, string.utf8.count <= frameLimit,
          let bytes = Data(base64Encoded: string), bytes.count <= cap,
          bytes.base64EncodedString() == string else { return nil }
    return bytes
}
let arguments = CommandLine.arguments
if arguments.count == 2 && arguments[1] == "health" {
    FileHandle.standardOutput.write(Data("goldway-cryptokit-v1\n".utf8))
    exit(0)
}
guard arguments.count == 2 && arguments[1] == "crypt" else { fail() }
do {
    var input = Data()
    while true {
        let chunk = try FileHandle.standardInput.read(upToCount: min(8192, frameLimit + 1 - input.count)) ?? Data()
        if chunk.isEmpty { break }
        input.append(chunk)
        if input.count > frameLimit { fail() }
    }
    // Reject duplicate keys by roundtripping canonical request bytes. Parent
    // uses the exact same sorted, compact ASCII JSON representation.
    guard let object = try JSONSerialization.jsonObject(with: input) as? [String: Any],
          Set(object.keys) == Set(["version", "mode", "key", "aad", "payload"]),
          let number = object["version"] as? NSNumber,
          CFGetTypeID(number) != CFBooleanGetTypeID(), number.compare(NSNumber(value: 1)) == .orderedSame,
          let mode = object["mode"] as? String, mode == "seal" || mode == "open",
          let keyData = decode(object, "key", 32), keyData.count == 32,
          let aad = decode(object, "aad", 4096), !aad.isEmpty,
          let payload = decode(object, "payload", mode == "seal" ? plainLimit : boxLimit),
          !payload.isEmpty else { fail() }
    let canonical = try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys, .withoutEscapingSlashes])
    guard canonical == input else { fail() }
    let key = SymmetricKey(data: keyData)
    let output: Data
    if mode == "seal" {
        // No caller nonce; library generates its default random nonce.
        let box = try AES.GCM.seal(payload, using: key, authenticating: aad)
        guard let combined = box.combined, combined.count <= boxLimit else { fail() }
        output = combined
    } else {
        guard payload.count >= 28 else { fail() }
        let box = try AES.GCM.SealedBox(combined: payload)
        // Nothing is emitted until authentication has succeeded.
        output = try AES.GCM.open(box, using: key, authenticating: aad)
        guard !output.isEmpty && output.count <= plainLimit else { fail() }
    }
    let response: [String: Any] = ["version": 1, "mode": mode, "payload": output.base64EncodedString()]
    let encoded = try JSONSerialization.data(withJSONObject: response, options: [.sortedKeys, .withoutEscapingSlashes])
    guard encoded.count <= frameLimit else { fail() }
    FileHandle.standardOutput.write(encoded)
} catch {
    fail()
}
