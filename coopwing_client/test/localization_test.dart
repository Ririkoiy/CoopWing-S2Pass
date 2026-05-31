import 'dart:ui' as ui;

import 'package:flutter_test/flutter_test.dart';
import 'package:s2pass_flutter_mock/services/localization.dart';

void main() {
  test('detects Chinese and non-Chinese system locales', () {
    final loc = Localization();

    loc.resetForTesting(locale: const ui.Locale('zh', 'CN'));
    expect(loc.language, Language.zh);

    loc.resetForTesting(locale: const ui.Locale('en', 'US'));
    expect(loc.language, Language.en);
  });

  test('manual language selection overrides system detection', () {
    final loc = Localization();

    loc.resetForTesting(locale: const ui.Locale('zh', 'CN'));
    expect(loc.language, Language.zh);

    loc.setLanguage(Language.en);
    loc.useSystemLocale(const ui.Locale('zh', 'CN'));
    expect(loc.language, Language.en);
    expect(loc.manualOverride, isTrue);
  });
}
