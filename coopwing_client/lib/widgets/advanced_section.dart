import 'package:flutter/material.dart';

import '../models/game_profile.dart';
import '../services/localization.dart';

class AdvancedSection extends StatelessWidget {
  const AdvancedSection({
    super.key,
    required this.profile,
    required this.developerMode,
  });

  final GameProfile profile;
  final bool developerMode;

  @override
  Widget build(BuildContext context) {
    return ExpansionTile(
      initiallyExpanded: false,
      leading: const Icon(Icons.tune),
      title: Text(Localization().get('advanced_settings')),
      subtitle: Text(
        Localization().get('hidden_by_default'),
      ),
      childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
      children: [
        if (profile.adapterType == AdapterType.genericUdpForward)
          _WarningBanner(
            text: Localization().get('exp_warning_text'),
          ),
        if (!developerMode)
          _WarningBanner(
            text: Localization().get('dev_mode_off_warning'),
          ),
        const SizedBox(height: 8),
        _FieldRow(label: 'local_bind_host', value: profile.localBindHost),
        _FieldRow(
          label: 'local_bind_port',
          value: profile.localBindPort?.toString() ?? 'auto',
        ),
        _FieldRow(label: 'remote_target_host', value: profile.remoteTargetHost),
        _FieldRow(
          label: 'remote_target_port',
          value: profile.remoteTargetPort?.toString() ?? '',
        ),
        _FieldRow(
          label: 'adapter_type',
          value: profile.adapterType.backendValue,
        ),
        _FieldRow(label: 'protocol', value: profile.protocol),
        _FieldRow(label: 'launch_args', value: profile.launchArgs),
        _FieldRow(
          label: 'expected_ports',
          value: profile.expectedPorts.join(', '),
        ),
      ],
    );
  }
}

class _FieldRow extends StatelessWidget {
  const _FieldRow({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        children: [
          SizedBox(
            width: 180,
            child: Text(label, style: Theme.of(context).textTheme.labelLarge),
          ),
          Expanded(
            child: SelectableText(
              value.isEmpty ? '(empty)' : value,
              maxLines: 1,
              style: const TextStyle(fontFamily: 'monospace'),
            ),
          ),
        ],
      ),
    );
  }
}

class _WarningBanner extends StatelessWidget {
  const _WarningBanner({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      width: double.infinity,
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: scheme.errorContainer.withValues(alpha: 0.32),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: scheme.error.withValues(alpha: 0.35)),
      ),
      child: Text(text),
    );
  }
}
