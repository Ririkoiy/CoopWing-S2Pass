import 'package:flutter/material.dart';

import '../models/backend_health.dart';
import '../services/localization.dart';
import '../widgets/content_column_page.dart';
import '../widgets/status_chip.dart';

class HomeScreen extends StatelessWidget {
  const HomeScreen({
    super.key,
    required this.health,
    required this.onNavigateMyGames,
    required this.onNavigateRoomConnection,
  });

  final BackendHealth? health;
  final VoidCallback onNavigateMyGames;
  final ValueChanged<String?> onNavigateRoomConnection;

  @override
  Widget build(BuildContext context) {
    final loc = Localization();
    final scheme = Theme.of(context).colorScheme;

    return ContentColumnPage(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      loc.get('app_title'),
                      style: Theme.of(context).textTheme.displaySmall,
                    ),
                    const SizedBox(height: 10),
                    Text(
                      loc.get('app_subtitle'),
                      style: Theme.of(context).textTheme.titleMedium?.copyWith(
                        color: scheme.onSurfaceVariant,
                      ),
                    ),
                    const SizedBox(height: 18),
                    Text(
                      loc.get('home_short_warning'),
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: Theme.of(context).textTheme.bodySmall?.copyWith(
                        color: scheme.onSurfaceVariant,
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 18),
              StatusChip(
                label: health == null
                    ? loc.get('loading')
                    : loc.get('status_${health!.status.backendValue}'),
                color: health?.status == BackendConnectionStatus.connected
                    ? Colors.teal
                    : scheme.error,
              ),
            ],
          ),
          const SizedBox(height: 40),
          LayoutBuilder(
            builder: (context, constraints) {
              final myGamesCard = _MyGamesEntryCard(onOpen: onNavigateMyGames);
              final roomCard = _RoomConnectionEntryCard(
                onOpen: () => onNavigateRoomConnection(null),
                onCreateRoom: () => onNavigateRoomConnection('create'),
                onJoinRoom: () => onNavigateRoomConnection('join'),
              );

              if (constraints.maxWidth >= 720) {
                return IntrinsicHeight(
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      Expanded(child: myGamesCard),
                      const SizedBox(width: 22),
                      Expanded(child: roomCard),
                    ],
                  ),
                );
              }

              return Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [myGamesCard, const SizedBox(height: 22), roomCard],
              );
            },
          ),
          const SizedBox(height: 28),
          Align(
            alignment: Alignment.centerRight,
            child: Text(
              loc.get('home_chinese_name_easter_egg'),
              textAlign: TextAlign.right,
              style: Theme.of(context).textTheme.bodySmall?.copyWith(
                color: scheme.onSurfaceVariant.withValues(alpha: 0.62),
                fontStyle: FontStyle.italic,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _MyGamesEntryCard extends StatelessWidget {
  const _MyGamesEntryCard({required this.onOpen});

  final VoidCallback onOpen;

  @override
  Widget build(BuildContext context) {
    final loc = Localization();
    final scheme = Theme.of(context).colorScheme;

    return _EntryCard(
      icon: Icons.sports_esports_outlined,
      title: loc.get('my_games'),
      subtitle: loc.get('preview_udp_game'),
      body: loc.get('relay_preview_local_validation'),
      trailing: FilledButton.icon(
        onPressed: onOpen,
        icon: const Icon(Icons.open_in_new),
        label: Text(loc.get('open')),
      ),
      footer: Wrap(
        spacing: 8,
        runSpacing: 8,
        children: [
          Chip(
            visualDensity: VisualDensity.compact,
            label: Text(loc.get('version')),
            side: BorderSide(color: scheme.outlineVariant),
          ),
          Chip(
            visualDensity: VisualDensity.compact,
            label: Text(loc.get('relay_only')),
            side: BorderSide(color: scheme.outlineVariant),
          ),
        ],
      ),
    );
  }
}

class _RoomConnectionEntryCard extends StatelessWidget {
  const _RoomConnectionEntryCard({
    required this.onOpen,
    required this.onCreateRoom,
    required this.onJoinRoom,
  });

  final VoidCallback onOpen;
  final VoidCallback onCreateRoom;
  final VoidCallback onJoinRoom;

  @override
  Widget build(BuildContext context) {
    final loc = Localization();

    return _EntryCard(
      icon: Icons.meeting_room_outlined,
      title: loc.get('room_connection'),
      subtitle: loc.get('room_entry_subtitle'),
      body: loc.get('room_connection_desc'),
      trailing: FilledButton.icon(
        onPressed: onOpen,
        icon: const Icon(Icons.open_in_new),
        label: Text(loc.get('open')),
      ),
      footer: Wrap(
        spacing: 8,
        runSpacing: 8,
        children: [
          OutlinedButton.icon(
            onPressed: onCreateRoom,
            icon: const Icon(Icons.add_circle_outline),
            label: Text(loc.get('create_room')),
          ),
          OutlinedButton.icon(
            onPressed: onJoinRoom,
            icon: const Icon(Icons.login),
            label: Text(loc.get('join_room')),
          ),
        ],
      ),
    );
  }
}

class _EntryCard extends StatelessWidget {
  const _EntryCard({
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.body,
    required this.trailing,
    required this.footer,
  });

  final IconData icon;
  final String title;
  final String subtitle;
  final String body;
  final Widget trailing;
  final Widget footer;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;

    return Container(
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(
        border: Border.all(color: scheme.outlineVariant),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(icon, color: scheme.primary),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(title, style: Theme.of(context).textTheme.titleMedium),
                    const SizedBox(height: 4),
                    Text(
                      subtitle,
                      style: TextStyle(color: scheme.onSurfaceVariant),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 12),
              trailing,
            ],
          ),
          const SizedBox(height: 12),
          Text(
            body,
            style: TextStyle(fontSize: 13, color: scheme.onSurfaceVariant),
          ),
          const SizedBox(height: 16),
          footer,
        ],
      ),
    );
  }
}
