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

    expect(find.text('Session details'), findsOneWidget);
    expect(find.text('session_id'), findsNothing);

    await _expandSessionDetails(tester);
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

    expect(find.text('stopped'), findsNothing);
    expect(find.text('Current Session Summary'), findsOneWidget);
    await _expandSessionDetails(tester);
    expect(find.text('stopped'), findsOneWidget);
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
    final createFields = find.byKey(const Key('create-basic-fields'));
    expect(createFields, findsOneWidget);
    expect(
      find.descendant(
        of: createFields,
        matching: find.widgetWithText(TextField, 'Player name'),
      ),
      findsOneWidget,
    );
    expect(
      find.descendant(
        of: createFields,
        matching: find.widgetWithText(TextField, 'Game / Bundle port'),
      ),
      findsOneWidget,
    );

    await _enterPlayerName(tester);
    final partialCreateButton = tester.widget<FilledButton>(
      find.widgetWithText(FilledButton, 'Create Room'),
    );
    expect(partialCreateButton.onPressed, isNull);
    expect(find.widgetWithText(TextField, 'Game server port'), findsNothing);
    expect(find.widgetWithText(TextField, 'Game bind port'), findsNothing);
    expect(
      find.widgetWithText(TextField, 'Game / Bundle port'),
      findsOneWidget,
    );

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
    final partialJoinButton = tester.widget<FilledButton>(
      find.widgetWithText(FilledButton, 'Join Room'),
    );
    expect(partialJoinButton.onPressed, isNull);
    expect(find.widgetWithText(TextField, 'Game server port'), findsNothing);
    expect(find.widgetWithText(TextField, 'Game bind port'), findsNothing);
    expect(find.widgetWithText(TextField, 'Game / Bundle port'), findsNothing);

    await tester.enterText(find.widgetWithText(TextField, 'Room ID'), 'MOCK42');
    await tester.pump();
    final enabledJoinButton = tester.widget<FilledButton>(
      find.widgetWithText(FilledButton, 'Join Room'),
    );
    expect(enabledJoinButton.onPressed, isNotNull);
  });

  testWidgets('Join room id does not update game or Bundle port fields', (
    tester,
  ) async {
    final client = _CapturingMockClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();

    await _enterPlayerName(tester);
    await _enterGameServerPort(tester, '27015');
    await tester.tap(find.text('Join Room'));
    await tester.pumpAndSettle();
    await tester.enterText(find.widgetWithText(TextField, 'Room ID'), 'RN4Y78');
    await tester.pump();

    await _switchToAdvancedTab(tester);
    await tester.ensureVisible(find.text('Advanced Backend Settings'));
    await tester.tap(find.text('Advanced Backend Settings'));
    await tester.pumpAndSettle();
    final advancedPortField = tester.widget<TextField>(
      find.widgetWithText(TextField, 'Game bind port'),
    );
    expect(advancedPortField.controller?.text, '27015');
    expect(advancedPortField.controller?.text, isNot('RN4Y78'));

    await _switchToRoomTab(tester);
    await tester.tap(find.text('Create Room'));
    await tester.pumpAndSettle();
    final createPortField = tester.widget<TextField>(
      find.widgetWithText(TextField, 'Game / Bundle port'),
    );
    expect(createPortField.controller?.text, '27015');
    expect(createPortField.controller?.text, isNot('RN4Y78'));
  });

  testWidgets('Numeric Create port does not update Join room id', (
    tester,
  ) async {
    final client = MockBackendClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();

    await _enterPlayerName(tester);
    await _enterGameServerPort(tester, '27015');
    await tester.tap(find.text('Join Room'));
    await tester.pumpAndSettle();

    final roomIdField = tester.widget<TextField>(
      find.widgetWithText(TextField, 'Room ID'),
    );
    expect(roomIdField.controller?.text, isEmpty);
    expect(roomIdField.controller?.text, isNot('27015'));
  });

  testWidgets('Join accepts alphanumeric room id without game port', (
    tester,
  ) async {
    final client = _CapturingMockClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();

    await _enterPlayerName(tester);
    await tester.tap(find.text('Join Room'));
    await tester.pumpAndSettle();
    await tester.enterText(find.widgetWithText(TextField, 'Room ID'), 'RN4Y78');
    await tester.pump();

    final joinButton = tester.widget<FilledButton>(
      find.widgetWithText(FilledButton, 'Join Room'),
    );
    expect(joinButton.onPressed, isNotNull);
    await tester.tap(find.widgetWithText(FilledButton, 'Join Room'));
    await tester.pumpAndSettle();

    expect(client.lastJoinGameServerPort, isNull);
    expect(client.lastJoinAdapterConfig?.adapterType, 'bundle');
    expect(client.lastJoinAdapterConfig?.targetPort, 0);
    expect(client.lastJoinServerHost, BackendBridgePanel.localPreviewRelayHost);
  });

  testWidgets('Create still requires a numeric game or Bundle port', (
    tester,
  ) async {
    final client = MockBackendClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();

    await _enterPlayerName(tester);
    var createButton = tester.widget<FilledButton>(
      find.widgetWithText(FilledButton, 'Create Room'),
    );
    expect(createButton.onPressed, isNull);

    await tester.enterText(
      find.widgetWithText(TextField, 'Game / Bundle port'),
      'RN4Y78',
    );
    await tester.pump();
    createButton = tester.widget<FilledButton>(
      find.widgetWithText(FilledButton, 'Create Room'),
    );
    expect(createButton.onPressed, isNull);
  });

  testWidgets('PID candidate apply updates port without changing room id', (
    tester,
  ) async {
    final client = _CapturingMockClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();

    await _enterPlayerName(tester);
    await tester.tap(find.text('Join Room'));
    await tester.pumpAndSettle();
    await tester.enterText(find.widgetWithText(TextField, 'Room ID'), 'RN4Y78');
    await tester.pump();

    await _switchToAdvancedTab(tester);
    await tester.enterText(
      find.widgetWithText(TextField, 'Process PID'),
      '4321',
    );
    await tester.tap(find.widgetWithText(FilledButton, 'Scan ports'));
    await tester.pumpAndSettle();
    await tester.tap(
      find.widgetWithText(OutlinedButton, 'Apply selected port'),
    );
    await tester.pumpAndSettle();

    await tester.ensureVisible(find.text('Advanced Backend Settings'));
    await tester.tap(find.text('Advanced Backend Settings'));
    await tester.pumpAndSettle();
    final advancedPortField = tester.widget<TextField>(
      find.widgetWithText(TextField, 'Game bind port'),
    );
    expect(advancedPortField.controller?.text, '27015');

    await _switchToRoomTab(tester);
    final roomIdField = tester.widget<TextField>(
      find.widgetWithText(TextField, 'Room ID'),
    );
    expect(roomIdField.controller?.text, 'RN4Y78');
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

  testWidgets('Running v2 session keeps participants visible and details collapsed', (
    tester,
  ) async {
    final client = MockBackendClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();

    await _enterPlayerName(tester);
    await _enterGameServerPort(tester, '27015');
    await tester.tap(find.widgetWithText(FilledButton, 'Create Room'));
    await tester.pumpAndSettle();

    expect(find.text('Local game connection'), findsOneWidget);
    expect(find.text('Players'), findsOneWidget);
    expect(find.text('3 / 4'), findsOneWidget);
    expect(find.text('Participants'), findsOneWidget);
    expect(find.text('Alice'), findsOneWidget);
    expect(find.text('Bob'), findsOneWidget);
    expect(find.text('Carol'), findsOneWidget);
    expect(find.text('Host'), findsOneWidget);
    expect(find.text('You'), findsOneWidget);
    expect(find.text('Session details'), findsOneWidget);
    expect(find.text('Protocol'), findsNothing);
    expect(find.text('v2 relay-only'), findsNothing);
    expect(find.text('Host player ID'), findsNothing);
    expect(find.text('p_mock_alice'), findsNothing);
    expect(find.text('Room ready'), findsNothing);
    expect(find.text('Relay ready'), findsNothing);
    expect(find.text('Last room event'), findsNothing);
    expect(find.text('Peer endpoint'), findsNothing);
    expect(find.text('Relay target'), findsNothing);

    await _expandSessionDetails(tester);

    expect(find.text('Protocol'), findsOneWidget);
    expect(find.text('v2 relay-only'), findsOneWidget);
    expect(find.text('Max players'), findsOneWidget);
    expect(find.text('Participant count'), findsOneWidget);
    expect(find.text('Host player ID'), findsOneWidget);
    expect(find.text('p_mock_alice'), findsOneWidget);
    expect(find.text('Room ready'), findsOneWidget);
    expect(find.text('Relay ready'), findsOneWidget);
    expect(find.text('Last room event'), findsOneWidget);
    expect(find.text('room_ready'), findsOneWidget);
    expect(find.text('Peer endpoint'), findsOneWidget);
    expect(find.text('198.51.100.44:42001'), findsOneWidget);
    expect(find.text('Relay target'), findsOneWidget);
    expect(find.text('120.27.210.184:9001'), findsOneWidget);
    expect(find.textContaining('relay_token'), findsNothing);

    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pump(const Duration(seconds: 1));
  });

  testWidgets('Stopped v2 session displays closed room state', (tester) async {
    final client = MockBackendClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();

    await _enterPlayerName(tester);
    await _enterGameServerPort(tester, '27015');
    await tester.tap(find.widgetWithText(FilledButton, 'Create Room'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Stop Session'));
    await tester.tap(find.text('Stop Session'));
    await tester.pumpAndSettle();

    expect(find.text('Current Session Summary'), findsOneWidget);
    expect(find.text('Session details'), findsOneWidget);
    expect(find.text('Room closed'), findsNothing);

    await _expandSessionDetails(tester);

    expect(find.text('Room closed'), findsOneWidget);
    expect(find.text('Yes'), findsAtLeastNWidgets(1));
    expect(find.textContaining('relay_token'), findsNothing);
  });

  testWidgets('v1 session without participants does not render empty noise', (
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
    expect(find.text('Participants'), findsNothing);
    expect(find.text('No participant list yet'), findsNothing);
    expect(find.textContaining('relay_token'), findsNothing);
  });

  testWidgets('Missing peer endpoint shows unavailable without using relay', (
    tester,
  ) async {
    final client = _AdapterStatusMockClient(null, v2RelayWithoutPeer: true);
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();

    await _enterPlayerName(tester);
    await _enterGameServerPort(tester, '27015');
    await tester.tap(find.widgetWithText(FilledButton, 'Create Room'));
    await tester.pumpAndSettle();

    expect(find.text('Session details'), findsOneWidget);
    expect(find.text('Peer endpoint'), findsNothing);

    await _expandSessionDetails(tester);

    expect(find.text('Peer endpoint'), findsOneWidget);
    expect(find.text('Not available yet'), findsOneWidget);
    expect(find.text('Relay target'), findsOneWidget);
    expect(find.text('120.27.210.184:9001'), findsOneWidget);
  });

  testWidgets('Room Connection shows relay inactivity note', (tester) async {
    final client = MockBackendClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();

    await _switchToAdvancedTab(tester);
    expect(
      find.textContaining(
        'Rooms disconnect automatically after 30 minutes without relay traffic.',
      ),
      findsOneWidget,
    );

    await _switchToRoomTab(tester);
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

  testWidgets('LAN Discovery section renders initial stopped state', (
    tester,
  ) async {
    final client = MockBackendClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();
    await _showLanDiscoverySection(tester);

    expect(find.text('LAN Discovery / 局域网发现'), findsOneWidget);
    expect(find.text('Stopped'), findsAtLeastNWidgets(1));
    expect(find.text('No peers found'), findsOneWidget);
    expect(find.text('Service port'), findsOneWidget);
    expect(find.text('Broadcast port'), findsOneWidget);
    expect(find.text('Peer count'), findsOneWidget);
  });

  testWidgets('LAN Discovery Start updates status and local peer id', (
    tester,
  ) async {
    final client = MockBackendClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();
    await _showLanDiscoverySection(tester);

    await tester.tap(find.widgetWithText(FilledButton, 'Start discovery'));
    await tester.pump();
    expect(find.text('Starting LAN Discovery...'), findsOneWidget);
    await tester.pumpAndSettle();

    expect(find.text('Running'), findsAtLeastNWidgets(1));
    expect(find.text('peer_mock_local'), findsOneWidget);
    expect(find.text('Mock Nearby Co-opWinG'), findsOneWidget);
  });

  testWidgets('LAN Discovery Stop returns to stopped and clears peers', (
    tester,
  ) async {
    final client = MockBackendClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();
    await _showLanDiscoverySection(tester);

    await tester.tap(find.widgetWithText(FilledButton, 'Start discovery'));
    await tester.pumpAndSettle();
    expect(find.text('Mock Nearby Co-opWinG'), findsOneWidget);

    await tester.tap(find.widgetWithText(OutlinedButton, 'Stop discovery'));
    await tester.pump();
    expect(find.text('Stopping LAN Discovery...'), findsOneWidget);
    await tester.pumpAndSettle();

    expect(find.text('Stopped'), findsAtLeastNWidgets(1));
    expect(find.text('No peers found'), findsOneWidget);
    expect(find.text('Mock Nearby Co-opWinG'), findsNothing);
  });

  testWidgets('LAN Discovery Refresh peers displays passive peer details', (
    tester,
  ) async {
    final client = MockBackendClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();
    await _showLanDiscoverySection(tester);

    await tester.tap(find.widgetWithText(FilledButton, 'Start discovery'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(OutlinedButton, 'Refresh peers'));
    await tester.pumpAndSettle();

    expect(find.text('Mock Nearby Co-opWinG'), findsOneWidget);
    expect(find.text('192.168.1.23:21520'), findsOneWidget);
    expect(find.textContaining('Version: 0.4.0-preview'), findsOneWidget);
    expect(find.textContaining('Last seen: 1.1s ago'), findsOneWidget);
    expect(find.textContaining('raw last_seen'), findsNothing);
  });

  testWidgets('LAN Discovery UI does not imply room join or protocol actions', (
    tester,
  ) async {
    final client = MockBackendClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();
    await _showLanDiscoverySection(tester);

    expect(find.textContaining('Join room from peer'), findsNothing);
    expect(find.textContaining('Auto join'), findsNothing);
    expect(find.textContaining('Connect peer'), findsNothing);
    expect(
      find.textContaining('Co-opWinG instances, not rooms'),
      findsOneWidget,
    );
    expect(find.textContaining('CREATE_ROOM'), findsNothing);
    expect(find.textContaining('JOIN_ROOM'), findsNothing);
    expect(find.textContaining('RELAY_ENABLED'), findsNothing);
  });

  testWidgets('LAN Discovery remains refreshable after session created', (
    tester,
  ) async {
    final client = MockBackendClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();
    await _showLanDiscoverySection(tester);
    await tester.tap(find.widgetWithText(FilledButton, 'Start discovery'));
    await tester.pumpAndSettle();

    await _switchToRoomTab(tester);
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

    await _showLanDiscoverySection(tester);
    await tester.tap(find.widgetWithText(OutlinedButton, 'Refresh peers'));
    await tester.pumpAndSettle();

    expect(find.text('Mock Nearby Co-opWinG'), findsOneWidget);

    // Drain any pending mock-delay timers triggered by tab switching.
    await tester.pump(const Duration(seconds: 1));
    await tester.pumpAndSettle();
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

    await _switchToAdvancedTab(tester);
    expect(find.text('高级后端设置'), findsOneWidget);
    expect(find.textContaining('30 分钟无 relay 流量会自动断开'), findsOneWidget);

    await _switchToRoomTab(tester);
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

  testWidgets('Refresh health updates reachable backend to online real_core', (
    tester,
  ) async {
    final client = _SwitchableHealthMockClient()..online = false;
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();

    expect(find.text('offline'), findsOneWidget);

    client.online = true;
    await tester.tap(find.byIcon(Icons.refresh));
    await tester.pumpAndSettle();

    expect(find.text('online real_core'), findsOneWidget);
    expect(find.textContaining('Backend offline'), findsNothing);
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
    await tester.pump();
    await _enterGameServerPort(tester, '27015');
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

  testWidgets('Advanced settings show release relay defaults', (tester) async {
    final client = MockBackendClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();

    await _enterPlayerName(tester);
    await tester.tap(find.text('Join Room'));
    await tester.pumpAndSettle();
    await tester.enterText(find.widgetWithText(TextField, 'Room ID'), 'MOCK42');
    await tester.pump();

    await _switchToAdvancedTab(tester);

    await tester.ensureVisible(find.text('Advanced Backend Settings'));
    await tester.tap(find.text('Advanced Backend Settings'));
    await tester.pumpAndSettle();

    expect(find.widgetWithText(TextField, 'Backend HTTP host'), findsOneWidget);
    expect(find.widgetWithText(TextField, 'Backend HTTP port'), findsOneWidget);
    expect(find.widgetWithText(TextField, 'Relay/root host'), findsOneWidget);
    expect(find.widgetWithText(TextField, 'Relay TCP port'), findsOneWidget);
    expect(find.widgetWithText(TextField, 'Relay UDP port'), findsOneWidget);
    expect(find.text('120.27.210.184'), findsOneWidget);
    expect(find.text('Default 120.27.210.184'), findsOneWidget);
    expect(find.text('127.0.0.1'), findsWidgets);
    expect(find.text('9000'), findsOneWidget);
    expect(find.text('9001'), findsOneWidget);
    expect(find.text('21520'), findsAtLeastNWidgets(1));
    expect(find.text('UDP + TCP Bundle'), findsOneWidget);
    expect(
      find.widgetWithText(TextField, 'Secondary IP address'),
      findsOneWidget,
    );
    expect(find.widgetWithText(TextField, 'Target interface'), findsOneWidget);
    expect(find.widgetWithText(TextField, 'Prefix length'), findsOneWidget);
  });

  testWidgets('Advanced tab scans PID ports and renders candidates', (
    tester,
  ) async {
    final client = _CapturingMockClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();
    await _switchToAdvancedTab(tester);

    expect(find.text('PID Port Detection'), findsOneWidget);
    expect(find.widgetWithText(TextField, 'Process PID'), findsOneWidget);
    await tester.enterText(
      find.widgetWithText(TextField, 'Process PID'),
      '4321',
    );
    await tester.tap(find.widgetWithText(FilledButton, 'Scan ports'));
    await tester.pumpAndSettle();

    expect(client.lastScannedPid, 4321);
    expect(find.text('TCP 0.0.0.0:27015'), findsOneWidget);
    expect(find.text('UDP 0.0.0.0:27016'), findsOneWidget);
    expect(find.text('Listen / high'), findsOneWidget);
    expect(find.text('TCP LISTEN on 0.0.0.0:27015'), findsOneWidget);
  });

  testWidgets('Applying PID candidate updates Bundle shared port', (
    tester,
  ) async {
    final client = _CapturingMockClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();
    await _switchToAdvancedTab(tester);
    await tester.enterText(
      find.widgetWithText(TextField, 'Process PID'),
      '4321',
    );
    await tester.tap(find.widgetWithText(FilledButton, 'Scan ports'));
    await tester.pumpAndSettle();
    await tester.tap(
      find.widgetWithText(OutlinedButton, 'Apply selected port'),
    );
    await tester.pumpAndSettle();

    expect(
      find.text('Applied the selected port as the shared Bundle TCP/UDP port.'),
      findsOneWidget,
    );
    await _switchToRoomTab(tester);
    final basicPortField = tester.widget<TextField>(
      find.widgetWithText(TextField, 'Game / Bundle port'),
    );
    expect(basicPortField.controller?.text, '27015');
    await _switchToAdvancedTab(tester);
    await tester.ensureVisible(find.text('Advanced Backend Settings'));
    await tester.tap(find.text('Advanced Backend Settings'));
    await tester.pumpAndSettle();
    final portField = tester.widget<TextField>(
      find.widgetWithText(TextField, 'Game bind port'),
    );
    expect(portField.controller?.text, '27015');
  });

  testWidgets('UDP Only applies UDP PID candidate to target port', (
    tester,
  ) async {
    final client = _CapturingMockClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();
    await _switchToAdvancedTab(tester);
    await tester.ensureVisible(find.text('Advanced Backend Settings'));
    await tester.tap(find.text('Advanced Backend Settings'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('UDP + TCP Bundle'));
    await tester.tap(find.text('UDP + TCP Bundle').last);
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('UDP Only').last);
    await tester.tap(find.text('UDP Only').last);
    await tester.pumpAndSettle();

    await tester.ensureVisible(find.widgetWithText(TextField, 'Process PID'));
    await tester.enterText(
      find.widgetWithText(TextField, 'Process PID'),
      '4321',
    );
    tester
        .widget<FilledButton>(find.widgetWithText(FilledButton, 'Scan ports'))
        .onPressed
        ?.call();
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('UDP 0.0.0.0:27016'));
    await tester.tap(find.text('UDP 0.0.0.0:27016'));
    await tester.pump();
    tester
        .widget<OutlinedButton>(
          find.widgetWithText(OutlinedButton, 'Apply selected port'),
        )
        .onPressed
        ?.call();
    await tester.pumpAndSettle();

    final portField = tester.widget<TextField>(
      find.widgetWithText(TextField, 'Game bind port'),
    );
    expect(portField.controller?.text, '27016');
    expect(
      find.text(
        'Applied the selected port as the UDP local bridge target port.',
      ),
      findsOneWidget,
    );
  });

  testWidgets('TCP Only applies TCP PID candidate to target port', (
    tester,
  ) async {
    final client = _CapturingMockClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();
    await _switchToAdvancedTab(tester);
    await tester.ensureVisible(find.text('Advanced Backend Settings'));
    await tester.tap(find.text('Advanced Backend Settings'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('UDP + TCP Bundle'));
    await tester.tap(find.text('UDP + TCP Bundle').last);
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('TCP Only').last);
    await tester.tap(find.text('TCP Only').last);
    await tester.pumpAndSettle();

    await tester.ensureVisible(find.widgetWithText(TextField, 'Process PID'));
    await tester.enterText(
      find.widgetWithText(TextField, 'Process PID'),
      '4321',
    );
    tester
        .widget<FilledButton>(find.widgetWithText(FilledButton, 'Scan ports'))
        .onPressed
        ?.call();
    await tester.pumpAndSettle();
    tester
        .widget<OutlinedButton>(
          find.widgetWithText(OutlinedButton, 'Apply selected port'),
        )
        .onPressed
        ?.call();
    await tester.pumpAndSettle();

    final portField = tester.widget<TextField>(
      find.widgetWithText(TextField, 'Game bind port'),
    );
    expect(portField.controller?.text, '27015');
    expect(
      find.text('Applied the selected port as the TCP forward target port.'),
      findsOneWidget,
    );
  });

  testWidgets('Create request uses relay and Bundle adapter defaults', (
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
    expect(client.lastCreateGameServerPort, 27015);
    expect(client.lastCreateForceRelay, isTrue);
    final config = client.lastCreateAdapterConfig!;
    expect(config.enabled, isTrue);
    expect(config.adapterType, 'bundle');
    expect(config.bindHost, '127.0.0.1');
    expect(config.bindPort, 0);
    expect(config.targetHost, '127.0.0.1');
    expect(config.targetPort, 27015);
    expect(config.secondaryIpRequest, isNull);
  });

  testWidgets('Secondary IP card shows admin state and recommendation', (
    tester,
  ) async {
    final client = MockBackendClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();
    await _switchToAdvancedTab(tester);

    expect(find.text('Secondary IP'), findsWidgets);
    expect(find.text('Backend admin/elevated'), findsAtLeastNWidgets(1));
    expect(find.text('Current default IPv4 interface'), findsOneWidget);
    expect(find.text('Recommended address'), findsOneWidget);
    expect(find.text('Ethernet (ifIndex 18) 192.168.5.42/24'), findsOneWidget);
    expect(find.text('192.168.5.233'), findsOneWidget);
    expect(find.text('Enable Secondary IP'), findsOneWidget);
    expect(find.text('Auto-select interface'), findsOneWidget);
    expect(
      find.textContaining('temporarily changes this computer network adapter'),
      findsOneWidget,
    );
  });

  testWidgets('Auto-select fills recommended Secondary IP but does not send', (
    tester,
  ) async {
    final client = _CapturingMockClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();
    await _switchToAdvancedTab(tester);

    await tester.ensureVisible(find.text('Auto-select interface'));
    await tester.tap(find.text('Auto-select interface'));
    await tester.pumpAndSettle();

    await tester.ensureVisible(find.text('Advanced Backend Settings'));
    await tester.tap(find.text('Advanced Backend Settings'));
    await tester.pumpAndSettle();

    final secondaryIpField = tester.widget<TextField>(
      find.widgetWithText(TextField, 'Secondary IP address'),
    );
    final interfaceField = tester.widget<TextField>(
      find.widgetWithText(TextField, 'Target interface'),
    );
    expect(secondaryIpField.controller?.text, '192.168.5.233');
    expect(interfaceField.controller?.text, '18');

    await _switchToRoomTab(tester);
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

    expect(client.lastCreateAdapterConfig!.secondaryIpRequest, isNull);
  });

  testWidgets('Create request sends Secondary IP request separately', (
    tester,
  ) async {
    final client = _CapturingMockClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();
    await _switchToAdvancedTab(tester);

    await tester.ensureVisible(find.text('Advanced Backend Settings'));
    await tester.tap(find.text('Advanced Backend Settings'));
    await tester.pumpAndSettle();
    await tester.enterText(
      find.widgetWithText(TextField, 'Secondary IP address'),
      '192.168.5.233',
    );
    await tester.enterText(
      find.widgetWithText(TextField, 'Target interface'),
      '18',
    );
    await tester.enterText(
      find.widgetWithText(TextField, 'Prefix length'),
      '24',
    );
    await tester.pump();

    await tester.ensureVisible(find.text('Enable Secondary IP'));
    await tester.tap(find.text('Enable Secondary IP'));
    await tester.pumpAndSettle();

    await _switchToRoomTab(tester);
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

    final request = client.lastCreateAdapterConfig!.secondaryIpRequest!;
    expect(request.ipAddress, '192.168.5.233');
    expect(request.interfaceHint, '18');
    expect(request.prefixLength, 24);
  });

  testWidgets(
    'Create request maps TCP Only to the existing TCP forward adapter',
    (tester) async {
      final client = _CapturingMockClient();
      addTearDown(client.dispose);

      await tester.pumpWidget(_bridgeTestApp(client));
      await tester.pumpAndSettle();
      await _switchToAdvancedTab(tester);

      await tester.ensureVisible(find.text('Advanced Backend Settings'));
      await tester.tap(find.text('Advanced Backend Settings'));
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.text('UDP + TCP Bundle'));
      await tester.tap(find.text('UDP + TCP Bundle').last);
      await tester.pumpAndSettle();
      await tester.tap(find.text('TCP Only').last);
      await tester.pumpAndSettle();

      await _switchToRoomTab(tester);
      await _enterPlayerName(tester);
      await _enterGameServerPort(tester, '25565');
      await tester.ensureVisible(
        find.widgetWithText(FilledButton, 'Create Room'),
      );
      tester
          .widget<FilledButton>(
            find.widgetWithText(FilledButton, 'Create Room'),
          )
          .onPressed
          ?.call();
      await tester.pumpAndSettle();

      final config = client.lastCreateAdapterConfig!;
      expect(config.enabled, isTrue);
      expect(config.adapterType, 'tcp_forward');
      expect(config.bindHost, '127.0.0.1');
      expect(config.bindPort, 0);
      expect(config.targetHost, '127.0.0.1');
      expect(config.targetPort, 25565);
      expect(config.targetPort, isNot(25566));
      await _switchToAdvancedTab(tester);
      await tester.ensureVisible(find.text('Advanced Backend Settings'));
      await tester.tap(find.text('Advanced Backend Settings'));
      await tester.pumpAndSettle();
      expect(
        find.textContaining('configured game server host and port'),
        findsOneWidget,
      );
    },
  );

  testWidgets('Adapter selector shows only simplified user-facing modes', (
    tester,
  ) async {
    final client = MockBackendClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();
    await _switchToAdvancedTab(tester);

    await tester.ensureVisible(find.text('Advanced Backend Settings'));
    await tester.tap(find.text('Advanced Backend Settings'));
    await tester.pumpAndSettle();
    expect(find.text('UDP + TCP Bundle'), findsOneWidget);
    await tester.ensureVisible(find.text('UDP + TCP Bundle'));
    await tester.tap(find.text('UDP + TCP Bundle').last);
    await tester.pumpAndSettle();

    expect(find.text('UDP + TCP Bundle'), findsWidgets);
    expect(find.text('UDP Only'), findsWidgets);
    expect(find.text('TCP Only'), findsOneWidget);
    expect(find.text('Coming soon'), findsNothing);
    expect(find.text('UDP Experimental'), findsNothing);
    expect(find.text('TCP Relay Experimental'), findsNothing);
    expect(find.text('Local TCP Forward'), findsNothing);
    expect(find.text('Off'), findsNothing);
  });

  testWidgets('UDP Only remains selectable and maps to UDP forwarding', (
    tester,
  ) async {
    final client = _CapturingMockClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();
    await _switchToAdvancedTab(tester);
    await tester.ensureVisible(find.text('Advanced Backend Settings'));
    await tester.tap(find.text('Advanced Backend Settings'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('UDP + TCP Bundle'));
    await tester.tap(find.text('UDP + TCP Bundle').last);
    await tester.pumpAndSettle();
    await tester.tap(find.text('UDP Only').last);
    await tester.pumpAndSettle();

    await _switchToRoomTab(tester);
    await _enterPlayerName(tester);
    await _enterGameServerPort(tester, '27015');
    await tester.tap(find.widgetWithText(FilledButton, 'Create Room'));
    await tester.pumpAndSettle();

    expect(client.lastCreateAdapterConfig?.adapterType, 'local_udp_bridge');
    expect(client.lastCreateAdapterConfig?.targetPort, 27015);
  });

  testWidgets('Join UDP Only sends local_udp_bridge without game port', (
    tester,
  ) async {
    final client = _CapturingMockClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client, initialMode: 'join'));
    await tester.pumpAndSettle();
    await _enterPlayerName(tester);
    await tester.enterText(find.widgetWithText(TextField, 'Room ID'), 'MOCK42');
    await tester.pump();

    await _switchToAdvancedTab(tester);
    await tester.ensureVisible(find.text('Advanced Backend Settings'));
    await tester.tap(find.text('Advanced Backend Settings'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('UDP + TCP Bundle'));
    await tester.tap(find.text('UDP + TCP Bundle').last);
    await tester.pumpAndSettle();
    await tester.tap(find.text('UDP Only').last);
    await tester.pumpAndSettle();

    await _switchToRoomTab(tester);
    await tester.tap(find.widgetWithText(FilledButton, 'Join Room'));
    await tester.pumpAndSettle();

    expect(client.lastJoinGameServerPort, isNull);
    expect(client.lastJoinAdapterConfig?.adapterType, 'local_udp_bridge');
    expect(client.lastJoinAdapterConfig?.adapterType, isNot('bundle'));
    expect(client.lastJoinAdapterConfig?.targetPort, isNull);
    expect(client.lastJoinAdapterConfig?.bindHost, '127.0.0.1');
    expect(client.lastJoinAdapterConfig?.bindPort, 0);
  });

  testWidgets('Force Relay is visible and enabled by default', (tester) async {
    final client = _CapturingMockClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();

    expect(find.text('Force Relay'), findsOneWidget);
    final checkbox = tester.widget<Checkbox>(find.byType(Checkbox));
    expect(checkbox.value, isTrue);
  });

  testWidgets('Create request passes disabled Force Relay when unchecked', (
    tester,
  ) async {
    final client = _CapturingMockClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();

    await tester.tap(find.byType(Checkbox));
    await tester.pumpAndSettle();
    await _enterPlayerName(tester);
    await _enterGameServerPort(tester, '27015');
    await tester.tap(find.widgetWithText(FilledButton, 'Create Room'));
    await tester.pumpAndSettle();

    expect(client.lastCreateForceRelay, isFalse);
  });

  testWidgets('Join request uses explicit Bundle config without game port', (
    tester,
  ) async {
    final client = _CapturingMockClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();

    await _enterPlayerName(tester);
    await tester.ensureVisible(find.text('Join Room').first);
    await tester.tap(find.text('Join Room').first);
    await tester.pumpAndSettle();
    await tester.enterText(find.widgetWithText(TextField, 'Room ID'), 'MOCK42');
    await tester.pump();
    await tester.tap(find.widgetWithText(FilledButton, 'Join Room'));
    await tester.pumpAndSettle();

    expect(client.lastJoinServerHost, BackendBridgePanel.localPreviewRelayHost);
    expect(client.lastJoinServerPort, BackendBridgePanel.defaultRelayTcpPort);
    expect(
      client.lastJoinServerUdpPort,
      BackendBridgePanel.defaultRelayUdpPort,
    );
    expect(client.lastJoinForceRelay, isTrue);
    expect(client.lastJoinGameServerPort, isNull);
    expect(client.lastJoinAdapterConfig?.adapterType, 'bundle');
    expect(client.lastJoinAdapterConfig?.targetPort, 0);
  });

  testWidgets('Join TCP Only sends explicit config without game_server_port', (
    tester,
  ) async {
    final client = _CapturingMockClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client, initialMode: 'join'));
    await tester.pumpAndSettle();

    await _enterPlayerName(tester);
    await tester.enterText(find.widgetWithText(TextField, 'Room ID'), 'MOCK42');
    await tester.pump();

    await _switchToAdvancedTab(tester);
    await tester.ensureVisible(find.text('Advanced Backend Settings'));
    await tester.tap(find.text('Advanced Backend Settings'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('UDP + TCP Bundle'));
    await tester.tap(find.text('UDP + TCP Bundle').last);
    await tester.pumpAndSettle();
    await tester.tap(find.text('TCP Only').last);
    await tester.pumpAndSettle();

    await _switchToRoomTab(tester);
    tester
        .widget<FilledButton>(find.widgetWithText(FilledButton, 'Join Room'))
        .onPressed
        ?.call();
    await tester.pumpAndSettle();

    expect(client.lastJoinGameServerPort, isNull);
    expect(client.lastJoinAdapterConfig?.adapterType, 'tcp_forward');
    expect(client.lastJoinAdapterConfig?.targetPort, isNull);
    expect(client.lastJoinAdapterConfig?.bindHost, '127.0.0.1');
    expect(client.lastJoinAdapterConfig?.bindPort, 0);
  });

  testWidgets('Bundle choice creates a wired bundle adapter config', (
    tester,
  ) async {
    final client = _CapturingMockClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();
    await _switchToAdvancedTab(tester);

    await tester.ensureVisible(find.text('Advanced Backend Settings'));
    await tester.tap(find.text('Advanced Backend Settings'));
    await tester.pumpAndSettle();
    expect(find.text('UDP + TCP Bundle'), findsOneWidget);

    await _switchToRoomTab(tester);
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

    expect(client.lastCreateAdapterConfig?.adapterType, 'bundle');
    expect(client.lastCreateAdapterConfig?.targetPort, 27015);
  });

  testWidgets(
    'Join Bundle sends local adapter config without game_server_port',
    (tester) async {
      final client = _CapturingMockClient();
      addTearDown(client.dispose);

      await tester.pumpWidget(_bridgeTestApp(client, initialMode: 'join'));
      await tester.pumpAndSettle();
      await _enterPlayerName(tester);
      await tester.enterText(
        find.widgetWithText(TextField, 'Room ID'),
        'MOCK42',
      );

      await _switchToAdvancedTab(tester);
      await tester.ensureVisible(find.text('Advanced Backend Settings'));
      await tester.tap(find.text('Advanced Backend Settings'));
      await tester.pumpAndSettle();
      expect(find.text('UDP + TCP Bundle'), findsOneWidget);

      await _switchToRoomTab(tester);
      final joinButton = find.widgetWithText(FilledButton, 'Join Room');
      expect(tester.widget<FilledButton>(joinButton).onPressed, isNotNull);

      await tester.tap(joinButton);
      await tester.pumpAndSettle();

      expect(client.lastJoinGameServerPort, isNull);
      expect(client.lastJoinAdapterConfig?.adapterType, 'bundle');
      expect(client.lastJoinAdapterConfig?.targetPort, 0);
      expect(client.lastJoinAdapterConfig?.bindHost, '127.0.0.1');
      expect(client.lastJoinAdapterConfig?.bindPort, 0);
    },
  );

  testWidgets('Bundle helper includes broadcast without visibility guarantee', (
    tester,
  ) async {
    final client = MockBackendClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();
    await _switchToAdvancedTab(tester);
    await tester.ensureVisible(find.text('Advanced Backend Settings'));
    await tester.tap(find.text('Advanced Backend Settings'));
    await tester.pumpAndSettle();

    expect(
      find.textContaining('UDP broadcast/LAN discovery forwarding'),
      findsOneWidget,
    );
    expect(find.textContaining('not guaranteed'), findsOneWidget);
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
        bindHost: '127.0.0.1',
        bindPort: 0,
      ),
      ['Adapter', 'Stopped / configured but not running'],
    );

    await _expectAdapterDisplay(
      tester,
      const AdapterStatus(
        enabled: true,
        status: 'ready',
        adapterType: 'tcp_relay',
        bindHost: '127.0.0.1',
        bindPort: 0,
        targetHost: '127.0.0.1',
        targetPort: 25565,
        counters: AdapterCounters(
          packetsFromGame: 0,
          packetsToTransport: 0,
          packetsFromTransport: 0,
          packetsToGame: 0,
        ),
      ),
      ['Adapter', 'Ready', 'Game server address', '127.0.0.1:25565'],
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
        targetPort: 27015,
        counters: AdapterCounters(
          packetsFromGame: 6,
          packetsToTransport: 6,
          packetsFromTransport: 5,
          packetsToGame: 5,
        ),
      ),
      [
        'Ready',
        'Local game connection',
        '127.0.0.1:40100',
        'Game server address',
        '127.0.0.1:27015',
        'The VPS/Relay address is for CoopWing only. Do not put it directly into the game connect command.',
        'Point the game connect command here, for example connect 127.0.0.1:port.',
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
        targetPort: 27015,
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

  testWidgets('Room Connection displays Secondary IP status', (tester) async {
    final client = _AdapterStatusMockClient(
      const AdapterStatus(
        enabled: true,
        status: 'ready',
        adapterType: 'local_udp_bridge',
        bindHost: '127.0.0.1',
        bindPort: 40100,
        targetHost: '127.0.0.1',
        targetPort: 27015,
        counters: AdapterCounters(
          packetsFromGame: 0,
          packetsToTransport: 0,
          packetsFromTransport: 0,
          packetsToGame: 0,
        ),
      ),
      secondaryIpEnabled: false,
      secondaryIpFallbackUsed: true,
      secondaryIpWarning: 'backend process is not elevated',
      backendAdmin: false,
      adapterBindMode: 'loopback',
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

    expect(find.text('Backend admin/elevated'), findsAtLeastNWidgets(1));
    expect(find.text('Secondary IP bind'), findsAtLeastNWidgets(1));
    expect(find.text('Failed'), findsAtLeastNWidgets(1));
    expect(find.text('Target interface'), findsOneWidget);
    expect(find.text('Secondary bind address'), findsOneWidget);
    expect(find.text('Adapter bind mode'), findsOneWidget);
    expect(find.text('Game target address'), findsOneWidget);
    expect(find.text('Secondary IP warning'), findsAtLeastNWidgets(1));
    expect(find.text('No'), findsAtLeastNWidgets(1));
    expect(find.text('loopback'), findsOneWidget);
    expect(find.text('127.0.0.1:27015'), findsAtLeastNWidgets(1));
    expect(
      find.text('backend process is not elevated'),
      findsAtLeastNWidgets(1),
    );
    expect(find.textContaining('relay_token'), findsNothing);
  });

  testWidgets(
    'Room Connection separates backend API and secondary bind address',
    (tester) async {
      final client = _AdapterStatusMockClient(
        const AdapterStatus(
          enabled: true,
          status: 'ready',
          adapterType: 'local_udp_bridge',
          bindHost: '192.168.5.233',
          bindPort: 40100,
          targetHost: '127.0.0.1',
          targetPort: 27015,
          counters: AdapterCounters(
            packetsFromGame: 0,
            packetsToTransport: 0,
            packetsFromTransport: 0,
            packetsToGame: 0,
          ),
        ),
        secondaryIpEnabled: true,
        secondaryIpFallbackUsed: false,
        backendAdmin: true,
        secondaryIpBindAddress: '192.168.5.233',
        secondaryIpInterfaceIndex: 18,
        secondaryIpInterfaceAlias: 'Ethernet',
        adapterBindMode: 'secondary_ip',
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

      expect(find.text('Assigned'), findsAtLeastNWidgets(1));
      expect(find.text('192.168.5.233'), findsAtLeastNWidgets(1));
      expect(find.text('Ethernet (ifIndex 18)'), findsOneWidget);
      expect(find.text('secondary_ip'), findsAtLeastNWidgets(1));
      expect(find.textContaining('127.0.0.1:21520'), findsAtLeastNWidgets(1));
      expect(find.textContaining('relay_token'), findsNothing);
    },
  );

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
        targetPort: 27015,
        counters: AdapterCounters(
          packetsFromGame: 0,
          packetsToTransport: 0,
          packetsFromTransport: 0,
          packetsToGame: 0,
        ),
      ),
      [
        'VPS/Relay 地址只供 CoopWing 使用，不应直接填进游戏 connect 命令。',
        '将游戏 connect 命令指向此地址，例如 connect 127.0.0.1:端口。',
        '未检测到游戏流量。请确认游戏连接的是本机连接地址。',
      ],
    );
  });

  testWidgets('Same lifecycle mode switching', (tester) async {
    final client = _CapturingMockClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();

    await _enterPlayerName(tester);
    await _enterGameServerPort(tester, '27015');

    await _expandAdvancedSettings(tester);
    await tester.ensureVisible(find.byType(DropdownButtonFormField<AdapterMode>));
    await tester.tap(find.byType(DropdownButtonFormField<AdapterMode>));
    await tester.pumpAndSettle();
    await tester.tap(find.text('UDP Only').last);
    await tester.pumpAndSettle();

    await _switchToRoomTab(tester);
    await tester.tap(find.widgetWithText(FilledButton, 'Create Room'));
    await tester.pumpAndSettle();

    expect(client.lastCreateAdapterConfig?.adapterType, 'local_udp_bridge');

    await tester.ensureVisible(find.text('Stop Session'));
    await tester.tap(find.text('Stop Session'));
    await tester.pumpAndSettle();

    await _expandAdvancedSettings(tester);
    await tester.ensureVisible(find.byType(DropdownButtonFormField<AdapterMode>));
    await tester.tap(find.byType(DropdownButtonFormField<AdapterMode>));
    await tester.pumpAndSettle();
    await tester.tap(find.text('UDP + TCP Bundle').last);
    await tester.pumpAndSettle();

    await _switchToRoomTab(tester);
    await tester.tap(find.widgetWithText(FilledButton, 'Create Room'));
    await tester.pumpAndSettle();

    expect(client.lastCreateAdapterConfig?.adapterType, 'bundle');
  });

  testWidgets('Bundle UI display maps TCP gameplay from tcp_relay diagnostics', (
    tester,
  ) async {
    final client = _AdapterStatusMockClient(
      const AdapterStatus(
        enabled: true,
        status: 'ready',
        adapterType: 'bundle',
        bindHost: '127.0.0.1',
        bindPort: 59000,
        counters: AdapterCounters(
          packetsFromGame: 0,
          packetsToTransport: 0,
          packetsFromTransport: 0,
          packetsToGame: 0,
        ),
        payloadDiagnostics: {
          'local_game_connection': {'host': '127.0.0.1', 'port': 59000},
          'discovery_helper_connection': {
            'host': '127.0.0.1',
            'port': 59001,
            'udp_available': true,
          },
          'rules': [
            {
              'id': 'mock_tcp_relay',
              'kind': 'tcp_relay',
              'running': true,
              'stats': {
                'packets_from_game': 7,
                'packets_to_transport': 7,
                'packets_from_transport': 5,
                'packets_to_game': 5,
              },
            },
            {
              'id': 'mock_udp_raw',
              'kind': 'udp_raw_bridge',
              'running': true,
              'stats': {
                'packets_from_game': 2,
                'packets_to_transport': 2,
                'packets_from_transport': 1,
                'packets_to_game': 1,
              },
            },
            {
              'id': 'mock_discovery',
              'kind': 'udp_broadcast_forward',
              'running': true,
              'stats': {
                'packets_from_game': 11,
                'packets_to_transport': 11,
                'packets_from_transport': 13,
                'packets_to_game': 13,
              },
            },
          ],
        },
      ),
    );
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();

    await _enterPlayerName(tester);
    await _enterGameServerPort(tester, '27015');
    await tester.tap(find.widgetWithText(FilledButton, 'Create Room'));
    await tester.pumpAndSettle();

    expect(find.text('Local game connection'), findsOneWidget);
    expect(find.text('127.0.0.1:59000'), findsOneWidget);
    expect(find.text('TCP: relay'), findsOneWidget);
    expect(find.text('UDP: raw gameplay bridge'), findsOneWidget);

    final tcpSection = find.byKey(const Key('bundle-tcp-gameplay-section'));
    final udpSection = find.byKey(const Key('bundle-udp-gameplay-section'));
    final discoverySection = find.byKey(
      const Key('bundle-discovery-helper-section'),
    );
    expect(tcpSection, findsOneWidget);
    expect(udpSection, findsOneWidget);
    expect(discoverySection, findsOneWidget);
    expect(
      find.descendant(of: tcpSection, matching: find.text('TCP gameplay')),
      findsOneWidget,
    );
    expect(
      find.descendant(of: tcpSection, matching: find.text('7')),
      findsOneWidget,
    );
    expect(
      find.descendant(of: tcpSection, matching: find.text('5')),
      findsOneWidget,
    );
    expect(
      find.descendant(of: udpSection, matching: find.text('UDP gameplay')),
      findsOneWidget,
    );
    expect(
      find.descendant(of: udpSection, matching: find.text('Raw UDP gameplay')),
      findsOneWidget,
    );
    expect(
      find.descendant(of: udpSection, matching: find.text('2')),
      findsOneWidget,
    );
    expect(
      find.descendant(of: udpSection, matching: find.text('1')),
      findsOneWidget,
    );
    expect(
      find.descendant(
        of: discoverySection,
        matching: find.text('Discovery helper traffic'),
      ),
      findsOneWidget,
    );
    expect(
      find.descendant(of: discoverySection, matching: find.text('11')),
      findsOneWidget,
    );
    expect(
      find.descendant(of: discoverySection, matching: find.text('13')),
      findsOneWidget,
    );
    expect(
      find.descendant(of: discoverySection, matching: find.text('7')),
      findsNothing,
    );

    expect(find.text(Localization().get('lan_discovery_helper')), findsOneWidget);
    expect(find.text('127.0.0.1:59001'), findsOneWidget);
    expect(
      find.textContaining(Localization().get('lan_discovery_helper_helper')),
      findsOneWidget,
    );
  });

  testWidgets('UDP Only no-target behavior', (tester) async {
    final client = _CapturingMockClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();

    await _expandAdvancedSettings(tester);
    await tester.ensureVisible(find.byType(DropdownButtonFormField<AdapterMode>));
    await tester.tap(find.byType(DropdownButtonFormField<AdapterMode>));
    await tester.pumpAndSettle();
    await tester.tap(find.text('UDP Only').last);
    await tester.pumpAndSettle();

    await _switchToRoomTab(tester);
    await tester.tap(find.text('Join Room'));
    await tester.pumpAndSettle();

    await _enterPlayerName(tester);
    await tester.enterText(find.widgetWithText(TextField, 'Room ID'), 'ABCDEF');
    await tester.pumpAndSettle();

    await tester.tap(find.widgetWithText(FilledButton, 'Join Room'));
    await tester.pumpAndSettle();

    expect(client.lastJoinAdapterConfig?.adapterType, 'local_udp_bridge');

    await _expandAdvancedSettings(tester);
    await tester.ensureVisible(find.byType(DropdownButtonFormField<AdapterMode>));
    final dropdown = tester.widget<DropdownButtonFormField<AdapterMode>>(
      find.byType(DropdownButtonFormField<AdapterMode>),
    );
    expect(dropdown.initialValue, AdapterMode.udpOnly);
  });

  testWidgets('Bundle Join no-target behavior', (tester) async {
    final client = _CapturingMockClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();

    await tester.tap(find.text('Join Room'));
    await tester.pumpAndSettle();

    await _enterPlayerName(tester);
    await tester.enterText(find.widgetWithText(TextField, 'Room ID'), 'ABCDEF');
    await tester.pumpAndSettle();

    await tester.tap(find.widgetWithText(FilledButton, 'Join Room'));
    await tester.pumpAndSettle();

    expect(client.lastJoinAdapterConfig?.adapterType, 'bundle');
    expect(client.lastJoinAdapterConfig?.targetPort, 0);
  });

  testWidgets('Reset local display resets adapter mode to Bundle', (tester) async {
    final client = MockBackendClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();

    await _enterPlayerName(tester);
    await _enterGameServerPort(tester, '27015');

    await _expandAdvancedSettings(tester);
    await tester.ensureVisible(find.byType(DropdownButtonFormField<AdapterMode>));
    await tester.tap(find.byType(DropdownButtonFormField<AdapterMode>));
    await tester.pumpAndSettle();
    await tester.tap(find.text('TCP Only').last);
    await tester.pumpAndSettle();

    await _switchToRoomTab(tester);
    await tester.tap(find.widgetWithText(FilledButton, 'Create Room'));
    await tester.pumpAndSettle();

    await tester.ensureVisible(find.text('Reset local display').first);
    await tester.tap(find.text('Reset local display').first);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Reset').last);
    await tester.pumpAndSettle();

    await _expandAdvancedSettings(tester);
    await tester.ensureVisible(find.byType(DropdownButtonFormField<AdapterMode>));
    final dropdown = tester.widget<DropdownButtonFormField<AdapterMode>>(
      find.byType(DropdownButtonFormField<AdapterMode>),
    );
    expect(dropdown.initialValue, AdapterMode.bundle);
  });

  testWidgets('backend offline displays neutral connecting copy and grace period success on create', (tester) async {
    final client = _SwitchableHealthMockClient()..online = false;
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();

    await _enterPlayerName(tester);
    await _enterGameServerPort(tester, '27015');

    await tester.tap(find.widgetWithText(FilledButton, 'Create Room'));
    await tester.pump();

    expect(find.text('Connecting to local backend...'), findsOneWidget);
    expect(find.textContaining('Backend offline'), findsNothing);

    client.online = true;

    await tester.pump(const Duration(milliseconds: 500));
    await tester.pumpAndSettle();

    expect(find.text('Current Session'), findsOneWidget);
    expect(find.text('Connecting to local backend...'), findsNothing);
  });

  testWidgets('backend offline grace period timeout on create', (tester) async {
    final client = _SwitchableHealthMockClient()..online = false;
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();

    await _enterPlayerName(tester);
    await _enterGameServerPort(tester, '27015');

    await tester.tap(find.widgetWithText(FilledButton, 'Create Room'));
    await tester.pump();

    expect(find.text('Connecting to local backend...'), findsOneWidget);

    await tester.pump(const Duration(milliseconds: 3500));
    await tester.pumpAndSettle();

    expect(find.textContaining('Backend offline'), findsOneWidget);
    expect(find.text('Connecting to local backend...'), findsNothing);
  });

  testWidgets('Secondary IP recommendation failure does not make backend offline', (tester) async {
    final client = _FailRecommendationMockClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();

    expect(find.text('online fake'), findsOneWidget);
    expect(find.textContaining('Backend offline'), findsNothing);
  });

  testWidgets('LAN discovery status failure does not make backend offline', (tester) async {
    final client = _FailLanStatusMockClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();

    expect(find.text('online fake'), findsOneWidget);
    expect(find.textContaining('Backend offline'), findsNothing);
  });

  testWidgets('SESSION_NOT_FOUND clears stale session but does not flag offline', (tester) async {
    final client = _NotFoundSessionMockClient();
    addTearDown(client.dispose);

    await tester.pumpWidget(_bridgeTestApp(client));
    await tester.pumpAndSettle();

    await _enterPlayerName(tester);
    await _enterGameServerPort(tester, '27015');
    await tester.tap(find.widgetWithText(FilledButton, 'Create Room'));
    await tester.pumpAndSettle();

    expect(find.text('Current Session'), findsOneWidget);

    await tester.pump(const Duration(milliseconds: 2500));
    await tester.pumpAndSettle();

    expect(find.widgetWithText(FilledButton, 'Create Room'), findsOneWidget);
    expect(find.text('Current Session'), findsNothing);
    expect(find.text('online fake'), findsOneWidget);
  });
}

Widget _bridgeTestApp(MockBackendClient client, {String? initialMode}) {
  return ListenableBuilder(
    listenable: Localization(),
    builder: (context, _) {
      return MaterialApp(
        home: Scaffold(
          body: SingleChildScrollView(
            child: BackendBridgePanel(
              client: client,
              onRunDiagnostics: _testReport,
              initialMode: initialMode,
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
  var returnToRoom = false;
  var field = find.widgetWithText(
    TextField,
    Localization().get('shared_game_port'),
  );
  if (field.evaluate().isEmpty) {
    field = find.widgetWithText(
      TextField,
      Localization().get('game_bind_port'),
    );
  }
  if (field.evaluate().isEmpty) {
    await _switchToAdvancedTab(tester);
    returnToRoom = true;
    field = find.widgetWithText(
      TextField,
      Localization().get('game_bind_port'),
    );
  }
  if (field.evaluate().isEmpty) {
    await tester.ensureVisible(
      find.text(Localization().get('advanced_backend_settings')),
    );
    await tester.tap(
      find.text(Localization().get('advanced_backend_settings')),
    );
    await tester.pumpAndSettle();
    field = find.widgetWithText(
      TextField,
      Localization().get('game_bind_port'),
    );
  }
  await tester.enterText(field, port);
  await tester.pump();
  if (returnToRoom) {
    await _switchToRoomTab(tester);
  }
}

Future<void> _switchToAdvancedTab(WidgetTester tester) async {
  // Tab label is localized — try en first, then zh.
  if (find.text('Advanced').evaluate().isNotEmpty) {
    await tester.ensureVisible(find.text('Advanced'));
    await tester.tap(find.text('Advanced'));
  } else {
    await tester.ensureVisible(find.text('高级'));
    await tester.tap(find.text('高级'));
  }
  await tester.pumpAndSettle();
}

Future<void> _switchToRoomTab(WidgetTester tester) async {
  if (find.text('Room').evaluate().isNotEmpty) {
    await tester.ensureVisible(find.text('Room'));
    await tester.tap(find.text('Room'));
  } else {
    await tester.ensureVisible(find.text('房间'));
    await tester.tap(find.text('房间'));
  }
  await tester.pumpAndSettle();
}

Future<void> _expandSessionDetails(WidgetTester tester) async {
  final details = find.text(Localization().get('session_details_title'));
  await tester.ensureVisible(details);
  await tester.tap(details);
  await tester.pumpAndSettle();
}

Future<void> _expandAdvancedSettings(WidgetTester tester) async {
  await _switchToAdvancedTab(tester);
  await tester.ensureVisible(find.text('Advanced Backend Settings'));
  await tester.tap(find.text('Advanced Backend Settings'));
  await tester.pumpAndSettle();
}

Future<void> _showLanDiscoverySection(WidgetTester tester) async {
  await _switchToAdvancedTab(tester);
  await tester.ensureVisible(find.text('LAN Discovery / 局域网发现'));
  await tester.pumpAndSettle();
}

class _CapturingMockClient extends MockBackendClient {
  String? lastCreateServerHost;
  int? lastCreateServerPort;
  int? lastCreateServerUdpPort;
  int? lastCreateGameServerPort;
  bool? lastCreateForceRelay;
  AdapterConfig? lastCreateAdapterConfig;
  String? lastJoinServerHost;
  int? lastJoinServerPort;
  int? lastJoinServerUdpPort;
  int? lastJoinGameServerPort;
  bool? lastJoinForceRelay;
  AdapterConfig? lastJoinAdapterConfig;
  int? lastScannedPid;

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
  }) {
    lastCreateServerHost = serverHost;
    lastCreateServerPort = serverPort;
    lastCreateServerUdpPort = serverUdpPort;
    lastCreateGameServerPort = gameServerPort;
    lastCreateForceRelay = forceRelay;
    lastCreateAdapterConfig = adapterConfig;
    return super.createSession(
      serverHost: serverHost,
      serverPort: serverPort,
      serverUdpPort: serverUdpPort,
      playerName: playerName,
      gameServerPort: gameServerPort,
      bindHost: bindHost,
      bindPort: bindPort,
      forceRelay: forceRelay,
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
    bool forceRelay = true,
    AdapterConfig? adapterConfig,
  }) {
    lastJoinServerHost = serverHost;
    lastJoinServerPort = serverPort;
    lastJoinServerUdpPort = serverUdpPort;
    lastJoinGameServerPort = gameServerPort;
    lastJoinForceRelay = forceRelay;
    lastJoinAdapterConfig = adapterConfig;
    return super.joinSession(
      serverHost: serverHost,
      serverPort: serverPort,
      serverUdpPort: serverUdpPort,
      roomId: roomId,
      playerName: playerName,
      gameServerHost: gameServerHost,
      gameServerPort: gameServerPort,
      forceRelay: forceRelay,
      adapterConfig: adapterConfig,
    );
  }

  @override
  Future<ProcessPortScanResult> scanProcessPorts(int pid) {
    lastScannedPid = pid;
    return super.scanProcessPorts(pid);
  }
}

class _SwitchableHealthMockClient extends MockBackendClient {
  bool online = true;

  @override
  Future<HealthStatus> health() async {
    if (!online) {
      return HealthStatus.offline();
    }
    return const HealthStatus(
      status: 'ok',
      version: '0.1.0',
      uptimeSeconds: 1,
      backend: 's2pass',
      mode: 'real_core',
    );
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
    required int gameServerPort,
    required String bindHost,
    required int bindPort,
    bool forceRelay = true,
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
      forceRelay: true,
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
  _AdapterStatusMockClient(
    this.adapterStatus, {
    this.v2RelayWithoutPeer = false,
    this.secondaryIpEnabled = false,
    this.secondaryIpFallbackUsed = false,
    this.secondaryIpWarning,
    this.backendAdmin = false,
    this.secondaryIpBindAddress,
    this.secondaryIpInterfaceIndex,
    this.secondaryIpInterfaceAlias,
    this.adapterBindMode = 'loopback',
  });

  final AdapterStatus? adapterStatus;
  final bool v2RelayWithoutPeer;
  final bool secondaryIpEnabled;
  final bool secondaryIpFallbackUsed;
  final String? secondaryIpWarning;
  final bool backendAdmin;
  final String? secondaryIpBindAddress;
  final int? secondaryIpInterfaceIndex;
  final String? secondaryIpInterfaceAlias;
  final String adapterBindMode;
  final num _now = DateTime.now().millisecondsSinceEpoch / 1000;

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
      forceRelay: true,
      createdAt: _now,
      updatedAt: _now,
      stats: SessionStats.empty(),
      adapterStatus: adapterStatus,
      protocolVersion: v2RelayWithoutPeer ? 2 : null,
      maxPlayers: v2RelayWithoutPeer ? 4 : null,
      participantCount: v2RelayWithoutPeer ? 1 : null,
      relayReady: v2RelayWithoutPeer,
      relayTargetHost: v2RelayWithoutPeer ? serverHost : null,
      relayTargetPort: v2RelayWithoutPeer ? serverUdpPort : null,
      secondaryIpEnabled: secondaryIpEnabled,
      secondaryIpFallbackUsed: secondaryIpFallbackUsed,
      secondaryIpWarning: secondaryIpWarning,
      backendAdmin: backendAdmin,
      secondaryIpBindAddress: secondaryIpBindAddress,
      secondaryIpInterfaceIndex: secondaryIpInterfaceIndex,
      secondaryIpInterfaceAlias: secondaryIpInterfaceAlias,
      adapterBindMode: adapterBindMode,
    );
  }
}

class _FailRecommendationMockClient extends MockBackendClient {
  @override
  Future<SecondaryIpRecommendation> getSecondaryIpRecommendation() async {
    throw const BackendError(code: 'API_ERROR', message: 'recommendation failed');
  }
}

class _FailLanStatusMockClient extends MockBackendClient {
  @override
  Future<LanDiscoveryStatus> getLanDiscoveryStatus() async {
    throw const BackendError(code: 'API_ERROR', message: 'lan status failed');
  }
}

class _NotFoundSessionMockClient extends MockBackendClient {
  int getStatusCalls = 0;

  @override
  Future<SessionInfo> getSessionStatus(String sessionId) async {
    getStatusCalls++;
    if (getStatusCalls > 1) {
      throw const BackendError(code: 'SESSION_NOT_FOUND', message: 'session not found');
    }
    return super.getSessionStatus(sessionId);
  }

  @override
  Future<List<SessionEvent>> getSessionLogs(String sessionId) async {
    if (getStatusCalls > 1) {
      throw const BackendError(code: 'SESSION_NOT_FOUND', message: 'session not found');
    }
    return super.getSessionLogs(sessionId);
  }
}
