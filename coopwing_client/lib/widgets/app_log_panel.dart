import 'package:flutter/material.dart';

import '../models/app_event.dart';
import '../services/localization.dart';

class AppLogPanel extends StatelessWidget {
  const AppLogPanel({super.key, required this.events, this.compact = false});

  final List<AppEvent> events;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final lines = events.reversed
        .take(compact ? 8 : 80)
        .map((event) {
          final time = TimeOfDay.fromDateTime(event.timestamp);
          final hour = time.hour.toString().padLeft(2, '0');
          final minute = time.minute.toString().padLeft(2, '0');
          return '$hour:$minute [${event.level}] ${event.source}: ${event.message}';
        })
        .join('\n');

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(18),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                const Icon(Icons.terminal, size: 18),
                const SizedBox(width: 8),
                Text(
                  compact
                      ? Localization().get('developer_console')
                      : Localization().get('mock_event_log'),
                  style: Theme.of(context).textTheme.titleMedium,
                ),
              ],
            ),
            const SizedBox(height: 12),
            Container(
              height: compact ? 180 : 300,
              padding: const EdgeInsets.all(14),
              decoration: BoxDecoration(
                color: Theme.of(context).colorScheme.surfaceContainerHighest,
                borderRadius: BorderRadius.circular(8),
              ),
              child: SingleChildScrollView(
                child: SelectableText(
                  lines.isEmpty ? Localization().get('no_mock_events') : lines,
                  style: const TextStyle(fontFamily: 'monospace', height: 1.35),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
