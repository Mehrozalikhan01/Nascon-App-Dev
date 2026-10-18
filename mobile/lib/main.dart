import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:http/http.dart' as http;
import 'package:mobile_scanner/mobile_scanner.dart';
import 'package:video_player/video_player.dart';
import 'services/api_service.dart';

const MethodChannel _deepLinkChannel = MethodChannel('nascon.deep_links');

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
  String? initialLink;
  try {
    initialLink = await _deepLinkChannel.invokeMethod<String>('initialLink');
  } catch (_) {
    // Web, desktop, and older installed builds do not expose the Android channel.
  }
  runApp(NasconLeagueApp(initialLink: initialLink));
}

class NasconLeagueApp extends StatelessWidget {
  final String? initialLink;
  const NasconLeagueApp({super.key, this.initialLink});

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
      home: DeepLinkRouter(initialLink: initialLink),
    );
  }
}

class DeepLinkRouter extends StatefulWidget {
  final String? initialLink;
  const DeepLinkRouter({super.key, this.initialLink});

  @override
  State<DeepLinkRouter> createState() => _DeepLinkRouterState();
}

class _DeepLinkRouterState extends State<DeepLinkRouter> {
  Uri? _link;

  @override
  void initState() {
    super.initState();
    _link = _parseLink(widget.initialLink);
    _deepLinkChannel.setMethodCallHandler((call) async {
      if (call.method == 'onLink' && call.arguments is String) {
        final next = _parseLink(call.arguments as String);
        if (next != null && mounted) setState(() => _link = next);
      }
    });
  }

  Uri? _parseLink(String? value) {
    if (value == null) return null;
    final uri = Uri.tryParse(value);
    return uri?.scheme == 'nascon' ? uri : null;
  }

  @override
  Widget build(BuildContext context) => _link == null
      ? const AuthGate()
      : DeepLinkLandingScreen(key: ValueKey(_link), uri: _link!);
}

class DeepLinkLandingScreen extends StatefulWidget {
  final Uri uri;
  const DeepLinkLandingScreen({super.key, required this.uri});

  @override
  State<DeepLinkLandingScreen> createState() => _DeepLinkLandingScreenState();
}

class QrScannerScreen extends StatefulWidget {
  const QrScannerScreen({super.key});

  @override
  State<QrScannerScreen> createState() => _QrScannerScreenState();
}

class _QrScannerScreenState extends State<QrScannerScreen> {
  bool _opening = false;

  Future<void> _openCode(String value) async {
    if (_opening) return;
    _opening = true;
    try {
      final scanned = Uri.tryParse(value.trim());
      if (scanned == null) throw const FormatException('This QR code is not a supported NaSCon link.');
      Uri? destination;
      if (scanned.scheme == 'nascon' && (scanned.host == 'entity' || scanned.host == 'stream')) {
        destination = scanned;
      } else if (scanned.pathSegments.length >= 2 && scanned.pathSegments.first == 'q') {
        final api = Uri.parse(ApiService.cleanBaseUrl);
        final request = http.Request('GET', api.replace(path: '/q/${scanned.pathSegments[1]}'))..followRedirects = false;
        final client = http.Client();
        final response = await client.send(request).timeout(const Duration(seconds: 5));
        client.close();
        final location = response.headers['location'];
        if (location != null) {
          final route = api.resolve(location);
          final segments = route.pathSegments;
          if (segments.length >= 3 && segments[0] == 'web' && segments[1] == 'matches') {
            destination = Uri(scheme: 'nascon', host: 'entity', path: '/${segments[2]}');
          } else if (segments.length >= 3 && segments[0] == 'web' && segments[1] == 'costream') {
            destination = Uri(scheme: 'nascon', host: 'stream', path: '/${segments[2]}');
          } else if (segments.length >= 3 && segments[0] == 'web' && segments[1] == 'players') {
            destination = Uri(scheme: 'nascon', host: 'entity', path: '/${segments[2]}');
          }
        }
      }
      if (destination == null) throw const FormatException('This QR code does not point to a supported NaSCon match or stream.');
      if (!mounted) return;
      await Navigator.of(context).push(MaterialPageRoute(builder: (_) => DeepLinkLandingScreen(uri: destination!)));
    } catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(error is FormatException ? error.message : 'Could not resolve that QR code. Check the server connection.')));
      }
    } finally {
      _opening = false;
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Scan NaSCon QR')),
    body: SafeArea(child: Column(children: [
      const Padding(padding: EdgeInsets.all(18), child: Text('Scan a match, player, club, or co-stream code to open it in NaSCon.', textAlign: TextAlign.center)),
      Expanded(child: Padding(padding: const EdgeInsets.all(18), child: ClipRRect(
        borderRadius: BorderRadius.circular(20),
        child: MobileScanner(
          onDetect: (capture) {
            for (final barcode in capture.barcodes) {
              final value = barcode.rawValue;
              if (value != null && value.isNotEmpty) { _openCode(value); break; }
            }
          },
          errorBuilder: (context, error) => Center(child: Text('Camera unavailable: ${error.errorCode.name}', textAlign: TextAlign.center)),
        ),
      ))),
    ])),
  );
}

class _DeepLinkLandingScreenState extends State<DeepLinkLandingScreen> {
  VideoPlayerController? _video;
  Map<String, dynamic>? _match;
  Map<String, dynamic>? _session;
  List<Map<String, dynamic>> _chatMessages = [];
  Timer? _chatPoll;
  String? _notice;
  bool _loading = true;

  String get _id => widget.uri.pathSegments.isEmpty ? '' : widget.uri.pathSegments.last;
  bool get _isStream => widget.uri.host == 'stream';

  @override
  void initState() {
    super.initState();
    _loading = false;
    _loadLinkedContent();
  }

  @override
  void didUpdateWidget(DeepLinkLandingScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.uri != widget.uri) {
      _chatPoll?.cancel();
      _video?.dispose();
      _video = null;
      _session = null;
      _match = null;
      _chatMessages = [];
      _loading = false;
      _loadLinkedContent();
    }
  }

  Future<void> _loadLinkedContent() async {
    if (mounted) setState(() => _loading = false);
    try {
      if (_isStream) {
        _loadChat();
        _chatPoll?.cancel();
        _chatPoll = Timer.periodic(const Duration(milliseconds: 250), (_) => _loadChat());
        final response = await http.get(Uri.parse('${ApiService.cleanBaseUrl}/api/v1/costream/${Uri.encodeComponent(_id)}'))
            .timeout(const Duration(seconds: 4));
        if (response.statusCode == 200) {
          _session = Map<String, dynamic>.from(jsonDecode(response.body) as Map);
          final url = _session?['composite_playback_url']?.toString();
          if (url != null && url.isNotEmpty) _initializeVideo(_localMediaUrl(url));
          _loadChat();
        } else {
          _notice = 'This co-stream is not available yet.';
        }
      } else {
        final response = await http.get(Uri.parse('${ApiService.cleanBaseUrl}/audit/v1/score/${Uri.encodeComponent(_id)}'))
            .timeout(const Duration(seconds: 4));
        if (response.statusCode == 200) {
          _match = Map<String, dynamic>.from(jsonDecode(response.body) as Map);
          final stream = await http.get(Uri.parse('${ApiService.cleanBaseUrl}/api/v1/matches/${Uri.encodeComponent(_id)}/playback'))
              .timeout(const Duration(seconds: 4));
          if (stream.statusCode == 200) {
            final data = jsonDecode(stream.body) as Map;
            final url = data['playback_url']?.toString();
            if (url != null && url.isNotEmpty) _initializeVideo(_localMediaUrl(url));
          }
        } else {
          _notice = 'This match is not available right now.';
        }
      }
    } catch (_) {
      _notice = 'Connect to the event server to load this live screen.';
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _loadChat() async {
    final room = _session?['chat_room_id']?.toString() ?? (_isStream ? 'chat-$_id' : null);
    if (room == null || room.isEmpty) return;
    try {
      final response = await http.get(Uri.parse('${ApiService.cleanBaseUrl}/api/v1/chat/${Uri.encodeComponent(room)}/messages'))
          .timeout(const Duration(seconds: 2));
      if (response.statusCode == 200 && mounted) {
        final data = jsonDecode(response.body) as Map;
        final items = (data['items'] as List? ?? const []).whereType<Map>().map((item) => Map<String, dynamic>.from(item)).toList();
        setState(() => _chatMessages = items);
      }
    } catch (_) {}
  }

  Uri _localMediaUrl(String value) {
    final remote = Uri.parse(value);
    final api = Uri.parse(ApiService.cleanBaseUrl);
    return remote.replace(scheme: api.scheme, host: api.host, port: 8888);
  }

  Future<void> _initializeVideo(Uri url) async {
    final controller = VideoPlayerController.networkUrl(url);
    await controller.initialize().timeout(const Duration(seconds: 5));
    if (!mounted) {
      await controller.dispose();
      return;
    }
    setState(() => _video = controller);
  }

  @override
  void dispose() {
    _chatPoll?.cancel();
    _video?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final home = _match?['home'] is Map ? (_match!['home'] as Map)['score'] : null;
    final away = _match?['away'] is Map ? (_match!['away'] as Map)['score'] : null;
    final title = _isStream ? 'Co-stream' : 'Match Live';
    return Scaffold(
      appBar: AppBar(title: Text(title), leading: IconButton(tooltip: 'Back to account', icon: const Icon(Icons.arrow_back), onPressed: () => Navigator.of(context).maybePop())),
      body: SafeArea(child: ListView(padding: const EdgeInsets.all(18), children: [
        Container(
          constraints: const BoxConstraints(minHeight: 220),
          decoration: BoxDecoration(color: const Color(0xFF071322), borderRadius: BorderRadius.circular(18)),
          clipBehavior: Clip.antiAlias,
          child: _video?.value.isInitialized == true
              ? AspectRatio(aspectRatio: _video!.value.aspectRatio, child: Stack(alignment: Alignment.bottomCenter, children: [VideoPlayer(_video!), VideoProgressIndicator(_video!, allowScrubbing: true)]))
              : const Center(child: Icon(Icons.sports_soccer, color: Colors.white70, size: 58)),
        ),
        const SizedBox(height: 20),
        Text(title, style: const TextStyle(fontSize: 25, fontWeight: FontWeight.w800, color: Color(0xFF10284B))),
        if (!_isStream && _match != null) ...[
          const SizedBox(height: 10),
          Text('${_match?['home']?['team_id'] ?? 'Home'}  ${home ?? 0} - ${away ?? 0}  ${_match?['away']?['team_id'] ?? 'Away'}', style: const TextStyle(fontSize: 19, fontWeight: FontWeight.w700)),
        ],
        if (_isStream) ...[
          const SizedBox(height: 10),
          Text('Session ${_session?['session_id'] ?? _id} · ${_session?['participant_count'] ?? 0} participants'),
          const SizedBox(height: 16),
          const Text('Live chat', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
          ..._chatMessages.map((message) => ListTile(
            dense: true,
            contentPadding: EdgeInsets.zero,
            title: Text(message['sender']?.toString() ?? 'Fan'),
            subtitle: Text(message['text']?.toString() ?? ''),
          )),
        ],
        if (_loading) const Padding(padding: EdgeInsets.only(top: 18), child: LinearProgressIndicator()),
        if (_notice != null) Padding(padding: const EdgeInsets.only(top: 12), child: Text(_notice!, style: const TextStyle(color: Color(0xFF596579)))),
        const SizedBox(height: 16),
        FilledButton.icon(onPressed: _video?.value.isInitialized == true ? () => setState(() => _video!.value.isPlaying ? _video!.pause() : _video!.play()) : null,
          icon: Icon(_video?.value.isPlaying == true ? Icons.pause : Icons.play_arrow), label: Text(_video?.value.isPlaying == true ? 'Pause stream' : 'Play stream')),
      ])),
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
          ApiService.currentTenantId = user["tenant_id"]?.toString() ?? ApiService.currentTenantId;
          ApiService.currentUserId = user["user_id"]?.toString() ?? ApiService.currentUserId;
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
      debugPrint("Sign-in request failed: $e");
      if (mounted) setState(() => _error = "Could not sign in. Check the server address and try again.");
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
                  label: const Text("LAN example (192.168.1.20)"),
                  onPressed: () => controller.text = "http://192.168.1.20:8080",
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
    case "UNAUTHENTICATED": return "Your session expired. Sign in again and retry.";
    case "FORBIDDEN": return "Your account does not have permission for this action.";
    case "VALIDATION_ERROR": return "Review the details and correct any missing or invalid fields.";
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

bool isCurrentScoringRole(String role) => role == "league_organizer" || role == "super_admin";

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
  String _selectedTenantId = "tenant-alpha";
  bool _isOnline = false;
  bool _checkingStatus = true;
  bool _syncing = false;
  bool _statusCheckInProgress = false;
  int _pendingCount = 0;
  List<Map<String, dynamic>> _pendingActions = [];
  List<Map<String, dynamic>> _syncHistory = [];
  Timer? _statusTimer;

  @override
  void initState() {
    super.initState();
    if (ApiService.currentTenantId != "global") {
      _selectedTenantId = ApiService.currentTenantId;
    }
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
    var history = <Map<String, dynamic>>[];
    try {
      online = await ApiService.checkHealth();
      queue = await ApiService.getPendingQueue();
      history = await ApiService.getSyncHistory();
    } catch (_) {
      // Keep the connection badge available even if local storage is damaged.
    } finally {
      _statusCheckInProgress = false;
      if (mounted) {
        setState(() {
          _isOnline = online;
          _pendingActions = queue;
          _pendingCount = queue.length;
          _syncHistory = history;
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

  Future<void> _clearSyncHistory() async {
    await ApiService.clearSyncHistory();
    if (mounted) setState(() => _syncHistory = []);
  }

  @override
  Widget build(BuildContext context) {
    final screens = [
      FixturesScreen(key: ValueKey("fixtures-$_selectedTenantId"), isOnline: _isOnline, tenantId: _selectedTenantId,
        onOpenStore: () => setState(() => _currentIndex = 2)),
      StandingsScreen(key: ValueKey("standings-$_selectedTenantId"), isOnline: _isOnline, tenantId: _selectedTenantId),
      StoreScreen(isOnline: _isOnline),
      ClubProfileScreen(key: ValueKey("club-$_selectedTenantId"), isOnline: _isOnline, tenantId: _selectedTenantId),
      AccountScreen(
        onRoleChanged: () {
          setState(() {
            if (ApiService.currentTenantId != "global") _selectedTenantId = ApiService.currentTenantId;
          });
          _checkStatus();
        },
        tenantId: _selectedTenantId,
        onTenantChanged: (tenant) => setState(() => _selectedTenantId = tenant),
        isOnline: _isOnline,
        pendingCount: _pendingCount,
        pendingActions: _pendingActions,
        syncHistory: _syncHistory,
        onSync: _performSync,
        onClearHistory: _clearSyncHistory,
        onLogout: widget.onLogout,
      ),
    ];

    final tenantPrimary = _selectedTenantId == "tenant-beta" ? const Color(0xFF4F3975) : const Color(0xFF10284B);
    return Scaffold(
      appBar: AppBar(
        backgroundColor: tenantPrimary,
        leading: Padding(
          padding: const EdgeInsets.all(8.0),
            child: const BrandMark(size: 38),
          ),
        title: const Text("NaSCon '26\nLeague & Club Ops", style: TextStyle(fontSize: 16, height: 1.05, fontWeight: FontWeight.w700)),
        actions: [
          // Offline / Online Status Badge
          InkWell(
            borderRadius: BorderRadius.circular(22),
            onTap: () => setState(() => _currentIndex = 4),
            child: Container(
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
          )),
        ],
      ),
      body: screens[_currentIndex],
      floatingActionButton: FloatingActionButton.extended(
        onPressed: () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => const QrScannerScreen())),
        icon: const Icon(Icons.qr_code_scanner),
        label: const Text('Scan QR'),
        tooltip: 'Scan a match, player, club, or stream QR code',
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _currentIndex,
        indicatorColor: _selectedTenantId == "tenant-beta" ? const Color(0xFFEDE5F7) : const Color(0xFFE7EEF5),
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
  final String tenantId;
  final VoidCallback onOpenStore;
  const FixturesScreen({super.key, required this.isOnline, required this.tenantId, required this.onOpenStore});

  @override
  State<FixturesScreen> createState() => _FixturesScreenState();
}

class _FixturesScreenState extends State<FixturesScreen> {
  List<dynamic> _fixtures = [];
  List<String> _leagueIds = const [];
  Map<String, dynamic> _teamNames = {};
  String _selectedLeagueId = "league-alpha-1";
  String _statusFilter = "all";
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
    final data = await ApiService.loadOverview(online: widget.isOnline, tenantId: widget.tenantId, leagueId: _selectedLeagueId);
    final fixtures = data["fixtures"];
    final leagueIds = data["league_ids"] is List
        ? (data["league_ids"] as List).map((value) => value.toString()).toList()
        : <String>[];
    if (mounted) {
      setState(() {
        _fixtures = fixtures is List ? fixtures : [];
        _leagueIds = leagueIds;
        if (_leagueIds.isNotEmpty && !_leagueIds.contains(_selectedLeagueId)) {
          _selectedLeagueId = _leagueIds.first;
        }
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
    final visibleFixtures = _fixtures.where((dynamic fixture) {
      final status = fixture is Map ? fixture["status"]?.toString() : null;
      if (_statusFilter == "live") return status == "in_progress";
      if (_statusFilter == "upcoming") return status == "scheduled";
      if (_statusFilter == "completed") return status == "completed";
      return true;
    }).toList();
    return RefreshIndicator(
      onRefresh: _loadFixtures,
      child: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          const Text("Fixtures", style: TextStyle(fontSize: 24, fontWeight: FontWeight.w800, color: Color(0xFF10284B))),
          const SizedBox(height: 4),
          Text("Live scores and upcoming matches", style: TextStyle(color: Colors.blueGrey[600])),
          if (_leagueIds.length > 1) ...[
            const SizedBox(height: 14),
            DropdownButtonFormField<String>(
              isExpanded: true,
              initialValue: _leagueIds.contains(_selectedLeagueId) ? _selectedLeagueId : _leagueIds.first,
              decoration: const InputDecoration(labelText: "Competition", prefixIcon: Icon(Icons.emoji_events_outlined)),
              items: _leagueIds.map((id) => DropdownMenuItem(value: id, child: Text(id.replaceAll("-", " ").toUpperCase()))).toList(),
              onChanged: (value) {
                if (value == null) return;
                setState(() => _selectedLeagueId = value);
                _loadFixtures();
              },
            ),
          ],
          const SizedBox(height: 12),
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Row(children: [
              for (final filter in const ["all", "live", "upcoming", "completed"])
                Padding(
                  padding: const EdgeInsets.only(right: 8),
                  child: ChoiceChip(
                    label: Text(filter[0].toUpperCase() + filter.substring(1)),
                    selected: _statusFilter == filter,
                    onSelected: (_) => setState(() => _statusFilter = filter),
                  ),
                ),
            ]),
          ),
          const SizedBox(height: 16),
          if (visibleFixtures.isEmpty)
            EmptyState(icon: Icons.filter_list_off, title: "No matching fixtures", message: "Try another status filter or competition."),
          ...visibleFixtures.map((dynamic f) {
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
      final response = await ApiService.fetchJson(
        "/audit/v1/score/${Uri.encodeComponent(matchId.toString())}",
        cacheKey: "score_$matchId",
        online: true,
      );
      if (response != null && mounted) {
        setState(() => _match = response);
      }
    } catch (_) {
      // Keep showing the last known score when the connection drops.
    }
  }

  Future<void> _recordMatchLifecycleEvent(String eventType) async {
    if (!isCurrentScoringRole(ApiService.currentRole) || !_isOnline) return;
    if (eventType == "match_end") {
      final shouldEnd = await showDialog<bool>(
        context: context,
        builder: (dialogContext) => AlertDialog(
          title: const Text("End this match?"),
          content: const Text("This finalizes the current score. Review the match timeline before confirming."),
          actions: [
            TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text("Keep live")),
            FilledButton(onPressed: () => Navigator.pop(dialogContext, true), child: const Text("End match")),
          ],
        ),
      );
      if (shouldEnd != true || !mounted) return;
    }
    try {
      final response = await http.post(
        Uri.parse("${ApiService.cleanBaseUrl}/audit/v1/events"),
        headers: ApiService.authHeaders,
        body: jsonEncode({
          "event_id": "evt-${DateTime.now().microsecondsSinceEpoch}",
          "match_id": _match["match_id"],
          "type": eventType,
          "payload": <String, dynamic>{},
        }),
      ).timeout(const Duration(seconds: 8));
      final decoded = response.body.isEmpty ? <String, dynamic>{} : jsonDecode(response.body);
      if (response.statusCode == 200 || response.statusCode == 201) {
        await _refreshMatch();
        return;
      }
      final error = decoded is Map ? decoded["error"] : null;
      final code = error is Map ? error["code"]?.toString() ?? "VALIDATION_ERROR" : "VALIDATION_ERROR";
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(explainApiError(code))));
    } catch (_) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("Could not reach the match server. No lifecycle change was confirmed.")));
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
    final matchStatus = match["status"]?.toString() ?? "scheduled";
    final canScore = isCurrentScoringRole(ApiService.currentRole);
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
                      label: Text(matchStatus.replaceAll("_", " ").toUpperCase()),
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
            if (canScore && matchStatus == "scheduled")
              FilledButton.icon(
                onPressed: isOnline ? () => _recordMatchLifecycleEvent("match_start") : null,
                icon: const Icon(Icons.play_arrow),
                label: const Text("Start match"),
                style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(48)),
              ),
            if (canScore && matchStatus == "in_progress") ...[
              FilledButton.icon(
                style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(48), backgroundColor: const Color(0xFF08775E), foregroundColor: Colors.white),
                icon: const Icon(Icons.scoreboard),
                label: const Text("Enter match scoring"),
                onPressed: () => Navigator.push(context, MaterialPageRoute(
                  builder: (_) => ScoringEntryScreen(matchId: match['match_id'],
                    homeTeamId: home['team_id'].toString(), awayTeamId: away['team_id'].toString(),
                    baseVersion: match['version'] as int? ?? 0,
                    compensatableEventIds: events.where((event) => event is Map &&
                      ["score", "discipline", "tiebreak_winner"].contains(event["type"]) && event["compensated"] != true)
                      .map((event) => (event as Map)["event_id"].toString()).toList(),
                    isOnline: isOnline),
                )),
              ),
              const SizedBox(height: 8),
              OutlinedButton.icon(
                onPressed: isOnline ? () => _recordMatchLifecycleEvent("match_end") : null,
                icon: const Icon(Icons.flag_outlined),
                label: const Text("End match"),
              ),
            ],
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
  final String tenantId;
  const StandingsScreen({super.key, required this.isOnline, required this.tenantId});

  @override
  State<StandingsScreen> createState() => _StandingsScreenState();
}

class _StandingsScreenState extends State<StandingsScreen> {
  List<dynamic> _table = [];
  Map<String, dynamic> _teamNames = {};
  Map<String, dynamic> _h2h = {};
  List<String> _leagueIds = const [];
  String _selectedLeagueId = "league-alpha-1";
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
    final data = await ApiService.loadOverview(online: widget.isOnline, tenantId: widget.tenantId, leagueId: _selectedLeagueId);
    final standings = data["standings"];
    final table = standings is Map ? standings["table"] : null;
    if (mounted) {
      setState(() {
        _table = table is List ? table : [];
        _teamNames = data["team_names"] is Map ? Map<String, dynamic>.from(data["team_names"] as Map) : {};
        _h2h = standings is Map && standings["h2h"] is Map
            ? Map<String, dynamic>.from(standings["h2h"] as Map)
            : {};
        _leagueIds = data["league_ids"] is List
            ? (data["league_ids"] as List).map((value) => value.toString()).toList()
            : <String>[];
        if (_leagueIds.isNotEmpty && !_leagueIds.contains(_selectedLeagueId)) {
          _selectedLeagueId = _leagueIds.first;
        }
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

  Future<void> _showHeadToHead(Map<String, dynamic> row) async {
    final teamId = row["team_id"]?.toString() ?? "";
    final opponents = _h2h[teamId] is Map
        ? Map<String, dynamic>.from(_h2h[teamId] as Map)
        : <String, dynamic>{};
    await showModalBottomSheet<void>(
      context: context,
      showDragHandle: true,
      builder: (context) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(20, 4, 20, 24),
          child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text("${_teamNames[teamId] ?? teamId} · Head to head", style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w800)),
            const SizedBox(height: 12),
            if (opponents.isEmpty) const Text("No head-to-head records are available yet."),
            ...opponents.entries.where((entry) => entry.key != teamId).map((entry) {
              final stats = entry.value is Map ? Map<String, dynamic>.from(entry.value as Map) : <String, dynamic>{};
              final scored = stats["scored"] ?? 0;
              final conceded = stats["conceded"] ?? 0;
              return ListTile(
                contentPadding: EdgeInsets.zero,
                title: Text(_teamNames[entry.key]?.toString() ?? entry.key),
                subtitle: Text("${stats['played'] ?? 0} played · ${stats['points'] ?? 0} points"),
                trailing: Text("$scored – $conceded", style: const TextStyle(fontWeight: FontWeight.w800)),
              );
            }),
          ]),
        ),
      ),
    );
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
          Text("${_selectedLeagueId.replaceAll('-', ' ').toUpperCase()}${_qualificationSpots == null ? '' : ' · ${_qualificationSpots!} qualifying spots'}",
            style: TextStyle(color: Colors.blueGrey[600])),
          if (_leagueIds.length > 1) ...[
            const SizedBox(height: 14),
            DropdownButtonFormField<String>(
              isExpanded: true,
              initialValue: _leagueIds.contains(_selectedLeagueId) ? _selectedLeagueId : _leagueIds.first,
              decoration: const InputDecoration(labelText: "Competition", prefixIcon: Icon(Icons.emoji_events_outlined)),
              items: _leagueIds.map((id) => DropdownMenuItem(value: id, child: Text(id.replaceAll("-", " ").toUpperCase()))).toList(),
              onChanged: (value) {
                if (value == null) return;
                setState(() => _selectedLeagueId = value);
                _loadStandings();
              },
            ),
          ],
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
              DataColumn(label: Text("W")),
              DataColumn(label: Text("D")),
              DataColumn(label: Text("L")),
              DataColumn(label: Text("GF")),
              DataColumn(label: Text("GA")),
              DataColumn(label: Text("Pts")),
              DataColumn(label: Text("Diff")),
              DataColumn(label: Text("Status")),
            ],
            rows: _table.map((row) {
              return DataRow(
                cells: [
                  DataCell(Text("${row['rank']}")),
                  DataCell(Text((_teamNames[row['team_id']]?.toString() ?? row['team_id'].toString()),
                    style: const TextStyle(fontWeight: FontWeight.bold)), onTap: () => _showHeadToHead(Map<String, dynamic>.from(row as Map))),
                  DataCell(Text("${row['played']}")),
                  DataCell(Text("${row['won']}")),
                  DataCell(Text("${row['drawn']}")),
                  DataCell(Text("${row['lost']}")),
                  DataCell(Text("${row['scored']}")),
                  DataCell(Text("${row['conceded']}")),
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
  final String tenantId;
  const ClubProfileScreen({super.key, required this.isOnline, required this.tenantId});

  @override
  State<ClubProfileScreen> createState() => _ClubProfileScreenState();
}

class _ClubProfileScreenState extends State<ClubProfileScreen> {
  Map<String, dynamic>? _club;
  List<String> _clubIds = const [];
  String _selectedClubId = "club-alpha-1";
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
    final data = await ApiService.loadOverview(online: widget.isOnline, tenantId: widget.tenantId, clubId: _selectedClubId);
    final club = data["club"];
    final clubIds = data["club_ids"] is List
        ? (data["club_ids"] as List).map((value) => value.toString()).toList()
        : <String>[];
    if (mounted) {
      setState(() {
        _club = club is Map && club.isNotEmpty ? Map<String, dynamic>.from(club) : null;
        _clubIds = clubIds;
        if (_clubIds.isNotEmpty && !_clubIds.contains(_selectedClubId)) {
          _selectedClubId = _clubIds.first;
        }
        _loading = false;
      });
    }
  }

  Future<void> _showPlayerHistory(Map<String, dynamic> player) async {
    final playerId = player["player_id"]?.toString() ?? "";
    if (playerId.isEmpty) return;
    final historyData = await ApiService.fetchJson(
      "/audit/v1/roster/history/${Uri.encodeComponent(playerId)}?as_of=${DateTime.now().toUtc().toIso8601String().substring(0, 10)}",
      cacheKey: "roster_history_${ApiService.currentEmail}_$playerId",
      online: widget.isOnline,
    );
    if (!mounted) return;
    final history = historyData?["history"] is List ? List<dynamic>.from(historyData!["history"] as List) : <dynamic>[];
    await showModalBottomSheet<void>(
      context: context,
      showDragHandle: true,
      builder: (context) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(20, 4, 20, 24),
          child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(player["name"]?.toString() ?? "Player profile", style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w800)),
            const SizedBox(height: 4),
            Text("${player['position'] ?? 'Player'} · ${player['role'] ?? 'squad member'}", style: TextStyle(color: Colors.blueGrey[600])),
            if (player["is_on_loan"] == true) ...[
              const SizedBox(height: 8),
              Text("On loan · returns ${player['loan_end_date'] ?? 'date not set'}", style: const TextStyle(color: Color(0xFF8A5A13), fontWeight: FontWeight.w700)),
            ],
            if (ApiService.currentRole == "player" && ApiService.currentUserId == playerId) ...[
              const SizedBox(height: 10),
              FilledButton.tonalIcon(
                onPressed: widget.isOnline ? () {
                  Navigator.pop(context);
                  _editPlayerProfile(player);
                } : null,
                icon: const Icon(Icons.edit_outlined),
                label: Text(widget.isOnline ? "Edit my profile" : "Connect to edit profile"),
              ),
            ],
            const SizedBox(height: 12),
            const Text("Roster history", style: TextStyle(fontWeight: FontWeight.w800)),
            const SizedBox(height: 6),
            if (history.isEmpty) Text(widget.isOnline ? "No roster changes recorded." : "No saved roster history is available offline."),
            ...history.take(8).map((entry) {
              final item = entry is Map ? Map<String, dynamic>.from(entry) : <String, dynamic>{};
              return ListTile(
                contentPadding: EdgeInsets.zero,
                dense: true,
                leading: const Icon(Icons.history, color: Color(0xFF1CA987)),
                title: Text((item["action"] ?? item["type"] ?? "Roster change").toString().replaceAll("_", " ").toUpperCase()),
                subtitle: Text("${item['effective_date'] ?? item['when'] ?? 'Date unavailable'}${item['why'] == null && item['reason'] == null ? '' : ' · ${item['why'] ?? item['reason']}'}"),
              );
            }),
          ]),
        ),
      ),
    );
    if (widget.isOnline && mounted) _loadClub();
  }

  Future<void> _editPlayerProfile(Map<String, dynamic> player) async {
    final playerId = player["player_id"]?.toString() ?? "";
    final nameController = TextEditingController(text: player["name"]?.toString() ?? "");
    var position = player["position"]?.toString() ?? "mf";
    final values = await showDialog<Map<String, String>>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
        builder: (dialogContext, setDialogState) => AlertDialog(
          title: const Text("Edit player profile"),
          content: Column(mainAxisSize: MainAxisSize.min, children: [
            TextField(controller: nameController, maxLength: 80,
              decoration: const InputDecoration(labelText: "Display name", prefixIcon: Icon(Icons.person_outline))),
            const SizedBox(height: 8),
            DropdownButtonFormField<String>(
              isExpanded: true,
              initialValue: position,
              decoration: const InputDecoration(labelText: "Position", prefixIcon: Icon(Icons.sports_soccer)),
              items: const [
                DropdownMenuItem(value: "gk", child: Text("Goalkeeper")),
                DropdownMenuItem(value: "df", child: Text("Defender")),
                DropdownMenuItem(value: "mf", child: Text("Midfielder")),
                DropdownMenuItem(value: "fw", child: Text("Forward")),
              ],
              onChanged: (value) { if (value != null) setDialogState(() => position = value); },
            ),
          ]),
          actions: [
            TextButton(onPressed: () => Navigator.pop(dialogContext), child: const Text("Cancel")),
            FilledButton(onPressed: () => Navigator.pop(dialogContext, {"name": nameController.text.trim(), "position": position}), child: const Text("Save profile")),
          ],
        ),
      ),
    );
    nameController.dispose();
    if (values == null || !mounted) return;
    if (values["name"]!.length < 2) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("Name must contain at least two characters.")));
      return;
    }
    try {
      final response = await http.patch(
        Uri.parse("${ApiService.cleanBaseUrl}/audit/v1/players/${Uri.encodeComponent(playerId)}"),
        headers: ApiService.authHeaders,
        body: jsonEncode(values),
      ).timeout(const Duration(seconds: 8));
      final decoded = response.body.isEmpty ? <String, dynamic>{} : jsonDecode(response.body);
      if (response.statusCode != 200) {
        final error = decoded is Map ? decoded["error"] : null;
        final code = error is Map ? error["code"]?.toString() ?? "VALIDATION_ERROR" : "VALIDATION_ERROR";
        throw ApiException(code, explainApiError(code));
      }
      await _loadClub();
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("Player profile updated and added to audit history.")));
    } on ApiException catch (error) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(error.message)));
    } catch (_) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("Could not update the profile. Check the server connection and try again.")));
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
          if (_clubIds.length > 1) ...[
            DropdownButtonFormField<String>(
              isExpanded: true,
              initialValue: _clubIds.contains(_selectedClubId) ? _selectedClubId : _clubIds.first,
              decoration: const InputDecoration(labelText: "Club", prefixIcon: Icon(Icons.shield_outlined)),
              items: _clubIds.map((id) => DropdownMenuItem(value: id, child: Text(id.replaceAll("-", " ").toUpperCase()))).toList(),
              onChanged: (value) {
                if (value == null) return;
                setState(() => _selectedClubId = value);
                _loadClub();
              },
            ),
            const SizedBox(height: 12),
          ],
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
                onPressed: () async {
                  await Navigator.push(context, MaterialPageRoute(
                    builder: (_) => RosterManagementScreen(isOnline: widget.isOnline)));
                  _loadClub();
                },
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
              onTap: () => _showPlayerHistory(p),
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
  int _quantity = 1;
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
    final pendingOrder = await ApiService.getCheckoutDraft();
    final draft = pendingOrder ?? await ApiService.getCheckoutCart();
    if (draft == null || !mounted) return;
    final items = draft["items"];
    final payment = draft["payment"];
    setState(() {
      _checkoutDraft = pendingOrder;
      if (items is List && items.isNotEmpty && items.first is Map) {
        _selectedVariant = (items.first as Map)["variant_id"]?.toString() ?? _selectedVariant;
        final savedQuantity = (items.first as Map)["quantity"];
        if (savedQuantity is num && savedQuantity.toInt() > 0) _quantity = savedQuantity.toInt();
      }
      _paymentToken = payment is Map ? payment["token"]?.toString() ?? _paymentToken : _paymentToken;
      _discountCtrl.text = draft["discount_code"]?.toString() ?? "";
      if (pendingOrder != null) {
        _checkoutMessage = "A previous order is awaiting confirmation. Retry safely; it uses the same order reference.";
      }
    });
  }

  Future<void> _persistCart() async {
    if (_checkoutDraft != null) return;
    await ApiService.saveCheckoutCart({
      "items": [{"variant_id": _selectedVariant, "quantity": _quantity}],
      "discount_code": _discountCtrl.text.trim().isEmpty ? null : _discountCtrl.text.trim().toUpperCase(),
      "payment": {"token": _paymentToken},
    });
  }

  void _changeQuantity(int delta) {
    if (_checkoutDraft != null) return;
    final next = _quantity + delta;
    if (next < 1 || (_availableStock != null && next > _availableStock!)) return;
    setState(() => _quantity = next);
    _persistCart();
  }

  Future<void> _loadInventory() async {
    if (widget.isOnline && mounted) setState(() => _inventoryLoading = true);
    try {
      final data = await ApiService.fetchJson(
        "/audit/v1/inventory/${Uri.encodeComponent(_selectedVariant)}",
        cacheKey: "inventory_$_selectedVariant",
        online: widget.isOnline,
      );
      if (data != null && mounted) {
        final stock = (data["stock"] as num?)?.toInt() ?? 0;
        var clamped = false;
        setState(() {
          _availableStock = stock;
          if (widget.isOnline && _checkoutDraft == null && stock > 0 && _quantity > stock) {
            _quantity = stock;
            clamped = true;
          }
        });
        if (clamped) _persistCart();
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
    _persistCart();
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
      case "VALIDATION_ERROR": return "Please check your order details and items.";
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
      "customer_id": ApiService.currentUserId.isNotEmpty
          ? ApiService.currentUserId
          : (ApiService.currentEmail.isNotEmpty ? ApiService.currentEmail : "cust-mobile-01"),
      "items": [{"variant_id": _selectedVariant, "quantity": _quantity}],
      "discount_code": _discountCtrl.text.trim().isEmpty ? null : _discountCtrl.text.trim().toUpperCase(),
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
          headers: ApiService.authHeaders,
        body: jsonEncode(draft),
      ).timeout(const Duration(seconds: 14));
      final decoded = jsonDecode(response.body);
      final data = decoded is Map<String, dynamic> ? decoded : <String, dynamic>{};
      if (response.statusCode == 200 || response.statusCode == 201) {
        await ApiService.clearCheckoutDraft();
        await ApiService.clearCheckoutCart();
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
        if (response.statusCode >= 500 || response.statusCode == 429) {
          if (mounted) {
            setState(() {
              _checkoutMessage = "The server is temporarily unavailable. The order reference is saved, so retrying will not create a second order.";
              _messageIsError = true;
            });
          }
          return;
        }
        await ApiService.clearCheckoutDraft();
        if (mounted) {
          setState(() {
          _checkoutDraft = null;
          _checkoutMessage = _checkoutError(code);
          _messageIsError = true;
        });
          _persistCart();
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
                const SizedBox(height: 12),
                Row(children: [
                  const Expanded(child: Text("Quantity", style: TextStyle(fontWeight: FontWeight.w700))),
                  IconButton(
                    tooltip: "Remove one",
                    onPressed: _checkoutDraft != null || _quantity <= 1 ? null : () => _changeQuantity(-1),
                    icon: const Icon(Icons.remove_circle_outline),
                  ),
                  Text("$_quantity", style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 16)),
                  IconButton(
                    tooltip: "Add one",
                    onPressed: _checkoutDraft != null || (_availableStock != null && _quantity >= _availableStock!)
                        ? null
                        : () => _changeQuantity(1),
                    icon: const Icon(Icons.add_circle_outline),
                  ),
                ]),
                const SizedBox(height: 14),
                TextField(controller: _discountCtrl, enabled: _checkoutDraft == null,
                  textCapitalization: TextCapitalization.characters,
                  decoration: const InputDecoration(labelText: "Discount code (optional)", prefixIcon: Icon(Icons.local_offer_outlined)),
                  onChanged: (_) { setState(() {}); _persistCart(); }),
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
                  onChanged: _checkoutDraft != null ? null : (value) {
                    if (value != null) {
                      setState(() => _paymentToken = value);
                      _persistCart();
                    }
                  },
                ),
                const SizedBox(height: 16),
                Row(children: [
                  const Text("Estimated total", style: TextStyle(fontWeight: FontWeight.w600)), const Spacer(),
                  Text("PKR ${(_quantity * 4500 * (discounted ? 0.9 : 1)).toStringAsFixed(0)}",
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

  Future<void> _confirmRosterAction() async {
    final actionLabel = switch (_actionType) {
      "loan" => "Loan player",
      "release" => "Release player",
      _ => "Transfer player",
    };
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text("Confirm $actionLabel"),
        content: Text(
          "${_selectedPlayer.replaceAll('-', ' ')} will be ${_actionType == 'loan' ? 'loaned to' : _actionType == 'release' ? 'released from' : 'moved to'} "
          "${_actionType == 'release' ? 'the squad' : _toSquad.replaceAll('-', ' ')}.\n\n"
          "Effective date: ${DateTime.now().toIso8601String().substring(0, 10)}\nReason: ${_reasonCtrl.text.trim().isEmpty ? 'Not provided' : _reasonCtrl.text.trim()}\n\n"
          "The move will be recorded in the player audit history.",
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text("Review")),
          FilledButton(onPressed: () => Navigator.pop(dialogContext, true), child: const Text("Confirm move")),
        ],
      ),
    );
    if (confirmed == true && mounted) await _submitRosterAction();
  }

  Future<void> _submitRosterAction() async {
    if (_reasonCtrl.text.trim().isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("Add a reason before submitting this roster change.")));
      return;
    }
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
      "requested_by": ApiService.currentEmail
    };

    if (widget.isOnline) {
      try {
        final res = await http.post(
          Uri.parse("${ApiService.baseUrl}/audit/v1/roster/transfer"),
          headers: ApiService.authHeaders,
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
              onPressed: _confirmRosterAction,
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
          headers: ApiService.authHeaders,
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
  final String tenantId;
  final ValueChanged<String> onTenantChanged;
  final bool isOnline;
  final int pendingCount;
  final List<Map<String, dynamic>> pendingActions;
  final List<Map<String, dynamic>> syncHistory;
  final VoidCallback onSync;
  final VoidCallback onClearHistory;
  final Future<void> Function() onLogout;

  const AccountScreen({
    super.key,
    required this.onRoleChanged,
    required this.tenantId,
    required this.onTenantChanged,
    required this.isOnline,
    required this.pendingCount,
    required this.pendingActions,
    required this.syncHistory,
    required this.onSync,
    required this.onClearHistory,
    required this.onLogout,
  });

  Future<void> _switchAccount(BuildContext context, String email) async {
    if (!isOnline) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text("Connect to the event server to switch demo accounts.")),
      );
      return;
    }
    String enteredPassword = "";
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) {
        final ctrl = TextEditingController();
        return AlertDialog(
          title: Text("Sign in as $email"),
          content: TextField(
            controller: ctrl,
            autofocus: true,
            obscureText: true,
            decoration: const InputDecoration(labelText: "Password"),
            onChanged: (val) => enteredPassword = val,
            onSubmitted: (val) {
              enteredPassword = val;
              Navigator.pop(dialogContext, true);
            },
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text("Cancel")),
            FilledButton(
              onPressed: () {
                enteredPassword = ctrl.text;
                Navigator.pop(dialogContext, true);
              },
              child: const Text("Sign in"),
            ),
          ],
        );
      },
    );
    if (confirmed != true || enteredPassword.isEmpty || !context.mounted) return;
    try {
      await ApiService.login(email, enteredPassword);
      await Future<void>.delayed(const Duration(milliseconds: 150));
      if (!context.mounted) return;
      onRoleChanged();
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Signed in as $email")));
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
    if (entered == null || entered.isEmpty || !context.mounted) return;
    try {
      await ApiService.setServerUrl(entered);
      final reachable = await ApiService.checkHealth();
      await Future<void>.delayed(const Duration(milliseconds: 150));
      if (!context.mounted) return;
      onRoleChanged();
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(reachable
            ? "Server is reachable at ${ApiService.baseUrl}"
            : "Address saved, but ${ApiService.baseUrl} did not respond. Check that the API is running."),
        backgroundColor: reachable ? const Color(0xFF08775E) : const Color(0xFF8A5A13),
      ));
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
        if (ApiService.currentRole == "super_admin") ...[
          Card(child: Padding(
            padding: const EdgeInsets.all(16),
            child: DropdownButtonFormField<String>(
              isExpanded: true,
              initialValue: tenantId,
              decoration: const InputDecoration(labelText: "Tenant workspace", prefixIcon: Icon(Icons.apartment_outlined)),
              items: const [
                DropdownMenuItem(value: "tenant-alpha", child: Text("Alpha League")),
                DropdownMenuItem(value: "tenant-beta", child: Text("Beta League")),
              ],
              onChanged: (value) { if (value != null) onTenantChanged(value); },
            ),
          )),
          const SizedBox(height: 12),
        ],
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
                  subtitle: Text("${action['action_id'] ?? 'Action'} · queued ${action['queued_at'] ?? 'on this device'} · version ${action['base_version'] ?? 0}"),
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
        const SizedBox(height: 12),
        Card(child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              const Icon(Icons.history, color: Color(0xFF1CA987)),
              const SizedBox(width: 10),
              const Expanded(child: Text("Recent sync history", style: TextStyle(fontSize: 16, fontWeight: FontWeight.w800))),
              if (syncHistory.isNotEmpty)
                TextButton(onPressed: onClearHistory, child: const Text("Clear")),
            ]),
            if (syncHistory.isEmpty)
              Text("Successful, duplicate, and rejected sync results will appear here.", style: TextStyle(color: Colors.blueGrey[600]))
            else ...syncHistory.take(5).map((entry) {
              final status = entry["status"]?.toString() ?? "unknown";
              final errorCode = entry["error_code"]?.toString();
              return ListTile(
                contentPadding: EdgeInsets.zero,
                dense: true,
                leading: Icon(status == "rejected" ? Icons.error_outline : Icons.check_circle_outline,
                  color: status == "rejected" ? const Color(0xFF9E342C) : const Color(0xFF08775E)),
                title: Text("${entry['action_id'] ?? 'Action'} · $status"),
                subtitle: Text(errorCode == null ? "Synced ${entry['synced_at'] ?? ''}" : explainApiError(errorCode)),
              );
            }),
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
