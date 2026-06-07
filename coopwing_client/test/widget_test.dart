import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:s2pass_flutter_mock/app.dart';
import 'package:s2pass_flutter_mock/screens/my_games_screen.dart';
import 'package:s2pass_flutter_mock/services/localization.dart';

void main() {
  setUp(() {
    disableDesktopDropForTesting = true;
  });

  tearDown(() {
    disableDesktopDropForTesting = false;
  });

  test('Executable path draft parses exe paths only', () {
    final draft = executablePathDraftFromPath(
      r'"C:\Games\Example Game\ExampleGame.exe"',
    );

    expect(draft, isNotNull);
    expect(draft!.executablePath, r'C:\Games\Example Game\ExampleGame.exe');
    expect(draft.displayName, 'ExampleGame');
    expect(draft.workingDirectory, r'C:\Games\Example Game');
    expect(
      executablePathDraftFromPath(r'C:\Games\Example Game\readme.txt'),
      isNull,
    );
  });

  testWidgets('v0.4 Preview shell renders Bundle dashboard', (tester) async {
    Localization().setLanguage(Language.en);
    await tester.pumpWidget(const S2PassPreviewApp());
    await tester.pumpAndSettle();

    expect(find.text('Co-opWinG'), findsWidgets);
    expect(
      find.text('Co-opWinG v0.4 Preview / Generic TCP+UDP Bundle'),
      findsWidgets,
    );
    expect(find.textContaining('Version:'), findsNothing);
    expect(find.byType(Image), findsWidgets);
    expect(find.text('中文名可能是合翼卫？ 卫什么啊，何意味啊。'), findsOneWidget);
    expect(find.text('My Games'), findsWidgets);
    expect(find.text('Network Doctor'), findsNothing);
    expect(find.textContaining('Generic UDP relay'), findsNothing);
    expect(find.textContaining('does not support TCP'), findsNothing);
    expect(find.textContaining('v0.3'), findsNothing);
    expect(
      find.textContaining('LAN room visibility is not guaranteed'),
      findsOneWidget,
    );
    expect(find.textContaining('relay-only multi-peer'), findsWidgets);
    expect(find.text('Add Game / Port Detection'), findsOneWidget);
    expect(find.textContaining('candidate TCP/UDP ports'), findsOneWidget);
    expect(
      find.text('Create or join a relay-only multi-peer room.'),
      findsOneWidget,
    );
  });

  testWidgets('My Games add form fills defaults from exe path', (tester) async {
    Localization().setLanguage(Language.en);
    await tester.pumpWidget(const S2PassPreviewApp());
    await tester.pumpAndSettle();

    await tester.tap(find.text('My Games').first);
    await tester.pumpAndSettle();

    expect(
      find.textContaining('process port detection preview'),
      findsOneWidget,
    );
    expect(find.textContaining('TCP/UDP candidates'), findsOneWidget);
    expect(find.text('Drag your game .exe here'), findsOneWidget);

    await tester.tap(find.widgetWithText(OutlinedButton, 'Add Game'));
    await tester.pumpAndSettle();

    expect(find.textContaining('Paste or drag a .exe path'), findsOneWidget);
    await tester.enterText(
      find.widgetWithText(TextField, 'Executable Path'),
      r'C:\Games\Example Game\ExampleGame.exe',
    );
    await tester.pumpAndSettle();

    final nameField = tester.widget<TextField>(
      find.widgetWithText(TextField, 'Game Name'),
    );
    final workDirField = tester.widget<TextField>(
      find.widgetWithText(TextField, 'Working Directory (optional)'),
    );
    expect(nameField.controller?.text, 'ExampleGame');
    expect(workDirField.controller?.text, r'C:\Games\Example Game');
  });

  testWidgets('My Games add form rejects non exe path without saving', (
    tester,
  ) async {
    Localization().setLanguage(Language.en);
    await tester.pumpWidget(const S2PassPreviewApp());
    await tester.pumpAndSettle();

    await tester.tap(find.text('My Games').first);
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(OutlinedButton, 'Add Game'));
    await tester.pumpAndSettle();

    await tester.enterText(
      find.widgetWithText(TextField, 'Executable Path'),
      r'C:\Games\Example Game\readme.txt',
    );
    await tester.tap(find.text('Apply path'));
    await tester.pumpAndSettle();

    expect(find.text('Provide a .exe executable path.'), findsOneWidget);
  });

  testWidgets('Doctor Settings and About copy reflects v0.4 Bundle scope', (
    tester,
  ) async {
    Localization().setLanguage(Language.en);
    await tester.pumpWidget(const S2PassPreviewApp());
    await tester.pumpAndSettle();

    await tester.tap(find.text('Doctor').first);
    await tester.pumpAndSettle();
    expect(find.textContaining('does not support TCP'), findsNothing);
    expect(
      find.textContaining('LAN Discovery only finds Co-opWinG instances'),
      findsOneWidget,
    );
    expect(
      find.textContaining('This tool does not modify system network settings.'),
      findsOneWidget,
    );

    await tester.tap(find.text('Settings').first);
    await tester.pumpAndSettle();
    expect(find.textContaining('experimental UDP bridge mode'), findsNothing);
    expect(
      find.textContaining('experimental forwarding/diagnostics options'),
      findsOneWidget,
    );

    await tester.tap(find.text('About').first);
    await tester.pumpAndSettle();
    expect(find.textContaining('Generic UDP relay validation'), findsNothing);
    expect(find.textContaining('does not support TCP'), findsNothing);
    expect(find.textContaining('Co-opWinG v0.4 Preview'), findsWidgets);
    expect(find.textContaining('Generic TCP+UDP Bundle'), findsWidgets);
    expect(
      find.textContaining(
        'Default Bundle mode starts TCP forwarding, UDP forwarding',
      ),
      findsOneWidget,
    );
    expect(
      find.textContaining('LAN room visibility is not guaranteed'),
      findsOneWidget,
    );
    expect(find.textContaining('relay-only multi-peer room'), findsOneWidget);
    expect(find.textContaining('LAN Discovery'), findsWidgets);
    expect(find.textContaining('v0.3'), findsNothing);
  });

  testWidgets('Settings default Relay/root host drives Room Connection', (
    tester,
  ) async {
    Localization().setLanguage(Language.en);
    await tester.pumpWidget(const S2PassPreviewApp());
    await tester.pumpAndSettle();

    await tester.tap(find.text('Settings').first);
    await tester.pumpAndSettle();

    expect(
      find.widgetWithText(TextField, 'Default Relay/root host (default_relay)'),
      findsOneWidget,
    );
    expect(
      find.text(
        'Used as the default VPS/relay server address for create/join room.',
      ),
      findsOneWidget,
    );
    expect(find.text('120.27.210.184'), findsOneWidget);

    await tester.enterText(
      find.widgetWithText(TextField, 'Default Relay/root host (default_relay)'),
      '203.0.113.7',
    );
    await tester.tap(find.widgetWithText(FilledButton, 'Save').first);
    await tester.pumpAndSettle();

    await tester.tap(find.text('Room Connection').first);
    await tester.pumpAndSettle();
    expect(find.textContaining('python -m backend.server'), findsNothing);
    expect(
      find.text('Local backend: managed automatically (127.0.0.1:21520)'),
      findsOneWidget,
    );

    await tester.ensureVisible(find.text('Advanced'));
    await tester.tap(find.text('Advanced'));
    await tester.pumpAndSettle();

    await tester.ensureVisible(find.text('Advanced Backend Settings'));
    await tester.tap(find.text('Advanced Backend Settings'));
    await tester.pumpAndSettle();

    expect(find.widgetWithText(TextField, 'Relay/root host'), findsOneWidget);
    expect(find.text('203.0.113.7'), findsOneWidget);
    expect(find.widgetWithText(TextField, 'Backend HTTP host'), findsOneWidget);
    expect(find.widgetWithText(TextField, 'Backend HTTP port'), findsOneWidget);
    expect(find.text('127.0.0.1'), findsWidgets);
    expect(find.text('21520'), findsOneWidget);
  });

  testWidgets('Settings shows Chinese Relay/root host wording', (tester) async {
    Localization().setLanguage(Language.zh);
    await tester.pumpWidget(const S2PassPreviewApp());
    await tester.pumpAndSettle();

    await tester.tap(find.text('设置').first);
    await tester.pumpAndSettle();

    expect(
      find.widgetWithText(TextField, '默认 Relay/root 主机 (default_relay)'),
      findsOneWidget,
    );
    expect(find.text('用于创建 / 加入房间时的默认 VPS/中继服务器地址。'), findsOneWidget);
  });
}
