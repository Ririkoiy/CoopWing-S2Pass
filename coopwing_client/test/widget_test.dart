import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:s2pass_flutter_mock/app.dart';
import 'package:s2pass_flutter_mock/services/localization.dart';

void main() {
  testWidgets('Preview 0.2 shell renders dashboard', (tester) async {
    Localization().setLanguage(Language.en);
    await tester.pumpWidget(const S2PassPreviewApp());
    await tester.pumpAndSettle();

    expect(find.text('Co-opWinG'), findsWidgets);
    expect(
      find.text('Developer Preview v0.1 / Generic UDP Relay Technical Preview'),
      findsWidgets,
    );
    expect(find.textContaining('Version:'), findsNothing);
    expect(find.byType(Image), findsWidgets);
    expect(find.text('中文名可能是合翼卫？ 卫什么啊，何意味啊。'), findsOneWidget);
    expect(find.text('My Games'), findsWidgets);
    expect(find.text('Network Doctor'), findsNothing);
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
