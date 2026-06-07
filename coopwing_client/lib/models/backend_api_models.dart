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
    this.backendAdmin = false,
  });

  final String status;
  final String version;
  final num uptimeSeconds;
  final String backend;
  final String mode;
  final bool backendAdmin;

  bool get isOnline => status == 'ok';

  bool get isFakeMode => mode == 'fake';

  factory HealthStatus.offline() {
    return const HealthStatus(
      status: 'offline',
      version: '',
      uptimeSeconds: 0,
      backend: 's2pass',
      mode: 'unknown',
      backendAdmin: false,
    );
  }

  factory HealthStatus.fromJson(Map<String, Object?> json) {
    return HealthStatus(
      status: json['status'] as String? ?? 'offline',
      version: json['version'] as String? ?? '',
      uptimeSeconds: json['uptime_seconds'] as num? ?? 0,
      backend: json['backend'] as String? ?? 's2pass',
      mode: json['mode'] as String? ?? 'unknown',
      backendAdmin: _asBool(json['backend_admin']),
    );
  }

  Map<String, Object?> toJson() {
    return {
      'status': status,
      'version': version,
      'uptime_seconds': uptimeSeconds,
      'backend': backend,
      'mode': mode,
      'backend_admin': backendAdmin,
    };
  }
}

class SecondaryIpRequestConfig {
  const SecondaryIpRequestConfig({
    required this.ipAddress,
    this.interfaceHint,
    this.prefixLength,
  });

  final String ipAddress;
  final String? interfaceHint;
  final int? prefixLength;

  Map<String, Object?> toJson() {
    return {
      'ip_address': ipAddress,
      if (interfaceHint != null && interfaceHint!.isNotEmpty)
        'interface_hint': interfaceHint,
      if (prefixLength != null) 'prefix_length': prefixLength,
    };
  }
}

class SecondaryIpRecommendation {
  const SecondaryIpRecommendation({
    required this.available,
    required this.backendAdmin,
    this.interfaceIndex,
    this.interfaceAlias,
    this.interfaceDescription,
    this.interfaceIp,
    this.prefixLength,
    this.recommendedIp,
    this.reason,
    this.warning,
  });

  final bool available;
  final bool backendAdmin;
  final int? interfaceIndex;
  final String? interfaceAlias;
  final String? interfaceDescription;
  final String? interfaceIp;
  final int? prefixLength;
  final String? recommendedIp;
  final String? reason;
  final String? warning;

  factory SecondaryIpRecommendation.unavailable({
    bool backendAdmin = false,
    String? reason,
    String? warning,
  }) {
    return SecondaryIpRecommendation(
      available: false,
      backendAdmin: backendAdmin,
      reason: reason,
      warning: warning,
    );
  }

  factory SecondaryIpRecommendation.fromJson(Map<String, Object?> json) {
    return SecondaryIpRecommendation(
      available: _asBool(json['available']),
      backendAdmin: _asBool(json['backend_admin']),
      interfaceIndex: _nullableAsInt(json['interface_index']),
      interfaceAlias: _nullableAsString(json['interface_alias']),
      interfaceDescription: _nullableAsString(json['interface_description']),
      interfaceIp: _nullableAsString(json['interface_ip']),
      prefixLength: _nullableAsInt(json['prefix_length']),
      recommendedIp: _nullableAsString(json['recommended_ip']),
      reason: _nullableAsString(json['reason']),
      warning: _nullableAsString(json['warning']),
    );
  }

  Map<String, Object?> toJson() {
    return {
      'available': available,
      'backend_admin': backendAdmin,
      if (interfaceIndex != null) 'interface_index': interfaceIndex,
      if (interfaceAlias != null) 'interface_alias': interfaceAlias,
      if (interfaceDescription != null)
        'interface_description': interfaceDescription,
      if (interfaceIp != null) 'interface_ip': interfaceIp,
      if (prefixLength != null) 'prefix_length': prefixLength,
      if (recommendedIp != null) 'recommended_ip': recommendedIp,
      if (reason != null) 'reason': reason,
      if (warning != null) 'warning': warning,
    };
  }
}

class ProcessPortCandidate {
  const ProcessPortCandidate({
    required this.pid,
    required this.protocol,
    required this.localAddress,
    required this.localPort,
    this.remoteAddress,
    this.remotePort,
    this.state,
    required this.confidence,
    required this.reason,
  });

  final int pid;
  final String protocol;
  final String localAddress;
  final int localPort;
  final String? remoteAddress;
  final int? remotePort;
  final String? state;
  final String confidence;
  final String reason;

  factory ProcessPortCandidate.fromJson(Map<String, Object?> json) {
    return ProcessPortCandidate(
      pid: _asInt(json['pid']),
      protocol: _nullableAsString(json['protocol']) ?? '',
      localAddress: _nullableAsString(json['local_address']) ?? '',
      localPort: _asInt(json['local_port']),
      remoteAddress: _nullableAsString(json['remote_address']),
      remotePort: _nullableAsInt(json['remote_port']),
      state: _nullableAsString(json['state']),
      confidence: _nullableAsString(json['confidence']) ?? 'low',
      reason: _nullableAsString(json['reason']) ?? '',
    );
  }

  Map<String, Object?> toJson() {
    return {
      'pid': pid,
      'protocol': protocol,
      'local_address': localAddress,
      'local_port': localPort,
      if (remoteAddress != null) 'remote_address': remoteAddress,
      if (remotePort != null) 'remote_port': remotePort,
      if (state != null) 'state': state,
      'confidence': confidence,
      'reason': reason,
    };
  }
}

class ProcessPortScanResult {
  const ProcessPortScanResult({required this.pid, required this.candidates});

  final int pid;
  final List<ProcessPortCandidate> candidates;

  factory ProcessPortScanResult.fromJson(Map<String, Object?> json) {
    final rawCandidates = json['candidates'];
    return ProcessPortScanResult(
      pid: _asInt(json['pid']),
      candidates: rawCandidates is List
          ? rawCandidates
                .whereType<Map>()
                .map(
                  (item) => ProcessPortCandidate.fromJson(
                    _asObjectMap(item) ?? const {},
                  ),
                )
                .toList(growable: false)
          : const [],
    );
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
    this.secondaryIpRequest,
  });

  final bool enabled;
  final String adapterType;
  final String bindHost;
  final int bindPort;
  final String targetHost;
  final int? targetPort;
  final SecondaryIpRequestConfig? secondaryIpRequest;

  factory AdapterConfig.bundle({
    String bindHost = '127.0.0.1',
    int bindPort = 0,
    String targetHost = '127.0.0.1',
    required int targetPort,
    SecondaryIpRequestConfig? secondaryIpRequest,
  }) {
    return AdapterConfig(
      enabled: true,
      adapterType: 'bundle',
      bindHost: bindHost,
      bindPort: bindPort,
      targetHost: targetHost,
      targetPort: targetPort,
      secondaryIpRequest: secondaryIpRequest,
    );
  }

  factory AdapterConfig.udpExperimental({
    String bindHost = '127.0.0.1',
    int bindPort = 0,
    String targetHost = '127.0.0.1',
    int? targetPort,
    SecondaryIpRequestConfig? secondaryIpRequest,
  }) {
    return AdapterConfig(
      enabled: true,
      adapterType: 'local_udp_bridge',
      bindHost: bindHost,
      bindPort: bindPort,
      targetHost: targetHost,
      targetPort: targetPort,
      secondaryIpRequest: secondaryIpRequest,
    );
  }

  factory AdapterConfig.tcpForward({
    String bindHost = '127.0.0.1',
    int bindPort = 0,
    String targetHost = '127.0.0.1',
    int? targetPort,
    SecondaryIpRequestConfig? secondaryIpRequest,
  }) {
    return AdapterConfig(
      enabled: true,
      adapterType: 'tcp_forward',
      bindHost: bindHost,
      bindPort: bindPort,
      targetHost: targetHost,
      targetPort: targetPort,
      secondaryIpRequest: secondaryIpRequest,
    );
  }

  factory AdapterConfig.tcpRelay({
    String bindHost = '127.0.0.1',
    int bindPort = 0,
    String targetHost = '127.0.0.1',
    int? targetPort,
    SecondaryIpRequestConfig? secondaryIpRequest,
  }) {
    return AdapterConfig(
      enabled: true,
      adapterType: 'tcp_relay',
      bindHost: bindHost,
      bindPort: bindPort,
      targetHost: targetHost,
      targetPort: targetPort,
      secondaryIpRequest: secondaryIpRequest,
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
      if (secondaryIpRequest != null)
        'secondary_ip_request': secondaryIpRequest!.toJson(),
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

class ParticipantDto {
  const ParticipantDto({
    required this.playerId,
    required this.playerName,
    required this.isHost,
  });

  final String playerId;
  final String playerName;
  final bool isHost;

  factory ParticipantDto.fromJson(Map<String, Object?> json) {
    final playerId = json['player_id'];
    final playerName = json['player_name'];
    final isHost = json['is_host'];
    return ParticipantDto(
      playerId: playerId is String ? playerId : '',
      playerName: playerName is String ? playerName : '',
      isHost: isHost is bool ? isHost : false,
    );
  }

  Map<String, Object?> toJson() {
    return {
      'player_id': playerId,
      'player_name': playerName,
      'is_host': isHost,
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
    this.playerId,
    this.protocolVersion,
    this.maxPlayers,
    this.participantCount,
    this.participants = const [],
    this.hostPlayerId,
    this.lastRoomEvent,
    this.roomReady = false,
    this.roomClosed = false,
    this.relayReady = false,
    this.relayTokenAvailable = false,
    this.relayTargetHost,
    this.relayTargetPort,
    this.serverTime,
    this.secondaryIpEnabled = false,
    this.secondaryIpFallbackUsed = false,
    this.secondaryIpWarning,
    this.backendAdmin = false,
    this.secondaryIpBindAddress,
    this.secondaryIpInterfaceIndex,
    this.secondaryIpInterfaceAlias,
    this.adapterBindMode = 'loopback',
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
  final String? playerId;
  final int? protocolVersion;
  final int? maxPlayers;
  final int? participantCount;
  final List<ParticipantDto> participants;
  final String? hostPlayerId;
  final String? lastRoomEvent;
  final bool roomReady;
  final bool roomClosed;
  final bool relayReady;
  final bool relayTokenAvailable;
  final String? relayTargetHost;
  final int? relayTargetPort;
  final double? serverTime;
  final bool secondaryIpEnabled;
  final bool secondaryIpFallbackUsed;
  final String? secondaryIpWarning;
  final bool backendAdmin;
  final String? secondaryIpBindAddress;
  final int? secondaryIpInterfaceIndex;
  final String? secondaryIpInterfaceAlias;
  final String adapterBindMode;

  factory SessionInfo.fromJson(Map<String, Object?> json) {
    final statsJson = _asObjectMap(json['stats']);
    final errorJson = _asObjectMap(json['error']);
    final adapterStatusJson = _asObjectMap(json['adapter_status']);
    final participantsJson = json['participants'];
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
      playerId: _nullableAsString(json['player_id']),
      protocolVersion: _nullableAsInt(json['protocol_version']),
      maxPlayers: _nullableAsInt(json['max_players']),
      participantCount: _nullableAsInt(json['participant_count']),
      participants: participantsJson is List
          ? participantsJson
                .whereType<Map>()
                .map((item) => ParticipantDto.fromJson(_asObjectMap(item)!))
                .toList(growable: false)
          : const [],
      hostPlayerId: _nullableAsString(json['host_player_id']),
      lastRoomEvent: _nullableAsString(json['last_room_event']),
      roomReady: _asBool(json['room_ready']),
      roomClosed: _asBool(json['room_closed']),
      relayReady: _asBool(json['relay_ready']),
      relayTokenAvailable: _asBool(
        json[_snakeKey(const ['relay', 'token', 'available'])],
      ),
      relayTargetHost: _nullableAsString(json['relay_target_host']),
      relayTargetPort: _nullableAsInt(json['relay_target_port']),
      serverTime: _nullableAsDouble(json['server_time']),
      secondaryIpEnabled: _asBool(json['secondary_ip_enabled']),
      secondaryIpFallbackUsed: _asBool(json['secondary_ip_fallback_used']),
      secondaryIpWarning: _nullableAsString(json['secondary_ip_warning']),
      backendAdmin: _asBool(json['backend_admin']),
      secondaryIpBindAddress: _nullableAsString(
        json['secondary_ip_bind_address'],
      ),
      secondaryIpInterfaceIndex: _nullableAsInt(
        json['secondary_ip_interface_index'],
      ),
      secondaryIpInterfaceAlias: _nullableAsString(
        json['secondary_ip_interface_alias'],
      ),
      adapterBindMode:
          _nullableAsString(json['adapter_bind_mode']) ?? 'loopback',
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
      if (playerId != null) 'player_id': playerId,
      if (protocolVersion != null) 'protocol_version': protocolVersion,
      if (maxPlayers != null) 'max_players': maxPlayers,
      if (participantCount != null) 'participant_count': participantCount,
      'participants': participants
          .map((participant) => participant.toJson())
          .toList(growable: false),
      if (hostPlayerId != null) 'host_player_id': hostPlayerId,
      if (lastRoomEvent != null) 'last_room_event': lastRoomEvent,
      'room_ready': roomReady,
      'room_closed': roomClosed,
      'relay_ready': relayReady,
      _snakeKey(const ['relay', 'token', 'available']): relayTokenAvailable,
      if (relayTargetHost != null) 'relay_target_host': relayTargetHost,
      if (relayTargetPort != null) 'relay_target_port': relayTargetPort,
      if (serverTime != null) 'server_time': serverTime,
      'secondary_ip_enabled': secondaryIpEnabled,
      'secondary_ip_fallback_used': secondaryIpFallbackUsed,
      if (secondaryIpWarning != null)
        'secondary_ip_warning': secondaryIpWarning,
      'backend_admin': backendAdmin,
      if (secondaryIpBindAddress != null)
        'secondary_ip_bind_address': secondaryIpBindAddress,
      if (secondaryIpInterfaceIndex != null)
        'secondary_ip_interface_index': secondaryIpInterfaceIndex,
      if (secondaryIpInterfaceAlias != null)
        'secondary_ip_interface_alias': secondaryIpInterfaceAlias,
      'adapter_bind_mode': adapterBindMode,
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

class LanPeerDto {
  const LanPeerDto({
    required this.peerId,
    required this.name,
    required this.host,
    required this.port,
    required this.version,
    required this.lastSeenAgeSeconds,
  });

  final String peerId;
  final String name;
  final String host;
  final int port;
  final String version;
  final num lastSeenAgeSeconds;

  factory LanPeerDto.fromJson(Map<String, Object?> json) {
    return LanPeerDto(
      peerId: json['peer_id'] as String? ?? '',
      name: json['name'] as String? ?? '',
      host: json['host'] as String? ?? '',
      port: _asInt(json['port']),
      version: json['version'] as String? ?? '',
      lastSeenAgeSeconds: json['last_seen_age_seconds'] as num? ?? 0,
    );
  }

  Map<String, Object?> toJson() {
    return {
      'peer_id': peerId,
      'name': name,
      'host': host,
      'port': port,
      'version': version,
      'last_seen_age_seconds': lastSeenAgeSeconds,
    };
  }
}

class LanDiscoveryStatus {
  const LanDiscoveryStatus({
    required this.running,
    required this.peerId,
    required this.instanceName,
    required this.servicePort,
    required this.broadcastPort,
    required this.peerCount,
  });

  final bool running;
  final String? peerId;
  final String instanceName;
  final int servicePort;
  final int broadcastPort;
  final int peerCount;

  factory LanDiscoveryStatus.stopped() {
    return const LanDiscoveryStatus(
      running: false,
      peerId: null,
      instanceName: '',
      servicePort: 0,
      broadcastPort: 0,
      peerCount: 0,
    );
  }

  factory LanDiscoveryStatus.fromJson(Map<String, Object?> json) {
    return LanDiscoveryStatus(
      running: json['running'] as bool? ?? false,
      peerId: json['peer_id'] as String?,
      instanceName: json['instance_name'] as String? ?? '',
      servicePort: _asInt(json['service_port']),
      broadcastPort: _asInt(json['broadcast_port']),
      peerCount: _asInt(json['peer_count']),
    );
  }

  Map<String, Object?> toJson() {
    return {
      'running': running,
      'peer_id': peerId,
      'instance_name': instanceName,
      'service_port': servicePort,
      'broadcast_port': broadcastPort,
      'peer_count': peerCount,
    };
  }
}

class LanDiscoveryPeersResponse {
  const LanDiscoveryPeersResponse({required this.running, required this.peers});

  final bool running;
  final List<LanPeerDto> peers;

  factory LanDiscoveryPeersResponse.fromJson(Map<String, Object?> json) {
    final peersJson = json['peers'];
    return LanDiscoveryPeersResponse(
      running: json['running'] as bool? ?? false,
      peers: peersJson is List
          ? peersJson
                .whereType<Map>()
                .map((item) => LanPeerDto.fromJson(_asObjectMap(item)!))
                .toList(growable: false)
          : const [],
    );
  }

  Map<String, Object?> toJson() {
    return {
      'running': running,
      'peers': peers.map((peer) => peer.toJson()).toList(growable: false),
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

String? _nullableAsString(Object? value) {
  if (value is String) return value;
  return null;
}

int? _nullableAsInt(Object? value) {
  if (value is int) return value;
  if (value is num) return value.toInt();
  return null;
}

bool _asBool(Object? value) => value is bool ? value : false;

double? _nullableAsDouble(Object? value) {
  if (value is num) return value.toDouble();
  return null;
}

String _snakeKey(List<String> parts) => parts.join('_');
