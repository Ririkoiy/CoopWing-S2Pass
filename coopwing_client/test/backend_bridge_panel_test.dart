import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:s2pass_flutter_mock/models/backend_api_models.dart';
import 'package:s2pass_flutter_mock/models/doctor_report.dart';
import 'package:s2pass_flutter_mock/services/mock_backend_client.dart';
import 'package:s2pass_flutter_mock/services/localization.dart';
import 'package:s2pass_flutter_mock/widgets/backend_bridge_panel.dart';

void main() {
  setUp(() {
    Localization().setLanguage(Language.en);
  });

  testWidgets('Reset local display asks for confirmation and clears display', (
    tester,
  ) async {
    final client = MockBackendClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SingleChildScrollView(
            child: BackendBridgePanel(
              client: client,
              onRunDiagnostics: _testReport,
            ),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    await _enterPlayerName(tester);
    await _enterGameServerPort(tester, '27015');
    await tester.tap(find.widgetWithText(FilledButton, 'Create Room'));
    await tester.pump();
    expect(find.text('Creating room...'), findsOneWidget);
    await tester.pumpAndSettle();

    expect(find.text('session_id'), findsOneWidget);
    expect(find.text('Current Session'), findsOneWidget);
    expect(find.text('Current Session Summary'), findsNothing);

    final resetButton = find.text('Reset local display').first;
    await tester.ensureVisible(resetButton);
    await tester.tap(resetButton);
    await tester.pumpAndSettle();

    expect(find.text('Reset local display?'), findsOneWidget);
    expect(
      find.textContaining('This only clears the local UI display.'),
      findsOneWidget,
    );

    await tester.tap(find.text('Reset').last);
    await tester.pumpAndSettle();

    expect(find.text('Create Room'), findsWidgets);
    expect(find.text('Current Session'), findsNothing);
    expect(find.text('session_id'), findsNothing);
    expect(find.textContaining('room_created'), findsNothing);
  });

  testWidgets('Stopped session keeps reset available and create join usable', (
    tester,
  ) async {
    final client = MockBackendClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SingleChildScrollView(
            child: BackendBridgePanel(
              client: client,
              onRunDiagnostics: _testReport,
            ),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    await _enterPlayerName(tester);
    await _enterGameServerPort(tester, '27015');
    await tester.tap(find.widgetWithText(FilledButton, 'Create Room'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Stop Session'));
    await tester.tap(find.text('Stop Session'));
    await tester.pump();
    expect(find.text('Stopping session...'), findsOneWidget);
    await tester.pumpAndSettle();

    expect(find.text('stopped'), findsOneWidget);
    expect(find.text('Current Session Summary'), findsOneWidget);
    expect(find.widgetWithText(FilledButton, 'Create Room'), findsOneWidget);
    expect(find.text('Reset local display'), findsOneWidget);
    expect(find.text('Stop Session'), findsNothing);
  });

  testWidgets('Create join modes show only the selected primary form', (
    tester,
  ) async {
    final client = MockBackendClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();

    final playerNameField = tester.widget<TextField>(
      find.widgetWithText(TextField, 'Player name'),
    );
    expect(playerNameField.controller?.text, isEmpty);
    expect(playerNameField.decoration?.hintText, 'PlayerA');
    final initialCreateButton = tester.widget<FilledButton>(
      find.widgetWithText(FilledButton, 'Create Room'),
    );
    expect(initialCreateButton.onPressed, isNull);

    await _enterPlayerName(tester);
    final partialCreateButton = tester.widget<FilledButton>(
      find.widgetWithText(FilledButton, 'Create Room'),
    );
    expect(partialCreateButton.onPressed, isNull);

    await _enterGameServerPort(tester, '27015');
    final enabledCreateButton = tester.widget<FilledButton>(
      find.widgetWithText(FilledButton, 'Create Room'),
    );
    expect(enabledCreateButton.onPressed, isNotNull);

    expect(find.widgetWithText(FilledButton, 'Create Room'), findsOneWidget);
    expect(find.widgetWithText(FilledButton, 'Join Room'), findsNothing);
    expect(find.text('Room ID'), findsNothing);

    await tester.tap(find.text('Join Room'));
    await tester.pumpAndSettle();

    expect(find.widgetWithText(FilledButton, 'Create Room'), findsNothing);
    expect(find.widgetWithText(FilledButton, 'Join Room'), findsOneWidget);
    expect(find.text('Room ID'), findsOneWidget);
  });

  testWidgets('Active session hides create join controls', (tester) async {
    final client = MockBackendClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();

    await _enterPlayerName(tester);
    await _enterGameServerPort(tester, '27015');
    await tester.tap(find.widgetWithText(FilledButton, 'Create Room'));
    await tester.pumpAndSettle();

    expect(find.text('Current Session'), findsOneWidget);
    expect(find.text('Current Session Summary'), findsNothing);
    expect(find.widgetWithText(FilledButton, 'Stop Session'), findsOneWidget);
    expect(find.widgetWithText(FilledButton, 'Create Room'), findsNothing);
    expect(find.widgetWithText(FilledButton, 'Join Room'), findsNothing);
  });

  testWidgets('Room Connection shows relay inactivity note', (tester) async {
    final client = MockBackendClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();

    expect(
      find.textContaining(
        'Rooms disconnect automatically after 30 minutes without relay traffic.',
      ),
      findsOneWidget,
    );

    await _enterPlayerName(tester);
    await _enterGameServerPort(tester, '27015');
    await tester.tap(find.widgetWithText(FilledButton, 'Create Room'));
    await tester.pumpAndSettle();

    expect(
      find.textContaining(
        'Rooms disconnect automatically after 30 minutes without relay traffic.',
      ),
      findsWidgets,
    );
  });

  testWidgets('Room Connection hides manual python backend launch text', (
    tester,
  ) async {
    final client = MockBackendClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();

    expect(find.textContaining('python -m backend.server'), findsNothing);
    expect(
      find.text('Local backend: managed automatically (127.0.0.1:21520)'),
      findsOneWidget,
    );
  });

  testWidgets('Room Connection renders key Chinese preview copy', (
    tester,
  ) async {
    Localization().setLanguage(Language.zh);
    final client = MockBackendClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();

    expect(find.text('创建房间'), findsWidgets);
    expect(find.text('加入房间'), findsWidgets);
    expect(find.text('玩家名称'), findsOneWidget);
    expect(find.text('高级后端设置'), findsOneWidget);
    expect(find.textContaining('30 分钟无 relay 流量会自动断开'), findsOneWidget);

    await _enterPlayerName(tester);
    await _enterGameServerPort(tester, '27015');
    await tester.tap(find.widgetWithText(FilledButton, '创建房间'));
    await tester.pumpAndSettle();

    expect(find.text('当前会话'), findsOneWidget);
    expect(find.text('房间 ID'), findsOneWidget);
    expect(find.text('停止会话'), findsOneWidget);
    expect(find.text('重置本地显示'), findsOneWidget);
    expect(find.textContaining('敏感中继凭证不会显示'), findsOneWidget);
  });

  testWidgets('Language switch updates Room Connection labels', (tester) async {
    final client = MockBackendClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();

    expect(find.widgetWithText(FilledButton, 'Create Room'), findsOneWidget);

    Localization().setLanguage(Language.zh);
    await tester.pumpAndSettle();

    expect(find.widgetWithText(FilledButton, '创建房间'), findsOneWidget);
    expect(find.text('玩家名称'), findsOneWidget);
  });

  testWidgets('Create join are disabled while backend is offline', (
    tester,
  ) async {
    final client = _SwitchableHealthMockClient()..online = false;
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();

    final createButton = tester.widget<FilledButton>(
      find.widgetWithText(FilledButton, 'Create Room'),
    );
    expect(createButton.onPressed, isNull);
    expect(find.textContaining('Backend offline'), findsOneWidget);
  });

  testWidgets('Reset clears room id and returns to create mode', (
    tester,
  ) async {
    final client = MockBackendClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();

    await _enterPlayerName(tester);
    await tester.tap(find.text('Join Room'));
    await tester.pumpAndSettle();
    await tester.enterText(find.widgetWithText(TextField, 'Room ID'), 'MOCK42');
    await tester.tap(find.widgetWithText(FilledButton, 'Join Room'));
    await tester.pumpAndSettle();

    await tester.ensureVisible(find.text('Reset local display').first);
    await tester.tap(find.text('Reset local display').first);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Reset').last);
    await tester.pumpAndSettle();

    expect(find.widgetWithText(FilledButton, 'Create Room'), findsOneWidget);
    expect(find.widgetWithText(FilledButton, 'Join Room'), findsNothing);
    expect(find.text('Room ID'), findsNothing);
    expect(find.text('MOCK42'), findsNothing);
  });

  testWidgets(
    'Offline active session disables stop but keeps reset available',
    (tester) async {
      final client = _SwitchableHealthMockClient();
      addTearDown(client.dispose);

      await tester.pumpWidget(_bridgeTestApp(client));
      await tester.pumpAndSettle();

      await _enterPlayerName(tester);
      await _enterGameServerPort(tester, '27015');
      await tester.tap(find.widgetWithText(FilledButton, 'Create Room'));
      await tester.pumpAndSettle();

      client.online = false;
      await tester.tap(find.byTooltip('Refresh health'));
      await tester.pumpAndSettle();

      final stopButton = tester.widget<FilledButton>(
        find.widgetWithText(FilledButton, 'Stop Session'),
      );
      expect(stopButton.onPressed, isNull);
      expect(find.text('Reset local display'), findsWidgets);
    },
  );

  testWidgets('Create display and join prefill use room_created log room id', (
    tester,
  ) async {
    final client = _StaleCreateRoomMockClient();
    addTearDown(client.dispose);
    String? clipboardText;
    tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
      SystemChannels.platform,
      (call) async {
        if (call.method == 'Clipboard.setData') {
          final args = call.arguments;
          if (args is Map) {
            clipboardText = args['text'] as String?;
          }
        }
        return null;
      },
    );
    addTearDown(
      () => tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
        SystemChannels.platform,
        null,
      ),
    );

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();

    await _enterPlayerName(tester);
    await _enterGameServerPort(tester, '27015');
    await tester.tap(find.widgetWithText(FilledButton, 'Create Room'));
    await tester.pumpAndSettle();

    expect(find.text('STALE1'), findsNothing);
    expect(find.text('REAL42'), findsNothing);

    await tester.pump(const Duration(seconds: 1));
    await tester.pump();

    expect(find.text('REAL42'), findsOneWidget);
    expect(find.text('STALE1'), findsNothing);

    await tester.tap(find.byTooltip('Copy Room ID'));
    await tester.pump();
    expect(clipboardText, 'REAL42');

    await tester.tap(find.text('Stop Session'));
    await tester.pumpAndSettle();

    await _enterPlayerName(tester);
    await tester.tap(find.text('Join Room'));
    await tester.pumpAndSettle();

    expect(find.text('REAL42'), findsWidgets);
    expect(find.widgetWithText(TextField, 'Room ID'), findsOneWidget);
  });

  testWidgets('Advanced settings show local preview defaults', (tester) async {
    final client = MockBackendClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();

    await tester.ensureVisible(find.text('Advanced Backend Settings'));
    await tester.tap(find.text('Advanced Backend Settings'));
    await tester.pumpAndSettle();

    expect(find.widgetWithText(TextField, 'Backend HTTP host'), findsOneWidget);
    expect(find.widgetWithText(TextField, 'Backend HTTP port'), findsOneWidget);
    expect(find.widgetWithText(TextField, 'Relay/root host'), findsOneWidget);
    expect(find.widgetWithText(TextField, 'Relay TCP port'), findsOneWidget);
    expect(find.widgetWithText(TextField, 'Relay UDP port'), findsOneWidget);
    expect(find.text('127.0.0.1'), findsWidgets);
    expect(find.text('9000'), findsOneWidget);
    expect(find.text('9001'), findsOneWidget);
    expect(find.text('21520'), findsOneWidget);
    expect(find.text('UDP Experimental'), findsOneWidget);
  });

  testWidgets('Create request uses relay and UDP adapter defaults', (
    tester,
  ) async {
    final client = _CapturingMockClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();

    await _enterPlayerName(tester);
    await _enterGameServerPort(tester, '27015');
    await tester.tap(find.widgetWithText(FilledButton, 'Create Room'));
    await tester.pumpAndSettle();

    expect(
      client.lastCreateServerHost,
      BackendBridgePanel.localPreviewRelayHost,
    );
    expect(client.lastCreateServerPort, BackendBridgePanel.defaultRelayTcpPort);
    expect(
      client.lastCreateServerUdpPort,
      BackendBridgePanel.defaultRelayUdpPort,
    );
    final config = client.lastCreateAdapterConfig!;
    expect(config.enabled, isTrue);
    expect(config.adapterType, 'local_udp_bridge');
    expect(config.bindHost, '127.0.0.1');
    expect(config.bindPort, 0);
    expect(config.targetHost, '127.0.0.1');
    expect(config.targetPort, 27015);
  });

  testWidgets('Join request uses relay and UDP adapter defaults', (
    tester,
  ) async {
    final client = _CapturingMockClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();

    await _enterPlayerName(tester);
    await tester.tap(find.text('Join Room'));
    await tester.pumpAndSettle();
    await tester.enterText(find.widgetWithText(TextField, 'Room ID'), 'MOCK42');
    await tester.tap(find.widgetWithText(FilledButton, 'Join Room'));
    await tester.pumpAndSettle();

    expect(client.lastJoinServerHost, BackendBridgePanel.localPreviewRelayHost);
    expect(client.lastJoinServerPort, BackendBridgePanel.defaultRelayTcpPort);
    expect(
      client.lastJoinServerUdpPort,
      BackendBridgePanel.defaultRelayUdpPort,
    );
    final config = client.lastJoinAdapterConfig!;
    expect(config.enabled, isTrue);
    expect(config.adapterType, 'local_udp_bridge');
    expect(config.bindHost, '127.0.0.1');
    expect(config.bindPort, 0);
    expect(config.targetHost, '127.0.0.1');
    expect(config.targetPort, isNull);
  });

  testWidgets('User can switch adapter mode Off', (tester) async {
    final client = _CapturingMockClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();

    await tester.ensureVisible(find.text('Advanced Backend Settings'));
    await tester.tap(find.text('Advanced Backend Settings'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('UDP Experimental'));
    await tester.tap(find.text('UDP Experimental').last);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Off').last);
    await tester.pumpAndSettle();

    await tester.ensureVisible(
      find.widgetWithText(FilledButton, 'Create Room'),
    );
    await _enterPlayerName(tester);
    await _enterGameServerPort(tester, '27015');
    await tester.ensureVisible(
      find.widgetWithText(FilledButton, 'Create Room'),
    );
    tester
        .widget<FilledButton>(find.widgetWithText(FilledButton, 'Create Room'))
        .onPressed
        ?.call();
    await tester.pumpAndSettle();

    expect(client.lastCreateAdapterConfig, isNull);
  });

  testWidgets('Absent adapter_status does not show adapter error', (
    tester,
  ) async {
    final client = _AdapterStatusMockClient(null);
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();

    await _enterPlayerName(tester);
    await _enterGameServerPort(tester, '27015');
    await tester.tap(find.widgetWithText(FilledButton, 'Create Room'));
    await tester.pumpAndSettle();

    expect(find.text('Current Session'), findsOneWidget);
    expect(find.text('adapter_error'), findsNothing);
    expect(find.text('Adapter'), findsNothing);
  });

  testWidgets('Adapter disabled stopped ready and error statuses display', (
    tester,
  ) async {
    await _expectAdapterDisplay(
      tester,
      const AdapterStatus(enabled: false, status: 'disabled'),
      ['Adapter', 'Disabled'],
    );

    await _expectAdapterDisplay(
      tester,
      const AdapterStatus(
        enabled: true,
        status: 'stopped',
        adapterType: 'local_udp_bridge',
      ),
      ['Adapter', 'Stopped / configured but not running'],
    );

    await _expectAdapterDisplay(
      tester,
      const AdapterStatus(
        enabled: true,
        status: 'ready',
        adapterType: 'local_udp_bridge',
        bindHost: '127.0.0.1',
        bindPort: 40100,
        targetHost: '127.0.0.1',
        targetPort: 40200,
        counters: AdapterCounters(
          packetsFromGame: 6,
          packetsToTransport: 6,
          packetsFromTransport: 5,
          packetsToGame: 5,
        ),
      ),
      [
        'Ready',
        'Local connection address',
        '127.0.0.1:40100',
        'Game server address',
        '127.0.0.1:40200',
        'The VPS/Relay address is for CoopWing only. Do not put it directly into the game connect command.',
        'Game clients should connect to the local connection address, for example connect 127.0.0.1:40100',
        'Realtime Traffic',
        'Cumulative Packets',
        'Game -> Relay',
        'Relay -> Game',
        'game -> transport',
        '6/6',
        'transport -> game',
        '5/5',
      ],
    );

    await _expectAdapterDisplay(
      tester,
      const AdapterStatus(
        enabled: true,
        status: 'error',
        adapterType: 'local_udp_bridge',
        bindHost: '127.0.0.1',
        bindPort: 40100,
        targetHost: '127.0.0.1',
        targetPort: 40200,
        counters: AdapterCounters(
          packetsFromGame: 0,
          packetsToTransport: 0,
          packetsFromTransport: 0,
          packetsToGame: 0,
        ),
        error: AdapterStatusError(
          code: 'ADAPTER_BIND_FAILED',
          message: 'Failed to bind UDP socket to 127.0.0.1:40100',
        ),
      ),
      [
        'Error',
        'ADAPTER_BIND_FAILED',
        'Failed to bind UDP socket to 127.0.0.1:40100',
      ],
    );
  });

  testWidgets('Game server address copy copies target host port', (
    tester,
  ) async {
    final client = _AdapterStatusMockClient(
      const AdapterStatus(
        enabled: true,
        status: 'ready',
        adapterType: 'local_udp_bridge',
        bindHost: '127.0.0.1',
        bindPort: 50001,
        targetHost: '192.168.1.100',
        targetPort: 27015,
        counters: AdapterCounters(
          packetsFromGame: 1,
          packetsToTransport: 1,
          packetsFromTransport: 1,
          packetsToGame: 1,
        ),
      ),
    );
    addTearDown(client.dispose);
    String? clipboardText;
    tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
      SystemChannels.platform,
      (call) async {
        if (call.method == 'Clipboard.setData') {
          final args = call.arguments;
          if (args is Map) {
            clipboardText = args['text'] as String?;
          }
        }
        return null;
      },
    );
    addTearDown(
      () => tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
        SystemChannels.platform,
        null,
      ),
    );

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();
    await _enterPlayerName(tester);
    await _enterGameServerPort(tester, '27015');
    await tester.tap(
      find.widgetWithText(FilledButton, Localization().get('create_room')),
    );
    await tester.pumpAndSettle();

    expect(find.text('192.168.1.100:27015'), findsAtLeast(1));

    final targetCopyButtons = find.byIcon(Icons.copy);
    await tester.tap(targetCopyButtons.at(2));
    await tester.pump();
    expect(clipboardText, '192.168.1.100:27015');
  });

  testWidgets('Game server address is highlighted with image asset icon', (
    tester,
  ) async {
    final client = _AdapterStatusMockClient(
      const AdapterStatus(
        enabled: true,
        status: 'ready',
        adapterType: 'local_udp_bridge',
        bindHost: '127.0.0.1',
        bindPort: 50001,
        targetHost: '127.0.0.1',
        targetPort: 27015,
        counters: AdapterCounters(
          packetsFromGame: 0,
          packetsToTransport: 0,
          packetsFromTransport: 0,
          packetsToGame: 0,
        ),
      ),
    );
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();
    await _enterPlayerName(tester);
    await _enterGameServerPort(tester, '27015');
    await tester.tap(
      find.widgetWithText(FilledButton, Localization().get('create_room')),
    );
    await tester.pumpAndSettle();

    expect(find.text('Game server address'), findsAtLeast(1));
    expect(find.text('127.0.0.1:27015'), findsAtLeast(1));
    final helper = find.textContaining(
      'The game server address should point to the host local game server port',
    );
    expect(helper, findsAtLeast(1));
  });

  testWidgets('Room Connection shows Chinese relay warning helper', (
    tester,
  ) async {
    Localization().setLanguage(Language.zh);
    await _expectAdapterDisplay(
      tester,
      const AdapterStatus(
        enabled: true,
        status: 'ready',
        adapterType: 'local_udp_bridge',
        bindHost: '127.0.0.1',
        bindPort: 40100,
        targetHost: '127.0.0.1',
        targetPort: 40200,
        counters: AdapterCounters(
          packetsFromGame: 0,
          packetsToTransport: 0,
          packetsFromTransport: 0,
          packetsToGame: 0,
        ),
      ),
      [
        'VPS/Relay 地址只供 CoopWing 使用，不应直接填进游戏 connect 命令。',
        '游戏客户端应连接本机连接地址，例如 connect 127.0.0.1:40100',
        '未检测到游戏流量。请确认游戏连接的是本机连接地址。',
      ],
    );
  });
}

Widget _bridgeTestApp(MockBackendClient client) {
  return ListenableBuilder(
    listenable: Localization(),
    builder: (context, _) {
      return MaterialApp(
        home: Scaffold(
          body: SingleChildScrollView(
            child: BackendBridgePanel(
              client: client,
              onRunDiagnostics: _testReport,
            ),
          ),
        ),
      );
    },
  );
}

Future<void> _enterPlayerName(
  WidgetTester tester, [
  String name = 'PlayerA',
]) async {
  await tester.enterText(
    find.widgetWithText(TextField, Localization().get('player_name')),
    name,
  );
  await tester.pump();
}

Future<void> _enterGameServerPort(
  WidgetTester tester, [
  String port = '27015',
]) async {
  await tester.enterText(
    find.widgetWithText(TextField, Localization().get('game_server_port')),
    port,
  );
  await tester.pump();
}

class _CapturingMockClient extends MockBackendClient {
  String? lastCreateServerHost;
  int? lastCreateServerPort;
  int? lastCreateServerUdpPort;
  AdapterConfig? lastCreateAdapterConfig;
  String? lastJoinServerHost;
  int? lastJoinServerPort;
  int? lastJoinServerUdpPort;
  AdapterConfig? lastJoinAdapterConfig;

  @override
  Future<SessionInfo> createSession({
    required String serverHost,
    required int serverPort,
    required int serverUdpPort,
    required String playerName,
    required String bindHost,
    required int bindPort,
    AdapterConfig? adapterConfig,
  }) {
    lastCreateServerHost = serverHost;
    lastCreateServerPort = serverPort;
    lastCreateServerUdpPort = serverUdpPort;
    lastCreateAdapterConfig = adapterConfig;
    return super.createSession(
      serverHost: serverHost,
      serverPort: serverPort,
      serverUdpPort: serverUdpPort,
      playerName: playerName,
      bindHost: bindHost,
      bindPort: bindPort,
      adapterConfig: adapterConfig,
    );
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
    AdapterConfig? adapterConfig,
  }) {
    lastJoinServerHost = serverHost;
    lastJoinServerPort = serverPort;
    lastJoinServerUdpPort = serverUdpPort;
    lastJoinAdapterConfig = adapterConfig;
    return super.joinSession(
      serverHost: serverHost,
      serverPort: serverPort,
      serverUdpPort: serverUdpPort,
      roomId: roomId,
      playerName: playerName,
      gameServerHost: gameServerHost,
      gameServerPort: gameServerPort,
      adapterConfig: adapterConfig,
    );
  }
}

class _SwitchableHealthMockClient extends MockBackendClient {
  bool online = true;

  @override
  Future<HealthStatus> health() async {
    if (!online) {
      return HealthStatus.offline();
    }
    return super.health();
  }
}

class _StaleCreateRoomMockClient extends MockBackendClient {
  int _logsCalls = 0;
  final num _now = DateTime.now().millisecondsSinceEpoch / 1000;

  @override
  Future<SessionInfo> createSession({
    required String serverHost,
    required int serverPort,
    required int serverUdpPort,
    required String playerName,
    required String bindHost,
    required int bindPort,
    AdapterConfig? adapterConfig,
  }) async {
    return _session(
      status: 'starting',
      roomId: 'STALE1',
      serverHost: serverHost,
      serverPort: serverPort,
      serverUdpPort: serverUdpPort,
      playerName: playerName,
    );
  }

  @override
  Future<SessionInfo> getSessionStatus(String sessionId) async {
    return _session(
      status: 'room_created',
      roomId: 'STALE1',
      playerName: 'PlayerA',
    );
  }

  @override
  Future<List<SessionEvent>> getSessionLogs(String sessionId) async {
    _logsCalls += 1;
    final events = <SessionEvent>[
      SessionEvent(
        type: 'session_created',
        message: 'Session created.',
        timestamp: _now,
        data: {'session_id': sessionId},
      ),
    ];
    if (_logsCalls > 1) {
      events.addAll([
        SessionEvent(
          type: 'room_created',
          message: 'Room REAL42 created.',
          timestamp: _now + 1,
          data: {'room_id': 'REAL42'},
        ),
        SessionEvent(
          type: 'relay_ready',
          message: 'Relay path ready.',
          timestamp: _now + 2,
          data: {'room_id': 'REAL42'},
        ),
        SessionEvent(
          type: 'session_running',
          message: 'Session running.',
          timestamp: _now + 3,
          data: {'session_id': sessionId},
        ),
      ]);
    }
    return events;
  }

  @override
  Future<SessionInfo> stopSession(String sessionId) async {
    return _session(status: 'stopped', roomId: 'STALE1', playerName: 'PlayerA');
  }

  SessionInfo _session({
    required String status,
    required String? roomId,
    required String playerName,
    String serverHost = MockBackendClient.defaultRelayHost,
    int serverPort = 9000,
    int serverUdpPort = 9001,
  }) {
    return SessionInfo(
      sessionId: 's_stale_create',
      role: 'create',
      status: status,
      roomId: roomId,
      playerName: playerName,
      serverHost: serverHost,
      serverPort: serverPort,
      serverUdpPort: serverUdpPort,
      adapterHost: '127.0.0.1',
      adapterPort: 40000,
      gameServerHost: '127.0.0.1',
      gameServerPort: 0,
      createdAt: _now,
      updatedAt: _now,
      stats: SessionStats.empty(),
    );
  }
}

Future<DoctorReport> _testReport() async {
  return DoctorReport(
    filename: 'test',
    createdAt: DateTime.now(),
    sizeBytes: null,
    reportType: ReportType.directory,
    summaryPath: 'summary.json',
    zipPath: null,
    summary: 'test',
    systemInfo: const [],
    networkInterfaces: const [],
    serverConnectivity: const [],
    natReachability: const [],
    recommendations: const [],
  );
}

Future<void> _expectAdapterDisplay(
  WidgetTester tester,
  AdapterStatus? adapterStatus,
  List<String> expectedTexts,
) async {
  final client = _AdapterStatusMockClient(adapterStatus);
  addTearDown(client.dispose);

  await tester.pumpWidget(const SizedBox.shrink());
  await tester.pumpAndSettle();
  await tester.pumpWidget(_bridgeTestApp(client));
  await tester.pumpAndSettle();
  await _enterPlayerName(tester);
  await _enterGameServerPort(tester, '27015');
  await tester.tap(
    find.widgetWithText(FilledButton, Localization().get('create_room')),
  );
  await tester.pumpAndSettle();

  for (final text in expectedTexts) {
    expect(find.text(text), findsAtLeastNWidgets(1));
  }
}

class _AdapterStatusMockClient extends MockBackendClient {
  _AdapterStatusMockClient(this.adapterStatus);

  final AdapterStatus? adapterStatus;
  final num _now = DateTime.now().millisecondsSinceEpoch / 1000;

  @override
  Future<SessionInfo> createSession({
    required String serverHost,
    required int serverPort,
    required int serverUdpPort,
    required String playerName,
    required String bindHost,
    required int bindPort,
    AdapterConfig? adapterConfig,
  }) async {
    return _session(
      serverHost: serverHost,
      serverPort: serverPort,
      serverUdpPort: serverUdpPort,
      playerName: playerName,
    );
  }

  @override
  Future<List<SessionEvent>> getSessionLogs(String sessionId) async {
    return [
      SessionEvent(
        type: 'room_created',
        message: 'Room ADAPT1 created.',
        timestamp: _now,
        data: {'room_id': 'ADAPT1'},
      ),
      SessionEvent(
        type: 'session_running',
        message: 'Session running.',
        timestamp: _now + 1,
        data: {'session_id': sessionId},
      ),
    ];
  }

  @override
  Future<SessionInfo> getSessionStatus(String sessionId) async {
    return _session(playerName: 'PlayerA');
  }

  SessionInfo _session({
    required String playerName,
    String serverHost = MockBackendClient.defaultRelayHost,
    int serverPort = 9000,
    int serverUdpPort = 9001,
  }) {
    return SessionInfo(
      sessionId: 's_adapter_status',
      role: 'create',
      status: 'running',
      roomId: 'ADAPT1',
      playerName: playerName,
      serverHost: serverHost,
      serverPort: serverPort,
      serverUdpPort: serverUdpPort,
      adapterHost: '127.0.0.1',
      adapterPort: 0,
      gameServerHost: '127.0.0.1',
      gameServerPort: 0,
      createdAt: _now,
      updatedAt: _now,
      stats: SessionStats.empty(),
      adapterStatus: adapterStatus,
    );
  }
}
