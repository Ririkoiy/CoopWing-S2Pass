import 'package:flutter/material.dart';

import '../models/game_profile.dart';
import '../services/localization.dart';
import 'status_chip.dart';

class GameCard extends StatelessWidget {
  const GameCard({
    super.key,
    required this.profile,
    required this.onOpen,
    required this.onLaunch,
    required this.onStop,
    required this.onRunDoctor,
  });

  final GameProfile profile;
  final VoidCallback onOpen;
  final VoidCallback onLaunch;
  final VoidCallback onStop;
  final VoidCallback onRunDoctor;

  @override
  Widget build(BuildContext context) {
    final canLaunch =
        profile.adapterType != AdapterType.diagnosticsOnly &&
        profile.status != ProfileStatus.running;
    final canStop = profile.status == ProfileStatus.running;

    return Card(
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onOpen,
        child: Padding(
          padding: const EdgeInsets.all(18),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Container(
                    width: 42,
                    height: 42,
                    decoration: BoxDecoration(
                      color: Theme.of(context).colorScheme.primaryContainer,
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: const Icon(Icons.sports_esports),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          profile.displayName,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: Theme.of(context).textTheme.titleMedium,
                        ),
                        const SizedBox(height: 6),
                        Wrap(
                          spacing: 8,
                          runSpacing: 8,
                          children: [
                            _ModeBadge(label: Localization().get('adapter_type_${profile.adapterType.backendValue}')),
                            StatusChip.profile(profile.status),
                          ],
                        ),
                      ],
                    ),
                  ),
                ],
              ),
              if (profile.errorMessage != null) ...[
                const SizedBox(height: 12),
                Text(
                  profile.errorMessage!,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              ],
              const Spacer(),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  FilledButton.icon(
                    onPressed: canLaunch ? onLaunch : null,
                    icon: const Icon(Icons.play_arrow),
                    label: Text(Localization().get('launch')),
                  ),
                  OutlinedButton.icon(
                    onPressed: canStop ? onStop : null,
                    icon: const Icon(Icons.stop),
                    label: Text(Localization().get('stop')),
                  ),
                  OutlinedButton.icon(
                    onPressed: onRunDoctor,
                    icon: const Icon(Icons.health_and_safety),
                    label: Text(Localization().get('run_doctor')),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _ModeBadge extends StatelessWidget {
  const _ModeBadge({required this.label});

  final String label;

  @override
  Widget build(BuildContext context) {
    return Chip(
      visualDensity: VisualDensity.compact,
      label: Text(label),
      avatar: const Icon(Icons.extension, size: 16),
    );
  }
}
