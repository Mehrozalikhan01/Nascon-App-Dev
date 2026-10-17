import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

class ApiService {
  static const String defaultBaseUrl = kIsWeb ? "http://localhost:8080" : "http://10.0.2.2:8080";
  static String baseUrl = defaultBaseUrl;
  static String? currentToken;
  static String currentRole = "viewer";
  static String currentEmail = "viewer@nascon.test";

  static Future<void> init() async {
    final prefs = await SharedPreferences.getInstance();
    final saved = prefs.getString("server_url");
    if (kIsWeb) {
      baseUrl = (saved == null || saved.contains("10.0.2.2")) ? "http://localhost:8080" : saved;
    } else {
      baseUrl = saved ?? defaultBaseUrl;
    }
    currentToken = prefs.getString("auth_token");
    currentRole = prefs.getString("auth_role") ?? "viewer";
    currentEmail = prefs.getString("auth_email") ?? "viewer@nascon.test";
  }

  static Future<void> setServerUrl(String url) async {
    baseUrl = url;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString("server_url", url);
  }

  static Future<void> saveAuth(String token, String role, String email) async {
    currentToken = token;
    currentRole = role;
    currentEmail = email;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString("auth_token", token);
    await prefs.setString("auth_role", role);
    await prefs.setString("auth_email", email);
  }

  static Future<void> logout() async {
    currentToken = null;
    currentRole = "viewer";
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove("auth_token");
    await prefs.remove("auth_role");
  }

  // --- LOCAL CACHING FOR DESK CHECK (NFR-4) ---
  static Future<void> cacheData(String key, dynamic data) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString("cache_$key", jsonEncode(data));
  }

  static Future<dynamic> getCachedData(String key) async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString("cache_$key");
    if (raw != null) {
      try {
        return jsonDecode(raw);
      } catch (_) {}
    }
    return null;
  }

  // --- OFFLINE ACTION QUEUE ---
  static Future<List<Map<String, dynamic>>> getPendingQueue() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString("offline_action_queue");
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
    await prefs.setString("offline_action_queue", jsonEncode(queue));
  }

  static Future<void> clearQueue() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove("offline_action_queue");
  }

  // --- API CALLS ---
  static Future<bool> checkHealth() async {
    try {
      final res = await http.get(Uri.parse("$baseUrl/health")).timeout(const Duration(seconds: 2));
      return res.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  static Future<Map<String, dynamic>?> syncOfflineActions(String deviceId) async {
    final queue = await getPendingQueue();
    if (queue.isEmpty) return null;

    try {
      final res = await http.post(
        Uri.parse("$baseUrl/audit/v1/offline-sync"),
        headers: {"Content-Type": "application/json"},
        body: jsonEncode({
          "device_id": deviceId,
          "actions": queue,
        }),
      ).timeout(const Duration(seconds: 5));

      if (res.statusCode == 200) {
        final data = jsonDecode(res.body);
        await clearQueue();
        return data;
      }
    } catch (_) {}
    return null;
  }
}
