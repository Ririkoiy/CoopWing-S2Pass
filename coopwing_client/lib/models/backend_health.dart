enum BackendConnectionStatus {
  idle('idle'),
  starting('starting'),
  connected('connected'),
  reconnecting('reconnecting'),
  relayFallback('relay_fallback'),
  disconnected('disconnected'),
  failed('failed'),
  stopped('stopped');

  const BackendConnectionStatus(this.backendValue);

  final String backendValue;
}

class BackendHealth {
  const BackendHealth({
    required this.version,
    required this.uptimeSeconds,
    required this.status,
  });

  final String version;
  final int uptimeSeconds;
  final BackendConnectionStatus status;

  factory BackendHealth.fromJson(Map<String, Object?> json) {
    final statusValue = json['status'] as String? ?? 'idle';
    return BackendHealth(
      version: json['version'] as String? ?? '',
      uptimeSeconds: json['uptime_seconds'] as int? ?? 0,
      status: BackendConnectionStatus.values.firstWhere(
        (status) => status.backendValue == statusValue,
        orElse: () => BackendConnectionStatus.idle,
      ),
    );
  }

  Map<String, Object?> toJson() {
    return {
      'version': version,
      'uptime_seconds': uptimeSeconds,
      'status': status.backendValue,
    };
  }

  String get statusLabel {
    return switch (status) {
      BackendConnectionStatus.idle => 'Idle',
      BackendConnectionStatus.starting => 'Starting',
      BackendConnectionStatus.connected => 'Mock Connected',
      BackendConnectionStatus.reconnecting => 'Reconnecting',
      BackendConnectionStatus.relayFallback => 'Relay Fallback',
      BackendConnectionStatus.disconnected => 'Disconnected',
      BackendConnectionStatus.failed => 'Failed',
      BackendConnectionStatus.stopped => 'Stopped',
    };
  }
}
