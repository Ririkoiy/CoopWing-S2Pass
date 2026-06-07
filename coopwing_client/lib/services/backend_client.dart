import '../models/app_event.dart';
import '../models/app_settings.dart';
import '../models/backend_api_models.dart';
import '../models/backend_health.dart';
import '../models/doctor_report.dart';
import '../models/game_profile.dart';
import '../models/server_preset.dart';

/// v0.3-J game profile API types.
typedef GameProfileList = List<GameProfileDto>;
typedef PortCandidateList = List<PortCandidateDto>;

abstract class BackendClient {
  Future<HealthStatus> health();

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
  });

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
  });

  Future<SessionInfo> getSessionStatus(String sessionId);

  Future<List<SessionInfo>> listSessions();

  Future<List<SessionEvent>> getSessionLogs(String sessionId);

  Future<SessionInfo> stopSession(String sessionId);

  Future<LanDiscoveryStatus> getLanDiscoveryStatus();

  Future<LanDiscoveryStatus> startLanDiscovery();

  Future<LanDiscoveryStatus> stopLanDiscovery();

  Future<LanDiscoveryPeersResponse> getLanDiscoveryPeers();

  Future<SecondaryIpRecommendation> getSecondaryIpRecommendation();

  Future<Map<String, dynamic>> releaseSecondaryIp();

  Future<Map<String, dynamic>> getSecondaryIpStatus();

  Future<ProcessPortScanResult> scanProcessPorts(int pid);

  Future<BackendHealth> getHealth();

  Future<AppSettings> getSettings();

  Future<AppSettings> saveSettings(AppSettings settings);

  Future<List<ServerPreset>> getServers();

  Future<ServerPreset> updateServerHost(String serverId, String host);

  Future<List<GameProfile>> getProfiles();

  Future<GameProfile> createProfileDraftFromExe(String path);

  Future<GameProfile> saveProfile(GameProfile profile);

  Future<void> deleteProfile(String profileId);

  Future<void> launchGame(String profileId);

  Future<void> stopGame(String profileId);

  Future<DoctorReport> runDoctor();

  Future<String> getDoctorStatus();

  Future<List<DoctorReport>> getDoctorReports();

  Future<DoctorReport?> getLastReport();

  Future<List<LogEvent>> getLogs();

  Stream<AppEvent> streamEvents();

  // ── v0.3-J Game Profile API ──────────────────────────────────────

  Future<GameProfileList> listGames();

  Future<GameProfileDto> createGame({
    required String displayName,
    required String executablePath,
    String? workingDirectory,
    List<String>? launchArgs,
    String? notes,
  });

  Future<GameProfileDto> getGame(String gameId);

  Future<void> deleteGame(String gameId);

  Future<ScanResultDto> scanGamePorts(
    String gameId, {
    String stage = 'manual',
    int? processId,
    bool includeLowConfidence = false,
  });

  Future<GameProfileDto> confirmGamePorts(
    String gameId, {
    required List<int> tcpPorts,
    required List<int> udpPorts,
  });
}
