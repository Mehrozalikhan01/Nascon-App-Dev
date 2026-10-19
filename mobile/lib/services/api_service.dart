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
  static String _baseUrl = defaultBaseUrl;
  static String get baseUrl => _baseUrl;
  static set baseUrl(String value) {
    var raw = value.trim();
    if (raw.contains('?')) raw = raw.split('?').first;
    if (raw.contains('#')) raw = raw.split('#').first;
    _baseUrl = raw.replaceFirst(RegExp(r'/+$'), '');
  }
  static String? currentToken;
  static String currentRole = "viewer";
  static String currentEmail = "viewer@nascon.test";
  static String currentTenantId = "tenant-alpha";
  static String currentUserId = "";
  static String? _deviceId;

  static bool get _usesAndroidStorage => !kIsWeb && defaultTargetPlatform == TargetPlatform.android;
  static bool get _usesIosStorage => !kIsWeb && defaultTargetPlatform == TargetPlatform.iOS;
  static bool get _usesSecureStorage => _usesAndroidStorage || _usesIosStorage;

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
    final candidate = saved ?? defaultBaseUrl;
    try {
      baseUrl = _normalizeServerUrl(candidate);
    } on FormatException {
      // Old builds accepted arbitrary URLs. Do not silently reuse a public
      // cleartext endpoint that could expose the restored bearer token.
      baseUrl = defaultBaseUrl;
      await prefs.remove("server_url");
    }
    if (_usesSecureStorage) {
      currentToken = await _secureChannel.invokeMethod<String>('readToken');
      // Migrate tokens created by earlier builds into the platform secure store.
      final legacyToken = prefs.getString("auth_token");
      if (currentToken == null && legacyToken != null) {
        await _secureChannel.invokeMethod<void>('writeToken', {"token": legacyToken});
        await prefs.remove("auth_token");
        currentToken = legacyToken;
      }
    } else {
      // Never restore a bearer token from unprotected preferences.
      currentToken = null;
      await prefs.remove("auth_token");
    }
    currentRole = prefs.getString("auth_role") ?? "viewer";
    currentEmail = prefs.getString("auth_email") ?? "";
    currentTenantId = prefs.getString("auth_tenant") ?? "tenant-alpha";
    currentUserId = prefs.getString("auth_user_id") ?? "";
  }

  static Future<void> setServerUrl(String url) async {
    final normalized = _normalizeServerUrl(url);
    baseUrl = normalized;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString("server_url", normalized);
  }

  static String get cleanBaseUrl {
    var raw = baseUrl.trim();
    if (raw.contains('?')) raw = raw.split('?').first;
    if (raw.contains('#')) raw = raw.split('#').first;
    return raw.replaceFirst(RegExp(r'/+$'), '');
  }

  static String _normalizeServerUrl(String url) {
    var raw = url.trim();
    if (raw.contains('?')) raw = raw.split('?').first;
    if (raw.contains('#')) raw = raw.split('#').first;
    raw = raw.replaceFirst(RegExp(r'/+$'), '');
    final parsed = Uri.tryParse(raw);
    if (parsed == null ||
        !const {"http", "https"}.contains(parsed.scheme) ||
        parsed.host.isEmpty ||
        parsed.userInfo.isNotEmpty ||
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
    return first == 10 || first == 127 || first == 172 || first == 192 || first == 169;
  }

  static Map<String, String> get authHeaders => {
        "Content-Type": "application/json",
        if (currentToken != null && currentToken!.isNotEmpty)
          "Authorization": "Bearer $currentToken",
      };

  static String get _accountStorageSuffix =>
      base64Url.encode(utf8.encode(currentEmail.trim().toLowerCase())).replaceAll("=", "");
  // Offline actions belong to this device, not to whichever account happens
  // to be signed in. This lets an Organizer queue a score, then a Manager queue
  // a roster move, and still see/sync both after switching roles offline.
  static const String _offlineQueueStorageKey = "offline_action_queue_v2";
  static const String _offlineHistoryStorageKey = "offline_sync_history_v2";

  static Future<void> saveAuth(String token, String role, String email, String tenantId, String userId) async {
    final prefs = await SharedPreferences.getInstance();
    if (_usesSecureStorage) {
      await _secureChannel.invokeMethod<void>('writeToken', {"token": token});
    }
    currentToken = token;
    currentRole = role;
    currentEmail = email;
    currentTenantId = tenantId;
    currentUserId = userId;
    await prefs.setString("auth_role", role);
    await prefs.setString("auth_email", email);
    await prefs.setString("auth_tenant", tenantId);
    await prefs.setString("auth_user_id", userId);
  }

  static Future<void> logout() async {
    currentToken = null;
    currentRole = "viewer";
    currentEmail = "";
    currentTenantId = "tenant-alpha";
    currentUserId = "";
    final prefs = await SharedPreferences.getInstance();
    if (_usesSecureStorage) {
      await _secureChannel.invokeMethod<void>('deleteToken');
    }
    await prefs.remove("auth_token");
    await prefs.remove("auth_role");
    await prefs.remove("auth_email");
    await prefs.remove("auth_tenant");
    await prefs.remove("auth_user_id");
  }

  static Future<Map<String, dynamic>> login(String email, String password) async {
    final response = await http.post(
      Uri.parse("$cleanBaseUrl/api/v1/auth/login"),
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
    final userMap = decoded["user"];
    if (userMap is! Map) {
      throw const ApiException("LOGIN_FAILED", "Invalid server response. Please try again.");
    }
    final user = Map<String, dynamic>.from(userMap);
    await saveAuth(
      decoded["access_token"].toString(),
      user["role"].toString(),
      user["email"].toString(),
      user["tenant_id"]?.toString() ?? "tenant-alpha",
      user["user_id"]?.toString() ?? "",
    );
    return user;
  }

  // --- LOCAL CACHING FOR DESK CHECK (NFR-4) ---
  static Future<void> cacheData(String key, dynamic data) async {
    final prefs = await SharedPreferences.getInstance();
    await _writeLocalValue(prefs, "cache_$key", jsonEncode(data));
  }

  static Future<Map<String, dynamic>?> fetchJson(
    String path, {
    required String cacheKey,
    required bool online,
  }) async {
    if (online) {
      try {
        final response = await http
            .get(Uri.parse("$cleanBaseUrl$path"), headers: authHeaders)
            .timeout(const Duration(seconds: 8));
        if (response.statusCode == 200) {
          final decoded = jsonDecode(response.body);
          if (decoded is Map) {
            final data = Map<String, dynamic>.from(decoded);
            await cacheData(cacheKey, data);
            return data;
          }
        }
      } catch (_) {
        // Fall through to the last-known copy below.
      }
    }
    final cached = await getCachedData(cacheKey);
    return cached is Map ? Map<String, dynamic>.from(cached) : null;
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

  static Future<Map<String, dynamic>> loadOverview({
    required bool online,
    String tenantId = "tenant-alpha",
    String leagueId = "league-alpha-1",
    String clubId = "club-alpha-1",
  }) async {
    final scopeKey = "${currentEmail.isEmpty ? 'signed_out' : currentEmail}_${tenantId}_${leagueId}_$clubId";
    final cacheKey = "mobile_overview_${scopeKey.replaceAll(RegExp(r'[^A-Za-z0-9_.-]'), '_')}";
    final path = "/audit/v1/mobile/overview?tenant_id=${Uri.encodeQueryComponent(tenantId)}&league_id=${Uri.encodeQueryComponent(leagueId)}&club_id=${Uri.encodeQueryComponent(clubId)}";
    if (online) {
      try {
        final data = await fetchJson(
          path,
          cacheKey: cacheKey,
          online: true,
        );
        if (data != null) return data;
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
    final key = "checkout_draft_${_accountStorageSuffix.isEmpty ? 'signed_out' : _accountStorageSuffix}";
    var raw = await _readLocalValue(prefs, key);
    if (raw == null && currentEmail.isNotEmpty) {
      raw = await _readLocalValue(prefs, "checkout_draft");
      if (raw != null) {
        await _writeLocalValue(prefs, key, raw);
        await _removeLocalValue(prefs, "checkout_draft");
      }
    }
    if (raw == null) return null;
    try {
      return Map<String, dynamic>.from(jsonDecode(raw) as Map);
    } catch (_) {
      await _removeLocalValue(prefs, key);
      return null;
    }
  }

  static Future<void> saveCheckoutDraft(Map<String, dynamic> data) async {
    final prefs = await SharedPreferences.getInstance();
    await _writeLocalValue(prefs, "checkout_draft_${_accountStorageSuffix.isEmpty ? 'signed_out' : _accountStorageSuffix}", jsonEncode(data));
  }

  static Future<void> clearCheckoutDraft() async {
    final prefs = await SharedPreferences.getInstance();
    await _removeLocalValue(prefs, "checkout_draft_${_accountStorageSuffix.isEmpty ? 'signed_out' : _accountStorageSuffix}");
  }

  static Future<Map<String, dynamic>?> getCheckoutCart() async {
    final prefs = await SharedPreferences.getInstance();
    final key = "checkout_cart_state_${_accountStorageSuffix.isEmpty ? 'signed_out' : _accountStorageSuffix}";
    var raw = await _readLocalValue(prefs, key);
    if (raw == null && currentEmail.isNotEmpty) {
      raw = await _readLocalValue(prefs, "checkout_cart_state");
      if (raw != null) {
        await _writeLocalValue(prefs, key, raw);
        await _removeLocalValue(prefs, "checkout_cart_state");
      }
    }
    if (raw == null) return null;
    try {
      return Map<String, dynamic>.from(jsonDecode(raw) as Map);
    } catch (_) {
      await _removeLocalValue(prefs, key);
      return null;
    }
  }

  static Future<void> saveCheckoutCart(Map<String, dynamic> data) async {
    final prefs = await SharedPreferences.getInstance();
    await _writeLocalValue(prefs, "checkout_cart_state_${_accountStorageSuffix.isEmpty ? 'signed_out' : _accountStorageSuffix}", jsonEncode(data));
  }

  static Future<void> clearCheckoutCart() async {
    final prefs = await SharedPreferences.getInstance();
    await _removeLocalValue(prefs, "checkout_cart_state_${_accountStorageSuffix.isEmpty ? 'signed_out' : _accountStorageSuffix}");
  }

  // --- OFFLINE ACTION QUEUE ---
  static Future<List<Map<String, dynamic>>> getPendingQueue() async {
    final prefs = await SharedPreferences.getInstance();
    final queue = <Map<String, dynamic>>[];
    final seen = <String>{};
    final accountKey = "offline_action_queue_${_accountStorageSuffix.isEmpty ? 'signed_out' : _accountStorageSuffix}";
    final legacyKeys = <String>[
      _offlineQueueStorageKey,
      accountKey,
      "offline_action_queue",
    ];
    for (final key in legacyKeys.toSet()) {
      final raw = await _readLocalValue(prefs, key);
      if (raw == null) continue;
      try {
        final decoded = jsonDecode(raw);
        if (decoded is! List) continue;
        for (final entry in decoded) {
          if (entry is! Map) continue;
          final action = Map<String, dynamic>.from(entry);
          final actionId = action["action_id"]?.toString();
          final deviceId = action["device_id"]?.toString() ?? "";
          final uniqueKey = "${deviceId}_$actionId";
          if (actionId == null || seen.add(uniqueKey)) queue.add(action);
        }
      } catch (_) {
        // Ignore damaged legacy values while preserving any valid entries.
      }
      if (key != _offlineQueueStorageKey) await _removeLocalValue(prefs, key);
    }
    if (queue.isNotEmpty) {
      await _writeLocalValue(prefs, _offlineQueueStorageKey, jsonEncode(queue));
    }
    return queue;
  }

  static Future<void> queueOfflineAction(Map<String, dynamic> action) async {
    action.putIfAbsent("queued_by", () => currentEmail);
    action.putIfAbsent("tenant_id", () => currentTenantId);
    final queue = await getPendingQueue();
    queue.add(action);
    final prefs = await SharedPreferences.getInstance();
    await _writeLocalValue(prefs, "offline_action_queue_${_accountStorageSuffix.isEmpty ? 'signed_out' : _accountStorageSuffix}", jsonEncode(queue));
  }

  static Future<void> _savePendingQueue(List<Map<String, dynamic>> queue) async {
    final prefs = await SharedPreferences.getInstance();
    if (queue.isEmpty) {
      await _removeLocalValue(prefs, _offlineQueueStorageKey);
    } else {
      await _writeLocalValue(prefs, _offlineQueueStorageKey, jsonEncode(queue));
    }
  }

  static Future<void> clearQueue() async {
    final prefs = await SharedPreferences.getInstance();
    await _removeLocalValue(prefs, _offlineQueueStorageKey);
  }

  static Future<List<Map<String, dynamic>>> getSyncHistory() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = await _readLocalValue(prefs, _offlineHistoryStorageKey);
    if (raw == null) return [];
    try {
      final decoded = jsonDecode(raw);
      if (decoded is List) {
        return decoded.map((item) => Map<String, dynamic>.from(item as Map)).toList();
      }
    } catch (_) {}
    return [];
  }

  static Future<void> clearSyncHistory() async {
    final prefs = await SharedPreferences.getInstance();
    await _removeLocalValue(prefs, _offlineHistoryStorageKey);
  }

  // --- API CALLS ---
  static Future<bool> checkHealth() async {
    try {
      final res = await http.get(Uri.parse("$cleanBaseUrl/health")).timeout(const Duration(seconds: 4));
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
          Uri.parse("$cleanBaseUrl/audit/v1/offline-sync"),
          headers: authHeaders,
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
    final response = {
      "device_id": deviceId,
      "results": results,
      "summary": {"applied": applied, "rejected": rejected, "duplicate": duplicate},
    };
    final history = await getSyncHistory();
    for (final result in results) {
      history.insert(0, {
        ...result,
        "synced_at": DateTime.now().toUtc().toIso8601String(),
      });
    }
    final prefs = await SharedPreferences.getInstance();
    await _writeLocalValue(
      prefs,
      _offlineHistoryStorageKey,
      jsonEncode(history.take(100).toList()),
    );
    return response;
  }
}

class ApiException implements Exception {
  final String code;
  final String message;

  const ApiException(this.code, this.message);

  @override
  String toString() => message;
}
