import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:http/http.dart' as http;
import 'services/api_service.dart';

String _utcTimestamp(DateTime value) {
  final utc = value.toUtc();
  String twoDigits(int number) => number.toString().padLeft(2, '0');
  return '${utc.year.toString().padLeft(4, '0')}-${twoDigits(utc.month)}-${twoDigits(utc.day)}'
      'T${twoDigits(utc.hour)}:${twoDigits(utc.minute)}:${twoDigits(utc.second)}Z';
}

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  try {
    await ApiService.init();
  } catch (e) {
    debugPrint("Init error: $e");
  }
  runApp(const NasconLeagueApp());
}

class NasconLeagueApp extends StatelessWidget {
  const NasconLeagueApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: "NaSCon '26 League Platform",
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF10284B),
          primary: const Color(0xFF10284B),
          secondary: const Color(0xFF1CA987),
          tertiary: const Color(0xFFE4B557),
          surface: const Color(0xFFFFFEFC),
        ),
        scaffoldBackgroundColor: const Color(0xFFF5F7F8),
        appBarTheme: const AppBarTheme(
          elevation: 0,
          centerTitle: false,
          backgroundColor: Color(0xFF10284B),
          foregroundColor: Colors.white,
        ),
        cardTheme: CardThemeData(
          elevation: 1,
          color: Colors.white,
          margin: EdgeInsets.zero,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(18),
            side: const BorderSide(color: Color(0xFFE8EDF0)),
          ),
        ),
        inputDecorationTheme: InputDecorationTheme(
          filled: true,
          fillColor: const Color(0xFFF8FAFB),
          contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
          border: OutlineInputBorder(borderRadius: BorderRadius.circular(14)),
          enabledBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(14),
            borderSide: const BorderSide(color: Color(0xFFDCE4E8)),
          ),
        ),
        navigationBarTheme: NavigationBarThemeData(
          backgroundColor: Colors.white,
          indicatorColor: const Color(0xFFE7EEF5),
          labelTextStyle: WidgetStateProperty.resolveWith((states) => TextStyle(
            fontSize: 12,
            fontWeight: states.contains(WidgetState.selected) ? FontWeight.w700 : FontWeight.w500,
          )),
        ),
      ),
      home: const AuthGate(),
    );
  }
}

class AuthGate extends StatefulWidget {
  const AuthGate({super.key});

  @override
  State<AuthGate> createState() => _AuthGateState();
}

class _AuthGateState extends State<AuthGate> {
  bool _ready = false;
  bool _signedIn = false;

  @override
  void initState() {
    super.initState();
    _restoreSession();
  }

  Future<void> _restoreSession() async {
    final token = ApiService.currentToken;
    if (token != null && token.isNotEmpty) {
      try {
        final res = await http.get(
          Uri.parse("${ApiService.baseUrl}/api/v1/auth/me"),
          headers: {"Authorization": "Bearer $token"},
        ).timeout(const Duration(seconds: 6));
        if (res.statusCode == 200) {
          final user = Map<String, dynamic>.from(jsonDecode(res.body) as Map);
          ApiService.currentRole = user["role"]?.toString() ?? ApiService.currentRole;
          _signedIn = true;
        } else {
          await ApiService.logout();
        }
      } catch (_) {
        // Keep an existing session available offline; the status banner shows
        // that server actions are temporarily unavailable.
        _signedIn = true;
      }
    }
    if (mounted) setState(() => _ready = true);
  }

  @override
  Widget build(BuildContext context) {
    if (!_ready) {
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
    }
    return _signedIn
        ? MainNavigationScreen(onLogout: () async {
            await ApiService.logout();
            if (mounted) setState(() => _signedIn = false);
          })
        : LoginScreen(onSignedIn: () => setState(() => _signedIn = true));
  }
}

class LoginScreen extends StatefulWidget {
  final VoidCallback onSignedIn;
  const LoginScreen({super.key, required this.onSignedIn});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _emailCtrl = TextEditingController(text: "manager@nascon.test");
  final _passwordCtrl = TextEditingController(text: "Nascon@26");
  bool _busy = false;
  bool _seeding = false;
  bool _hidePassword = true;
  bool? _serverReachable;
  String? _error;

  static const List<Map<String, String>> _demoAccounts = [
    {"label": "Manager", "email": "manager@nascon.test", "desc": "Club Operations"},
    {"label": "Admin", "email": "admin@nascon.test", "desc": "Platform Admin"},
    {"label": "Official", "email": "organizer@nascon.test", "desc": "Match Official"},
    {"label": "Player", "email": "player@nascon.test", "desc": "Squad Player"},
    {"label": "Viewer", "email": "viewer@nascon.test", "desc": "Fan & Store"},
  ];

  @override
  void initState() {
    super.initState();
    _checkServer();
  }

  @override
  void dispose() {
    _emailCtrl.dispose();
    _passwordCtrl.dispose();
    super.dispose();
  }

  Future<void> _checkServer() async {
    final ok = await ApiService.checkHealth();
    if (mounted) {
      setState(() => _serverReachable = ok);
    }
  }

  Future<void> _signIn([String? email, String? password]) async {
    if (_busy) return;
    final em = (email ?? _emailCtrl.text).trim();
    final pw = password ?? _passwordCtrl.text;
    if (em.isEmpty || pw.isEmpty) {
      setState(() => _error = "Please enter both email and password.");
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await ApiService.login(em, pw);
      if (mounted) widget.onSignedIn();
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } on TimeoutException {
      if (mounted) setState(() => _error = "The server did not answer in time. Check the server address and try again.");
    } catch (e) {
      if (mounted) setState(() => _error = "Sign in failed: $e (Server: ${ApiService.baseUrl})");
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _autoSeed() async {
    if (_seeding) return;
    setState(() => _seeding = true);
    try {
      final res = await http.post(
        Uri.parse("${ApiService.baseUrl}/audit/v1/reset"),
        headers: const {"Content-Type": "application/json"},
        body: jsonEncode({"seed": 42}),
      ).timeout(const Duration(seconds: 8));
      if (mounted) {
        if (res.statusCode == 200) {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(
              content: Text("Demo data reset & seeded successfully!"),
              backgroundColor: Color(0xFF08775E),
            ),
          );
          _checkServer();
        } else {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text("Server responded with code ${res.statusCode}")),
          );
        }
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text("Could not reach ${ApiService.baseUrl} to seed demo data.")),
        );
      }
    } finally {
      if (mounted) setState(() => _seeding = false);
    }
  }

  Future<void> _configureServer() async {
    final controller = TextEditingController(text: ApiService.baseUrl);
    final entered = await showDialog<String>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text("Event Server Address"),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            TextField(
              controller: controller,
              keyboardType: TextInputType.url,
              decoration: const InputDecoration(
                labelText: "Server address",
                hintText: "http://localhost:8080",
                helperText: "Format: http://<ip-or-host>:8080",
              ),
            ),
            const SizedBox(height: 12),
            const Text("Quick Presets:", style: TextStyle(fontWeight: FontWeight.w600, fontSize: 12)),
            const SizedBox(height: 6),
            Wrap(
              spacing: 6,
              children: [
                ActionChip(
                  label: const Text("Localhost"),
                  onPressed: () => controller.text = "http://localhost:8080",
                ),
                ActionChip(
                  label: const Text("Emulator (10.0.2.2)"),
                  onPressed: () => controller.text = "http://10.0.2.2:8080",
                ),
                ActionChip(
                  label: const Text("LAN (172.15.4.12)"),
                  onPressed: () => controller.text = "http://172.15.4.12:8080",
                ),
              ],
            ),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(dialogContext), child: const Text("Cancel")),
          FilledButton(onPressed: () => Navigator.pop(dialogContext, controller.text.trim()), child: const Text("Save & Connect")),
        ],
      ),
    );
    controller.dispose();
    if (entered == null || entered.isEmpty || !mounted) return;
    try {
      await ApiService.setServerUrl(entered);
      await _checkServer();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(_serverReachable == true
              ? "Connected to ${ApiService.baseUrl}"
              : "Address saved, but ${ApiService.baseUrl} did not respond."),
          backgroundColor: _serverReachable == true ? const Color(0xFF08775E) : const Color(0xFF8A5A13),
        ));
      }
    } on FormatException catch (err) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(err.message)));
    } catch (_) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("Error saving server address.")));
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 480),
            child: SingleChildScrollView(
              padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const SizedBox(height: 8),
                  const Center(child: BrandMark(size: 68)),
                  const SizedBox(height: 14),
                  const Text("NaSCon '26", textAlign: TextAlign.center,
                    style: TextStyle(fontSize: 28, fontWeight: FontWeight.w800, color: Color(0xFF10284B))),
                  const SizedBox(height: 4),
                  Text("Multi-Sport League & Club Operations", textAlign: TextAlign.center,
                    style: TextStyle(fontSize: 14, color: Colors.blueGrey[600])),
                  const SizedBox(height: 16),

                  // Server Connection Status Banner
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
                    decoration: BoxDecoration(
                      color: _serverReachable == true ? const Color(0xFFE8F5E9) : const Color(0xFFFFF8E1),
                      borderRadius: BorderRadius.circular(10),
                      border: Border.all(
                        color: _serverReachable == true ? const Color(0xFFA5D6A7) : const Color(0xFFFFE082),
                      ),
                    ),
                    child: Row(
                      children: [
                        Icon(
                          _serverReachable == true ? Icons.check_circle : Icons.cloud_queue,
                          size: 18,
                          color: _serverReachable == true ? const Color(0xFF2E7D32) : const Color(0xFFF57F17),
                        ),
                        const SizedBox(width: 8),
                        Expanded(
                          child: Text(
                            "${_serverReachable == true ? 'Connected' : 'Server'}: ${ApiService.baseUrl}",
                            style: TextStyle(
                              fontSize: 12,
                              fontWeight: FontWeight.w600,
                              color: _serverReachable == true ? const Color(0xFF1B5E20) : const Color(0xFFE65100),
                            ),
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                        InkWell(
                          onTap: _configureServer,
                          borderRadius: BorderRadius.circular(6),
                          child: const Padding(
                            padding: EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                            child: Text("Change", style: TextStyle(fontSize: 12, fontWeight: FontWeight.bold, color: Color(0xFF10284B), decoration: TextDecoration.underline)),
                          ),
                        ),
                      ],
                    ),
                  ),

                  const SizedBox(height: 16),
                  Card(
                    elevation: 2,
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
                    child: Padding(
                      padding: const EdgeInsets.all(20),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          const Text("Welcome back", style: TextStyle(fontSize: 20, fontWeight: FontWeight.w700)),
                          const SizedBox(height: 4),
                          Text("Choose a demo role or enter credentials.", style: TextStyle(color: Colors.blueGrey[600], fontSize: 13)),
                          const SizedBox(height: 14),

                          // Demo Role Chips
                          const Text("Quick Demo Accounts:", style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: Color(0xFF10284B))),
                          const SizedBox(height: 8),
                          Wrap(
                            spacing: 6,
                            runSpacing: 6,
                            children: _demoAccounts.map((acc) {
                              final isSelected = _emailCtrl.text == acc["email"];
                              return ChoiceChip(
                                label: Text(acc["label"]!),
                                selected: isSelected,
                                onSelected: (sel) {
                                  if (sel) {
                                    setState(() {
                                      _emailCtrl.text = acc["email"]!;
                                      _passwordCtrl.text = "Nascon@26";
                                      _error = null;
                                    });
                                  }
                                },
                                selectedColor: const Color(0xFF10284B),
                                labelStyle: TextStyle(
                                  color: isSelected ? Colors.white : const Color(0xFF10284B),
                                  fontWeight: FontWeight.w600,
                                  fontSize: 12,
                                ),
                              );
                            }).toList(),
                          ),
                          const SizedBox(height: 16),

                          TextField(
                            controller: _emailCtrl,
                            keyboardType: TextInputType.emailAddress,
                            autofillHints: const [AutofillHints.username],
                            decoration: const InputDecoration(
                              labelText: "Email address",
                              prefixIcon: Icon(Icons.mail_outline),
                              border: OutlineInputBorder(),
                              contentPadding: EdgeInsets.symmetric(horizontal: 14, vertical: 12),
                            ),
                            onSubmitted: (_) => _signIn(),
                          ),
                          const SizedBox(height: 12),
                          TextField(
                            controller: _passwordCtrl,
                            obscureText: _hidePassword,
                            autofillHints: const [AutofillHints.password],
                            decoration: InputDecoration(
                              labelText: "Password",
                              prefixIcon: const Icon(Icons.lock_outline),
                              border: const OutlineInputBorder(),
                              contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
                              suffixIcon: IconButton(
                                tooltip: _hidePassword ? "Show password" : "Hide password",
                                onPressed: () => setState(() => _hidePassword = !_hidePassword),
                                icon: Icon(_hidePassword ? Icons.visibility_outlined : Icons.visibility_off_outlined),
                              ),
                            ),
                            onSubmitted: (_) => _signIn(),
                          ),
                          if (_error != null) ...[
                            const SizedBox(height: 12),
                            Container(
                              padding: const EdgeInsets.all(12),
                              decoration: BoxDecoration(color: const Color(0xFFFFF1EF), borderRadius: BorderRadius.circular(10)),
                              child: Text(_error!, style: const TextStyle(color: Color(0xFF9E342C), fontSize: 13)),
                            ),
                          ],
                          const SizedBox(height: 18),
                          FilledButton.icon(
                            onPressed: _busy ? null : () => _signIn(),
                            icon: _busy
                                ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                                : const Icon(Icons.login),
                            label: Text(_busy ? "Signing in…" : "Sign In"),
                            style: FilledButton.styleFrom(
                              minimumSize: const Size.fromHeight(48),
                              backgroundColor: const Color(0xFF10284B),
                            ),
                          ),
                          const SizedBox(height: 10),
                          OutlinedButton.icon(
                            onPressed: _seeding ? null : _autoSeed,
                            icon: _seeding
                                ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                                : const Icon(Icons.refresh, size: 18),
                            label: Text(_seeding ? "Seeding baseline…" : "Seed / Reset Demo Data"),
                            style: OutlinedButton.styleFrom(
                              minimumSize: const Size.fromHeight(44),
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                  const SizedBox(height: 14),
                  const Text("All demo passwords are: Nascon@26",
                    textAlign: TextAlign.center, style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: Color(0xFF47607E))),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class BrandMark extends StatelessWidget {
  final double size;
  const BrandMark({super.key, this.size = 36});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        gradient: const LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight,
          colors: [Color(0xFF1CA987), Color(0xFF08775E)]),
        borderRadius: BorderRadius.circular(size * .3),
        border: Border.all(color: const Color(0xFFE4B557), width: size * .035),
        boxShadow: const [BoxShadow(color: Color(0x33223952), blurRadius: 12, offset: Offset(0, 5))],
      ),
      child: Center(
        child: Text("N", style: TextStyle(color: Colors.white, fontWeight: FontWeight.w900, fontSize: size * .56)),
      ),
    );
  }
}

class EmptyState extends StatelessWidget {
  final IconData icon;
  final String title;
  final String message;
  const EmptyState({super.key, required this.icon, required this.title, required this.message});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 28, vertical: 20),
      child: Column(mainAxisSize: MainAxisSize.min, children: [
        Container(width: 72, height: 72,
          decoration: const BoxDecoration(color: Color(0xFFE8F0F4), shape: BoxShape.circle),
          child: Icon(icon, color: const Color(0xFF527184), size: 32)),
        const SizedBox(height: 16),
        Text(title, textAlign: TextAlign.center,
          style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w800, color: Color(0xFF10284B))),
        const SizedBox(height: 8),
        Text(message, textAlign: TextAlign.center,
          style: TextStyle(height: 1.4, color: Colors.blueGrey[600])),
      ]),
    );
  }
}

String explainApiError(String code) {
  switch (code) {
    case "MATCH_NOT_FOUND": return "That match could not be found. Refresh fixtures and try again.";
    case "MATCH_NOT_IN_PROGRESS": return "This match is not live yet. Start the match before recording a score.";
    case "ILLEGAL_TRANSITION": return "That action is not allowed for the match's current status.";
    case "UNKNOWN_EVENT_TYPE": return "This scoring event is not available for the selected sport.";
    case "EVENT_NOT_FOUND": return "The event selected for correction was not found.";
    case "INVALID_COMPENSATION": return "That event has already been corrected or cannot be reversed.";
    case "DUPLICATE_EVENT_ID": return "This action was already submitted. Refresh the match before trying again.";
    case "PLAYER_NOT_FOUND": return "That player is no longer registered in the club.";
    case "SQUAD_NOT_FOUND": return "The selected squad could not be found.";
    case "NOT_IN_SQUAD": return "The player is no longer in the selected squad.";
    case "CROSS_TENANT_TRANSFER": return "Players can only move between clubs in the same tenant.";
    case "TRANSFER_WINDOW_CLOSED": return "The transfer window is closed for that date.";
    case "PLAYER_ON_LOAN": return "This player is already on loan.";
    case "SQUAD_FULL": return "The destination squad has no available places.";
    case "MIN_POSITION_VIOLATION": return "This move would leave the current squad without a required position.";
    case "INVALID_BASE_VERSION": return "This action was based on an older version. Review the latest match or roster.";
    default: return "The server could not apply this action. Review the latest details and try again.";
  }
}

class ConnectionBadge extends StatelessWidget {
  final bool online;
  const ConnectionBadge({super.key, required this.online});

  @override
  Widget build(BuildContext context) {
    final color = online ? const Color(0xFF11795F) : const Color(0xFF8A5A13);
    return Padding(
      padding: const EdgeInsets.only(right: 8),
      child: Chip(
        avatar: Icon(online ? Icons.wifi : Icons.wifi_off, size: 16, color: Colors.white),
        label: Text(online ? "Online" : "Offline", style: const TextStyle(color: Colors.white, fontSize: 12)),
        backgroundColor: color,
        visualDensity: VisualDensity.compact,
        side: BorderSide.none,
      ),
    );
  }
}

class MainNavigationScreen extends StatefulWidget {
  final Future<void> Function() onLogout;
  const MainNavigationScreen({super.key, required this.onLogout});

  @override
  State<MainNavigationScreen> createState() => _MainNavigationScreenState();
}

class _MainNavigationScreenState extends State<MainNavigationScreen> {
  int _currentIndex = 0;
  bool _isOnline = false;
  bool _checkingStatus = true;
  bool _syncing = false;
  bool _statusCheckInProgress = false;
  int _pendingCount = 0;
  List<Map<String, dynamic>> _pendingActions = [];
  Timer? _statusTimer;

  @override
  void initState() {
    super.initState();
    _checkStatus();
    _statusTimer = Timer.periodic(const Duration(seconds: 4), (_) => _checkStatus());
  }

  @override
  void dispose() {
    _statusTimer?.cancel();
    super.dispose();
  }

  Future<void> _checkStatus() async {
    if (_statusCheckInProgress) return;
    _statusCheckInProgress = true;
    var online = false;
    var queue = <Map<String, dynamic>>[];
    try {
      online = await ApiService.checkHealth();
      queue = await ApiService.getPendingQueue();
    } catch (_) {
      // Keep the connection badge available even if local storage is damaged.
    } finally {
      _statusCheckInProgress = false;
      if (mounted) {
        setState(() {
          _isOnline = online;
          _pendingActions = queue;
          _pendingCount = queue.length;
          _checkingStatus = false;
        });
      }
    }
  }

  Future<void> _performSync() async {
    if (mounted) setState(() => _syncing = true);
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text("Syncing pending offline actions…")),
    );
    try {
      final res = await ApiService.syncOfflineActions(await ApiService.getDeviceId());
      if (res == null) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text("Sync could not reach the server. Your queued actions are still saved.")),
        );
        }
        return;
      }
      final summary = res["summary"] is Map ? Map<String, dynamic>.from(res["summary"] as Map) : <String, dynamic>{};
      final results = res["results"] is List ? List<dynamic>.from(res["results"] as List) : <dynamic>[];
      if (mounted) {
        await showDialog<void>(
          context: context,
          builder: (ctx) => AlertDialog(
            title: const Text("Sync results"),
            content: SizedBox(
              width: 420,
              child: SingleChildScrollView(child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text("${summary['applied'] ?? 0} applied · ${summary['rejected'] ?? 0} need attention · ${summary['duplicate'] ?? 0} already received"),
                  const SizedBox(height: 10),
                  ...results.map((dynamic entry) {
                    final result = entry is Map ? Map<String, dynamic>.from(entry) : <String, dynamic>{};
                    final status = result["status"]?.toString() ?? "unknown";
                    final errorCode = result["error_code"]?.toString();
                    final explanation = errorCode == null ? "Action synchronized." : explainApiError(errorCode);
                    return ListTile(
                      contentPadding: EdgeInsets.zero,
                      dense: true,
                      leading: Icon(status == "applied" || status == "duplicate" ? Icons.check_circle_outline : Icons.info_outline,
                        color: status == "rejected" ? const Color(0xFF9E342C) : const Color(0xFF08775E)),
                      title: Text("${result['action_id'] ?? 'Action'} · $status"),
                      subtitle: Text(explanation),
                    );
                  }),
                ],
              )),
            ),
            actions: [
              FilledButton(onPressed: () => Navigator.pop(ctx), child: const Text("Done")),
            ],
          ),
        );
      }
    } finally {
      if (mounted) setState(() => _syncing = false);
      await _checkStatus();
    }
  }

  @override
  Widget build(BuildContext context) {
    final screens = [
      FixturesScreen(isOnline: _isOnline, onOpenStore: () => setState(() => _currentIndex = 2)),
      StandingsScreen(isOnline: _isOnline),
      StoreScreen(isOnline: _isOnline),
      ClubProfileScreen(isOnline: _isOnline),
      AccountScreen(
        onRoleChanged: () { setState(() {}); _checkStatus(); },
        isOnline: _isOnline,
        pendingCount: _pendingCount,
        pendingActions: _pendingActions,
        onSync: _performSync,
        onLogout: widget.onLogout,
      ),
    ];

    return Scaffold(
      appBar: AppBar(
        leading: Padding(
          padding: const EdgeInsets.all(8.0),
            child: const BrandMark(size: 38),
          ),
        title: const Text("NaSCon '26\nLeague & Club Ops", style: TextStyle(fontSize: 16, height: 1.05, fontWeight: FontWeight.w700)),
        actions: [
          // Offline / Online Status Badge
          Container(
            margin: const EdgeInsets.symmetric(horizontal: 8, vertical: 12),
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
            decoration: BoxDecoration(
              color: _syncing ? const Color(0xFF1B5B9B) : (_checkingStatus ? Colors.blueGrey[700] : (_isOnline ? const Color(0xFF11795F) : const Color(0xFF8A5A13))),
              borderRadius: BorderRadius.circular(20),
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(
                  _syncing || _checkingStatus ? Icons.sync : (_isOnline ? Icons.wifi : Icons.wifi_off),
                  size: 14,
                  color: Colors.white,
                ),
                const SizedBox(width: 4),
                Text(
                  _syncing ? "Syncing" : (_checkingStatus ? "Checking" : (_isOnline ? "Online" : "Offline")),
                  style: const TextStyle(fontSize: 12, fontWeight: FontWeight.bold, color: Colors.white),
                ),
                if (_pendingCount > 0) ...[
                  const SizedBox(width: 4),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
                    decoration: BoxDecoration(
                      color: Colors.red[700],
                      shape: BoxShape.circle,
                    ),
                    child: Text(
                      "$_pendingCount",
                      style: const TextStyle(fontSize: 10, color: Colors.white),
                    ),
                  )
                ]
              ],
            ),
          ),
        ],
      ),
      body: screens[_currentIndex],
      bottomNavigationBar: NavigationBar(
        selectedIndex: _currentIndex,
        onDestinationSelected: (idx) => setState(() => _currentIndex = idx),
        destinations: const [
          NavigationDestination(icon: Icon(Icons.sports_soccer), label: "Fixtures"),
          NavigationDestination(icon: Icon(Icons.leaderboard), label: "Standings"),
          NavigationDestination(icon: Icon(Icons.shopping_bag), label: "Store"),
          NavigationDestination(icon: Icon(Icons.shield), label: "Club"),
          NavigationDestination(icon: Icon(Icons.person), label: "Account"),
        ],
      ),
    );
  }
}

// ==========================================
// 1. FIXTURES SCREEN
// ==========================================
class FixturesScreen extends StatefulWidget {
  final bool isOnline;
  final VoidCallback onOpenStore;
  const FixturesScreen({super.key, required this.isOnline, required this.onOpenStore});

  @override
  State<FixturesScreen> createState() => _FixturesScreenState();
}

class _FixturesScreenState extends State<FixturesScreen> {
  List<dynamic> _fixtures = [];
  Map<String, dynamic> _teamNames = {};
  bool _loading = true;
  String? _loadMessage;

  @override
  void initState() {
    super.initState();
    _loadFixtures();
  }

  @override
  void didUpdateWidget(covariant FixturesScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.isOnline != widget.isOnline && widget.isOnline) _loadFixtures();
  }

  Future<void> _loadFixtures() async {
    if (mounted) setState(() { _loading = true; _loadMessage = null; });
    final data = await ApiService.loadOverview(online: widget.isOnline);
    final fixtures = data["fixtures"];
    if (mounted) {
      setState(() {
        _fixtures = fixtures is List ? fixtures : [];
        _teamNames = data["team_names"] is Map ? Map<String, dynamic>.from(data["team_names"] as Map) : {};
        _loading = false;
        if (_fixtures.isEmpty) {
          _loadMessage = widget.isOnline
              ? "No fixtures are available yet. Reset the demo data or add a league fixture."
              : "No saved fixtures are available offline. Connect to the event server to load them.";
        }
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_fixtures.isEmpty) {
      return RefreshIndicator(
        onRefresh: _loadFixtures,
        child: ListView(children: [
          const SizedBox(height: 90),
          EmptyState(icon: Icons.sports_soccer, title: "No fixtures yet", message: _loadMessage ?? "Pull down to try again."),
        ]),
      );
    }
    return RefreshIndicator(
      onRefresh: _loadFixtures,
      child: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          const Text("Fixtures", style: TextStyle(fontSize: 24, fontWeight: FontWeight.w800, color: Color(0xFF10284B))),
          const SizedBox(height: 4),
          Text("Live scores and upcoming matches", style: TextStyle(color: Colors.blueGrey[600])),
          const SizedBox(height: 16),
          ..._fixtures.map((dynamic f) {
            final home = Map<String, dynamic>.from(f["home"] as Map);
            final away = Map<String, dynamic>.from(f["away"] as Map);
            final homeName = _teamNames[home["team_id"]]?.toString() ?? home["team_id"].toString();
            final awayName = _teamNames[away["team_id"]]?.toString() ?? away["team_id"].toString();
            final status = f["status"]?.toString() ?? "scheduled";
            final live = status == "in_progress";
            return Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: Card(
                child: InkWell(
                  borderRadius: BorderRadius.circular(18),
                  onTap: () => Navigator.push(context, MaterialPageRoute(
                    builder: (_) => MatchDetailScreen(match: f, teamNames: _teamNames,
                      isOnline: widget.isOnline, onOpenStore: widget.onOpenStore))),
                  child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        Row(children: [
                          Icon(live ? Icons.circle : Icons.event_outlined,
                            size: live ? 9 : 16, color: live ? const Color(0xFFD85749) : Colors.blueGrey),
                          const SizedBox(width: 7),
                          Text(live ? "LIVE NOW" : status.replaceAll("_", " ").toUpperCase(),
                            style: TextStyle(fontSize: 11, letterSpacing: .8, fontWeight: FontWeight.w800,
                              color: live ? const Color(0xFFB64538) : Colors.blueGrey[600])),
                          const Spacer(),
                          Text("${f['sport_id'] ?? 'Match'}", style: TextStyle(color: Colors.blueGrey[500], fontSize: 12)),
                        ]),
                        const SizedBox(height: 14),
                        Row(children: [
                          Expanded(child: Text(homeName.toUpperCase(),
                            maxLines: 1, overflow: TextOverflow.ellipsis,
                            style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15))),
                          Text("${home["score"] ?? 0}  :  ${away["score"] ?? 0}",
                            style: const TextStyle(fontSize: 21, fontWeight: FontWeight.w800, color: Color(0xFF10284B))),
                          Expanded(child: Text(awayName.toUpperCase(), textAlign: TextAlign.end,
                            maxLines: 1, overflow: TextOverflow.ellipsis,
                            style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15))),
                        ]),
                        const SizedBox(height: 10),
                        Row(children: [
                          Text("Match ${f['match_id']}", style: TextStyle(color: Colors.blueGrey[500], fontSize: 12)),
                          const Spacer(),
                          const Icon(Icons.arrow_forward, size: 17, color: Color(0xFF1CA987)),
                        ]),
                      ],
                    ),
                  ),
                ),
              ),
            );
          }),
        ],
      ),
    );
  }
}

// ==========================================
// 2. MATCH DETAIL & PINNED SHOWCASE
// ==========================================
class MatchDetailScreen extends StatefulWidget {
  final dynamic match;
  final Map<String, dynamic> teamNames;
  final bool isOnline;
  final VoidCallback onOpenStore;
  const MatchDetailScreen({super.key, required this.match, required this.teamNames, required this.isOnline, required this.onOpenStore});

  @override
  State<MatchDetailScreen> createState() => _MatchDetailScreenState();
}

class _MatchDetailScreenState extends State<MatchDetailScreen> {
  late dynamic _match;
  late bool _isOnline;
  Timer? _refreshTimer;

  @override
  void initState() {
    super.initState();
    _match = widget.match;
    _isOnline = widget.isOnline;
    _refreshTimer = Timer.periodic(const Duration(seconds: 8), (_) => _refreshMatch());
  }

  @override
  void didUpdateWidget(covariant MatchDetailScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.isOnline != widget.isOnline) {
      _isOnline = widget.isOnline;
      if (widget.isOnline) _refreshMatch();
    }
  }

  @override
  void dispose() {
    _refreshTimer?.cancel();
    super.dispose();
  }

  Future<void> _refreshMatch() async {
    try {
      final reachable = await ApiService.checkHealth();
      if (mounted && reachable != _isOnline) setState(() => _isOnline = reachable);
      if (!reachable) return;
      final matchId = _match["match_id"];
      final response = await http.get(Uri.parse("${ApiService.baseUrl}/audit/v1/score/$matchId"))
          .timeout(const Duration(seconds: 4));
      if (response.statusCode == 200 && mounted) {
        setState(() => _match = jsonDecode(response.body));
      }
    } catch (_) {
      // Keep showing the last known score when the connection drops.
    }
  }

  @override
  Widget build(BuildContext context) {
    final match = _match;
    final teamNames = widget.teamNames;
    final isOnline = _isOnline;
    final onOpenStore = widget.onOpenStore;
    final home = Map<String, dynamic>.from(match["home"] as Map);
    final away = Map<String, dynamic>.from(match["away"] as Map);
    final homeName = teamNames[home["team_id"]]?.toString() ?? home["team_id"].toString();
    final awayName = teamNames[away["team_id"]]?.toString() ?? away["team_id"].toString();
    final events = match["events"] is List ? List<dynamic>.from(match["events"] as List) : <dynamic>[];
    return Scaffold(
      appBar: AppBar(
        title: Text("Match ${match['match_id']}"),
        actions: [
          ConnectionBadge(online: isOnline),
          IconButton(tooltip: "Refresh score", onPressed: _refreshMatch, icon: const Icon(Icons.refresh)),
        ],
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Card(
              color: Colors.blue[900],
              child: Padding(
                padding: const EdgeInsets.symmetric(vertical: 24, horizontal: 16),
                child: Column(
                  children: [
                    Text(
                      "$homeName vs $awayName",
                      style: const TextStyle(color: Colors.white70, fontSize: 16),
                    ),
                    const SizedBox(height: 8),
                    Text(
                      "${home['score']} - ${away['score']}",
                      style: const TextStyle(color: Colors.white, fontSize: 36, fontWeight: FontWeight.bold),
                    ),
                    const SizedBox(height: 4),
                    Chip(
                      label: Text(match['status'].toUpperCase()),
                      backgroundColor: const Color(0xFFE4B557),
                    )
                  ],
                ),
              ),
            ),
            const SizedBox(height: 16),
            const Text("Match timeline", style: TextStyle(fontWeight: FontWeight.w800, fontSize: 18, color: Color(0xFF10284B))),
            const SizedBox(height: 8),
            if (events.isEmpty) const EmptyState(icon: Icons.timeline, title: "No events recorded", message: "Match events will appear here as the scorekeeper updates play."),
            ...events.where((event) => event is Map && event["type"] != "match_init").map((dynamic event) {
              final item = Map<String, dynamic>.from(event as Map);
              final type = item["type"]?.toString() ?? "event";
              final payload = item["payload"] is Map ? Map<String, dynamic>.from(item["payload"] as Map) : <String, dynamic>{};
              final teamId = payload["team_id"]?.toString();
              final team = teamNames[teamId]?.toString() ?? teamId ?? "Match official";
              final description = type == "score"
                  ? "${payload['event_type']?.toString().replaceAll('_', ' ') ?? 'score'} · $team"
                  : type == "compensation"
                      ? "Scoring correction recorded"
                      : type == "match_start" ? "Match started" : type.replaceAll("_", " ");
              final compensated = item["compensated"] == true;
              return Card(child: ListTile(
                dense: true,
                leading: Icon(type == "score" ? Icons.sports_soccer : Icons.flag_outlined,
                  color: compensated ? Colors.blueGrey : const Color(0xFF1CA987)),
                title: Text(description, style: TextStyle(decoration: compensated ? TextDecoration.lineThrough : null)),
                subtitle: Text("Event ${item['sequence'] ?? ''}${compensated ? ' · corrected' : ''}"),
              ));
            }),
            const SizedBox(height: 16),
            const Text("Pinned Match Merchandise", style: TextStyle(fontWeight: FontWeight.bold, fontSize: 18)),
            const SizedBox(height: 8),
            Card(
              child: ListTile(
                leading: const Icon(Icons.checkroom, size: 36, color: Colors.blue),
                title: const Text("Official Alpha Jersey 2026"),
                subtitle: const Text("PKR 4500.00 • In Stock"),
                trailing: FilledButton(
                  onPressed: () { Navigator.pop(context); onOpenStore(); },
                  child: const Text("Buy"),
                ),
              ),
            ),
            const SizedBox(height: 16),
            if (ApiService.currentRole == "league_organizer" || ApiService.currentRole == "super_admin") ...[
              ElevatedButton.icon(
                style: ElevatedButton.styleFrom(
                  backgroundColor: Colors.teal[800],
                  foregroundColor: Colors.white,
                  padding: const EdgeInsets.all(14),
                ),
                icon: const Icon(Icons.scoreboard),
                label: const Text("Organizer: Enter Match Scoring"),
                onPressed: () {
                  Navigator.push(
                    context,
                    MaterialPageRoute(
                      builder: (_) => ScoringEntryScreen(matchId: match['match_id'],
                        homeTeamId: home['team_id'].toString(), awayTeamId: away['team_id'].toString(),
                        baseVersion: match['version'] as int? ?? 0,
                        compensatableEventIds: events.where((event) => event is Map &&
                          ["score", "discipline", "tiebreak_winner"].contains(event["type"]) && event["compensated"] != true)
                          .map((event) => (event as Map)["event_id"].toString()).toList(),
                        isOnline: isOnline),
                    ),
                  );
                },
              )
            ]
          ],
        ),
      ),
    );
  }
}

// ==========================================
// 3. STANDINGS SCREEN
// ==========================================
class StandingsScreen extends StatefulWidget {
  final bool isOnline;
  const StandingsScreen({super.key, required this.isOnline});

  @override
  State<StandingsScreen> createState() => _StandingsScreenState();
}

class _StandingsScreenState extends State<StandingsScreen> {
  List<dynamic> _table = [];
  Map<String, dynamic> _teamNames = {};
  int? _qualificationSpots;
  bool _loading = true;
  String? _loadMessage;

  @override
  void initState() {
    super.initState();
    _loadStandings();
  }

  @override
  void didUpdateWidget(covariant StandingsScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.isOnline != widget.isOnline && widget.isOnline) _loadStandings();
  }

  Future<void> _loadStandings() async {
    if (mounted) setState(() { _loading = true; _loadMessage = null; });
    final data = await ApiService.loadOverview(online: widget.isOnline);
    final standings = data["standings"];
    final table = standings is Map ? standings["table"] : null;
    if (mounted) {
      setState(() {
        _table = table is List ? table : [];
        _teamNames = data["team_names"] is Map ? Map<String, dynamic>.from(data["team_names"] as Map) : {};
        _qualificationSpots = standings is Map ? (standings["qualification_spots"] as num?)?.toInt() : null;
        _loading = false;
        if (_table.isEmpty) {
          _loadMessage = widget.isOnline
              ? "Standings will appear after the league has fixtures and results."
              : "No saved standings are available offline. Connect to load the latest table.";
        }
      });
    }
  }

  Color _getStatusColor(String status) {
    if (status == "clinched") return Colors.green[700]!;
    if (status == "eliminated") return Colors.red[700]!;
    return Colors.blue[700]!;
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_table.isEmpty) {
      return RefreshIndicator(
        onRefresh: _loadStandings,
        child: ListView(children: [
          const SizedBox(height: 90),
          EmptyState(icon: Icons.leaderboard_outlined, title: "Standings unavailable", message: _loadMessage ?? "Pull down to try again."),
        ]),
      );
    }
    return SingleChildScrollView(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const Text("League table", style: TextStyle(fontSize: 24, fontWeight: FontWeight.w800, color: Color(0xFF10284B))),
          const SizedBox(height: 4),
          Text("League Alpha${_qualificationSpots == null ? '' : ' · ${_qualificationSpots!} qualifying spots'}",
            style: TextStyle(color: Colors.blueGrey[600])),
          const SizedBox(height: 16),
          Card(
            child: SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: DataTable(
            columnSpacing: 18,
            headingRowHeight: 48,
            dataRowMinHeight: 54,
            dataRowMaxHeight: 62,
            columns: const [
              DataColumn(label: Text("#")),
              DataColumn(label: Text("Team")),
              DataColumn(label: Text("P")),
              DataColumn(label: Text("Pts")),
              DataColumn(label: Text("Diff")),
              DataColumn(label: Text("Status")),
            ],
            rows: _table.map((row) {
              return DataRow(
                cells: [
                  DataCell(Text("${row['rank']}")),
                  DataCell(Text((_teamNames[row['team_id']]?.toString() ?? row['team_id'].toString()),
                    style: const TextStyle(fontWeight: FontWeight.bold))),
                  DataCell(Text("${row['played']}")),
                  DataCell(Text("${row['points']}", style: const TextStyle(fontWeight: FontWeight.bold))),
                  DataCell(Text("${row['diff']}")),
                  DataCell(
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                      decoration: BoxDecoration(
                        color: _getStatusColor(row['status']),
                        borderRadius: BorderRadius.circular(4),
                      ),
                      child: Text(
                        row['status'],
                        style: const TextStyle(color: Colors.white, fontSize: 11),
                      ),
                    ),
                  ),
                ],
              );
            }).toList(),
            ),
          ),
          ),
        ],
      ),
    );
  }
}

// ==========================================
// 4. CLUB PROFILE SCREEN
// ==========================================
class ClubProfileScreen extends StatefulWidget {
  final bool isOnline;
  const ClubProfileScreen({super.key, required this.isOnline});

  @override
  State<ClubProfileScreen> createState() => _ClubProfileScreenState();
}

class _ClubProfileScreenState extends State<ClubProfileScreen> {
  Map<String, dynamic>? _club;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _loadClub();
  }

  @override
  void didUpdateWidget(covariant ClubProfileScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.isOnline != widget.isOnline && widget.isOnline) _loadClub();
  }

  Future<void> _loadClub() async {
    if (mounted) setState(() => _loading = true);
    final data = await ApiService.loadOverview(online: widget.isOnline);
    final club = data["club"];
    if (mounted) {
      setState(() {
        _club = club is Map && club.isNotEmpty ? Map<String, dynamic>.from(club) : null;
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_club == null) {
      return ListView(children: [
        const SizedBox(height: 90),
        EmptyState(icon: Icons.shield_outlined, title: "Club profile unavailable",
          message: widget.isOnline ? "No club profile is linked to this account." : "Connect to load your saved club profile."),
      ]);
    }
    final roster = _club!["roster"] is List ? _club!["roster"] as List<dynamic> : <dynamic>[];
    final staff = _club!["staff"] is Map ? Map<String, dynamic>.from(_club!["staff"] as Map) : <String, dynamic>{};

    return SingleChildScrollView(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Container(
            padding: const EdgeInsets.all(22),
            decoration: BoxDecoration(
              gradient: const LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight,
                colors: [Color(0xFF10284B), Color(0xFF1C4665)]),
              borderRadius: BorderRadius.circular(20),
            ),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              const BrandMark(size: 48),
              const SizedBox(height: 18),
              Text(_club!["name"]?.toString() ?? "Club", style: const TextStyle(color: Colors.white, fontSize: 24, fontWeight: FontWeight.w800)),
              const SizedBox(height: 8),
              Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                const Icon(Icons.place_outlined, size: 18, color: Color(0xFFE4B557)),
                const SizedBox(width: 6),
                Expanded(child: Text(_club!["venue"]?.toString() ?? "Venue to be confirmed",
                  style: const TextStyle(color: Colors.white70, height: 1.35))),
              ]),
            ]),
          ),
          const SizedBox(height: 16),
          Row(children: [
            const Expanded(child: Text("Club staff", style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800))),
          ]),
          const SizedBox(height: 8),
          if (staff.isNotEmpty) Card(child: Column(children: staff.entries.map((entry) => ListTile(
            leading: const Icon(Icons.badge_outlined, color: Color(0xFF1CA987)),
            title: Text(entry.value.toString()),
            subtitle: Text(entry.key[0].toUpperCase() + entry.key.substring(1)),
            dense: true,
          )).toList())) else const Text("No staff details provided."),
          const SizedBox(height: 18),
          Row(children: [
            const Expanded(child: Text("Active squad", style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800))),
            if (ApiService.currentRole == "club_manager" || ApiService.currentRole == "super_admin")
              FilledButton.tonalIcon(
                icon: const Icon(Icons.swap_horiz),
                label: const Text("Manage"),
                onPressed: () => Navigator.push(context, MaterialPageRoute(
                  builder: (_) => RosterManagementScreen(isOnline: widget.isOnline))),
              ),
          ]),
          const SizedBox(height: 8),
          if (roster.isEmpty) const Text("No players are registered in this squad.")
          else ...roster.map((dynamic player) {
            final p = Map<String, dynamic>.from(player as Map);
            final pos = p["position"]?.toString() ?? "player";
            return Padding(padding: const EdgeInsets.only(bottom: 8), child: Card(child: ListTile(
              leading: CircleAvatar(backgroundColor: const Color(0xFFE8F5F1),
                child: Text(pos.substring(0, 1).toUpperCase(), style: const TextStyle(color: Color(0xFF08775E), fontWeight: FontWeight.bold))),
              title: Text(p["name"]?.toString() ?? "Player", style: const TextStyle(fontWeight: FontWeight.w700)),
              subtitle: Text("${pos.toUpperCase()} · ${p['role'] ?? 'squad member'}"),
              trailing: const Icon(Icons.chevron_right),
            )));
          }),
        ],
      ),
    );
  }
}

// ==========================================
// 5. STORE & CHECKOUT SCREEN
// ==========================================
class StoreScreen extends StatefulWidget {
  final bool isOnline;
  const StoreScreen({super.key, required this.isOnline});

  @override
  State<StoreScreen> createState() => _StoreScreenState();
}

class _StoreScreenState extends State<StoreScreen> {
  static const MethodChannel _receiptChannel = MethodChannel('nascon.receipt');
  String _selectedVariant = "var-jersey-001-M";
  final TextEditingController _discountCtrl = TextEditingController();
  String _paymentToken = "tok_success";
  bool _processing = false;
  bool _inventoryLoading = false;
  int? _availableStock;
  Map<String, dynamic>? _checkoutDraft;
  Map<String, dynamic>? _lastReceipt;
  String? _checkoutMessage;
  bool _messageIsError = false;

  @override
  void initState() {
    super.initState();
    _restoreCheckoutDraft();
    _loadInventory();
  }

  @override
  void didUpdateWidget(covariant StoreScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.isOnline != widget.isOnline && widget.isOnline) _loadInventory();
  }

  @override
  void dispose() {
    _discountCtrl.dispose();
    super.dispose();
  }

  Future<void> _restoreCheckoutDraft() async {
    final draft = await ApiService.getCheckoutDraft();
    if (draft == null || !mounted) return;
    final items = draft["items"];
    final payment = draft["payment"];
    setState(() {
      _checkoutDraft = draft;
      if (items is List && items.isNotEmpty && items.first is Map) {
        _selectedVariant = (items.first as Map)["variant_id"]?.toString() ?? _selectedVariant;
      }
      _paymentToken = payment is Map ? payment["token"]?.toString() ?? _paymentToken : _paymentToken;
      _discountCtrl.text = draft["discount_code"]?.toString() ?? "";
      _checkoutMessage = "A previous order is awaiting confirmation. Retry safely; it uses the same order reference.";
    });
  }

  Future<void> _loadInventory() async {
    if (!widget.isOnline) return;
    if (mounted) setState(() => _inventoryLoading = true);
    try {
      final response = await http.get(Uri.parse("${ApiService.baseUrl}/audit/v1/inventory/$_selectedVariant"))
          .timeout(const Duration(seconds: 6));
      if (response.statusCode == 200) {
        final data = jsonDecode(response.body) as Map<String, dynamic>;
        if (mounted) setState(() => _availableStock = (data["stock"] as num?)?.toInt() ?? 0);
      }
    } catch (_) {
      if (mounted) setState(() => _availableStock = null);
    } finally {
      if (mounted) setState(() => _inventoryLoading = false);
    }
  }

  void _selectVariant(String variant) {
    if (_checkoutDraft != null) return;
    setState(() {
      _selectedVariant = variant;
      _availableStock = null;
      _checkoutMessage = null;
    });
    _loadInventory();
  }

  String _checkoutError(String code) {
    switch (code) {
      case "OUT_OF_STOCK": return "That size just sold out. Choose another size or refresh stock.";
      case "INVALID_DISCOUNT_CODE": return "That discount code is not valid. Check it and try again.";
      case "PAYMENT_DECLINED": return "The payment was declined. Choose another payment option.";
      case "PAYMENT_CAPTURE_FAILED": return "Payment could not be completed. No order was placed; please retry.";
      case "DUPLICATE_ORDER_ID": return "This order reference was used with different details. Start a new order.";
      case "VARIANT_NOT_FOUND": return "This product option is unavailable. Refresh the store and try again.";
      default: return "We could not place this order. Review the details and try again.";
    }
  }

  Future<void> _showReceiptDetails() async {
    final receipt = _lastReceipt;
    if (receipt == null || !mounted) return;
    final payload = receipt["payload"] is Map
        ? Map<String, dynamic>.from(receipt["payload"] as Map)
        : <String, dynamic>{};
    final orderId = payload["order_id"]?.toString() ?? "Order";
    final totalMinor = payload["total_minor"] is num
        ? (payload["total_minor"] as num).toInt()
        : 0;
    final signature = receipt["signature"]?.toString() ?? "";
    await showDialog<void>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text("Payment receipt"),
        content: SizedBox(
          width: 420,
          child: SingleChildScrollView(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text("Order reference", style: TextStyle(color: Colors.blueGrey[600], fontSize: 12)),
                SelectableText(orderId, style: const TextStyle(fontWeight: FontWeight.w700)),
                const SizedBox(height: 12),
                Text("Total paid", style: TextStyle(color: Colors.blueGrey[600], fontSize: 12)),
                Text("PKR ${(totalMinor / 100).toStringAsFixed(2)}", style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w800)),
                const SizedBox(height: 12),
                Text("Receipt signature", style: TextStyle(color: Colors.blueGrey[600], fontSize: 12)),
                SelectableText(signature, style: const TextStyle(fontSize: 11, fontFamily: 'monospace')),
                const SizedBox(height: 8),
                const Text("Payment used the simulated event checkout.", style: TextStyle(fontSize: 12)),
              ],
            ),
          ),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(dialogContext), child: const Text("Close")),
          FilledButton.icon(
            onPressed: () {
              Navigator.pop(dialogContext);
              _saveReceiptPdf();
            },
            icon: const Icon(Icons.download_outlined),
            label: const Text("Save PDF"),
          ),
        ],
      ),
    );
  }

  Future<void> _saveReceiptPdf() async {
    final receipt = _lastReceipt;
    if (receipt == null || !mounted) return;
    final payload = receipt["payload"] is Map
        ? Map<String, dynamic>.from(receipt["payload"] as Map)
        : <String, dynamic>{};
    try {
      final savedName = await _receiptChannel.invokeMethod<String>("saveAndOpen", {
        "pdfBase64": receipt["pdf_base64"],
        "orderId": payload["order_id"]?.toString() ?? "receipt",
      });
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(savedName == null
              ? "Receipt saved to your Downloads folder."
              : "Receipt saved as $savedName in Downloads/NaSCon."),
        ));
      }
    } on PlatformException {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text("Could not save this receipt. You can still view its details here.")),
      );
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text("Receipt PDF saving is available in the Android app.")),
      );
      }
    }
  }

  Future<void> _checkout() async {
    if (!widget.isOnline) {
      setState(() {
        _checkoutMessage = "The store needs a server connection to confirm stock and payment. Your basket is still here.";
        _messageIsError = true;
      });
      return;
    }
    if (_availableStock == 0) {
      setState(() { _checkoutMessage = "This size is out of stock. Choose another size."; _messageIsError = true; });
      return;
    }
    setState(() { _processing = true; _checkoutMessage = null; });
    final draft = _checkoutDraft ?? <String, dynamic>{
      "order_id": "ord-${DateTime.now().microsecondsSinceEpoch}",
      "customer_id": ApiService.currentEmail.isEmpty ? "cust-mobile-01" : ApiService.currentEmail,
      "items": [{"variant_id": _selectedVariant, "quantity": 1}],
      "discount_code": _discountCtrl.text.trim().isEmpty ? null : _discountCtrl.text.trim(),
      "payment": {"token": _paymentToken},
    };
    if (_checkoutDraft == null) {
      _checkoutDraft = Map<String, dynamic>.from(draft);
      await ApiService.saveCheckoutDraft(_checkoutDraft!);
    }
    final orderId = draft["order_id"].toString();
    try {
      final response = await http.post(
        Uri.parse("${ApiService.baseUrl}/audit/v1/checkout"),
        headers: const {"Content-Type": "application/json"},
        body: jsonEncode(draft),
      ).timeout(const Duration(seconds: 14));
      final decoded = jsonDecode(response.body);
      final data = decoded is Map<String, dynamic> ? decoded : <String, dynamic>{};
      if (response.statusCode == 200 || response.statusCode == 201) {
        await ApiService.clearCheckoutDraft();
        if (mounted) {
          setState(() {
          _lastReceipt = data["receipt"] is Map ? Map<String, dynamic>.from(data["receipt"]) : null;
          _checkoutDraft = null;
          _checkoutMessage = "Payment confirmed. Your signed receipt is ready below.";
          _messageIsError = false;
        });
        }
        if (mounted) {
          showDialog<void>(context: context, builder: (ctx) => AlertDialog(
            title: const Text("Order confirmed"),
            content: Text("Order $orderId was paid successfully. Your receipt is available in the store."),
            actions: [FilledButton(onPressed: () => Navigator.pop(ctx), child: const Text("Done"))],
          ));
        }
      } else {
        final error = data["error"];
        final code = error is Map ? error["code"]?.toString() ?? "CHECKOUT_FAILED" : "CHECKOUT_FAILED";
        await ApiService.clearCheckoutDraft();
        if (mounted) {
          setState(() {
          _checkoutDraft = null;
          _checkoutMessage = _checkoutError(code);
          _messageIsError = true;
        });
        }
      }
    } on TimeoutException {
      if (mounted) {
        setState(() {
        _checkoutMessage = "The server took too long to confirm this order. We saved its reference; retrying is safe.";
        _messageIsError = true;
      });
      }
    } catch (_) {
      if (mounted) {
        setState(() {
        _checkoutMessage = "We could not reach the event server. Check its address and connection, then retry this saved order.";
        _messageIsError = true;
      });
      }
    } finally {
      if (mounted) {
        setState(() => _processing = false);
        _loadInventory();
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final discounted = _discountCtrl.text.trim().toUpperCase() == "WELCOME10";
    return SingleChildScrollView(
      padding: const EdgeInsets.all(16),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        const Text("Club store", style: TextStyle(fontSize: 24, fontWeight: FontWeight.w800, color: Color(0xFF10284B))),
        const SizedBox(height: 4),
        Text("Official merchandise · demo checkout", style: TextStyle(color: Colors.blueGrey[600])),
        const SizedBox(height: 16),
        if (!widget.isOnline) Container(
          padding: const EdgeInsets.all(14), margin: const EdgeInsets.only(bottom: 14),
          decoration: BoxDecoration(color: const Color(0xFFFFF4DD), borderRadius: BorderRadius.circular(14)),
          child: const Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Icon(Icons.wifi_off, color: Color(0xFF8A5A13)), SizedBox(width: 10),
            Expanded(child: Text("Checkout is paused while the server is unreachable. Check the server address in Account.")),
          ]),
        ),
        if (_checkoutMessage != null) Container(
          padding: const EdgeInsets.all(14), margin: const EdgeInsets.only(bottom: 14),
          decoration: BoxDecoration(
            color: _messageIsError ? const Color(0xFFFFF1EF) : const Color(0xFFE9F6F1),
            borderRadius: BorderRadius.circular(14),
            border: Border.all(color: _messageIsError ? const Color(0xFFF0C9C4) : const Color(0xFFC9E7DA)),
          ),
          child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Icon(_messageIsError ? Icons.info_outline : Icons.check_circle_outline,
              color: _messageIsError ? const Color(0xFF9E342C) : const Color(0xFF08775E)),
            const SizedBox(width: 10),
            Expanded(child: Text(_checkoutMessage!, style: TextStyle(
              color: _messageIsError ? const Color(0xFF7C302A) : const Color(0xFF075D49)))),
          ]),
        ),
        Card(
          clipBehavior: Clip.antiAlias,
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Container(
              height: 176,
              decoration: const BoxDecoration(gradient: LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight,
                colors: [Color(0xFF10284B), Color(0xFF1B765F)])),
              child: Stack(alignment: Alignment.center, children: [
                Positioned(right: -20, top: -32, child: Container(width: 170, height: 170,
                  decoration: BoxDecoration(shape: BoxShape.circle, border: Border.all(color: const Color(0x33FFFFFF), width: 2)))),
                Positioned(left: 18, top: 18, child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                  decoration: BoxDecoration(color: const Color(0xFFE4B557), borderRadius: BorderRadius.circular(30)),
                  child: const Text("OFFICIAL KIT", style: TextStyle(fontSize: 10, fontWeight: FontWeight.w900, letterSpacing: 1.1, color: Color(0xFF10284B))),
                )),
                const Icon(Icons.sports_soccer, size: 92, color: Colors.white),
                const Positioned(bottom: 14, right: 16, child: BrandMark(size: 36)),
              ]),
            ),
            Padding(
              padding: const EdgeInsets.all(18),
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                const Text("NaSCon Club Jersey", style: TextStyle(fontSize: 21, fontWeight: FontWeight.w800, color: Color(0xFF10284B))),
                const SizedBox(height: 4),
                Text("Season '26 · match-day edition", style: TextStyle(color: Colors.blueGrey[600])),
                const SizedBox(height: 12),
                Row(children: [
                  const Text("PKR 4,500", style: TextStyle(fontSize: 20, fontWeight: FontWeight.w800, color: Color(0xFF08775E))),
                  const Spacer(),
                  if (_inventoryLoading) const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2))
                  else if (_availableStock != null) Text(_availableStock! > 0 ? "${_availableStock!} in stock" : "Sold out",
                    style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: _availableStock! > 0 ? const Color(0xFF08775E) : const Color(0xFF9E342C)))
                  else Text(widget.isOnline ? "Stock unavailable" : "Stock checks when online",
                    style: TextStyle(fontSize: 12, color: Colors.blueGrey[500])),
                ]),
                const SizedBox(height: 18),
                const Text("Choose a size", style: TextStyle(fontWeight: FontWeight.w700)),
                const SizedBox(height: 8),
                Wrap(spacing: 8, children: [
                  ChoiceChip(label: const Text("Medium"), selected: _selectedVariant == "var-jersey-001-M",
                    onSelected: _checkoutDraft != null ? null : (_) => _selectVariant("var-jersey-001-M")),
                  ChoiceChip(label: const Text("Large"), selected: _selectedVariant == "var-jersey-001-L",
                    onSelected: _checkoutDraft != null ? null : (_) => _selectVariant("var-jersey-001-L")),
                ]),
                const SizedBox(height: 14),
                TextField(controller: _discountCtrl, enabled: _checkoutDraft == null,
                  textCapitalization: TextCapitalization.characters,
                  decoration: const InputDecoration(labelText: "Discount code (optional)", prefixIcon: Icon(Icons.local_offer_outlined)),
                  onChanged: (_) => setState(() {})),
                const SizedBox(height: 12),
                DropdownButtonFormField<String>(
                  isExpanded: true,
                  initialValue: _paymentToken,
                  decoration: const InputDecoration(labelText: "Demo payment", prefixIcon: Icon(Icons.credit_card_outlined)),
                  items: const [
                    DropdownMenuItem(value: "tok_success", child: Text("Test payment · success", overflow: TextOverflow.ellipsis)),
                    DropdownMenuItem(value: "tok_declined", child: Text("Test payment · declined", overflow: TextOverflow.ellipsis)),
                    DropdownMenuItem(value: "tok_capture_fail", child: Text("Test payment · capture failure", overflow: TextOverflow.ellipsis)),
                  ],
                  onChanged: _checkoutDraft != null ? null : (value) { if (value != null) setState(() => _paymentToken = value); },
                ),
                const SizedBox(height: 16),
                Row(children: [
                  const Text("Estimated total", style: TextStyle(fontWeight: FontWeight.w600)), const Spacer(),
                  Text(discounted ? "PKR 4,050" : "PKR 4,500",
                    style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w800, color: Color(0xFF10284B))),
                ]),
                const SizedBox(height: 16),
                FilledButton.icon(
                  onPressed: _processing || !widget.isOnline || _availableStock == 0 ? null : _checkout,
                  icon: _processing
                    ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                    : const Icon(Icons.lock_outline),
                  label: Text(_processing ? "Confirming order…" : "Confirm demo purchase"),
                  style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(50)),
                ),
                const SizedBox(height: 8),
                Text("Payments are simulated for the NaSCon demo environment.", textAlign: TextAlign.center,
                  style: TextStyle(fontSize: 11, color: Colors.blueGrey[500])),
              ]),
            ),
          ]),
        ),
        if (_lastReceipt != null) ...[
          const SizedBox(height: 16),
          Card(color: const Color(0xFFE9F6F1), child: ListTile(
            leading: const Icon(Icons.verified_outlined, color: Color(0xFF08775E), size: 32),
            title: Text("Receipt · ${(_lastReceipt!["payload"] as Map?)?["order_id"] ?? "Order"}", style: const TextStyle(fontWeight: FontWeight.w700)),
            subtitle: const Text("Payment confirmed · view or save the signed receipt"),
            onTap: _showReceiptDetails,
            trailing: IconButton(
              tooltip: "Save receipt PDF",
              onPressed: _saveReceiptPdf,
              icon: const Icon(Icons.download_outlined, color: Color(0xFF08775E)),
            ),
          )),
        ],
      ]),
    );
  }
}

// ==========================================
// 6. ROSTER MANAGEMENT & OFFLINE QUEUEING
// ==========================================
class RosterManagementScreen extends StatefulWidget {
  final bool isOnline;
  const RosterManagementScreen({super.key, required this.isOnline});

  @override
  State<RosterManagementScreen> createState() => _RosterManagementScreenState();
}

class _RosterManagementScreenState extends State<RosterManagementScreen> {
  String _selectedPlayer = "player-alpha-3";
  String _actionType = "transfer";
  String _toSquad = "club-alpha-2";
  final TextEditingController _reasonCtrl = TextEditingController(text: "Tactical loan");

  Future<void> _submitRosterAction() async {
    final actId = "a-${DateTime.now().microsecondsSinceEpoch}";
    final payload = {
      "request_id": "tr-$actId",
      "league_id": "league-alpha-1",
      "player_id": _selectedPlayer,
      "type": _actionType,
      "from_squad_id": "club-alpha-1",
      "to_squad_id": _actionType == "release" ? null : _toSquad,
      "effective_date": DateTime.now().toIso8601String().substring(0, 10),
      "loan_end_date": _actionType == "loan" ? DateTime.now().add(const Duration(days: 90)).toIso8601String().substring(0, 10) : null,
      "reason": _reasonCtrl.text,
      "requested_by": "manager@nascon.test"
    };

    if (widget.isOnline) {
      try {
        final res = await http.post(
          Uri.parse("${ApiService.baseUrl}/audit/v1/roster/transfer"),
          headers: {"Content-Type": "application/json"},
          body: jsonEncode(payload),
        ).timeout(const Duration(seconds: 6));
        if (res.statusCode == 200 || res.statusCode == 201) {
          if (!mounted) return;
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text("Roster action applied successfully!")),
          );
          Navigator.pop(context);
          return;
        }
        dynamic decoded;
        try {
          decoded = jsonDecode(res.body);
        } catch (_) {
          if (mounted) {
            ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text("The server returned an unreadable response. Your move was not queued.")),
          );
          }
          return;
        }
        final error = decoded is Map ? decoded["error"] : null;
        final code = error is Map ? error["code"]?.toString() ?? "VALIDATION_ERROR" : "VALIDATION_ERROR";
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(SnackBar(
            content: Text(explainApiError(code)),
            backgroundColor: const Color(0xFF9E342C),
          ));
        }
        return;
      } on TimeoutException {
        // Queue with the same request ID; server-side idempotency makes retry safe.
      } catch (_) {
        // Network loss: persist the request for synchronization.
      }
    }

    // Offline Queueing (Section 8.4 Desk Check requirement!)
    await ApiService.queueOfflineAction({
      "action_id": actId,
      "type": "roster_transfer",
      "queued_at": _utcTimestamp(DateTime.now()),
      "base_version": 0,
      "payload": payload,
    });

    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text("Offline: Roster action queued locally! Will sync when online."),
          backgroundColor: Colors.amber,
        ),
      );
      Navigator.pop(context);
    }
  }

  @override
  void dispose() {
    _reasonCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text("Manage Squad Roster"), actions: [ConnectionBadge(online: widget.isOnline)]),
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(16),
          child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            DropdownButtonFormField<String>(
              isExpanded: true,
              initialValue: _selectedPlayer,
              decoration: const InputDecoration(labelText: "Player", border: OutlineInputBorder()),
              items: const [
                DropdownMenuItem(value: "player-alpha-1", child: Text("Ali Raza · Goalkeeper", overflow: TextOverflow.ellipsis)),
                DropdownMenuItem(value: "player-alpha-3", child: Text("Bilal Ahmed · Defender", overflow: TextOverflow.ellipsis)),
              ],
              onChanged: (v) => setState(() => _selectedPlayer = v!),
            ),
            const SizedBox(height: 12),
            DropdownButtonFormField<String>(
              isExpanded: true,
              initialValue: _actionType,
              decoration: const InputDecoration(labelText: "Action Type", border: OutlineInputBorder()),
              items: const [
                DropdownMenuItem(value: "transfer", child: Text("Permanent Transfer", overflow: TextOverflow.ellipsis)),
                DropdownMenuItem(value: "loan", child: Text("Loan with Auto-Return", overflow: TextOverflow.ellipsis)),
                DropdownMenuItem(value: "release", child: Text("Release Player", overflow: TextOverflow.ellipsis)),
              ],
              onChanged: (v) => setState(() => _actionType = v!),
            ),
            const SizedBox(height: 12),
            if (_actionType != "release")
              DropdownButtonFormField<String>(
                isExpanded: true,
                initialValue: _toSquad,
                decoration: const InputDecoration(labelText: "Destination Squad", border: OutlineInputBorder()),
                items: const [
                  DropdownMenuItem(value: "club-alpha-2", child: Text("Club Alpha 2", overflow: TextOverflow.ellipsis)),
                ],
                onChanged: (v) => setState(() => _toSquad = v!),
              ),
            const SizedBox(height: 12),
            TextField(
              controller: _reasonCtrl,
              decoration: const InputDecoration(labelText: "Reason", border: OutlineInputBorder()),
            ),
            const SizedBox(height: 24),
            ElevatedButton(
              style: ElevatedButton.styleFrom(
                minimumSize: const Size.fromHeight(48),
                backgroundColor: Colors.teal[800],
                foregroundColor: Colors.white,
              ),
              onPressed: _submitRosterAction,
              child: const Text("Submit Roster Move"),
            ),
          ],
          ),
        ),
      ),
    );
  }
}

// ==========================================
// 7. SCORING ENTRY & OFFLINE QUEUEING
// ==========================================
class ScoringEntryScreen extends StatefulWidget {
  final String matchId;
  final String homeTeamId;
  final String awayTeamId;
  final int baseVersion;
  final List<String> compensatableEventIds;
  final bool isOnline;
  const ScoringEntryScreen({super.key, required this.matchId, required this.homeTeamId,
    required this.awayTeamId, required this.baseVersion, required this.compensatableEventIds,
    required this.isOnline});

  @override
  State<ScoringEntryScreen> createState() => _ScoringEntryScreenState();
}

class _ScoringEntryScreenState extends State<ScoringEntryScreen> {
  late String _selectedTeam;
  String _eventType = "goal";
  String _actionMode = "score";
  String? _selectedCompensation;
  final TextEditingController _reasonCtrl = TextEditingController();

  @override
  void initState() {
    super.initState();
    _selectedTeam = widget.homeTeamId;
    _selectedCompensation = widget.compensatableEventIds.isEmpty ? null : widget.compensatableEventIds.last;
  }

  @override
  void dispose() {
    _reasonCtrl.dispose();
    super.dispose();
  }

  Future<void> _submitScore() async {
    final eid = "evt-${DateTime.now().microsecondsSinceEpoch}";
    if (_actionMode == "compensation" && (_selectedCompensation == null || _reasonCtrl.text.trim().isEmpty)) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("Choose a score event and explain the correction.")));
      return;
    }
    final payload = _actionMode == "compensation"
        ? {"event_id": eid, "match_id": widget.matchId, "type": "compensation",
            "payload": {"compensates": _selectedCompensation, "reason": _reasonCtrl.text.trim()}}
        : {"event_id": eid, "match_id": widget.matchId, "type": "score",
            "payload": {"team_id": _selectedTeam, "event_type": _eventType}};

    if (widget.isOnline) {
      try {
        final res = await http.post(
          Uri.parse("${ApiService.baseUrl}/audit/v1/events"),
          headers: {"Content-Type": "application/json"},
          body: jsonEncode(payload),
        ).timeout(const Duration(seconds: 6));
        if (res.statusCode == 201 || res.statusCode == 200) {
          if (!mounted) return;
          ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("Score event appended!")));
          Navigator.pop(context);
          return;
        }
        dynamic decoded;
        try {
          decoded = jsonDecode(res.body);
        } catch (_) {
          if (mounted) {
            ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text("The server returned an unreadable response. The score was not queued.")),
          );
          }
          return;
        }
        final error = decoded is Map ? decoded["error"] : null;
        final code = error is Map ? error["code"]?.toString() ?? "VALIDATION_ERROR" : "VALIDATION_ERROR";
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(SnackBar(
            content: Text(explainApiError(code)),
            backgroundColor: const Color(0xFF9E342C),
          ));
        }
        return;
      } on TimeoutException {
        // The request may have reached the server. Keep its event ID for an idempotent retry.
      } catch (_) {
        // The server is unavailable; queue this event locally.
      }
    }

    // Offline Queueing (Section 8.4 Desk Check requirement!)
    await ApiService.queueOfflineAction({
      "action_id": eid,
      "type": "score_event",
      "queued_at": _utcTimestamp(DateTime.now()),
      "base_version": widget.baseVersion,
      "payload": payload,
    });

    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text("Offline: Score queued locally! Will sync when network reconnects."),
          backgroundColor: Colors.amber,
        ),
      );
      Navigator.pop(context);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text("Scoring · ${widget.matchId}"),
        actions: [ConnectionBadge(online: widget.isOnline)],
      ),
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(16),
          child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            DropdownButtonFormField<String>(
              isExpanded: true,
              initialValue: _actionMode,
              decoration: const InputDecoration(labelText: "Action", prefixIcon: Icon(Icons.edit_note)),
              items: const [
                DropdownMenuItem(value: "score", child: Text("Record score", overflow: TextOverflow.ellipsis)),
                DropdownMenuItem(value: "compensation", child: Text("Correct a previous score", overflow: TextOverflow.ellipsis)),
              ],
              onChanged: (value) => setState(() => _actionMode = value ?? "score"),
            ),
            const SizedBox(height: 12),
            if (_actionMode == "score") ...[
              DropdownButtonFormField<String>(
                isExpanded: true,
                initialValue: _selectedTeam,
                decoration: const InputDecoration(labelText: "Scoring team", prefixIcon: Icon(Icons.shield_outlined)),
                items: [
                  DropdownMenuItem(value: widget.homeTeamId, child: Text(widget.homeTeamId.toUpperCase(), overflow: TextOverflow.ellipsis)),
                  DropdownMenuItem(value: widget.awayTeamId, child: Text(widget.awayTeamId.toUpperCase(), overflow: TextOverflow.ellipsis)),
                ],
                onChanged: (v) { if (v != null) setState(() => _selectedTeam = v); },
              ),
              const SizedBox(height: 12),
            DropdownButtonFormField<String>(
              isExpanded: true,
              initialValue: _eventType,
              decoration: const InputDecoration(labelText: "Scoring event", prefixIcon: Icon(Icons.sports_soccer)),
              items: const [
                DropdownMenuItem(value: "goal", child: Text("Goal (1 pt)", overflow: TextOverflow.ellipsis)),
                DropdownMenuItem(value: "two_pointer", child: Text("Two Pointer (2 pts)", overflow: TextOverflow.ellipsis)),
                DropdownMenuItem(value: "six", child: Text("Six (6 runs)", overflow: TextOverflow.ellipsis)),
              ],
              onChanged: (v) => setState(() => _eventType = v!),
            ),
            ] else ...[
              if (widget.compensatableEventIds.isEmpty)
                const EmptyState(icon: Icons.undo, title: "No score event to correct", message: "Score a goal first, then you can add a correction.")
              else DropdownButtonFormField<String>(
                isExpanded: true,
                initialValue: _selectedCompensation,
                decoration: const InputDecoration(labelText: "Event to correct", prefixIcon: Icon(Icons.undo)),
                items: widget.compensatableEventIds.map((id) => DropdownMenuItem(value: id, child: Text(id, overflow: TextOverflow.ellipsis))).toList(),
                onChanged: (id) => setState(() => _selectedCompensation = id),
              ),
              const SizedBox(height: 12),
              TextField(controller: _reasonCtrl, maxLines: 2,
                decoration: const InputDecoration(labelText: "Correction reason", prefixIcon: Icon(Icons.notes_outlined))),
            ],
            const SizedBox(height: 24),
            FilledButton.icon(
              style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(48)),
              onPressed: _actionMode == "compensation" && widget.compensatableEventIds.isEmpty ? null : _submitScore,
              icon: const Icon(Icons.check),
              label: Text(widget.isOnline ? "Save match event" : "Queue event for sync"),
            ),
          ],
          ),
        ),
      ),
    );
  }
}

// ==========================================
// 8. ACCOUNT, SERVER CONFIG & DEMO SWITCHER
// ==========================================
class AccountScreen extends StatelessWidget {
  final VoidCallback onRoleChanged;
  final bool isOnline;
  final int pendingCount;
  final List<Map<String, dynamic>> pendingActions;
  final VoidCallback onSync;
  final Future<void> Function() onLogout;

  const AccountScreen({
    super.key,
    required this.onRoleChanged,
    required this.isOnline,
    required this.pendingCount,
    required this.pendingActions,
    required this.onSync,
    required this.onLogout,
  });

  Future<void> _switchAccount(BuildContext context, String email) async {
    if (!isOnline) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text("Connect to the event server to switch demo accounts.")),
      );
      return;
    }
    final passwordController = TextEditingController();
    final password = await showDialog<String>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text("Sign in as $email"),
        content: TextField(
          controller: passwordController,
          autofocus: true,
          obscureText: true,
          decoration: const InputDecoration(labelText: "Password"),
          onSubmitted: (value) => Navigator.pop(dialogContext, value),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(dialogContext), child: const Text("Cancel")),
          FilledButton(onPressed: () => Navigator.pop(dialogContext, passwordController.text), child: const Text("Sign in")),
        ],
      ),
    );
    passwordController.dispose();
    if (password == null || !context.mounted) return;
    try {
      await ApiService.login(email, password);
      onRoleChanged();
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Signed in as $email")));
      }
    } on ApiException catch (error) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(error.message)));
      }
    } catch (_) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("Could not sign in. Check the server connection.")));
      }
    }
  }

  Future<void> _configureServer(BuildContext context) async {
    final controller = TextEditingController(text: ApiService.baseUrl);
    final entered = await showDialog<String>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text("Event server"),
        content: TextField(
          controller: controller,
          keyboardType: TextInputType.url,
          decoration: const InputDecoration(
            labelText: "Server address",
            hintText: "http://192.168.1.20:8080",
            helperText: "Use your computer's LAN address on a physical phone.",
          ),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(dialogContext), child: const Text("Cancel")),
          FilledButton(onPressed: () => Navigator.pop(dialogContext, controller.text.trim()), child: const Text("Save & test")),
        ],
      ),
    );
    controller.dispose();
    if (entered == null || entered.isEmpty || !context.mounted) return;
    try {
      await ApiService.setServerUrl(entered);
      final reachable = await ApiService.checkHealth();
      onRoleChanged();
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(reachable
              ? "Server is reachable at ${ApiService.baseUrl}"
              : "Address saved, but ${ApiService.baseUrl} did not respond. Check that the API is running."),
          backgroundColor: reachable ? const Color(0xFF08775E) : const Color(0xFF8A5A13),
        ));
      }
    } on FormatException catch (error) {
      if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(error.message)));
    } catch (_) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text("The address could not be saved. Please try again.")),
      );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final roleLabel = ApiService.currentRole.replaceAll("_", " ").toUpperCase();
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Container(
          padding: const EdgeInsets.all(18),
          decoration: BoxDecoration(
            gradient: const LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight,
              colors: [Color(0xFF10284B), Color(0xFF1C4665)]),
            borderRadius: BorderRadius.circular(20),
          ),
          child: Row(children: [
            const CircleAvatar(radius: 25, backgroundColor: Color(0x33FFFFFF),
              child: Icon(Icons.person_outline, color: Colors.white, size: 28)),
            const SizedBox(width: 14),
            Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(ApiService.currentEmail.isEmpty ? "Account" : ApiService.currentEmail,
                maxLines: 1, overflow: TextOverflow.ellipsis,
                style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w800, fontSize: 16)),
              const SizedBox(height: 4),
              Text(roleLabel, style: const TextStyle(color: Color(0xFFD1E3E8), fontSize: 12, letterSpacing: .6)),
            ])),
            IconButton(tooltip: "Sign out", onPressed: onLogout,
              icon: const Icon(Icons.logout, color: Colors.white)),
          ]),
        ),
        const SizedBox(height: 16),
        Card(child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              const Icon(Icons.dns_outlined, color: Color(0xFF1CA987)),
              const SizedBox(width: 10),
              const Expanded(child: Text("Server connection", style: TextStyle(fontSize: 16, fontWeight: FontWeight.w800))),
              Container(width: 9, height: 9, decoration: BoxDecoration(
                color: isOnline ? const Color(0xFF1CA987) : const Color(0xFFD39B36), shape: BoxShape.circle)),
              const SizedBox(width: 6),
              Text(isOnline ? "Ready" : "Offline", style: TextStyle(
                fontWeight: FontWeight.w700, color: isOnline ? const Color(0xFF08775E) : const Color(0xFF8A5A13))),
            ]),
            const SizedBox(height: 6),
            Text(ApiService.baseUrl, style: TextStyle(fontSize: 13, color: Colors.blueGrey[600])),
            const SizedBox(height: 12),
            OutlinedButton.icon(
              onPressed: () => _configureServer(context),
              icon: const Icon(Icons.tune),
              label: const Text("Change address and test"),
              style: OutlinedButton.styleFrom(minimumSize: const Size.fromHeight(46)),
            ),
          ]),
        )),
        const SizedBox(height: 12),
        Card(child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Row(children: [
              const Icon(Icons.sync_alt, color: Color(0xFF1CA987)),
              const SizedBox(width: 10),
              const Expanded(child: Text("Offline action queue", style: TextStyle(fontSize: 16, fontWeight: FontWeight.w800))),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
                decoration: BoxDecoration(color: pendingCount > 0 ? const Color(0xFFFFF0D4) : const Color(0xFFE9F6F1), borderRadius: BorderRadius.circular(20)),
                child: Text("$pendingCount", style: TextStyle(fontWeight: FontWeight.w800,
                  color: pendingCount > 0 ? const Color(0xFF8A5A13) : const Color(0xFF08775E))),
              ),
            ]),
            const SizedBox(height: 8),
            Text(pendingCount > 0
              ? "$pendingCount action${pendingCount == 1 ? '' : 's'} saved on this device."
              : "All queued actions have been synchronized.", style: TextStyle(color: Colors.blueGrey[600])),
            if (pendingActions.isNotEmpty) ...[
              const Divider(height: 22),
              ...pendingActions.take(5).map((action) {
                final type = action["type"]?.toString();
                final label = type == "score_event" ? "Scoring update" :
                    type == "roster_transfer" ? "Roster move" : "Pending action";
                return ListTile(
                  contentPadding: EdgeInsets.zero,
                  dense: true,
                  leading: const Icon(Icons.schedule_outlined, color: Color(0xFF8A5A13)),
                  title: Text(label, style: const TextStyle(fontWeight: FontWeight.w700)),
                  subtitle: Text("${action['action_id'] ?? 'Action'} · waiting to sync"),
                );
              }),
              if (pendingActions.length > 5)
                Text("And ${pendingActions.length - 5} more", style: TextStyle(color: Colors.blueGrey[600])),
            ],
            const SizedBox(height: 12),
            FilledButton.tonalIcon(
              icon: const Icon(Icons.sync),
              label: const Text("Sync now"),
              onPressed: isOnline && pendingCount > 0 ? onSync : null,
            ),
          ]),
        )),
        const SizedBox(height: 18),
        const Text("Demo accounts", style: TextStyle(fontSize: 17, fontWeight: FontWeight.w800, color: Color(0xFF10284B))),
        const SizedBox(height: 4),
        Text("Switching signs in through the server. Credentials are in the project README.",
          style: TextStyle(fontSize: 12, color: Colors.blueGrey[600])),
        const SizedBox(height: 8),
        _demoTile(context, "Platform Super-Admin", "admin@nascon.test", Icons.admin_panel_settings_outlined),
        _demoTile(context, "League Organizer", "organizer@nascon.test", Icons.event_available_outlined),
        _demoTile(context, "Club Manager", "manager@nascon.test", Icons.shield_outlined),
        _demoTile(context, "Player", "player@nascon.test", Icons.sports_outlined),
        _demoTile(context, "General Viewer", "viewer@nascon.test", Icons.visibility_outlined),
      ],
    );
  }

  Widget _demoTile(BuildContext context, String title, String email, IconData icon) {
    final selected = ApiService.currentEmail == email;
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Card(child: ListTile(
        leading: Icon(icon, color: const Color(0xFF527184)),
        title: Text(title, style: const TextStyle(fontWeight: FontWeight.w700)),
        subtitle: Text(email),
        trailing: selected ? const Icon(Icons.check_circle, color: Color(0xFF1CA987)) : const Icon(Icons.chevron_right),
        onTap: selected ? null : () => _switchAccount(context, email),
      )),
    );
  }
}
