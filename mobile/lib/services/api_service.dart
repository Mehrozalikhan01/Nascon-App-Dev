import 'dart:convert';
import 'dart:math';
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

class ApiService {
  static const MethodChannel _secureChannel = MethodChannel('nascon.secure_storage');
  static const MethodChannel _localChannel = MethodChannel('nascon.local_store');
  static const String defaultBaseUrl = "http://localhost:8080";
  static String baseUrl = defaultBaseUrl;
  static String? currentToken;
  static String currentRole = "viewer";
  static String currentEmail = "viewer@nascon.test";
  static String? _deviceId;

  static bool get _usesAndroidStorage => !kIsWeb && defaultTargetPlatform == TargetPlatform.android;

  static Future<String?> _readLocalValue(SharedPreferences prefs, String key) async {
    if (_usesAndroidStorage) {
      try {
        return await _localChannel.invokeMethod<String>('get', {"key": key});
      } catch (_) {}
    }
    return prefs.getString(key);
  }

  static Future<void> _writeLocalValue(SharedPreferences prefs, String key, String value) async {
    if (_usesAndroidStorage) {
      try {
        await _localChannel.invokeMethod<void>('set', {"key": key, "value": value});
        return;
      } catch (_) {}
    }
    await prefs.setString(key, value);
  }

  static Future<void> _removeLocalValue(SharedPreferences prefs, String key) async {
    if (_usesAndroidStorage) {
      try {
        await _localChannel.invokeMethod<void>('remove', {"key": key});
        return;
      } catch (_) {}
    }
    await prefs.remove(key);
  }

  static Future<void> init() async {
    final prefs = await SharedPreferences.getInstance();
    final saved = prefs.getString("server_url");
    var candidate = saved ?? defaultBaseUrl;
    if (candidate.contains("10.0.2.2")) {
      candidate = "http://localhost:8080";
    }
    try {
      baseUrl = _normalizeServerUrl(candidate);
    } on FormatException {
      // Old builds accepted arbitrary URLs. Do not silently reuse a public
      // cleartext endpoint that could expose the restored bearer token.
      baseUrl = defaultBaseUrl;
      await prefs.remove("server_url");
    }
    if (_usesAndroidStorage) {
      currentToken = await _secureChannel.invokeMethod<String>('readToken');
      // Migrate the token created by earlier builds into Android Keystore.
      final legacyToken = prefs.getString("auth_token");
      if (currentToken == null && legacyToken != null) {
        await _secureChannel.invokeMethod<void>('writeToken', {"token": legacyToken});
        await prefs.remove("auth_token");
        currentToken = legacyToken;
      }
    } else {
      currentToken = prefs.getString("auth_token");
    }
    currentRole = prefs.getString("auth_role") ?? "viewer";
    currentEmail = prefs.getString("auth_email") ?? "";
  }

  static Future<void> setServerUrl(String url) async {
    final normalized = _normalizeServerUrl(url);
    baseUrl = normalized;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString("server_url", normalized);
  }

  static String _normalizeServerUrl(String url) {
    final parsed = Uri.tryParse(url.trim());
    if (parsed == null ||
        !const {"http", "https"}.contains(parsed.scheme) ||
        parsed.host.isEmpty ||
        parsed.userInfo.isNotEmpty ||
        parsed.query.isNotEmpty ||
        parsed.fragment.isNotEmpty ||
        (parsed.path.isNotEmpty && parsed.path != "/")) {
      throw const FormatException("Enter a valid server address, such as http://192.168.1.20:8080");
    }
    final host = parsed.host.toLowerCase();
    if (parsed.scheme == "http" && !_isLocalOrPrivateHost(host)) {
      throw const FormatException("Use HTTPS for public servers. Unencrypted HTTP is allowed only for local demo servers.");
    }
    final normalized = parsed.replace(path: "", query: "", fragment: "").toString().replaceFirst(RegExp(r"/$"), "");
    return normalized;
  }

  static bool _isLocalOrPrivateHost(String host) {
    if (host == "localhost" || host == "::1" || host == "10.0.2.2" || host.endsWith(".local")) return true;
    final parts = host.split('.');
    if (parts.length != 4) return false;
    final octets = parts.map(int.tryParse).toList();
    if (octets.any((part) => part == null || part < 0 || part > 255)) return false;
    final first = octets[0]!;
    final second = octets[1]!;
    return first == 10 || first == 127 ||
        (first == 192 && second == 168) ||
        (first == 172 && second >= 16 && second <= 31) ||
        (first == 169 && second == 254);
  }

  static Future<void> saveAuth(String token, String role, String email) async {
    currentToken = token;
    currentRole = role;
    currentEmail = email;
    final prefs = await SharedPreferences.getInstance();
    if (_usesAndroidStorage) {
      try {
        await _secureChannel.invokeMethod<void>('writeToken', {"token": token});
        await prefs.remove("auth_token");
      } catch (_) {
        await prefs.setString("auth_token", token);
      }
    } else {
      await prefs.setString("auth_token", token);
    }
    await prefs.setString("auth_role", role);
    await prefs.setString("auth_email", email);
  }

  static Future<void> logout() async {
    currentToken = null;
    currentRole = "viewer";
    currentEmail = "";
    final prefs = await SharedPreferences.getInstance();
    if (_usesAndroidStorage) {
      try {
        await _secureChannel.invokeMethod<void>('deleteToken');
        await prefs.remove("auth_token");
      } catch (_) {
        await prefs.remove("auth_token");
      }
    } else {
      await prefs.remove("auth_token");
    }
    await prefs.remove("auth_role");
    await prefs.remove("auth_email");
  }

  static Future<Map<String, dynamic>> login(String email, String password) async {
    final response = await http.post(
      Uri.parse("$baseUrl/api/v1/auth/login"),
      headers: const {"Content-Type": "application/json"},
      body: jsonEncode({"email": email.trim(), "password": password}),
    ).timeout(const Duration(seconds: 10));
    final decoded = jsonDecode(response.body);
    if (response.statusCode != 200 || decoded is! Map<String, dynamic>) {
      final error = decoded is Map ? decoded["error"] : null;
      final code = error is Map ? error["code"]?.toString() : null;
      throw ApiException(
        code ?? "LOGIN_FAILED",
        code == "INVALID_CREDENTIALS"
            ? "That email and password do not match."
            : "We could not sign you in. Check your server connection and try again.",
      );
    }
    final user = Map<String, dynamic>.from(decoded["user"] as Map);
    await saveAuth(
      decoded["access_token"].toString(),
      user["role"].toString(),
      user["email"].toString(),
    );
    return user;
  }

  // --- LOCAL CACHING FOR DESK CHECK (NFR-4) ---
  static Future<void> cacheData(String key, dynamic data) async {
    final prefs = await SharedPreferences.getInstance();
    await _writeLocalValue(prefs, "cache_$key", jsonEncode(data));
  }

  static Future<dynamic> getCachedData(String key) async {
    final prefs = await SharedPreferences.getInstance();
    final raw = await _readLocalValue(prefs, "cache_$key");
    if (raw != null) {
      try {
        return jsonDecode(raw);
      } catch (_) {}
    }
    return null;
  }

  static Future<Map<String, dynamic>> loadOverview({required bool online}) async {
    final cacheKey = "mobile_overview_${currentEmail.isEmpty ? 'signed_out' : currentEmail}";
    if (online) {
      try {
        final res = await http
            .get(Uri.parse("$baseUrl/audit/v1/mobile/overview"), headers: currentToken == null
                ? const {}
                : {"Authorization": "Bearer $currentToken"})
            .timeout(const Duration(seconds: 8));
        if (res.statusCode == 200) {
          final data = Map<String, dynamic>.from(jsonDecode(res.body) as Map);
          await cacheData(cacheKey, data);
          return data;
        }
      } catch (_) {
        // A last-known copy is still useful when the connection drops.
      }
    }
    final cached = await getCachedData(cacheKey);
    return cached is Map ? Map<String, dynamic>.from(cached) : <String, dynamic>{};
  }

  static Future<String> getDeviceId() async {
    if (_deviceId != null) return _deviceId!;
    final prefs = await SharedPreferences.getInstance();
    _deviceId = prefs.getString("device_id");
    if (_deviceId == null || _deviceId!.isEmpty) {
      final randomPart = Random.secure().nextInt(1 << 32).toRadixString(16);
      _deviceId = "device-${DateTime.now().microsecondsSinceEpoch}-$randomPart";
      await prefs.setString("device_id", _deviceId!);
    }
    return _deviceId!;
  }

  static Future<Map<String, dynamic>?> getCheckoutDraft() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = await _readLocalValue(prefs, "checkout_draft");
    if (raw == null) return null;
    try {
      return Map<String, dynamic>.from(jsonDecode(raw) as Map);
    } catch (_) {
      await _removeLocalValue(prefs, "checkout_draft");
      return null;
    }
  }

  static Future<void> saveCheckoutDraft(Map<String, dynamic> data) async {
    final prefs = await SharedPreferences.getInstance();
    await _writeLocalValue(prefs, "checkout_draft", jsonEncode(data));
  }

  static Future<void> clearCheckoutDraft() async {
    final prefs = await SharedPreferences.getInstance();
    await _removeLocalValue(prefs, "checkout_draft");
  }

  // --- OFFLINE ACTION QUEUE ---
  static Future<List<Map<String, dynamic>>> getPendingQueue() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = await _readLocalValue(prefs, "offline_action_queue");
    if (raw != null) {
      try {
        final List<dynamic> list = jsonDecode(raw);
        return list.map((e) => Map<String, dynamic>.from(e)).toList();
      } catch (_) {}
    }
    return [];
  }

  static Future<void> queueOfflineAction(Map<String, dynamic> action) async {
    final queue = await getPendingQueue();
    queue.add(action);
    final prefs = await SharedPreferences.getInstance();
    await _writeLocalValue(prefs, "offline_action_queue", jsonEncode(queue));
  }

  static Future<void> _savePendingQueue(List<Map<String, dynamic>> queue) async {
    final prefs = await SharedPreferences.getInstance();
    if (queue.isEmpty) {
      await _removeLocalValue(prefs, "offline_action_queue");
    } else {
      await _writeLocalValue(prefs, "offline_action_queue", jsonEncode(queue));
    }
  }

  static Future<void> clearQueue() async {
    final prefs = await SharedPreferences.getInstance();
    await _removeLocalValue(prefs, "offline_action_queue");
  }

  // --- API CALLS ---
  static Future<bool> checkHealth() async {
    try {
      final res = await http.get(Uri.parse("$baseUrl/health")).timeout(const Duration(seconds: 4));
      if (res.statusCode != 200) return false;
      final body = jsonDecode(res.body);
      return body is Map && body["status"] == "ok";
    } catch (_) {
      return false;
    }
  }

  static Future<Map<String, dynamic>?> syncOfflineActions(String deviceId) async {
    final queue = await getPendingQueue();
    if (queue.isEmpty) return null;

    final results = <Map<String, dynamic>>[];
    var applied = 0;
    var rejected = 0;
    var duplicate = 0;

    for (var start = 0; start < queue.length; start += 100) {
      final end = min(start + 100, queue.length);
      final batch = queue.sublist(start, end);
      try {
        final res = await http.post(
          Uri.parse("$baseUrl/audit/v1/offline-sync"),
          headers: const {"Content-Type": "application/json"},
          body: jsonEncode({"device_id": deviceId, "actions": batch}),
        ).timeout(const Duration(seconds: 8));
        if (res.statusCode != 200) return null;

        final decoded = jsonDecode(res.body);
        if (decoded is! Map || decoded["results"] is! List || decoded["summary"] is! Map) return null;
        final batchResults = (decoded["results"] as List)
            .map((value) => value is Map ? Map<String, dynamic>.from(value) : null)
            .toList();
        if (batchResults.length != batch.length || batchResults.any((value) => value == null)) return null;
        for (var index = 0; index < batch.length; index++) {
          if (batchResults[index]!["action_id"] != batch[index]["action_id"]) return null;
        }

        final summary = Map<String, dynamic>.from(decoded["summary"] as Map);
        final batchApplied = summary["applied"];
        final batchRejected = summary["rejected"];
        final batchDuplicate = summary["duplicate"];
        if (batchApplied is! int || batchRejected is! int || batchDuplicate is! int ||
            batchApplied + batchRejected + batchDuplicate != batch.length) {
          return null;
        }

        results.addAll(batchResults.cast<Map<String, dynamic>>());
        applied += batchApplied;
        rejected += batchRejected;
        duplicate += batchDuplicate;
        // Persist progress after every acknowledged batch. If the app closes
        // between batches, only the unprocessed actions remain queued.
        await _savePendingQueue(queue.sublist(end));
      } catch (_) {
        // Keep every unacknowledged action on-device for an idempotent retry.
        return null;
      }
    }
    return {
      "device_id": deviceId,
      "results": results,
      "summary": {"applied": applied, "rejected": rejected, "duplicate": duplicate},
    };
  }
}

class ApiException implements Exception {
  final String code;
  final String message;

  const ApiException(this.code, this.message);

  @override
  String toString() => message;
}
