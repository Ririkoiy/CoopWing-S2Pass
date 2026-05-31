import 'package:flutter/material.dart';

import '../models/doctor_report.dart';
import '../models/game_profile.dart';
import '../services/localization.dart';
import '../widgets/content_column_page.dart';
import '../widgets/status_chip.dart';

class MyGamesScreen extends StatelessWidget {
  const MyGamesScreen({
    super.key,
    required this.profiles,
    required this.onAddGame,
    required this.onOpenProfile,
    required this.onLaunch,
    required this.onStop,
    required this.onRunDoctor,
    required this.onNavigateRoomConnection,
  });

  final List<GameProfile> profiles;
  final VoidCallback onAddGame;
  final ValueChanged<GameProfile> onOpenProfile;
  final Future<void> Function(String profileId) onLaunch;
  final Future<void> Function(String profileId) onStop;
  final Future<DoctorReport> Function(String profileId) onRunDoctor;
  final ValueChanged<String?> onNavigateRoomConnection;

  @override
  Widget build(BuildContext context) {
    final loc = Localization();
    final profile = profiles.isEmpty ? null : profiles.first;

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
                      loc.get('my_games'),
                      style: Theme.of(context).textTheme.headlineMedium,
                    ),
                    const SizedBox(height: 8),
                    Text(
                      loc.get('my_games_desc'),
                      style: TextStyle(
                        color: Theme.of(context).colorScheme.onSurfaceVariant,
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 18),
              FilledButton.tonalIcon(
                onPressed: onAddGame,
                icon: const Icon(Icons.add),
                label: Text(loc.get('add_game')),
              ),
            ],
          ),
          const SizedBox(height: 18),
          _PreviewGameCard(
            profile: profile,
            onOpenProfile: profile == null
                ? null
                : () => onOpenProfile(profile),
            onLaunch: profile == null
                ? null
                : () => onLaunch(profile.profileId),
            onStop: profile == null ? null : () => onStop(profile.profileId),
            onRunDoctor: profile == null
                ? null
                : () => onRunDoctor(profile.profileId),
            onNavigateRoomConnection: () => onNavigateRoomConnection(null),
          ),
        ],
      ),
    );
  }
}

class _PreviewGameCard extends StatefulWidget {
  const _PreviewGameCard({
    required this.profile,
    required this.onOpenProfile,
    required this.onLaunch,
    required this.onStop,
    required this.onRunDoctor,
    required this.onNavigateRoomConnection,
  });

  final GameProfile? profile;
  final VoidCallback? onOpenProfile;
  final VoidCallback? onLaunch;
  final VoidCallback? onStop;
  final VoidCallback? onRunDoctor;
  final VoidCallback onNavigateRoomConnection;

  @override
  State<_PreviewGameCard> createState() => _PreviewGameCardState();
}

class _PreviewGameCardState extends State<_PreviewGameCard> {
  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final loc = Localization();
    final profile = widget.profile;
    final displayName = profile?.displayName ?? loc.get('preview_udp_game');
    final canLaunch =
        profile != null &&
        profile.adapterType != AdapterType.diagnosticsOnly &&
        profile.status != ProfileStatus.running;
    final canStop = profile?.status == ProfileStatus.running;

    return Container(
      decoration: BoxDecoration(
        border: Border.all(color: scheme.outlineVariant),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Material(
        color: Colors.transparent,
        child: Column(
          children: [
            ListTile(
              contentPadding: const EdgeInsets.symmetric(
                horizontal: 18,
                vertical: 10,
              ),
              leading: Container(
                width: 42,
                height: 42,
                decoration: BoxDecoration(
                  color: scheme.primaryContainer,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: const Icon(Icons.sports_esports),
              ),
              title: Text(
                displayName,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
              subtitle: Wrap(
                spacing: 6,
                runSpacing: 4,
                children: [
                  Chip(
                    visualDensity: VisualDensity.compact,
                    label: Text(
                      profile == null
                          ? loc.get('relay_preview_local_validation')
                          : loc.get(
                              'adapter_type_${profile.adapterType.backendValue}',
                            ),
                    ),
                  ),
                  if (profile != null) StatusChip.profile(profile.status),
                ],
              ),
              trailing: IconButton(
                tooltip: _expanded ? 'Collapse' : 'Expand',
                onPressed: () => setState(() => _expanded = !_expanded),
                icon: Icon(_expanded ? Icons.expand_less : Icons.expand_more),
              ),
              onTap: () => setState(() => _expanded = !_expanded),
            ),
            if (_expanded) ...[
              const Divider(height: 1),
              Padding(
                padding: const EdgeInsets.all(18),
                child: Align(
                  alignment: Alignment.centerLeft,
                  child: Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: [
                      FilledButton.icon(
                        onPressed: canLaunch ? widget.onLaunch : null,
                        icon: const Icon(Icons.play_arrow),
                        label: Text(loc.get('launch')),
                      ),
                      OutlinedButton.icon(
                        onPressed: canStop ? widget.onStop : null,
                        icon: const Icon(Icons.stop),
                        label: Text(loc.get('stop')),
                      ),
                      OutlinedButton.icon(
                        onPressed: widget.onRunDoctor,
                        icon: const Icon(Icons.health_and_safety),
                        label: Text(loc.get('run_doctor')),
                      ),
                      OutlinedButton.icon(
                        onPressed: widget.onNavigateRoomConnection,
                        icon: const Icon(Icons.meeting_room_outlined),
                        label: Text(loc.get('room_connection')),
                      ),
                      if (widget.onOpenProfile != null)
                        IconButton.outlined(
                          tooltip: 'Open profile details',
                          onPressed: widget.onOpenProfile,
                          icon: const Icon(Icons.tune),
                        ),
                    ],
                  ),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
