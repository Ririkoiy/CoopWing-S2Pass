import 'package:flutter/material.dart';

import '../models/doctor_report.dart';
import '../models/game_profile.dart';

import '../services/localization.dart';

class StatusChip extends StatelessWidget {
  const StatusChip({
    super.key,
    required this.label,
    required this.color,
    this.icon = Icons.circle,
  });

  factory StatusChip.profile(ProfileStatus status) {
    final color = switch (status) {
      ProfileStatus.ready => Colors.teal,
      ProfileStatus.running => Colors.lightGreen,
      ProfileStatus.error => Colors.deepOrange,
    };
    final label = switch (status) {
      ProfileStatus.ready => Localization().get('ready'),
      ProfileStatus.running => Localization().get('running'),
      ProfileStatus.error => Localization().get('status_failed'),
    };
    return StatusChip(label: label, color: color);
  }

  factory StatusChip.doctor(DoctorStatus status) {
    final color = switch (status) {
      DoctorStatus.idle => Colors.blueGrey,
      DoctorStatus.running => Colors.amber,
      DoctorStatus.completed => Colors.teal,
      DoctorStatus.failed => Colors.deepOrange,
    };
    return StatusChip(
      label: Localization().get('doctor_status_${status.label}'),
      color: color,
    );
  }

  final String label;
  final Color color;
  final IconData icon;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.14),
        border: Border.all(color: color.withValues(alpha: 0.55)),
        borderRadius: BorderRadius.circular(999),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 12, color: color),
          const SizedBox(width: 6),
          Text(
            label,
            style: Theme.of(context).textTheme.labelMedium?.copyWith(
              color: scheme.onSurface,
              fontWeight: FontWeight.w700,
            ),
          ),
        ],
      ),
    );
  }
}
