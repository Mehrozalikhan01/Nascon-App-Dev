import Flutter
import UIKit
import Security

@main
@objc class AppDelegate: FlutterAppDelegate, FlutterImplicitEngineDelegate {
  override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  ) -> Bool {
    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }

  func didInitializeImplicitFlutterEngine(_ engineBridge: FlutterImplicitEngineBridge) {
    GeneratedPluginRegistrant.register(with: engineBridge.pluginRegistry)
    let channel = FlutterMethodChannel(
      name: "nascon.secure_storage",
      binaryMessenger: engineBridge.applicationRegistrar.messenger()
    )
    channel.setMethodCallHandler { call, result in
      do {
        switch call.method {
        case "readToken":
          result(try Self.readToken())
        case "writeToken":
          guard let arguments = call.arguments as? [String: Any],
                let token = arguments["token"] as? String,
                !token.isEmpty else {
            result(FlutterError(code: "INVALID_TOKEN", message: "Token is empty", details: nil))
            return
          }
          try Self.writeToken(token)
          result(nil)
        case "deleteToken":
          try Self.deleteToken()
          result(nil)
        default:
          result(FlutterMethodNotImplemented)
        }
      } catch {
        result(FlutterError(code: "SECURE_STORAGE_ERROR", message: "Could not access secure token storage", details: error.localizedDescription))
      }
    }
  }

  private static var tokenQuery: [String: Any] {
    [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: "com.nascon.league.mobile_app.secure",
      kSecAttrAccount as String: "access_token"
    ]
  }

  private static func readToken() throws -> String? {
    var query = tokenQuery
    query[kSecReturnData as String] = true
    query[kSecMatchLimit as String] = kSecMatchLimitOne
    var item: CFTypeRef?
    let status = SecItemCopyMatching(query as CFDictionary, &item)
    if status == errSecItemNotFound { return nil }
    guard status == errSecSuccess, let data = item as? Data,
          let token = String(data: data, encoding: .utf8) else {
      throw NSError(domain: NSOSStatusErrorDomain, code: Int(status))
    }
    return token
  }

  private static func writeToken(_ token: String) throws {
    let data = Data(token.utf8)
    let status = SecItemUpdate(tokenQuery as CFDictionary, [kSecValueData as String: data] as CFDictionary)
    if status == errSecItemNotFound {
      var item = tokenQuery
      item[kSecValueData as String] = data
      item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
      let addStatus = SecItemAdd(item as CFDictionary, nil)
      guard addStatus == errSecSuccess else {
        throw NSError(domain: NSOSStatusErrorDomain, code: Int(addStatus))
      }
    } else if status != errSecSuccess {
      throw NSError(domain: NSOSStatusErrorDomain, code: Int(status))
    }
  }

  private static func deleteToken() throws {
    let status = SecItemDelete(tokenQuery as CFDictionary)
    guard status == errSecSuccess || status == errSecItemNotFound else {
      throw NSError(domain: NSOSStatusErrorDomain, code: Int(status))
    }
  }
}
