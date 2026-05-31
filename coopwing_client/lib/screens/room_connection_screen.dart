import 'package:flutter/material.dart';

import '../models/doctor_report.dart';
import '../services/backend_client.dart';
import '../services/localization.dart';
import '../widgets/backend_bridge_panel.dart';
import '../widgets/content_column_page.dart';

class RoomConnectionScreen extends StatelessWidget {
  const RoomConnectionScreen({
    super.key,
    required this.backendClient,
    required this.defaultServerHost,
    required this.backendApiPort,
    required this.onRunDiagnostics,
    this.initialMode,
  });

  final BackendClient backendClient;
  final String defaultServerHost;
  final int backendApiPort;
  final Future<DoctorReport> Function() onRunDiagnostics;
  final String? initialMode;

  @override
  Widget build(BuildContext context) {
    final loc = Localization();
    return ContentColumnPage(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            loc.get('room_connection'),
            style: Theme.of(context).textTheme.headlineMedium,
          ),
          const SizedBox(height: 6),
          Text(
            loc.get('room_connection_desc'),
            style: TextStyle(
              color: Theme.of(context).colorScheme.onSurfaceVariant,
            ),
          ),
          const SizedBox(height: 18),
          BackendBridgePanel(
            client: backendClient,
            defaultServerHost: defaultServerHost,
            backendApiPort: backendApiPort,
            onRunDiagnostics: onRunDiagnostics,
            initialMode: initialMode,
          ),
        ],
      ),
    );
  }
}
