import 'dart:async';

import '../models/app_event.dart';
import '../models/app_settings.dart';
import '../models/api_response.dart';
import '../models/backend_api_models.dart';
import '../models/backend_health.dart';
import '../models/doctor_report.dart';
import '../models/game_profile.dart';
import '../models/server_preset.dart';
import 'backend_client.dart';

class MockBackendException implements Exception {
  MockBackendException(this.error);

  MockBackendException.fromParts(
    String code,
    String message, {
    Map<String, Object?> details = const {},
  }) : error = ApiError(code: code, message: message, details: details);

  final ApiError error;

  String get code => error.code;

  String get message => error.message;

  @override
  String toString() => '${error.code}: ${error.message}';
}

class MockBackendClient implements BackendClient {
  MockBackendClient() {
    _emit('backend_ready', 'Backend', 'INFO', 'Mock backend ready.');
    _emit('profile_loaded', 'Profiles', 'INFO', 'Loaded mock game profiles.');
  }

  static const defaultRelayHost = String.fromEnvironment(
    'COOPWING_DEFAULT_RELAY_HOST',
    defaultValue: '120.27.210.184',
  );
  static const previewVersion = '0.4.0-preview';
  static const defaultBackendApiPort = 21520;

  final StreamController<AppEvent> _events =
      StreamController<AppEvent>.broadcast();

  final DateTime _startedAt = DateTime.now();
  final List<LogEvent> _logs = [];
  final List<SessionInfo> _sessions = [];
  final Map<String, List<SessionEvent>> _sessionLogs = {};
  final List<DoctorReport> _reports = [];
  bool _lanDiscoveryRunning = false;
  DoctorStatus _doctorStatus = DoctorStatus.idle;

  late AppSettings _settings = const AppSettings(
    defaultServerId: 'default_relay',
    backendApiPort: defaultBackendApiPort,
    logLevel: 'INFO',
    developerMode: false,
    theme: 'dark',
  );

  late final List<ServerPreset> _servers = [
    const ServerPreset(
      serverId: 'default_relay',
      displayName: 'Default VPS',
      host: defaultRelayHost,
      description: 'Default Preview relay/signaling server preset',
      enabled: true,
    ),
  ];

  final List<GameProfile> _profiles = [
    const GameProfile(
      profileId: 'mock_starlight',
      displayName: 'Starlight Sandbox',
      exePath: r'C:\Games\StarlightSandbox\StarlightSandbox.exe',
      workingDir: r'C:\Games\StarlightSandbox',
      adapterType: AdapterType.launchOnly,
      protocol: '',
      localBindHost: '127.0.0.1',
      localBindPort: null,
      remoteTargetHost: '',
      remoteTargetPort: null,
      launchArgs: '',
      expectedProcessName: '',
      expectedPorts: [],
      doctorProfile: {},
      notes: 'Mock launch-only profile.',
      status: ProfileStatus.ready,
    ),
    const GameProfile(
      profileId: 'mock_diagnostics',
      displayName: 'Network Check Only',
      exePath: '',
      workingDir: '',
      adapterType: AdapterType.diagnosticsOnly,
      protocol: '',
      localBindHost: '127.0.0.1',
      localBindPort: null,
      remoteTargetHost: '',
      remoteTargetPort: null,
      launchArgs: '',
      expectedProcessName: '',
      expectedPorts: [],
      doctorProfile: {},
      notes: 'Read-only diagnostics profile.',
      status: ProfileStatus.ready,
    ),
  ];

  DoctorReport? _lastReport;

  @override
  Stream<AppEvent> streamEvents() => _events.stream;

  @override
  Future<HealthStatus> health() async {
    await _mockDelay();
    return HealthStatus(
      status: 'ok',
      version: previewVersion,
      uptimeSeconds: DateTime.now().difference(_startedAt).inSeconds,
      backend: 'mock',
      mode: 'fake',
    );
  }

  @override
  Future<BackendHealth> getHealth() async {
    await _mockDelay();
    return BackendHealth(
      version: previewVersion,
      uptimeSeconds: DateTime.now().difference(_startedAt).inSeconds,
      status: BackendConnectionStatus.connected,
    );
  }

  @override
  Future<SessionInfo> createSession({
    required String serverHost,
    required int serverPort,
    required int serverUdpPort,
    required String playerName,
    required int gameServerPort,
    required String bindHost,
    required int bindPort,
    bool forceRelay = true,
    AdapterConfig? adapterConfig,
  }) async {
    await _mockDelay();
    final session = _makeSession(
      role: 'create',
      status: 'running',
      roomId: 'MOCK42',
      playerName: playerName,
      serverHost: serverHost,
      serverPort: serverPort,
      serverUdpPort: serverUdpPort,
      adapterHost: bindHost,
      adapterPort: bindPort,
      gameServerHost: '127.0.0.1',
      gameServerPort: gameServerPort,
      forceRelay: forceRelay,
      adapterStatus: _mockAdapterStatus(adapterConfig),
    );
    _sessions.insert(0, session);
    _sessionLogs[session.sessionId] = [
      SessionEvent(
        type: 'session_created',
        message: 'Mock create session created.',
        timestamp: session.createdAt,
        data: {'session_id': session.sessionId, 'role': 'create'},
      ),
      SessionEvent(
        type: 'room_created',
        message: 'Room ${session.roomId} created.',
        timestamp: session.updatedAt,
        data: {'room_id': session.roomId},
      ),
      SessionEvent(
        type: 'relay_ready',
        message: 'Mock relay path ready.',
        timestamp: session.updatedAt,
        data: {'room_id': session.roomId},
      ),
      SessionEvent(
        type: 'session_running',
        message: 'Mock session running.',
        timestamp: session.updatedAt,
        data: {'session_id': session.sessionId},
      ),
    ];
    _emit('session_created', 'Backend', 'INFO', 'Mock create session ready.');
    return session;
  }

  @override
  Future<SessionInfo> joinSession({
    required String serverHost,
    required int serverPort,
    required int serverUdpPort,
    required String roomId,
    required String playerName,
    required String gameServerHost,
    int? gameServerPort,
    bool forceRelay = true,
    AdapterConfig? adapterConfig,
  }) async {
    await _mockDelay();
    final session = _makeSession(
      role: 'join',
      status: 'running',
      roomId: roomId,
      playerName: playerName,
      serverHost: serverHost,
      serverPort: serverPort,
      serverUdpPort: serverUdpPort,
      adapterHost: '127.0.0.1',
      adapterPort: 0,
      gameServerHost: gameServerHost,
      gameServerPort: gameServerPort,
      forceRelay: forceRelay,
      adapterStatus: _mockAdapterStatus(adapterConfig),
    );
    _sessions.insert(0, session);
    _sessionLogs[session.sessionId] = [
      SessionEvent(
        type: 'session_created',
        message: 'Mock join session created.',
        timestamp: session.createdAt,
        data: {'session_id': session.sessionId, 'role': 'join'},
      ),
      SessionEvent(
        type: 'room_joined',
        message: 'Joined room ${session.roomId}.',
        timestamp: session.updatedAt,
        data: {'room_id': session.roomId},
      ),
      SessionEvent(
        type: 'relay_ready',
        message: 'Mock relay path ready.',
        timestamp: session.updatedAt,
        data: {'room_id': session.roomId},
      ),
      SessionEvent(
        type: 'session_running',
        message: 'Mock session running.',
        timestamp: session.updatedAt,
        data: {'session_id': session.sessionId},
      ),
    ];
    _emit('session_joined', 'Backend', 'INFO', 'Mock join session ready.');
    return session;
  }

  @override
  Future<SessionInfo> getSessionStatus(String sessionId) async {
    await _mockDelay();
    return _findSession(sessionId);
  }

  @override
  Future<List<SessionInfo>> listSessions() async {
    await _mockDelay();
    return List.unmodifiable(_sessions);
  }

  @override
  Future<List<SessionEvent>> getSessionLogs(String sessionId) async {
    await _mockDelay();
    _findSession(sessionId);
    return List.unmodifiable(_sessionLogs[sessionId] ?? const []);
  }

  @override
  Future<SessionInfo> stopSession(String sessionId) async {
    await _mockDelay();
    final index = _sessions.indexWhere((item) => item.sessionId == sessionId);
    if (index == -1) {
      throw const BackendError(
        code: 'SESSION_NOT_FOUND',
        message: 'Session not found.',
      );
    }
    final current = _sessions[index];
    final stopped = SessionInfo(
      sessionId: current.sessionId,
      role: current.role,
      status: 'stopped',
      roomId: current.roomId,
      playerName: current.playerName,
      serverHost: current.serverHost,
      serverPort: current.serverPort,
      serverUdpPort: current.serverUdpPort,
      adapterHost: current.adapterHost,
      adapterPort: current.adapterPort,
      gameServerHost: current.gameServerHost,
      gameServerPort: current.gameServerPort,
      forceRelay: current.forceRelay,
      createdAt: current.createdAt,
      updatedAt: DateTime.now().millisecondsSinceEpoch / 1000,
      stats: current.stats,
      adapterStatus: current.adapterStatus,
      playerId: current.playerId,
      protocolVersion: current.protocolVersion,
      maxPlayers: current.maxPlayers,
      participantCount: current.participantCount,
      participants: current.participants,
      hostPlayerId: current.hostPlayerId,
      lastRoomEvent: current.lastRoomEvent,
      roomReady: current.roomReady,
      roomClosed: true,
      relayReady: current.relayReady,
      relayTokenAvailable: current.relayTokenAvailable,
      relayTargetHost: current.relayTargetHost,
      relayTargetPort: current.relayTargetPort,
      serverTime: current.serverTime,
      secondaryIpEnabled: current.secondaryIpEnabled,
      secondaryIpFallbackUsed: current.secondaryIpFallbackUsed,
      secondaryIpWarning: current.secondaryIpWarning,
      backendAdmin: current.backendAdmin,
      secondaryIpBindAddress: current.secondaryIpBindAddress,
      secondaryIpInterfaceIndex: current.secondaryIpInterfaceIndex,
      secondaryIpInterfaceAlias: current.secondaryIpInterfaceAlias,
      adapterBindMode: current.adapterBindMode,
    );
    _sessions[index] = stopped;
    _sessionLogs[sessionId]?.add(
      SessionEvent(
        type: 'session_stopped',
        message: 'Mock session stopped.',
        timestamp: stopped.updatedAt,
        data: {'session_id': sessionId},
      ),
    );
    _emit('session_stopped', 'Backend', 'INFO', 'Mock session stopped.');
    return stopped;
  }

  @override
  Future<LanDiscoveryStatus> getLanDiscoveryStatus() async {
    await _mockDelay();
    return _lanDiscoveryStatus();
  }

  @override
  Future<LanDiscoveryStatus> startLanDiscovery() async {
    await _mockDelay();
    _lanDiscoveryRunning = true;
    _emit(
      'lan_discovery_started',
      'Backend',
      'INFO',
      'Mock LAN discovery started.',
    );
    return _lanDiscoveryStatus();
  }

  @override
  Future<LanDiscoveryStatus> stopLanDiscovery() async {
    await _mockDelay();
    _lanDiscoveryRunning = false;
    _emit(
      'lan_discovery_stopped',
      'Backend',
      'INFO',
      'Mock LAN discovery stopped.',
    );
    return _lanDiscoveryStatus();
  }

  @override
  Future<LanDiscoveryPeersResponse> getLanDiscoveryPeers() async {
    await _mockDelay();
    return LanDiscoveryPeersResponse(
      running: _lanDiscoveryRunning,
      peers: _lanDiscoveryRunning ? _lanDiscoveryPeers() : const [],
    );
  }

  @override
  Future<SecondaryIpRecommendation> getSecondaryIpRecommendation() async {
    await _mockDelay();
    return const SecondaryIpRecommendation(
      available: true,
      backendAdmin: false,
      interfaceIndex: 18,
      interfaceAlias: 'Ethernet',
      interfaceDescription: 'Intel Ethernet',
      interfaceIp: '192.168.5.42',
      prefixLength: 24,
      recommendedIp: '192.168.5.233',
    );
  }

  @override
  Future<Map<String, dynamic>> releaseSecondaryIp() async {
    await _mockDelay();
    return <String, dynamic>{'ok': true, 'items': <Map<String, dynamic>>[]};
  }

  @override
  Future<Map<String, dynamic>> getSecondaryIpStatus() async {
    await _mockDelay();
    return <String, dynamic>{
      'allocated': false,
      'backend_admin': false,
      'interface_index': null,
      'interface_alias': null,
      'allocated_ip': null,
      'prefix_length': null,
      'bind_mode': 'loopback',
      'source': 'none',
      'last_error': null,
      'original_dhcp_ips': <String>[],
    };
  }

  @override
  Future<ProcessPortScanResult> scanProcessPorts(int pid) async {
    await _mockDelay();
    return ProcessPortScanResult(
      pid: pid,
      candidates: [
        ProcessPortCandidate(
          pid: pid,
          protocol: 'tcp',
          localAddress: '0.0.0.0',
          localPort: 27015,
          state: 'Listen',
          confidence: 'high',
          reason: 'TCP LISTEN on 0.0.0.0:27015',
        ),
        ProcessPortCandidate(
          pid: pid,
          protocol: 'udp',
          localAddress: '0.0.0.0',
          localPort: 27016,
          confidence: 'high',
          reason: 'UDP bound 0.0.0.0:27016',
        ),
      ],
    );
  }

  @override
  Future<AppSettings> getSettings() async {
    await _mockDelay();
    return _settings;
  }

  @override
  Future<AppSettings> saveSettings(AppSettings settings) async {
    await _mockDelay();
    _settings = settings;
    _emit(
      'settings_saved',
      'Settings',
      'INFO',
      'Mock settings saved in memory.',
    );
    return _settings;
  }

  @override
  Future<List<ServerPreset>> getServers() async {
    await _mockDelay();
    return List.unmodifiable(_servers);
  }

  @override
  Future<ServerPreset> updateServerHost(String serverId, String host) async {
    await _mockDelay();
    final index = _servers.indexWhere((server) => server.serverId == serverId);
    if (index == -1) {
      throw MockBackendException.fromParts(
        'SERVER_NOT_FOUND',
        'Server preset not found.',
        details: {'server_id': serverId},
      );
    }
    final trimmed = host.trim();
    if (trimmed.isEmpty) {
      throw MockBackendException.fromParts(
        'SERVER_INVALID_HOST',
        'Please enter a server host.',
        details: {'field': 'host'},
      );
    }
    _servers[index] = _servers[index].copyWith(host: trimmed);
    _emit('server_saved', 'Settings', 'INFO', 'Updated mock server host.');
    return _servers[index];
  }

  @override
  Future<List<GameProfile>> getProfiles() async {
    await _mockDelay();
    return List.unmodifiable(_profiles);
  }

  @override
  Future<GameProfile> createProfileDraftFromExe(String path) async {
    await _mockDelay();
    final trimmed = path.trim();
    if (trimmed.isEmpty) {
      throw MockBackendException.fromParts(
        'PROFILE_INVALID_EXE',
        'Please enter or drop a .exe path.',
        details: {'field': 'exe_path', 'reason': 'empty_path'},
      );
    }
    if (!trimmed.toLowerCase().endsWith('.exe')) {
      throw MockBackendException.fromParts(
        'PROFILE_INVALID_EXE',
        'Please select a valid .exe game file.',
        details: {
          'field': 'exe_path',
          'value': trimmed,
          'reason': 'extension_is_not_exe',
          'future_error_codes': [
            'PROFILE_EXE_NOT_FOUND',
            'PROFILE_EXE_IS_DIRECTORY',
            'PROFILE_EXE_ACCESS_DENIED',
          ],
        },
      );
    }

    final separatorIndex = trimmed.lastIndexOf(RegExp(r'[\\/]'));
    final fileName = separatorIndex >= 0
        ? trimmed.substring(separatorIndex + 1)
        : trimmed;
    final displayName = fileName.substring(0, fileName.length - 4);
    final workingDir = separatorIndex >= 0
        ? trimmed.substring(0, separatorIndex)
        : '';

    final draft = GameProfile(
      profileId: 'draft_${DateTime.now().microsecondsSinceEpoch}',
      displayName: displayName,
      exePath: trimmed,
      workingDir: workingDir,
      adapterType: AdapterType.launchOnly,
      protocol: '',
      localBindHost: '127.0.0.1',
      localBindPort: null,
      remoteTargetHost: '',
      remoteTargetPort: null,
      launchArgs: '',
      expectedProcessName: '',
      expectedPorts: const [],
      doctorProfile: const {},
      notes: '',
      status: ProfileStatus.ready,
    );
    _emit(
      'game_profile_created_from_exe',
      'Profiles',
      'INFO',
      'Created mock draft for ${draft.displayName}.',
    );
    return draft;
  }

  @override
  Future<GameProfile> saveProfile(GameProfile profile) async {
    await _mockDelay();
    final cleanProfile = profile.copyWith(
      status: profile.status == ProfileStatus.running
          ? ProfileStatus.ready
          : profile.status,
      clearErrorMessage: true,
    );
    final index = _profiles.indexWhere(
      (item) => item.profileId == cleanProfile.profileId,
    );
    if (index == -1) {
      _profiles.add(cleanProfile);
    } else {
      _profiles[index] = cleanProfile;
    }
    _emit(
      'profile_saved',
      'Profiles',
      'INFO',
      'Saved mock profile: ${cleanProfile.displayName}.',
    );
    return cleanProfile;
  }

  @override
  Future<void> deleteProfile(String profileId) async {
    await _mockDelay();
    final index = _profiles.indexWhere(
      (profile) => profile.profileId == profileId,
    );
    if (index == -1) {
      throw MockBackendException.fromParts(
        'PROFILE_NOT_FOUND',
        'Profile not found.',
        details: {'profile_id': profileId},
      );
    }
    final removed = _profiles.removeAt(index);
    _emit(
      'profile_deleted',
      'Profiles',
      'INFO',
      'Deleted mock profile: ${removed.displayName}.',
    );
  }

  @override
  Future<void> launchGame(String profileId) async {
    await _mockDelay();
    final index = _profileIndex(profileId);
    final profile = _profiles[index];
    if (profile.adapterType == AdapterType.diagnosticsOnly) {
      _profiles[index] = profile.copyWith(
        status: ProfileStatus.error,
        errorMessage: 'Diagnostics-only profiles cannot launch a game.',
      );
      _emit(
        'launch_failed',
        'Launch',
        'WARN',
        'Launch blocked by profile mode.',
      );
      return;
    }
    _profiles[index] = profile.copyWith(
      status: ProfileStatus.running,
      lastLaunchedAt: DateTime.now(),
      clearErrorMessage: true,
    );
    _emit(
      'launch_started',
      'Launch',
      'INFO',
      'Mock launch started for ${profile.displayName}.',
    );
  }

  @override
  Future<void> stopGame(String profileId) async {
    await _mockDelay();
    final index = _profileIndex(profileId);
    final profile = _profiles[index];
    _profiles[index] = profile.copyWith(status: ProfileStatus.ready);
    _emit(
      'launch_stopped',
      'Launch',
      'INFO',
      'Mock launch stopped for ${profile.displayName}.',
    );
  }

  @override
  Future<DoctorReport> runDoctor() async {
    _doctorStatus = DoctorStatus.running;
    _emit('doctor_started', 'Doctor', 'INFO', 'Mock diagnostics started.');
    await Future<void>.delayed(const Duration(milliseconds: 650));
    _lastReport = DoctorReport(
      filename:
          's2pass_diagnostics_mock_${DateTime.now().millisecondsSinceEpoch}',
      createdAt: DateTime.now(),
      sizeBytes: null,
      reportType: ReportType.directory,
      summaryPath:
          r'C:\Co-opWinG\diagnostics\s2pass_diagnostics_mock\summary.json',
      zipPath: null,
      summary: 'Mock diagnostics completed. No system changes were made.',
      systemInfo: const [
        'Platform: Windows desktop mock',
        'Backend: MockBackendClient',
        'Mode: read-only diagnostics prototype',
      ],
      networkInterfaces: const [
        'Loopback: 127.0.0.1',
        'Primary adapter: Mock Ethernet, private address redacted',
      ],
      serverConnectivity: [
        'Default relay preset reachable: not tested in Preview 0.2 mock UI',
        'Configured host: ${defaultServerHost()}',
      ],
      natReachability: const [
        'NAT type: unknown in mock mode',
        'Reachability: not measured',
      ],
      recommendations: const [
        'Use this page as a read-only report viewer prototype.',
        'Review reports for local network information before sharing.',
        'Real Network Doctor execution is intentionally not wired in this UI shell.',
      ],
    );
    _reports.insert(0, _lastReport!);
    _doctorStatus = DoctorStatus.completed;
    _emit(
      'doctor_finished',
      'Doctor',
      'INFO',
      'Mock diagnostics finished; report is ready.',
    );
    return _lastReport!;
  }

  @override
  Future<String> getDoctorStatus() async {
    await _mockDelay();
    return _doctorStatus.label;
  }

  @override
  Future<List<DoctorReport>> getDoctorReports() async {
    await _mockDelay();
    if (_reports.isEmpty) {
      return [
        DoctorReport(
          filename: 's2pass_diagnostics_mock_seed.zip',
          createdAt: _startedAt,
          sizeBytes: 4096,
          reportType: ReportType.zip,
          summaryPath:
              r'C:\Co-opWinG\diagnostics\s2pass_diagnostics_mock_seed\summary.json',
          zipPath: r'C:\Co-opWinG\diagnostics\s2pass_diagnostics_mock_seed.zip',
          summary: 'Seed mock zip report. No file system access was performed.',
          systemInfo: const ['Platform: Windows desktop mock'],
          networkInterfaces: const ['Loopback: 127.0.0.1'],
          serverConnectivity: [
            'Configured host: ${defaultServerHost()}',
            'Connectivity not tested in mock mode.',
          ],
          natReachability: const ['NAT type: unknown in mock mode'],
          recommendations: const [
            'Treat this as report metadata only.',
            'Export and folder actions remain placeholders.',
          ],
        ),
      ];
    }
    return List.unmodifiable(_reports);
  }

  @override
  Future<List<LogEvent>> getLogs() async {
    await _mockDelay();
    return List.unmodifiable(_logs);
  }

  @override
  Future<DoctorReport?> getLastReport() async {
    await _mockDelay();
    return _lastReport;
  }

  String defaultServerHost() {
    final id = _settings.defaultServerId;
    return _servers
        .firstWhere(
          (server) => server.serverId == id,
          orElse: () => _servers.first,
        )
        .host;
  }

  int _profileIndex(String profileId) {
    final index = _profiles.indexWhere(
      (profile) => profile.profileId == profileId,
    );
    if (index == -1) {
      throw MockBackendException.fromParts(
        'PROFILE_NOT_FOUND',
        'Profile not found.',
        details: {'profile_id': profileId},
      );
    }
    return index;
  }

  Future<void> _mockDelay() {
    return Future<void>.delayed(const Duration(milliseconds: 120));
  }

  void _emit(String event, String source, String level, String message) {
    final log = LogEvent(
      timestamp: DateTime.now(),
      source: source,
      level: level,
      message: message,
    );
    _logs.insert(0, log);
    if (_logs.length > 200) {
      _logs.removeLast();
    }
    _events.add(
      AppEvent(
        event: event,
        timestamp: log.timestamp,
        payload: {'source': source, 'level': level, 'message': message},
      ),
    );
  }

  SessionInfo _makeSession({
    required String role,
    required String status,
    required String? roomId,
    required String playerName,
    required String serverHost,
    required int serverPort,
    required int serverUdpPort,
    required String adapterHost,
    required int adapterPort,
    required String gameServerHost,
    int? gameServerPort,
    bool forceRelay = true,
    AdapterStatus? adapterStatus,
  }) {
    final now = DateTime.now().millisecondsSinceEpoch / 1000;
    const participants = [
      ParticipantDto(
        playerId: 'p_mock_alice',
        playerName: 'Alice',
        isHost: true,
      ),
      ParticipantDto(playerId: 'p_mock_bob', playerName: 'Bob', isHost: false),
      ParticipantDto(
        playerId: 'p_mock_carol',
        playerName: 'Carol',
        isHost: false,
      ),
    ];
    return SessionInfo(
      sessionId: 's_${DateTime.now().microsecondsSinceEpoch.toRadixString(16)}',
      role: role,
      status: status,
      roomId: roomId,
      playerName: playerName,
      serverHost: serverHost,
      serverPort: serverPort,
      serverUdpPort: serverUdpPort,
      adapterHost: adapterHost,
      adapterPort: adapterPort,
      gameServerHost: gameServerHost,
      gameServerPort: gameServerPort,
      forceRelay: forceRelay,
      createdAt: now,
      updatedAt: now,
      stats: SessionStats.empty(),
      adapterStatus: adapterStatus,
      playerId: role == 'create' ? 'p_mock_alice' : 'p_mock_bob',
      protocolVersion: 2,
      maxPlayers: 4,
      participantCount: participants.length,
      participants: participants,
      hostPlayerId: 'p_mock_alice',
      lastRoomEvent: 'room_ready',
      roomReady: true,
      roomClosed: false,
      relayReady: true,
      relayTokenAvailable: true,
      relayTargetHost: serverHost,
      relayTargetPort: serverUdpPort,
      serverTime: now,
      secondaryIpEnabled:
          adapterStatus?.bindHost != null &&
          adapterStatus!.bindHost != '127.0.0.1',
      secondaryIpFallbackUsed: false,
      secondaryIpWarning: null,
      backendAdmin: false,
      secondaryIpBindAddress:
          adapterStatus?.bindHost == null ||
              adapterStatus!.bindHost == '127.0.0.1'
          ? null
          : adapterStatus.bindHost,
      adapterBindMode:
          adapterStatus?.bindHost == null ||
              adapterStatus!.bindHost == '127.0.0.1'
          ? 'loopback'
          : 'secondary_ip',
    );
  }

  AdapterStatus? _mockAdapterStatus(AdapterConfig? config) {
    if (config == null) return null;
    if (!config.enabled) {
      return const AdapterStatus(enabled: false, status: 'disabled');
    }
    return AdapterStatus(
      enabled: true,
      status: 'stopped',
      adapterType: config.adapterType,
      bindHost: config.bindHost,
      bindPort: config.bindPort,
      targetHost: config.targetHost,
      targetPort: config.targetPort,
      counters: AdapterCounters.empty(),
    );
  }

  LanDiscoveryStatus _lanDiscoveryStatus() {
    return LanDiscoveryStatus(
      running: _lanDiscoveryRunning,
      peerId: _lanDiscoveryRunning ? 'peer_mock_local' : null,
      instanceName: 'Mock Co-opWinG',
      servicePort: defaultBackendApiPort,
      broadcastPort: 37020,
      peerCount: _lanDiscoveryRunning ? _lanDiscoveryPeers().length : 0,
    );
  }

  List<LanPeerDto> _lanDiscoveryPeers() {
    return const [
      LanPeerDto(
        peerId: 'peer_mock_neighbor',
        name: 'Mock Nearby Co-opWinG',
        host: '192.168.1.23',
        port: defaultBackendApiPort,
        version: previewVersion,
        lastSeenAgeSeconds: 1.1,
      ),
    ];
  }

  // ── v0.3-J Game Profile mock implementation ────────────────────────

  final List<GameProfileDto> _gameProfiles = [];

  @override
  Future<GameProfileList> listGames() async {
    await _mockDelay();
    return List<GameProfileDto>.from(_gameProfiles);
  }

  @override
  Future<GameProfileDto> createGame({
    required String displayName,
    required String executablePath,
    String? workingDirectory,
    List<String>? launchArgs,
    String? notes,
  }) async {
    await _mockDelay();
    final now = DateTime.now().millisecondsSinceEpoch.toDouble() / 1000.0;
    final game = GameProfileDto(
      gameId:
          'mock_g_${_gameProfiles.length + 1}_${DateTime.now().millisecond}',
      displayName: displayName,
      executablePath: executablePath,
      workingDirectory: workingDirectory,
      launchArgs: launchArgs,
      notes: notes,
      createdAt: now,
      updatedAt: now,
    );
    _gameProfiles.insert(0, game);
    return game;
  }

  @override
  Future<GameProfileDto> getGame(String gameId) async {
    await _mockDelay();
    final idx = _gameProfiles.indexWhere((g) => g.gameId == gameId);
    if (idx < 0) {
      throw const BackendError(
        code: 'GAME_NOT_FOUND',
        message: 'Game profile not found.',
      );
    }
    return _gameProfiles[idx];
  }

  @override
  Future<void> deleteGame(String gameId) async {
    await _mockDelay();
    _gameProfiles.removeWhere((g) => g.gameId == gameId);
  }

  @override
  Future<ScanResultDto> scanGamePorts(
    String gameId, {
    String stage = 'manual',
    int? processId,
    bool includeLowConfidence = false,
  }) async {
    await _mockDelay();
    final candidates = [
      PortCandidateDto(
        protocol: 'tcp',
        port: 27015,
        processId: 12345,
        processName: 'hl2',
        localAddress: '0.0.0.0',
        confidence: 'high',
        reason: 'TCP LISTEN on 0.0.0.0:27015',
      ),
      PortCandidateDto(
        protocol: 'udp',
        port: 27015,
        processId: 12345,
        processName: 'hl2',
        localAddress: '0.0.0.0',
        confidence: 'high',
        reason: 'UDP bound 0.0.0.0:27015',
      ),
      PortCandidateDto(
        protocol: 'tcp',
        port: 27005,
        processId: 12345,
        processName: 'hl2',
        localAddress: '127.0.0.1',
        confidence: 'medium',
        reason: 'TCP LISTEN on loopback 127.0.0.1:27005',
      ),
      PortCandidateDto(
        protocol: 'udp',
        port: 50000,
        processId: 12345,
        processName: 'hl2',
        localAddress: '0.0.0.0',
        confidence: 'low',
        reason: 'ephemeral outbound port',
      ),
    ];
    final scanResult = ScanResultDto(
      candidates: candidates,
      stage: stage,
      scannedAt: DateTime.now().millisecondsSinceEpoch.toDouble() / 1000.0,
      processName: 'hl2',
      processId: 12345,
    );
    final idx = _gameProfiles.indexWhere((g) => g.gameId == gameId);
    if (idx >= 0) {
      _gameProfiles[idx] = GameProfileDto(
        gameId: _gameProfiles[idx].gameId,
        displayName: _gameProfiles[idx].displayName,
        executablePath: _gameProfiles[idx].executablePath,
        workingDirectory: _gameProfiles[idx].workingDirectory,
        launchArgs: _gameProfiles[idx].launchArgs,
        confirmedTcpPorts: _gameProfiles[idx].confirmedTcpPorts,
        confirmedUdpPorts: _gameProfiles[idx].confirmedUdpPorts,
        candidatePorts: candidates,
        notes: _gameProfiles[idx].notes,
        createdAt: _gameProfiles[idx].createdAt,
        updatedAt: DateTime.now().millisecondsSinceEpoch.toDouble() / 1000.0,
      );
    }
    return scanResult;
  }

  @override
  Future<GameProfileDto> confirmGamePorts(
    String gameId, {
    required List<int> tcpPorts,
    required List<int> udpPorts,
  }) async {
    await _mockDelay();
    final idx = _gameProfiles.indexWhere((g) => g.gameId == gameId);
    if (idx < 0) {
      throw const BackendError(
        code: 'GAME_NOT_FOUND',
        message: 'Game profile not found.',
      );
    }
    final updated = GameProfileDto(
      gameId: _gameProfiles[idx].gameId,
      displayName: _gameProfiles[idx].displayName,
      executablePath: _gameProfiles[idx].executablePath,
      workingDirectory: _gameProfiles[idx].workingDirectory,
      launchArgs: _gameProfiles[idx].launchArgs,
      confirmedTcpPorts: tcpPorts..sort(),
      confirmedUdpPorts: udpPorts..sort(),
      candidatePorts: _gameProfiles[idx].candidatePorts,
      notes: _gameProfiles[idx].notes,
      createdAt: _gameProfiles[idx].createdAt,
      updatedAt: DateTime.now().millisecondsSinceEpoch.toDouble() / 1000.0,
    );
    _gameProfiles[idx] = updated;
    return updated;
  }

  // ── helpers ─────────────────────────────────────────────────────────

  SessionInfo _findSession(String sessionId) {
    return _sessions.firstWhere(
      (item) => item.sessionId == sessionId,
      orElse: () => throw const BackendError(
        code: 'SESSION_NOT_FOUND',
        message: 'Session not found.',
      ),
    );
  }

  ApiResponse<T> mockSuccessEnvelope<T>(T data) {
    return ApiResponse<T>.success(data);
  }

  ApiResponse<T> mockErrorEnvelope<T>(ApiError error) {
    return ApiResponse<T>.failure(error);
  }

  void dispose() {
    _events.close();
  }
}
