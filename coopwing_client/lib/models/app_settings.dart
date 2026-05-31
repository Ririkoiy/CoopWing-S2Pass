class AppSettings {
  const AppSettings({
    required this.defaultServerId,
    required this.backendApiPort,
    required this.logLevel,
    required this.developerMode,
    required this.theme,
  });

  final String defaultServerId;
  final int backendApiPort;
  final String logLevel;
  final bool developerMode;
  final String theme;

  factory AppSettings.fromJson(Map<String, Object?> json) {
    return AppSettings(
      defaultServerId: json['default_server_id'] as String? ?? '',
      backendApiPort: json['backend_api_port'] as int? ?? 21520,
      logLevel: json['log_level'] as String? ?? 'INFO',
      developerMode: json['developer_mode'] as bool? ?? false,
      theme: json['theme'] as String? ?? 'dark',
    );
  }

  Map<String, Object?> toJson() {
    return {
      'default_server_id': defaultServerId,
      'backend_api_port': backendApiPort,
      'log_level': logLevel,
      'developer_mode': developerMode,
      'theme': theme,
    };
  }

  AppSettings copyWith({
    String? defaultServerId,
    int? backendApiPort,
    String? logLevel,
    bool? developerMode,
    String? theme,
  }) {
    return AppSettings(
      defaultServerId: defaultServerId ?? this.defaultServerId,
      backendApiPort: backendApiPort ?? this.backendApiPort,
      logLevel: logLevel ?? this.logLevel,
      developerMode: developerMode ?? this.developerMode,
      theme: theme ?? this.theme,
    );
  }
}
