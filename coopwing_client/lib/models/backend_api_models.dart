class BackendError implements Exception {
  const BackendError({
    required this.code,
    required this.message,
    this.details = const {},
  });

  final String code;
  final String message;
  final Map<String, Object?> details;

  factory BackendError.fromJson(Map<String, Object?> json) {
    return BackendError(
      code: json['code'] as String? ?? 'INTERNAL_ERROR',
      message: json['message'] as String? ?? 'Unexpected backend error.',
      details: _asObjectMap(json['details']) ?? const {},
    );
  }

  Map<String, Object?> toJson() {
    return {'code': code, 'message': message, 'details': details};
  }

  @override
  String toString() => '$code: $message';
}

class HealthStatus {
  const HealthStatus({
    required this.status,
    required this.version,
    required this.uptimeSeconds,
    required this.backend,
    required this.mode,
  });

  final String status;
  final String version;
  final num uptimeSeconds;
  final String backend;
  final String mode;

  bool get isOnline => status == 'ok';

  bool get isFakeMode => mode == 'fake';

  factory HealthStatus.offline() {
    return const HealthStatus(
      status: 'offline',
      version: '',
      uptimeSeconds: 0,
      backend: 's2pass',
      mode: 'unknown',
    );
  }

  factory HealthStatus.fromJson(Map<String, Object?> json) {
    return HealthStatus(
      status: json['status'] as String? ?? 'offline',
      version: json['version'] as String? ?? '',
      uptimeSeconds: json['uptime_seconds'] as num? ?? 0,
      backend: json['backend'] as String? ?? 's2pass',
      mode: json['mode'] as String? ?? 'unknown',
    );
  }

  Map<String, Object?> toJson() {
    return {
      'status': status,
      'version': version,
      'uptime_seconds': uptimeSeconds,
      'backend': backend,
      'mode': mode,
    };
  }
}

class SessionStats {
  const SessionStats({
    required this.packetsFromGame,
    required this.packetsToTransport,
    required this.packetsFromTransport,
    required this.packetsToGame,
    required this.hasError,
  });

  final int packetsFromGame;
  final int packetsToTransport;
  final int packetsFromTransport;
  final int packetsToGame;
  final bool hasError;

  factory SessionStats.empty() {
    return const SessionStats(
      packetsFromGame: 0,
      packetsToTransport: 0,
      packetsFromTransport: 0,
      packetsToGame: 0,
      hasError: false,
    );
  }

  factory SessionStats.fromJson(Map<String, Object?> json) {
    return SessionStats(
      packetsFromGame: json['packets_from_game'] as int? ?? 0,
      packetsToTransport: json['packets_to_transport'] as int? ?? 0,
      packetsFromTransport: json['packets_from_transport'] as int? ?? 0,
      packetsToGame: json['packets_to_game'] as int? ?? 0,
      hasError: json['has_error'] as bool? ?? false,
    );
  }

  Map<String, Object?> toJson() {
    return {
      'packets_from_game': packetsFromGame,
      'packets_to_transport': packetsToTransport,
      'packets_from_transport': packetsFromTransport,
      'packets_to_game': packetsToGame,
      'has_error': hasError,
    };
  }
}

class AdapterConfig {
  const AdapterConfig({
    required this.enabled,
    required this.adapterType,
    required this.bindHost,
    required this.bindPort,
    required this.targetHost,
    this.targetPort,
  });

  final bool enabled;
  final String adapterType;
  final String bindHost;
  final int bindPort;
  final String targetHost;
  final int? targetPort;

  factory AdapterConfig.udpExperimental({
    String bindHost = '127.0.0.1',
    int bindPort = 0,
    String targetHost = '127.0.0.1',
    int? targetPort,
  }) {
    return AdapterConfig(
      enabled: true,
      adapterType: 'local_udp_bridge',
      bindHost: bindHost,
      bindPort: bindPort,
      targetHost: targetHost,
      targetPort: targetPort,
    );
  }

  factory AdapterConfig.tcpForward({
    String bindHost = '127.0.0.1',
    int bindPort = 0,
    String targetHost = '127.0.0.1',
    int? targetPort,
  }) {
    return AdapterConfig(
      enabled: true,
      adapterType: 'tcp_forward',
      bindHost: bindHost,
      bindPort: bindPort,
      targetHost: targetHost,
      targetPort: targetPort,
    );
  }

  factory AdapterConfig.tcpRelay({
    String bindHost = '127.0.0.1',
    int bindPort = 0,
    String targetHost = '127.0.0.1',
    int? targetPort,
  }) {
    return AdapterConfig(
      enabled: true,
      adapterType: 'tcp_relay',
      bindHost: bindHost,
      bindPort: bindPort,
      targetHost: targetHost,
      targetPort: targetPort,
    );
  }

  Map<String, Object?> toJson() {
    return {
      'enabled': enabled,
      'adapter_type': adapterType,
      'bind_host': bindHost,
      'bind_port': bindPort,
      'target_host': targetHost,
      if (targetPort != null) 'target_port': targetPort,
    };
  }
}

class AdapterCounters {
  const AdapterCounters({
    required this.packetsFromGame,
    required this.packetsToTransport,
    required this.packetsFromTransport,
    required this.packetsToGame,
    this.bytesFromGame,
    this.bytesToTransport,
    this.bytesFromTransport,
    this.bytesToGame,
  });

  final int packetsFromGame;
  final int packetsToTransport;
  final int packetsFromTransport;
  final int packetsToGame;
  final int? bytesFromGame;
  final int? bytesToTransport;
  final int? bytesFromTransport;
  final int? bytesToGame;

  bool get hasByteCounters =>
      bytesFromGame != null &&
      bytesToTransport != null &&
      bytesFromTransport != null &&
      bytesToGame != null;

  factory AdapterCounters.empty() {
    return const AdapterCounters(
      packetsFromGame: 0,
      packetsToTransport: 0,
      packetsFromTransport: 0,
      packetsToGame: 0,
    );
  }

  factory AdapterCounters.fromJson(Map<String, Object?> json) {
    return AdapterCounters(
      packetsFromGame: _asInt(json['packets_from_game']),
      packetsToTransport: _asInt(json['packets_to_transport']),
      packetsFromTransport: _asInt(json['packets_from_transport']),
      packetsToGame: _asInt(json['packets_to_game']),
      bytesFromGame: _nullableAsInt(json['bytes_from_game']),
      bytesToTransport: _nullableAsInt(json['bytes_to_transport']),
      bytesFromTransport: _nullableAsInt(json['bytes_from_transport']),
      bytesToGame: _nullableAsInt(json['bytes_to_game']),
    );
  }

  Map<String, Object?> toJson() {
    return {
      'packets_from_game': packetsFromGame,
      'packets_to_transport': packetsToTransport,
      'packets_from_transport': packetsFromTransport,
      'packets_to_game': packetsToGame,
      if (bytesFromGame != null) 'bytes_from_game': bytesFromGame,
      if (bytesToTransport != null) 'bytes_to_transport': bytesToTransport,
      if (bytesFromTransport != null)
        'bytes_from_transport': bytesFromTransport,
      if (bytesToGame != null) 'bytes_to_game': bytesToGame,
    };
  }
}

class AdapterStatusError {
  const AdapterStatusError({required this.code, required this.message});

  final String code;
  final String message;

  factory AdapterStatusError.fromJson(Map<String, Object?> json) {
    return AdapterStatusError(
      code: json['code'] as String? ?? 'ADAPTER_ERROR',
      message: json['message'] as String? ?? '',
    );
  }

  Map<String, Object?> toJson() {
    return {'code': code, 'message': message};
  }
}

class AdapterStatus {
  const AdapterStatus({
    required this.enabled,
    required this.status,
    this.adapterType,
    this.bindHost,
    this.bindPort,
    this.targetHost,
    this.targetPort,
    this.counters,
    this.error,
  });

  final bool enabled;
  final String status;
  final String? adapterType;
  final String? bindHost;
  final int? bindPort;
  final String? targetHost;
  final int? targetPort;
  final AdapterCounters? counters;
  final AdapterStatusError? error;

  factory AdapterStatus.fromJson(Map<String, Object?> json) {
    final countersJson = _asObjectMap(json['counters']);
    final errorJson = _asObjectMap(json['error']);
    return AdapterStatus(
      enabled: json['enabled'] as bool? ?? false,
      status: json['status'] as String? ?? 'unknown',
      adapterType: json['adapter_type'] as String?,
      bindHost: json['bind_host'] as String?,
      bindPort: _nullableAsInt(json['bind_port']),
      targetHost: json['target_host'] as String?,
      targetPort: _nullableAsInt(json['target_port']),
      counters: countersJson == null
          ? null
          : AdapterCounters.fromJson(countersJson),
      error: errorJson == null ? null : AdapterStatusError.fromJson(errorJson),
    );
  }

  Map<String, Object?> toJson() {
    return {
      'enabled': enabled,
      'status': status,
      if (adapterType != null) 'adapter_type': adapterType,
      if (bindHost != null) 'bind_host': bindHost,
      if (bindPort != null) 'bind_port': bindPort,
      if (targetHost != null) 'target_host': targetHost,
      if (targetPort != null) 'target_port': targetPort,
      if (counters != null) 'counters': counters!.toJson(),
      if (error != null) 'error': error!.toJson(),
    };
  }
}

class SessionInfo {
  const SessionInfo({
    required this.sessionId,
    required this.role,
    required this.status,
    required this.roomId,
    required this.playerName,
    required this.serverHost,
    required this.serverPort,
    required this.serverUdpPort,
    required this.adapterHost,
    required this.adapterPort,
    required this.gameServerHost,
    this.gameServerPort,
    required this.forceRelay,
    required this.createdAt,
    required this.updatedAt,
    required this.stats,
    this.adapterStatus,
    this.error,
  });

  final String sessionId;
  final String role;
  final String status;
  final String? roomId;
  final String playerName;
  final String serverHost;
  final int serverPort;
  final int serverUdpPort;
  final String adapterHost;
  final int adapterPort;
  final String gameServerHost;
  final int? gameServerPort;
  final bool forceRelay;
  final num createdAt;
  final num updatedAt;
  final SessionStats stats;
  final AdapterStatus? adapterStatus;
  final BackendError? error;

  factory SessionInfo.fromJson(Map<String, Object?> json) {
    final statsJson = _asObjectMap(json['stats']);
    final errorJson = _asObjectMap(json['error']);
    final adapterStatusJson = _asObjectMap(json['adapter_status']);
    return SessionInfo(
      sessionId: json['session_id'] as String? ?? '',
      role: json['role'] as String? ?? '',
      status: json['status'] as String? ?? '',
      roomId: json['room_id'] as String?,
      playerName: json['player_name'] as String? ?? '',
      serverHost: json['server_host'] as String? ?? '',
      serverPort: json['server_port'] as int? ?? 0,
      serverUdpPort: json['server_udp_port'] as int? ?? 0,
      adapterHost: json['adapter_host'] as String? ?? '127.0.0.1',
      adapterPort: json['adapter_port'] as int? ?? 0,
      gameServerHost: json['game_server_host'] as String? ?? '127.0.0.1',
      gameServerPort: _nullableAsInt(json['game_server_port']),
      forceRelay: json['force_relay'] as bool? ?? true,
      createdAt: json['created_at'] as num? ?? 0,
      updatedAt: json['updated_at'] as num? ?? 0,
      stats: statsJson == null
          ? SessionStats.empty()
          : SessionStats.fromJson(statsJson),
      adapterStatus: adapterStatusJson == null
          ? null
          : AdapterStatus.fromJson(adapterStatusJson),
      error: errorJson == null ? null : BackendError.fromJson(errorJson),
    );
  }

  Map<String, Object?> toJson() {
    return {
      'session_id': sessionId,
      'role': role,
      'status': status,
      'room_id': roomId,
      'player_name': playerName,
      'server_host': serverHost,
      'server_port': serverPort,
      'server_udp_port': serverUdpPort,
      'adapter_host': adapterHost,
      'adapter_port': adapterPort,
      'game_server_host': gameServerHost,
      if (gameServerPort != null) 'game_server_port': gameServerPort,
      'force_relay': forceRelay,
      'created_at': createdAt,
      'updated_at': updatedAt,
      'stats': stats.toJson(),
      if (adapterStatus != null) 'adapter_status': adapterStatus!.toJson(),
      if (error != null) 'error': error!.toJson(),
    };
  }
}

class SessionEvent {
  const SessionEvent({
    required this.type,
    required this.message,
    required this.timestamp,
    required this.data,
  });

  final String type;
  final String message;
  final num timestamp;
  final Map<String, Object?> data;

  factory SessionEvent.fromJson(Map<String, Object?> json) {
    return SessionEvent(
      type: json['type'] as String? ?? 'event',
      message: json['message'] as String? ?? '',
      timestamp: json['timestamp'] as num? ?? 0,
      data: _asObjectMap(json['data']) ?? const {},
    );
  }

  Map<String, Object?> toJson() {
    return {
      'type': type,
      'message': message,
      'timestamp': timestamp,
      'data': data,
    };
  }
}

Map<String, Object?>? _asObjectMap(Object? value) {
  if (value is Map) {
    return value.map((key, item) => MapEntry(key.toString(), item));
  }
  return null;
}

int _asInt(Object? value) => _nullableAsInt(value) ?? 0;

int? _nullableAsInt(Object? value) {
  if (value is int) return value;
  if (value is num) return value.toInt();
  return null;
}
