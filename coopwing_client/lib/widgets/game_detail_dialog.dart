import 'package:flutter/material.dart';

import '../models/game_profile.dart';
import '../services/backend_client.dart';
import '../services/localization.dart';
import 'advanced_section.dart';
import 'status_chip.dart';

class GameDetailDialog extends StatefulWidget {
  const GameDetailDialog({
    super.key,
    required this.client,
    required this.profile,
    required this.developerMode,
  });

  final BackendClient client;
  final GameProfile profile;
  final bool developerMode;

  @override
  State<GameDetailDialog> createState() => _GameDetailDialogState();
}

class _GameDetailDialogState extends State<GameDetailDialog> {
  late GameProfile _profile = widget.profile;
  bool _busy = false;

  Future<void> _saveMode(AdapterType? mode) async {
    if (mode == null) {
      return;
    }
    setState(() => _busy = true);
    final saved = await widget.client.saveProfile(
      _profile.copyWith(adapterType: mode),
    );
    setState(() {
      _profile = saved;
      _busy = false;
    });
  }

  Future<void> _launch() async {
    setState(() => _busy = true);
    await widget.client.launchGame(_profile.profileId);
    await _refreshProfile();
  }

  Future<void> _stop() async {
    setState(() => _busy = true);
    await widget.client.stopGame(_profile.profileId);
    await _refreshProfile();
  }

  Future<void> _runDoctor() async {
    setState(() => _busy = true);
    await widget.client.runDoctor();
    await _refreshProfile();
  }

  Future<void> _delete() async {
    setState(() => _busy = true);
    await widget.client.deleteProfile(_profile.profileId);
    if (mounted) {
      Navigator.of(context).pop(true);
    }
  }

  Future<void> _refreshProfile() async {
    final profiles = await widget.client.getProfiles();
    if (!mounted) {
      return;
    }
    setState(() {
      _profile = profiles.firstWhere(
        (profile) => profile.profileId == _profile.profileId,
        orElse: () => _profile,
      );
      _busy = false;
    });
  }

  @override
  Widget build(BuildContext context) {
    final canLaunch =
        _profile.adapterType != AdapterType.diagnosticsOnly &&
        _profile.status != ProfileStatus.running;
    final canStop = _profile.status == ProfileStatus.running;
    final modes = [
      AdapterType.launchOnly,
      AdapterType.diagnosticsOnly,
      if (widget.developerMode) AdapterType.genericUdpForward,
    ];

    return Dialog(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 780),
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: SingleChildScrollView(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              mainAxisSize: MainAxisSize.min,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        _profile.displayName,
                        style: Theme.of(context).textTheme.headlineSmall,
                      ),
                    ),
                    StatusChip.profile(_profile.status),
                    const SizedBox(width: 8),
                    IconButton(
                      tooltip: Localization().get('close'),
                      onPressed: () => Navigator.of(context).pop(true),
                      icon: const Icon(Icons.close),
                    ),
                  ],
                ),
                const SizedBox(height: 12),
                Tooltip(
                  message: _profile.exePath,
                  child: Text(
                    _profile.exePath.isEmpty
                        ? Localization().get('no_exe_path')
                        : _profile.exePath,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(fontFamily: 'monospace'),
                  ),
                ),
                const SizedBox(height: 18),
                DropdownButtonFormField<AdapterType>(
                  key: ValueKey(_profile.adapterType),
                  initialValue: modes.contains(_profile.adapterType)
                      ? _profile.adapterType
                      : AdapterType.launchOnly,
                  decoration: InputDecoration(
                    labelText: Localization().get('mode'),
                    border: const OutlineInputBorder(),
                  ),
                  items: modes
                      .map(
                        (mode) => DropdownMenuItem(
                          value: mode,
                          child: Text(Localization().get('adapter_type_${mode.backendValue}')),
                        ),
                      )
                      .toList(),
                  onChanged: _busy ? null : _saveMode,
                ),
                if (_profile.adapterType == AdapterType.genericUdpForward) ...[
                  const SizedBox(height: 12),
                  _InlineWarning(
                    text: Localization().get('exp_warning_text'),
                  ),
                ],
                const SizedBox(height: 18),
                Wrap(
                  spacing: 10,
                  runSpacing: 10,
                  children: [
                    FilledButton.icon(
                      onPressed: _busy || !canLaunch ? null : _launch,
                      icon: const Icon(Icons.play_arrow),
                      label: Text(Localization().get('launch')),
                    ),
                    OutlinedButton.icon(
                      onPressed: _busy || !canStop ? null : _stop,
                      icon: const Icon(Icons.stop),
                      label: Text(Localization().get('stop')),
                    ),
                    OutlinedButton.icon(
                      onPressed: _busy ? null : _runDoctor,
                      icon: const Icon(Icons.health_and_safety),
                      label: Text(Localization().get('run_doctor')),
                    ),
                    TextButton.icon(
                      onPressed: _busy ? null : _delete,
                      icon: const Icon(Icons.delete_outline),
                      label: Text(Localization().get('delete')),
                    ),
                  ],
                ),
                if (_profile.errorMessage != null) ...[
                  const SizedBox(height: 12),
                  Text(
                    _profile.errorMessage!,
                    style: TextStyle(
                      color: Theme.of(context).colorScheme.error,
                    ),
                  ),
                ],
                const SizedBox(height: 16),
                AdvancedSection(
                  profile: _profile,
                  developerMode: widget.developerMode,
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _InlineWarning extends StatelessWidget {
  const _InlineWarning({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: scheme.errorContainer.withValues(alpha: 0.35),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Text(text),
    );
  }
}
