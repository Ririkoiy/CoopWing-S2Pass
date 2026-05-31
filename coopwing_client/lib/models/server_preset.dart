class ServerPreset {
  const ServerPreset({
    required this.serverId,
    required this.displayName,
    required this.host,
    required this.description,
    required this.enabled,
  });

  final String serverId;
  final String displayName;
  final String host;
  final String description;
  final bool enabled;

  factory ServerPreset.fromJson(Map<String, Object?> json) {
    return ServerPreset(
      serverId: json['server_id'] as String? ?? '',
      displayName: json['display_name'] as String? ?? '',
      host: json['host'] as String? ?? '',
      description: json['description'] as String? ?? '',
      enabled: json['enabled'] as bool? ?? true,
    );
  }

  Map<String, Object?> toJson() {
    return {
      'server_id': serverId,
      'display_name': displayName,
      'host': host,
      'description': description,
      'enabled': enabled,
    };
  }

  ServerPreset copyWith({
    String? serverId,
    String? displayName,
    String? host,
    String? description,
    bool? enabled,
  }) {
    return ServerPreset(
      serverId: serverId ?? this.serverId,
      displayName: displayName ?? this.displayName,
      host: host ?? this.host,
      description: description ?? this.description,
      enabled: enabled ?? this.enabled,
    );
  }
}
