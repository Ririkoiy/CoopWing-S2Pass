import 'dart:async';
import 'dart:convert';
import 'dart:io';

import '../models/app_event.dart';
import '../models/app_settings.dart';
import '../models/backend_api_models.dart';
import '../models/backend_health.dart';
import '../models/doctor_report.dart';
import '../models/game_profile.dart';
import '../models/server_preset.dart';
import 'backend_client.dart';

class HttpBackendClient implements BackendClient {
  HttpBackendClient({
    this.baseUrl = 'http://127.0.0.1:21520',
    Duration timeout = const Duration(seconds: 2),
  }) : _timeout = timeout;

  static const offlineMessage =
      'Backend offline. It may be starting. Check logs/backend.log if the issue persists.';

  final String baseUrl;
  final Duration _timeout;
  final HttpClient _client = HttpClient();

  @override
  Future<HealthStatus> health() async {
    return HealthStatus.fromJson(await _request('GET', '/health'));
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
    final json = await _request(
      'POST',
      '/sessions/create',
      body: {
        'server_host': serverHost,
        'server_port': serverPort,
        'server_udp_port': serverUdpPort,
        'player_name': playerName,
        'game_server_port': gameServerPort,
        'bind_host': bindHost,
        'bind_port': bindPort,
        'force_relay': forceRelay,
        if (adapterConfig != null) 'adapter_config': adapterConfig.toJson(),
      },
    );
    return SessionInfo.fromJson(json);
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
    final json = await _request(
      'POST',
      '/sessions/join',
      body: {
        'server_host': serverHost,
        'server_port': serverPort,
        'server_udp_port': serverUdpPort,
        'room_id': roomId,
        'player_name': playerName,
        'game_server_host': gameServerHost,
        'force_relay': forceRelay,
        if (gameServerPort != null) 'game_server_port': gameServerPort,
        if (adapterConfig != null) 'adapter_config': adapterConfig.toJson(),
      },
    );
    return SessionInfo.fromJson(json);
  }

  @override
  Future<SessionInfo> getSessionStatus(String sessionId) async {
    final json = await _request('GET', '/sessions/$sessionId/status');
    return SessionInfo.fromJson(json);
  }

  @override
  Future<List<SessionInfo>> listSessions() async {
    final json = await _request('GET', '/sessions');
    final sessions = json['sessions'];
    if (sessions is! List) {
      return const [];
    }
    return sessions
        .whereType<Map>()
        .map((item) => SessionInfo.fromJson(_asObjectMap(item)))
        .toList(growable: false);
  }

  @override
  Future<List<SessionEvent>> getSessionLogs(String sessionId) async {
    final json = await _request('GET', '/sessions/$sessionId/logs');
    final events = json['events'];
    if (events is! List) {
      return const [];
    }
    return events
        .whereType<Map>()
        .map((item) => SessionEvent.fromJson(_asObjectMap(item)))
        .toList(growable: false);
  }

  @override
  Future<SessionInfo> stopSession(String sessionId) async {
    final json = await _request('POST', '/sessions/$sessionId/stop', body: {});
    return SessionInfo.fromJson(json);
  }

  @override
  Future<LanDiscoveryStatus> getLanDiscoveryStatus() async {
    final json = await _request('GET', '/api/lan-discovery/status');
    return LanDiscoveryStatus.fromJson(json);
  }

  @override
  Future<LanDiscoveryStatus> startLanDiscovery() async {
    final json = await _request('POST', '/api/lan-discovery/start');
    return LanDiscoveryStatus.fromJson(json);
  }

  @override
  Future<LanDiscoveryStatus> stopLanDiscovery() async {
    final json = await _request('POST', '/api/lan-discovery/stop');
    return LanDiscoveryStatus.fromJson(json);
  }

  @override
  Future<LanDiscoveryPeersResponse> getLanDiscoveryPeers() async {
    final json = await _request('GET', '/api/lan-discovery/peers');
    return LanDiscoveryPeersResponse.fromJson(json);
  }

  @override
  Future<SecondaryIpRecommendation> getSecondaryIpRecommendation() async {
    final json = await _request('GET', '/api/secondary-ip/recommendation');
    return SecondaryIpRecommendation.fromJson(json);
  }

  @override
  Future<Map<String, dynamic>> releaseSecondaryIp() async {
    return await _request('POST', '/api/secondary-ip/release');
  }

  @override
  Future<Map<String, dynamic>> getSecondaryIpStatus() async {
    return await _request('GET', '/api/secondary-ip/status');
  }

  @override
  Future<ProcessPortScanResult> scanProcessPorts(int pid) async {
    final json = await _request(
      'POST',
      '/process-ports/scan',
      body: {'pid': pid},
    );
    return ProcessPortScanResult.fromJson(json);
  }

  @override
  Future<BackendHealth> getHealth() async {
    final value = await health();
    return BackendHealth(
      version: value.version,
      uptimeSeconds: value.uptimeSeconds.round(),
      status: value.isOnline
          ? BackendConnectionStatus.connected
          : BackendConnectionStatus.disconnected,
    );
  }

  @override
  Future<AppSettings> getSettings() {
    throw UnsupportedError(
      'HttpBackendClient does not serve settings in P4.2.',
    );
  }

  @override
  Future<AppSettings> saveSettings(AppSettings settings) {
    throw UnsupportedError(
      'HttpBackendClient does not serve settings in P4.2.',
    );
  }

  @override
  Future<List<ServerPreset>> getServers() {
    throw UnsupportedError('HttpBackendClient does not serve presets in P4.2.');
  }

  @override
  Future<ServerPreset> updateServerHost(String serverId, String host) {
    throw UnsupportedError('HttpBackendClient does not serve presets in P4.2.');
  }

  @override
  Future<List<GameProfile>> getProfiles() {
    throw UnsupportedError(
      'HttpBackendClient does not serve profiles in P4.2.',
    );
  }

  @override
  Future<GameProfile> createProfileDraftFromExe(String path) {
    throw UnsupportedError(
      'HttpBackendClient does not serve profiles in P4.2.',
    );
  }

  @override
  Future<GameProfile> saveProfile(GameProfile profile) {
    throw UnsupportedError(
      'HttpBackendClient does not serve profiles in P4.2.',
    );
  }

  @override
  Future<void> deleteProfile(String profileId) {
    throw UnsupportedError(
      'HttpBackendClient does not serve profiles in P4.2.',
    );
  }

  @override
  Future<void> launchGame(String profileId) {
    throw UnsupportedError('HttpBackendClient does not launch games in P4.2.');
  }

  @override
  Future<void> stopGame(String profileId) {
    throw UnsupportedError('HttpBackendClient does not launch games in P4.2.');
  }

  @override
  Future<DoctorReport> runDoctor() {
    throw UnsupportedError(
      'HttpBackendClient does not run diagnostics in P4.2.',
    );
  }

  @override
  Future<String> getDoctorStatus() {
    throw UnsupportedError(
      'HttpBackendClient does not run diagnostics in P4.2.',
    );
  }

  @override
  Future<List<DoctorReport>> getDoctorReports() {
    throw UnsupportedError(
      'HttpBackendClient does not run diagnostics in P4.2.',
    );
  }

  @override
  Future<DoctorReport?> getLastReport() {
    throw UnsupportedError(
      'HttpBackendClient does not run diagnostics in P4.2.',
    );
  }

  @override
  Future<List<LogEvent>> getLogs() {
    throw UnsupportedError(
      'HttpBackendClient does not serve app logs in P4.2.',
    );
  }

  @override
  Stream<AppEvent> streamEvents() => const Stream<AppEvent>.empty();

  void dispose() {
    _client.close(force: true);
  }

  Future<Map<String, Object?>> _request(
    String method,
    String path, {
    Map<String, Object?>? body,
  }) async {
    final uri = Uri.parse('$baseUrl$path');
    try {
      final request = await _client.openUrl(method, uri).timeout(_timeout);
      request.headers.set(HttpHeaders.acceptHeader, 'application/json');
      if (body != null) {
        final encoded = utf8.encode(jsonEncode(body));
        request.headers.set(
          HttpHeaders.contentTypeHeader,
          'application/json; charset=utf-8',
        );
        request.headers.contentLength = encoded.length;
        request.add(encoded);
      }

      final response = await request.close().timeout(_timeout);
      final responseBody = await utf8.decoder.bind(response).join();
      final decoded = responseBody.isEmpty
          ? <String, Object?>{}
          : jsonDecode(responseBody);
      final data = decoded is Map ? _asObjectMap(decoded) : <String, Object?>{};

      if (response.statusCode >= 400) {
        final errorJson = data['error'];
        if (errorJson is Map) {
          throw BackendError.fromJson(_asObjectMap(errorJson));
        }
        throw BackendError(
          code: 'HTTP_${response.statusCode}',
          message: 'Backend returned HTTP ${response.statusCode}.',
        );
      }

      return data;
    } on BackendError {
      rethrow;
    } on TimeoutException {
      throw const BackendError(
        code: 'BACKEND_OFFLINE',
        message: offlineMessage,
      );
    } on SocketException {
      throw const BackendError(
        code: 'BACKEND_OFFLINE',
        message: offlineMessage,
      );
    } on HttpException {
      throw const BackendError(
        code: 'BACKEND_OFFLINE',
        message: offlineMessage,
      );
    } on FormatException catch (error) {
      throw BackendError(
        code: 'INVALID_RESPONSE',
        message: 'Backend returned invalid JSON.',
        details: {'error': error.message},
      );
    }
  }

  // ── v0.3-J Game Profile API (wired to HTTP) ──────────────────────

  @override
  Future<GameProfileList> listGames() async {
    final json = await _request('GET', '/api/games');
    final games = json['games'];
    if (games is! List<Object?>) return [];
    return games
        .whereType<Map<dynamic, dynamic>>()
        .map((e) => GameProfileDto.fromJson(_asObjectMap(e)))
        .toList(growable: false);
  }

  @override
  Future<GameProfileDto> createGame({
    required String displayName,
    required String executablePath,
    String? workingDirectory,
    List<String>? launchArgs,
    String? notes,
  }) async {
    final body = <String, Object?>{
      'display_name': displayName,
      'executable_path': executablePath,
    };
    if (workingDirectory != null) body['working_directory'] = workingDirectory;
    if (launchArgs != null) body['launch_args'] = launchArgs;
    if (notes != null) body['notes'] = notes;
    final json = await _request('POST', '/api/games', body: body);
    return GameProfileDto.fromJson(json);
  }

  @override
  Future<GameProfileDto> getGame(String gameId) async {
    final json = await _request('GET', '/api/games/$gameId');
    return GameProfileDto.fromJson(json);
  }

  @override
  Future<void> deleteGame(String gameId) async {
    await _request('DELETE', '/api/games/$gameId');
  }

  @override
  Future<ScanResultDto> scanGamePorts(
    String gameId, {
    String stage = 'manual',
    int? processId,
    bool includeLowConfidence = false,
  }) async {
    final body = <String, Object?>{
      'stage': stage,
      'include_low_confidence': includeLowConfidence,
    };
    if (processId != null) body['process_id'] = processId;
    final json = await _request(
      'POST',
      '/api/games/$gameId/scan-ports',
      body: body,
    );
    return ScanResultDto.fromJson(json);
  }

  @override
  Future<GameProfileDto> confirmGamePorts(
    String gameId, {
    required List<int> tcpPorts,
    required List<int> udpPorts,
  }) async {
    final body = <String, Object?>{
      'tcp_ports': tcpPorts,
      'udp_ports': udpPorts,
    };
    final json = await _request(
      'POST',
      '/api/games/$gameId/confirm-ports',
      body: body,
    );
    return GameProfileDto.fromJson(json);
  }
}

Map<String, Object?> _asObjectMap(Map<dynamic, dynamic> value) {
  return value.map((key, item) => MapEntry(key.toString(), item));
}
