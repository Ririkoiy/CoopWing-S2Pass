class AppEvent {
  const AppEvent({
    required this.event,
    required this.timestamp,
    required this.payload,
  });

  final String event;
  final DateTime timestamp;
  final Map<String, Object?> payload;

  factory AppEvent.fromJson(Map<String, Object?> json) {
    return AppEvent(
      event: json['event'] as String? ?? 'log',
      timestamp:
          DateTime.tryParse(json['timestamp'] as String? ?? '') ??
          DateTime.fromMillisecondsSinceEpoch(0),
      payload: json['payload'] as Map<String, Object?>? ?? const {},
    );
  }

  Map<String, Object?> toJson() {
    return {
      'event': event,
      'timestamp': timestamp.toIso8601String(),
      'payload': payload,
    };
  }

  String get source => payload['source'] as String? ?? 'Backend';

  String get level => payload['level'] as String? ?? 'INFO';

  String get message => payload['message'] as String? ?? event;
}

class LogEvent {
  const LogEvent({
    required this.timestamp,
    required this.source,
    required this.level,
    required this.message,
  });

  final DateTime timestamp;
  final String source;
  final String level;
  final String message;

  factory LogEvent.fromJson(Map<String, Object?> json) {
    return LogEvent(
      timestamp:
          DateTime.tryParse(json['timestamp'] as String? ?? '') ??
          DateTime.fromMillisecondsSinceEpoch(0),
      source: json['source'] as String? ?? 'Backend',
      level: json['level'] as String? ?? 'INFO',
      message: json['message'] as String? ?? '',
    );
  }

  Map<String, Object?> toJson() {
    return {
      'timestamp': timestamp.toIso8601String(),
      'source': source,
      'level': level,
      'message': message,
    };
  }
}
