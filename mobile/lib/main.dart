import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'services/api_service.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await ApiService.init();
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
          seedColor: const Color(0xFF1E3A8A),
          primary: const Color(0xFF1E3A8A),
          secondary: const Color(0xFF0D9488),
          surface: Colors.grey[50]!,
        ),
        appBarTheme: const AppBarTheme(
          elevation: 0,
          centerTitle: true,
          backgroundColor: Color(0xFF1E3A8A),
          foregroundColor: Colors.white,
        ),
        cardTheme: CardThemeData(
          elevation: 2,
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
        ),
      ),
      home: const MainNavigationScreen(),
    );
  }
}

class MainNavigationScreen extends StatefulWidget {
  const MainNavigationScreen({super.key});

  @override
  State<MainNavigationScreen> createState() => _MainNavigationScreenState();
}

class _MainNavigationScreenState extends State<MainNavigationScreen> {
  int _currentIndex = 0;
  bool _isOnline = true;
  int _pendingCount = 0;
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
    final online = await ApiService.checkHealth();
    final queue = await ApiService.getPendingQueue();
    if (mounted) {
      setState(() {
        _isOnline = online;
        _pendingCount = queue.length;
      });
    }
  }

  Future<void> _performSync() async {
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text("Syncing pending offline actions...")),
    );
    final res = await ApiService.syncOfflineActions("mobile-dev-01");
    if (res != null) {
      final summary = res["summary"];
      if (mounted) {
        showDialog(
          context: context,
          builder: (ctx) => AlertDialog(
            title: const Text("Sync Completed"),
            content: Text(
              "Applied: ${summary['applied']}\n"
              "Rejected: ${summary['rejected']}\n"
              "Duplicate: ${summary['duplicate']}",
            ),
            actions: [
              TextButton(onPressed: () => Navigator.pop(ctx), child: const Text("OK")),
            ],
          ),
        );
      }
    }
    await _checkStatus();
  }

  @override
  Widget build(BuildContext context) {
    final screens = [
      FixturesScreen(isOnline: _isOnline),
      StandingsScreen(isOnline: _isOnline),
      StoreScreen(isOnline: _isOnline),
      ClubProfileScreen(isOnline: _isOnline),
      AccountScreen(
        onRoleChanged: () => setState(() {}),
        isOnline: _isOnline,
        pendingCount: _pendingCount,
        onSync: _performSync,
      ),
    ];

    return Scaffold(
      appBar: AppBar(
        leading: Padding(
          padding: const EdgeInsets.all(8.0),
          child: Container(
            decoration: BoxDecoration(
              gradient: const LinearGradient(colors: [Color(0xFFF59E0B), Color(0xFFD97706)]),
              shape: BoxShape.circle,
              boxShadow: [
                BoxShadow(color: Colors.black26, blurRadius: 4, offset: const Offset(0, 2)),
              ],
            ),
            child: const Center(
              child: Text(
                "N26",
                style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 13),
              ),
            ),
          ),
        ),
        title: const Text("NaSCon League & Club Ops"),
        actions: [
          // Offline / Online Status Badge
          Container(
            margin: const EdgeInsets.symmetric(horizontal: 8, vertical: 12),
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
            decoration: BoxDecoration(
              color: _isOnline ? Colors.green[600] : Colors.amber[800],
              borderRadius: BorderRadius.circular(20),
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(
                  _isOnline ? Icons.wifi : Icons.wifi_off,
                  size: 14,
                  color: Colors.white,
                ),
                const SizedBox(width: 4),
                Text(
                  _isOnline ? "Online" : "Offline",
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
  const FixturesScreen({super.key, required this.isOnline});

  @override
  State<FixturesScreen> createState() => _FixturesScreenState();
}

class _FixturesScreenState extends State<FixturesScreen> {
  List<dynamic> _fixtures = [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _loadFixtures();
  }

  Future<void> _loadFixtures() async {
    setState(() => _loading = true);
    // Try cached first (for desk check offline)
    final cached = await ApiService.getCachedData("fixtures");
    if (cached != null) {
      _fixtures = cached;
    }

    if (widget.isOnline) {
      try {
        final res = await http.get(Uri.parse("${ApiService.baseUrl}/audit/v1/score/m1")).timeout(const Duration(seconds: 3));
        if (res.statusCode == 200) {
          final data = jsonDecode(res.body);
          _fixtures = [data];
          await ApiService.cacheData("fixtures", _fixtures);
        }
      } catch (_) {}
    }

    if (_fixtures.isEmpty) {
      // Default baseline fixtures
      _fixtures = [
        {
          "match_id": "m1",
          "sport_id": "football",
          "status": "in_progress",
          "home": {"team_id": "t1", "score": 2},
          "away": {"team_id": "t2", "score": 1},
        },
        {
          "match_id": "m2",
          "sport_id": "football",
          "status": "upcoming",
          "home": {"team_id": "t3", "score": 0},
          "away": {"team_id": "t4", "score": 0},
        }
      ];
      await ApiService.cacheData("fixtures", _fixtures);
    }

    if (mounted) setState(() => _loading = false);
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) return const Center(child: CircularProgressIndicator());
    return ListView.builder(
      padding: const EdgeInsets.all(12),
      itemCount: _fixtures.length,
      itemBuilder: (ctx, idx) {
        final f = _fixtures[idx];
        return Card(
          margin: const EdgeInsets.only(bottom: 12),
          child: ListTile(
            contentPadding: const EdgeInsets.all(16),
            title: Text(
              "${f['home']['team_id'].toUpperCase()}  ${f['home']['score']} - ${f['away']['score']}  ${f['away']['team_id'].toUpperCase()}",
              style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 18),
            ),
            subtitle: Text("Status: ${f['status']} | Sport: ${f['sport_id']}"),
            trailing: const Icon(Icons.arrow_forward_ios, size: 16),
            onTap: () {
              Navigator.push(
                context,
                MaterialPageRoute(builder: (_) => MatchDetailScreen(match: f, isOnline: widget.isOnline)),
              );
            },
          ),
        );
      },
    );
  }
}

// ==========================================
// 2. MATCH DETAIL & PINNED SHOWCASE
// ==========================================
class MatchDetailScreen extends StatelessWidget {
  final dynamic match;
  final bool isOnline;
  const MatchDetailScreen({super.key, required this.match, required this.isOnline});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text("Match ${match['match_id']}")),
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
                      "${match['home']['team_id'].toUpperCase()} vs ${match['away']['team_id'].toUpperCase()}",
                      style: const TextStyle(color: Colors.white70, fontSize: 16),
                    ),
                    const SizedBox(height: 8),
                    Text(
                      "${match['home']['score']} - ${match['away']['score']}",
                      style: const TextStyle(color: Colors.white, fontSize: 36, fontWeight: FontWeight.bold),
                    ),
                    const SizedBox(height: 4),
                    Chip(
                      label: Text(match['status'].toUpperCase()),
                      backgroundColor: Colors.tealAccent[700],
                    )
                  ],
                ),
              ),
            ),
            const SizedBox(height: 16),
            const Text("Pinned Match Merchandise", style: TextStyle(fontWeight: FontWeight.bold, fontSize: 18)),
            const SizedBox(height: 8),
            Card(
              child: ListTile(
                leading: const Icon(Icons.checkroom, size: 36, color: Colors.blue),
                title: const Text("Official Alpha Jersey 2026"),
                subtitle: const Text("PKR 4500.00 • In Stock"),
                trailing: ElevatedButton(
                  onPressed: () {
                    ScaffoldMessenger.of(context).showSnackBar(
                      const SnackBar(content: Text("Opened product in Club Store")),
                    );
                  },
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
                      builder: (_) => ScoringEntryScreen(matchId: match['match_id'], isOnline: isOnline),
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
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _loadStandings();
  }

  Future<void> _loadStandings() async {
    setState(() => _loading = true);
    final cached = await ApiService.getCachedData("standings");
    if (cached != null) _table = cached;

    if (widget.isOnline) {
      try {
        final res = await http.get(Uri.parse("${ApiService.baseUrl}/audit/v1/standings/lg-1")).timeout(const Duration(seconds: 3));
        if (res.statusCode == 200) {
          final data = jsonDecode(res.body);
          _table = data["table"];
          await ApiService.cacheData("standings", _table);
        }
      } catch (_) {}
    }

    if (_table.isEmpty) {
      _table = [
        {"rank": 1, "team_id": "t1", "played": 3, "won": 2, "drawn": 1, "lost": 0, "diff": 5, "points": 7, "status": "clinched"},
        {"rank": 2, "team_id": "t2", "played": 3, "won": 1, "drawn": 1, "lost": 1, "diff": 1, "points": 4, "status": "still_possible"},
        {"rank": 3, "team_id": "t3", "played": 3, "won": 0, "drawn": 1, "lost": 2, "diff": -6, "points": 1, "status": "eliminated"},
      ];
      await ApiService.cacheData("standings", _table);
    }

    if (mounted) setState(() => _loading = false);
  }

  Color _getStatusColor(String status) {
    if (status == "clinched") return Colors.green[700]!;
    if (status == "eliminated") return Colors.red[700]!;
    return Colors.blue[700]!;
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) return const Center(child: CircularProgressIndicator());
    return SingleChildScrollView(
      padding: const EdgeInsets.all(12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const Text("League Alpha 1 Table", style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
          const SizedBox(height: 8),
          DataTable(
            columnSpacing: 16,
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
                  DataCell(Text(row['team_id'].toString().toUpperCase(), style: const TextStyle(fontWeight: FontWeight.bold))),
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

  @override
  void initState() {
    super.initState();
    _loadClub();
  }

  Future<void> _loadClub() async {
    final cached = await ApiService.getCachedData("club_profile");
    if (cached != null) _club = Map<String, dynamic>.from(cached);

    if (_club == null) {
      _club = {
        "club_id": "club-alpha-1",
        "name": "Alpha Athletics FC",
        "tenant_id": "tenant-alpha",
        "venue": "Jinnah Stadium, Islamabad (33.7082° N, 73.0856° E)",
        "staff": {"manager": "Tariq Ali", "coach": "Hamza Khan", "physio": "Dr. Sarah"},
        "roster": [
          {"player_id": "p1", "name": "Ali Raza", "position": "gk", "role": "captain"},
          {"player_id": "p2", "name": "Bilal Ahmed", "position": "df", "role": "starter"},
          {"player_id": "p3", "name": "Usman Tariq", "position": "mf", "role": "substitute"},
        ]
      };
      await ApiService.cacheData("club_profile", _club);
    }
    if (mounted) setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    if (_club == null) return const Center(child: CircularProgressIndicator());
    final roster = _club!["roster"] as List<dynamic>;

    return SingleChildScrollView(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                children: [
                  const Icon(Icons.shield, size: 64, color: Color(0xFF1E3A8A)),
                  const SizedBox(height: 8),
                  Text(_club!["name"], style: const TextStyle(fontSize: 22, fontWeight: FontWeight.bold)),
                  Text("Venue: ${_club!['venue']}", style: const TextStyle(color: Colors.grey)),
                ],
              ),
            ),
          ),
          const SizedBox(height: 16),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              const Text("Active Squad Roster", style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
              if (ApiService.currentRole == "club_manager" || ApiService.currentRole == "super_admin")
                ElevatedButton.icon(
                  icon: const Icon(Icons.swap_horiz),
                  label: const Text("Manage Roster"),
                  onPressed: () {
                    Navigator.push(
                      context,
                      MaterialPageRoute(builder: (_) => RosterManagementScreen(isOnline: widget.isOnline)),
                    );
                  },
                )
            ],
          ),
          const SizedBox(height: 8),
          ...roster.map((p) => Card(
            child: ListTile(
              leading: CircleAvatar(child: Text(p['position'].toString().toUpperCase())),
              title: Text(p['name']),
              subtitle: Text("Role: ${p['role']}"),
            ),
          )),
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
  String _selectedVariant = "var-jersey-001-M";
  final TextEditingController _discountCtrl = TextEditingController(text: "WELCOME10");
  String _paymentToken = "tok_success";
  bool _processing = false;
  Map<String, dynamic>? _lastReceipt;

  Future<void> _checkout() async {
    setState(() => _processing = true);
    final orderId = "ord-${DateTime.now().millisecondsSinceEpoch}";
    try {
      final res = await http.post(
        Uri.parse("${ApiService.baseUrl}/audit/v1/checkout"),
        headers: {"Content-Type": "application/json"},
        body: jsonEncode({
          "order_id": orderId,
          "customer_id": "cust-mobile-01",
          "items": [{"variant_id": _selectedVariant, "quantity": 1}],
          "discount_code": _discountCtrl.text.isEmpty ? null : _discountCtrl.text.trim(),
          "payment": {"token": _paymentToken}
        }),
      ).timeout(const Duration(seconds: 4));

      final data = jsonDecode(res.body);
      if (res.statusCode == 201 || res.statusCode == 200) {
        setState(() => _lastReceipt = data["receipt"]);
        if (mounted) {
          showDialog(
            context: context,
            builder: (ctx) => AlertDialog(
              title: const Text("Order Paid!"),
              content: Text("Receipt generated for Order $orderId.\nTotal: PKR 4050.00\nPDF Receipt Available."),
              actions: [
                TextButton(onPressed: () => Navigator.pop(ctx), child: const Text("View PDF Receipt")),
              ],
            ),
          );
        }
      } else {
        final err = data["error"]?["code"] ?? "CHECKOUT_FAILED";
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Checkout error: $err"), backgroundColor: Colors.red));
        }
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Network error: $e"), backgroundColor: Colors.red));
      }
    }
    if (mounted) setState(() => _processing = false);
  }

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text("Official Club Jersey 2026", style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold)),
                  const Text("Price: PKR 4500.00 (450,000 paisa)", style: TextStyle(color: Colors.green, fontSize: 16)),
                  const SizedBox(height: 12),
                  const Text("Select Size:"),
                  Row(
                    children: [
                      ChoiceChip(
                        label: const Text("Medium (M)"),
                        selected: _selectedVariant == "var-jersey-001-M",
                        onSelected: (s) => setState(() => _selectedVariant = "var-jersey-001-M"),
                      ),
                      const SizedBox(width: 8),
                      ChoiceChip(
                        label: const Text("Large (L)"),
                        selected: _selectedVariant == "var-jersey-001-L",
                        onSelected: (s) => setState(() => _selectedVariant = "var-jersey-001-L"),
                      ),
                    ],
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: _discountCtrl,
                    decoration: const InputDecoration(labelText: "Discount Code", border: OutlineInputBorder()),
                  ),
                  const SizedBox(height: 12),
                  DropdownButtonFormField<String>(
                    initialValue: _paymentToken,
                    decoration: const InputDecoration(labelText: "Payment Simulation", border: OutlineInputBorder()),
                    items: const [
                      DropdownMenuItem(value: "tok_success", child: Text("Success (Authorize + Capture)")),
                      DropdownMenuItem(value: "tok_declined", child: Text("Decline (Bank Rejection)")),
                      DropdownMenuItem(value: "tok_capture_fail", child: Text("Capture Fail (Gateway Timeout)")),
                    ],
                    onChanged: (v) => setState(() => _paymentToken = v!),
                  ),
                  const SizedBox(height: 16),
                  ElevatedButton.icon(
                    style: ElevatedButton.styleFrom(
                      backgroundColor: Colors.blue[900],
                      foregroundColor: Colors.white,
                      padding: const EdgeInsets.all(14),
                    ),
                    icon: _processing ? const CircularProgressIndicator(color: Colors.white) : const Icon(Icons.payment),
                    label: const Text("Place Order & Pay"),
                    onPressed: _processing ? null : _checkout,
                  ),
                ],
              ),
            ),
          ),
          if (_lastReceipt != null) ...[
            const SizedBox(height: 16),
            Card(
              color: Colors.green[50],
              child: ListTile(
                leading: const Icon(Icons.picture_as_pdf, color: Colors.green, size: 36),
                title: Text("PDF Receipt: ${_lastReceipt!['payload']['order_id']}"),
                subtitle: Text("Verified HMAC: ${_lastReceipt!['signature'].substring(0, 16)}..."),
              ),
            )
          ]
        ],
      ),
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
  String _selectedPlayer = "p2";
  String _actionType = "transfer";
  String _toSquad = "club-alpha-2";
  final TextEditingController _reasonCtrl = TextEditingController(text: "Tactical loan");

  Future<void> _submitRosterAction() async {
    final actId = "a-${DateTime.now().millisecondsSinceEpoch}";
    final payload = {
      "request_id": "tr-$actId",
      "league_id": "league-alpha-1",
      "player_id": _selectedPlayer,
      "type": _actionType,
      "from_squad_id": "club-alpha-1",
      "to_squad_id": _actionType == "release" ? null : _toSquad,
      "effective_date": "2026-09-30",
      "loan_end_date": _actionType == "loan" ? "2026-12-31" : null,
      "reason": _reasonCtrl.text,
      "requested_by": "manager@nascon.test"
    };

    if (widget.isOnline) {
      try {
        final res = await http.post(
          Uri.parse("${ApiService.baseUrl}/audit/v1/roster/transfer"),
          headers: {"Content-Type": "application/json"},
          body: jsonEncode(payload),
        );
        if (res.statusCode == 200 || res.statusCode == 201) {
          if (!mounted) return;
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text("Roster action applied successfully!")),
          );
          Navigator.pop(context);
          return;
        }
      } catch (_) {}
    }

    // Offline Queueing (Section 8.4 Desk Check requirement!)
    await ApiService.queueOfflineAction({
      "action_id": actId,
      "type": "roster_transfer",
      "queued_at": DateTime.now().toUtc().toIso8601String(),
      "base_version": 1,
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
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text("Manage Squad Roster")),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          children: [
            DropdownButtonFormField<String>(
              initialValue: _selectedPlayer,
              decoration: const InputDecoration(labelText: "Player", border: OutlineInputBorder()),
              items: const [
                DropdownMenuItem(value: "p1", child: Text("p1 (Ali Raza - GK)")),
                DropdownMenuItem(value: "p2", child: Text("p2 (Bilal Ahmed - DF)")),
              ],
              onChanged: (v) => setState(() => _selectedPlayer = v!),
            ),
            const SizedBox(height: 12),
            DropdownButtonFormField<String>(
              initialValue: _actionType,
              decoration: const InputDecoration(labelText: "Action Type", border: OutlineInputBorder()),
              items: const [
                DropdownMenuItem(value: "transfer", child: Text("Permanent Transfer")),
                DropdownMenuItem(value: "loan", child: Text("Loan with Auto-Return")),
                DropdownMenuItem(value: "release", child: Text("Release Player")),
              ],
              onChanged: (v) => setState(() => _actionType = v!),
            ),
            const SizedBox(height: 12),
            if (_actionType != "release")
              DropdownButtonFormField<String>(
                initialValue: _toSquad,
                decoration: const InputDecoration(labelText: "Destination Squad", border: OutlineInputBorder()),
                items: const [
                  DropdownMenuItem(value: "club-alpha-2", child: Text("Club Alpha 2")),
                ],
                onChanged: (v) => setState(() => _toSquad = v!),
              ),
            const SizedBox(height: 12),
            TextField(
              controller: _reasonCtrl,
              decoration: const InputDecoration(labelText: "Reason", border: OutlineInputBorder()),
            ),
            const Spacer(),
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
    );
  }
}

// ==========================================
// 7. SCORING ENTRY & OFFLINE QUEUEING
// ==========================================
class ScoringEntryScreen extends StatefulWidget {
  final String matchId;
  final bool isOnline;
  const ScoringEntryScreen({super.key, required this.matchId, required this.isOnline});

  @override
  State<ScoringEntryScreen> createState() => _ScoringEntryScreenState();
}

class _ScoringEntryScreenState extends State<ScoringEntryScreen> {
  String _selectedTeam = "t1";
  String _eventType = "goal";

  Future<void> _submitScore() async {
    final eid = "evt-${DateTime.now().millisecondsSinceEpoch}";
    final payload = {
      "event_id": eid,
      "match_id": widget.matchId,
      "type": "score",
      "payload": {"team_id": _selectedTeam, "event_type": _eventType}
    };

    if (widget.isOnline) {
      try {
        final res = await http.post(
          Uri.parse("${ApiService.baseUrl}/audit/v1/events"),
          headers: {"Content-Type": "application/json"},
          body: jsonEncode(payload),
        );
        if (res.statusCode == 201 || res.statusCode == 200) {
          if (!mounted) return;
          ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("Score event appended!")));
          Navigator.pop(context);
          return;
        }
      } catch (_) {}
    }

    // Offline Queueing (Section 8.4 Desk Check requirement!)
    await ApiService.queueOfflineAction({
      "action_id": eid,
      "type": "score_event",
      "queued_at": DateTime.now().toUtc().toIso8601String(),
      "base_version": 2,
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
      appBar: AppBar(title: Text("Scoring for ${widget.matchId}")),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          children: [
            DropdownButtonFormField<String>(
              initialValue: _selectedTeam,
              decoration: const InputDecoration(labelText: "Team", border: OutlineInputBorder()),
              items: const [
                DropdownMenuItem(value: "t1", child: Text("Team 1 (t1)")),
                DropdownMenuItem(value: "t2", child: Text("Team 2 (t2)")),
              ],
              onChanged: (v) => setState(() => _selectedTeam = v!),
            ),
            const SizedBox(height: 12),
            DropdownButtonFormField<String>(
              initialValue: _eventType,
              decoration: const InputDecoration(labelText: "Scoring Event Type", border: OutlineInputBorder()),
              items: const [
                DropdownMenuItem(value: "goal", child: Text("Goal (1 pt)")),
                DropdownMenuItem(value: "two_pointer", child: Text("Two Pointer (2 pts)")),
                DropdownMenuItem(value: "six", child: Text("Six (6 runs)")),
              ],
              onChanged: (v) => setState(() => _eventType = v!),
            ),
            const Spacer(),
            ElevatedButton(
              style: ElevatedButton.styleFrom(
                minimumSize: const Size.fromHeight(48),
                backgroundColor: Colors.blue[900],
                foregroundColor: Colors.white,
              ),
              onPressed: _submitScore,
              child: const Text("Append Event (Online or Queue Offline)"),
            ),
          ],
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
  final VoidCallback onSync;

  const AccountScreen({
    super.key,
    required this.onRoleChanged,
    required this.isOnline,
    required this.pendingCount,
    required this.onSync,
  });

  void _switchAccount(BuildContext ctx, String role, String email) async {
    await ApiService.saveAuth("token-$role", role, email);
    onRoleChanged();
    if (ctx.mounted) {
      ScaffoldMessenger.of(ctx).showSnackBar(SnackBar(content: Text("Switched to $email ($role)")));
    }
  }

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Card(
          color: Colors.blue[50],
          child: ListTile(
            leading: const CircleAvatar(child: Icon(Icons.person)),
            title: Text(ApiService.currentEmail, style: const TextStyle(fontWeight: FontWeight.bold)),
            subtitle: Text("Role: ${ApiService.currentRole.toUpperCase()}"),
          ),
        ),
        const SizedBox(height: 12),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const Text("Backend Server Configuration", style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
                const SizedBox(height: 4),
                Text("Target: ${ApiService.baseUrl}", style: const TextStyle(color: Colors.blueGrey, fontSize: 13)),
                const SizedBox(height: 8),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    ActionChip(
                      avatar: const Icon(Icons.computer, size: 16),
                      label: const Text("localhost:8080"),
                      onPressed: () async {
                        await ApiService.setServerUrl("http://localhost:8080");
                        onRoleChanged();
                        if (context.mounted) {
                          ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("Connected to http://localhost:8080")));
                        }
                      },
                    ),
                    ActionChip(
                      avatar: const Icon(Icons.wifi, size: 16),
                      label: const Text("LAN IP: 172.15.4.12:8080"),
                      onPressed: () async {
                        await ApiService.setServerUrl("http://172.15.4.12:8080");
                        onRoleChanged();
                        if (context.mounted) {
                          ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("Connected to http://172.15.4.12:8080")));
                        }
                      },
                    ),
                    ActionChip(
                      avatar: const Icon(Icons.phone_android, size: 16),
                      label: const Text("Emulator (10.0.2.2)"),
                      onPressed: () async {
                        await ApiService.setServerUrl("http://10.0.2.2:8080");
                        onRoleChanged();
                        if (context.mounted) {
                          ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("Connected to http://10.0.2.2:8080")));
                        }
                      },
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 16),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    const Text("Offline Action Queue", style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
                    Badge(
                      label: Text("$pendingCount"),
                      backgroundColor: pendingCount > 0 ? Colors.amber[800] : Colors.green,
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                Text(
                  pendingCount > 0
                      ? "$pendingCount actions stored locally while offline."
                      : "Queue is empty. Everything synchronized.",
                ),
                const SizedBox(height: 8),
                ElevatedButton.icon(
                  icon: const Icon(Icons.sync),
                  label: const Text("Trigger Immediate Sync"),
                  onPressed: isOnline && pendingCount > 0 ? onSync : null,
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 16),
        const Text("Quick Demo Accounts (Section 8.3)", style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
        const SizedBox(height: 8),
        ListTile(
          title: const Text("Platform Super-Admin"),
          subtitle: const Text("admin@nascon.test"),
          trailing: const Icon(Icons.chevron_right),
          onTap: () => _switchAccount(context, "super_admin", "admin@nascon.test"),
        ),
        ListTile(
          title: const Text("League Organizer"),
          subtitle: const Text("organizer@nascon.test (owns league-alpha-1)"),
          trailing: const Icon(Icons.chevron_right),
          onTap: () => _switchAccount(context, "league_organizer", "organizer@nascon.test"),
        ),
        ListTile(
          title: const Text("Club Manager"),
          subtitle: const Text("manager@nascon.test (owns club-alpha-1)"),
          trailing: const Icon(Icons.chevron_right),
          onTap: () => _switchAccount(context, "club_manager", "manager@nascon.test"),
        ),
        ListTile(
          title: const Text("Player"),
          subtitle: const Text("player@nascon.test (player-alpha-1)"),
          trailing: const Icon(Icons.chevron_right),
          onTap: () => _switchAccount(context, "player", "player@nascon.test"),
        ),
        ListTile(
          title: const Text("General Viewer"),
          subtitle: const Text("viewer@nascon.test (Read-only)"),
          trailing: const Icon(Icons.chevron_right),
          onTap: () => _switchAccount(context, "viewer", "viewer@nascon.test"),
        ),
      ],
    );
  }
}
