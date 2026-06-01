import '../models/app_event.dart';
import '../models/app_settings.dart';
import '../models/backend_api_models.dart';
import '../models/backend_health.dart';
import '../models/doctor_report.dart';
import '../models/game_profile.dart';
import '../models/server_preset.dart';

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
}
