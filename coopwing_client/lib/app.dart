import 'dart:async';

import 'package:flutter/material.dart';

import 'models/app_event.dart';
import 'models/app_settings.dart';
import 'models/backend_health.dart';
import 'models/doctor_report.dart';
import 'models/server_preset.dart';
import 'screens/about_screen.dart';
import 'screens/home_screen.dart';
import 'screens/my_games_screen.dart';
import 'screens/network_doctor_screen.dart';
import 'screens/room_connection_screen.dart';
import 'screens/settings_screen.dart';
import 'services/backend_client.dart';
import 'services/backend_process_manager.dart';
import 'services/http_backend_client.dart';
import 'services/mock_backend_client.dart';
import 'services/localization.dart';

class S2PassPreviewApp extends StatelessWidget {
  const S2PassPreviewApp({super.key});

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: Localization(),
      builder: (context, _) {
        return MaterialApp(
          title: Localization().get('app_title'),
          debugShowCheckedModeBanner: false,
          themeMode: ThemeMode.light,
          theme: ThemeData(
            useMaterial3: true,
            colorSchemeSeed: Colors.teal,
            brightness: Brightness.light,
            scaffoldBackgroundColor: const Color(0xFFF7F2E8),
            cardTheme: const CardThemeData(
              color: Colors.white,
              elevation: 0,
              margin: EdgeInsets.zero,
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.all(Radius.circular(8)),
              ),
            ),
          ),
          home: const _PreviewShell(),
        );
      },
    );
  }
}

class _SidebarLogo extends StatelessWidget {
  const _SidebarLogo();

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;

    return Center(
      child: Tooltip(
        message: Localization().get('app_title'),
        child: SizedBox(
          width: 68,
          height: 68,
          child: DecoratedBox(
            decoration: BoxDecoration(
              color: Colors.white.withValues(alpha: 0.62),
              borderRadius: BorderRadius.circular(14),
              border: Border.all(
                color: colorScheme.outlineVariant.withValues(alpha: 0.45),
              ),
            ),
            child: Center(
              child: ClipRRect(
                borderRadius: BorderRadius.circular(10),
                child: Image.asset(
                  'assets/branding/app_icon.png',
                  width: 52,
                  height: 52,
                  fit: BoxFit.contain,
                  errorBuilder: (context, error, stackTrace) => SizedBox(
                    width: 52,
                    height: 52,
                    child: Icon(
                      Icons.apps,
                      color: colorScheme.primary,
                      size: 30,
                    ),
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _Sidebar extends StatelessWidget {
  const _Sidebar({
    required this.selectedIndex,
    required this.onDestinationSelected,
  });

  static const double width = 196;

  final int selectedIndex;
  final ValueChanged<int> onDestinationSelected;

  @override
  Widget build(BuildContext context) {
    final loc = Localization();
    final destinations = [
      _SidebarDestination(
        icon: Icons.dashboard_outlined,
        selectedIcon: Icons.dashboard,
        label: loc.get('home'),
      ),
      _SidebarDestination(
        icon: Icons.sports_esports_outlined,
        selectedIcon: Icons.sports_esports,
        label: loc.get('my_games'),
      ),
      _SidebarDestination(
        icon: Icons.meeting_room_outlined,
        selectedIcon: Icons.meeting_room,
        label: loc.get('room_connection'),
      ),
      _SidebarDestination(
        icon: Icons.health_and_safety_outlined,
        selectedIcon: Icons.health_and_safety,
        label: loc.get('doctor'),
      ),
      _SidebarDestination(
        icon: Icons.settings_outlined,
        selectedIcon: Icons.settings,
        label: loc.get('settings'),
      ),
      _SidebarDestination(
        icon: Icons.info_outline,
        selectedIcon: Icons.info,
        label: loc.get('about'),
      ),
    ];

    return SizedBox(
      width: width,
      child: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(18, 32, 18, 16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const _SidebarLogo(),
              const SizedBox(height: 24),
              for (var index = 0; index < destinations.length; index++) ...[
                _SidebarItem(
                  destination: destinations[index],
                  selected: selectedIndex == index,
                  onTap: () => onDestinationSelected(index),
                ),
                if (index != destinations.length - 1) const SizedBox(height: 7),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

class _SidebarDestination {
  const _SidebarDestination({
    required this.icon,
    required this.selectedIcon,
    required this.label,
  });

  final IconData icon;
  final IconData selectedIcon;
  final String label;
}

class _SidebarItem extends StatelessWidget {
  const _SidebarItem({
    required this.destination,
    required this.selected,
    required this.onTap,
  });

  final _SidebarDestination destination;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;
    final foreground = selected
        ? colorScheme.onPrimaryContainer
        : colorScheme.onSurfaceVariant;

    return Tooltip(
      message: destination.label,
      child: Material(
        color: Colors.transparent,
        borderRadius: BorderRadius.circular(14),
        clipBehavior: Clip.antiAlias,
        child: InkWell(
          onTap: onTap,
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 140),
            curve: Curves.easeOut,
            height: 50,
            padding: const EdgeInsets.symmetric(horizontal: 13),
            decoration: BoxDecoration(
              color: selected
                  ? colorScheme.primaryContainer.withValues(alpha: 0.7)
                  : Colors.transparent,
              borderRadius: BorderRadius.circular(14),
            ),
            child: Row(
              children: [
                Icon(
                  selected ? destination.selectedIcon : destination.icon,
                  size: 22,
                  color: foreground,
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Text(
                    destination.label,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: Theme.of(context).textTheme.labelMedium?.copyWith(
                      color: foreground,
                      fontSize: 14,
                      fontWeight: FontWeight.w600,
                      height: 1.08,
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _PreviewShell extends StatefulWidget {
  const _PreviewShell();

  @override
  State<_PreviewShell> createState() => _PreviewShellState();
}

class _PreviewShellState extends State<_PreviewShell> {
  final MockBackendClient _mockClient = MockBackendClient();
  final HttpBackendClient _httpClient = HttpBackendClient();
  final BackendProcessManager _backendProcessManager = BackendProcessManager();
  late final BackendClient _client = _mockClient;
  final List<AppEvent> _events = [];
  StreamSubscription<AppEvent>? _eventSubscription;

  int _selectedIndex = 0;
  String? _roomConnectionMode;
  bool _loading = true;
  BackendHealth? _health;
  AppSettings? _settings;
  List<ServerPreset> _servers = [];

  bool get _developerMode => _settings?.developerMode ?? false;

  String get _defaultServerHost {
    final settings = _settings;
    if (settings == null || _servers.isEmpty) {
      return MockBackendClient.defaultRelayHost;
    }
    return _servers
        .firstWhere(
          (server) => server.serverId == settings.defaultServerId,
          orElse: () => _servers.first,
        )
        .host;
  }

  @override
  void initState() {
    super.initState();
    _eventSubscription = _client.streamEvents().listen((event) {
      if (!mounted) {
        return;
      }
      setState(() {
        _events.insert(0, event);
        if (_events.length > 120) {
          _events.removeLast();
        }
      });
    });
    _backendProcessManager.addListener(_onBackendProcessChanged);
    _backendProcessManager.ensureBackendRunning();
    _load();
  }

  void _onBackendProcessChanged() {
    if (!mounted) return;
    setState(() {});
  }

  @override
  void dispose() {
    _eventSubscription?.cancel();
    _httpClient.dispose();
    _mockClient.dispose();
    _backendProcessManager.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    final health = await _client.getHealth();
    final settings = await _client.getSettings();
    final servers = await _client.getServers();
    if (!mounted) {
      return;
    }
    setState(() {
      _health = health;
      _settings = settings;
      _servers = servers;
      _loading = false;
    });
  }

  Future<void> _refreshSettings() async {
    final settings = await _client.getSettings();
    final servers = await _client.getServers();
    if (mounted) {
      setState(() {
        _settings = settings;
        _servers = servers;
      });
    }
  }

  Future<DoctorReport> _runDoctor() {
    setState(() => _selectedIndex = 3);
    return _client.runDoctor();
  }

  Future<void> _saveSettings(AppSettings settings) async {
    await _client.saveSettings(settings);
    await _refreshSettings();
  }

  Future<void> _saveServerHost(String serverId, String host) async {
    await _client.updateServerHost(serverId, host);
    await _refreshSettings();
  }

  @override
  Widget build(BuildContext context) {
    final settings = _settings;
    final health = _health;
    final pages = [
      // 0 Home
      HomeScreen(
        health: health,
        onNavigateMyGames: () => setState(() => _selectedIndex = 1),
        onNavigateRoomConnection: (mode) {
          setState(() {
            _roomConnectionMode = mode;
            _selectedIndex = 2;
          });
        },
      ),
      // 1 My Games
      MyGamesScreen(client: _client),
      // 2 Room Connection
      RoomConnectionScreen(
        backendClient: _httpClient,
        defaultServerHost: _defaultServerHost,
        backendApiPort: settings?.backendApiPort ?? 21520,
        onRunDiagnostics: _runDoctor,
        initialMode: _roomConnectionMode,
      ),
      // 3 Network Diagnostics
      NetworkDoctorScreen(client: _client),
      // 4 Settings
      if (settings != null)
        SettingsScreen(
          settings: settings,
          servers: _servers,
          events: _events,
          onSaveSettings: _saveSettings,
          onSaveServerHost: _saveServerHost,
        )
      else
        const Center(child: CircularProgressIndicator()),
      // 5 About
      AboutScreen(developerMode: _developerMode, events: _events),
    ];

    return Scaffold(
      backgroundColor: const Color(0xFFF7F2E8),
      body: Row(
        children: [
          _Sidebar(
            selectedIndex: _selectedIndex,
            onDestinationSelected: (index) {
              setState(() => _selectedIndex = index);
            },
          ),
          Expanded(
            child: _loading
                ? const Center(child: CircularProgressIndicator())
                : LayoutBuilder(
                    builder: (context, constraints) {
                      final isHome = _selectedIndex == 0;
                      final isAbout = _selectedIndex == 5;
                      final panel = Container(
                        decoration: BoxDecoration(
                          color: Colors.white.withValues(alpha: 0.92),
                          borderRadius: const BorderRadius.horizontal(
                            left: Radius.circular(32),
                            right: Radius.circular(16),
                          ),
                          boxShadow: [
                            BoxShadow(
                              color: Colors.black.withValues(alpha: 0.04),
                              blurRadius: 16,
                              offset: const Offset(-2, 2),
                            ),
                          ],
                        ),
                        child: ClipRRect(
                          borderRadius: const BorderRadius.horizontal(
                            left: Radius.circular(32),
                            right: Radius.circular(16),
                          ),
                          child: pages[_selectedIndex],
                        ),
                      );

                      if (isAbout) {
                        return Padding(
                          padding: const EdgeInsets.fromLTRB(24, 24, 24, 24),
                          child: Align(
                            alignment: const Alignment(0, -0.15),
                            child: pages[_selectedIndex],
                          ),
                        );
                      }

                      if (!isHome) {
                        return Padding(
                          padding: const EdgeInsets.fromLTRB(0, 16, 16, 16),
                          child: Align(
                            alignment: const Alignment(0, -0.15),
                            child: ConstrainedBox(
                              constraints: BoxConstraints(
                                maxHeight: constraints.maxHeight,
                              ),
                              child: panel,
                            ),
                          ),
                        );
                      }

                      return Padding(
                        padding: const EdgeInsets.fromLTRB(8, 28, 28, 28),
                        child: Align(
                          alignment: Alignment.center,
                          child: ConstrainedBox(
                            constraints: BoxConstraints(
                              maxWidth: constraints.maxWidth < 1040
                                  ? constraints.maxWidth
                                  : 1040,
                              maxHeight: constraints.maxHeight < 640
                                  ? constraints.maxHeight
                                  : 640,
                            ),
                            child: panel,
                          ),
                        ),
                      );
                    },
                  ),
          ),
        ],
      ),
    );
  }
}
