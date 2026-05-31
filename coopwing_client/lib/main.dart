import 'package:flutter/material.dart';

import 'app.dart';
import 'services/localization.dart';

void main() {
  Localization().useSystemLocale();
  runApp(const S2PassPreviewApp());
}
