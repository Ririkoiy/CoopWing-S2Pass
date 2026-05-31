import 'package:flutter_test/flutter_test.dart';
import 'package:s2pass_flutter_mock/models/backend_api_models.dart';
import 'package:s2pass_flutter_mock/models/doctor_report.dart';
import 'package:s2pass_flutter_mock/models/game_profile.dart';
import 'package:s2pass_flutter_mock/services/backend_client.dart';
import 'package:s2pass_flutter_mock/services/mock_backend_client.dart';

void main() {
  test('MockBackendClient satisfies BackendClient contract', () async {
    final MockBackendClient mockClient = MockBackendClient();
    final BackendClient client = mockClient;
    addTearDown(mockClient.dispose);

    final health = await client.getHealth();
    expect(health.statusLabel, 'Mock Connected');

    final settings = await client.getSettings();
    expect(settings.backendApiPort, 21520);

    final servers = await client.getServers();
    expect(servers.single.host, MockBackendClient.defaultRelayHost);

    final profiles = await client.getProfiles();
    expect(profiles, isNotEmpty);
  });

  test('Add Game draft validates exe path and saves only on request', () async {
    final MockBackendClient mockClient = MockBackendClient();
    final BackendClient client = mockClient;
    addTearDown(mockClient.dispose);

    await expectLater(
      client.createProfileDraftFromExe(''),
      throwsA(
        isA<MockBackendException>().having(
          (error) => error.code,
          'code',
          'PROFILE_INVALID_EXE',
        ),
      ),
    );

    await expectLater(
      client.createProfileDraftFromExe(r'C:\Games\Example\readme.txt'),
      throwsA(
        isA<MockBackendException>().having(
          (error) => error.code,
          'code',
          'PROFILE_INVALID_EXE',
        ),
      ),
    );

    final before = await client.getProfiles();
    final draft = await client.createProfileDraftFromExe(
      r'C:\Games\Example\Example.exe',
    );

    expect(draft.displayName, 'Example');
    expect(draft.adapterType, AdapterType.launchOnly);
    expect((await client.getProfiles()).length, before.length);

    await client.saveProfile(draft);
    expect((await client.getProfiles()).length, before.length + 1);
  });

  test('Doctor reports support directory and zip metadata', () async {
    final MockBackendClient mockClient = MockBackendClient();
    final BackendClient client = mockClient;
    addTearDown(mockClient.dispose);

    final seedReports = await client.getDoctorReports();
    expect(seedReports.single.reportType, ReportType.zip);
    expect(seedReports.single.zipPath, isNotNull);

    final report = await client.runDoctor();
    expect(report.reportType, ReportType.directory);
    expect(report.zipPath, isNull);

    final status = await client.getDoctorStatus();
    expect(status, DoctorStatus.completed.label);
  });

  test('MockBackendClient preserves fake session API methods', () async {
    final MockBackendClient mockClient = MockBackendClient();
    final BackendClient client = mockClient;
    addTearDown(mockClient.dispose);

    final health = await client.health();
    expect(health.isOnline, isTrue);
    expect(health.isFakeMode, isTrue);

    final created = await client.createSession(
      serverHost: MockBackendClient.defaultRelayHost,
      serverPort: 9000,
      serverUdpPort: 9001,
      playerName: 'CreatorA',
      bindHost: '127.0.0.1',
      bindPort: 0,
    );
    expect(created.role, 'create');
    expect(created.roomId, isNotEmpty);

    final logs = await client.getSessionLogs(created.sessionId);
    expect(logs.map((event) => event.type), contains('room_created'));

    final stopped = await client.stopSession(created.sessionId);
    expect(stopped.status, 'stopped');
  });

  test('BackendError parses backend error envelope payload', () {
    final error = BackendError.fromJson({
      'code': 'SESSION_ALREADY_STOPPED',
      'message': 'Session is already stopped.',
      'details': {'session_id': 's_test'},
    });

    expect(error.code, 'SESSION_ALREADY_STOPPED');
    expect(error.message, 'Session is already stopped.');
    expect(error.details['session_id'], 's_test');
  });
}
