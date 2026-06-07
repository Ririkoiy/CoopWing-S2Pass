/// v0.3-J Game Profile and Port Candidate DTOs aligned with backend API.
/// Keeps legacy adapter-type GameProfile for backward compatibility.

// ── legacy adapter-type model (preserved for existing callers) ──────

enum AdapterType {
  launchOnly('launch_only', 'Launch Only'),
  genericUdpForward('generic_udp_forward', 'UDP Only'),
  diagnosticsOnly('diagnostics_only', 'Diagnostics Only');

  const AdapterType(this.backendValue, this.label);

  final String backendValue;
  final String label;
}

enum ProfileStatus {
  ready('Ready'),
  running('Running'),
  error('Error');

  const ProfileStatus(this.label);

  final String label;
}

class GameProfile {
  const GameProfile({
    required this.profileId,
    required this.displayName,
    required this.exePath,
    required this.workingDir,
    required this.adapterType,
    required this.protocol,
    required this.localBindHost,
    required this.localBindPort,
    required this.remoteTargetHost,
    required this.remoteTargetPort,
    required this.launchArgs,
    required this.expectedProcessName,
    required this.expectedPorts,
    required this.doctorProfile,
    required this.notes,
    required this.status,
    this.lastLaunchedAt,
    this.errorMessage,
  });

  final String profileId;
  final String displayName;
  final String exePath;
  final String workingDir;
  final AdapterType adapterType;
  final String protocol;
  final String localBindHost;
  final int? localBindPort;
  final String remoteTargetHost;
  final int? remoteTargetPort;
  final String launchArgs;
  final String expectedProcessName;
  final List<int> expectedPorts;
  final Map<String, Object?> doctorProfile;
  final String notes;
  final ProfileStatus status;
  final DateTime? lastLaunchedAt;
  final String? errorMessage;

  factory GameProfile.fromJson(Map<String, Object?> json) {
    final adapterValue = json['adapter_type'] as String? ?? 'launch_only';
    return GameProfile(
      profileId: json['profile_id'] as String? ?? '',
      displayName: json['display_name'] as String? ?? '',
      exePath: json['exe_path'] as String? ?? '',
      workingDir: json['working_dir'] as String? ?? '',
      launchArgs: json['launch_args'] as String? ?? '',
      adapterType: AdapterType.values.firstWhere(
        (type) => type.backendValue == adapterValue,
        orElse: () => AdapterType.launchOnly,
      ),
      protocol: json['protocol'] as String? ?? '',
      localBindHost: json['local_bind_host'] as String? ?? '127.0.0.1',
      localBindPort: json['local_bind_port'] as int?,
      remoteTargetHost: json['remote_target_host'] as String? ?? '',
      remoteTargetPort: json['remote_target_port'] as int?,
      expectedProcessName: json['expected_process_name'] as String? ?? '',
      expectedPorts: _intList(json['expected_ports']),
      doctorProfile: json['doctor_profile'] as Map<String, Object?>? ?? const {},
      notes: json['notes'] as String? ?? '',
      status: ProfileStatus.ready,
    );
  }

  Map<String, Object?> toJson() => {
        'profile_id': profileId,
        'display_name': displayName,
        'exe_path': exePath,
        'working_dir': workingDir,
        'launch_args': launchArgs,
        'adapter_type': adapterType.backendValue,
        'protocol': protocol,
        'local_bind_host': localBindHost,
        'local_bind_port': localBindPort,
        'remote_target_host': remoteTargetHost,
        'remote_target_port': remoteTargetPort,
        'expected_process_name': expectedProcessName,
        'expected_ports': expectedPorts,
        'doctor_profile': doctorProfile,
        'notes': notes,
      };

  static List<int> _intList(Object? value) {
    if (value is List) return value.whereType<int>().toList();
    return const [];
  }

  GameProfile copyWith({
    String? profileId, String? displayName, String? exePath, String? workingDir,
    AdapterType? adapterType, String? protocol, String? localBindHost,
    int? localBindPort, bool clearLocalBindPort = false,
    String? remoteTargetHost, int? remoteTargetPort, bool clearRemoteTargetPort = false,
    String? launchArgs, String? expectedProcessName, List<int>? expectedPorts,
    Map<String, Object?>? doctorProfile, String? notes,
    ProfileStatus? status, DateTime? lastLaunchedAt,
    String? errorMessage, bool clearErrorMessage = false,
  }) {
    return GameProfile(
      profileId: profileId ?? this.profileId,
      displayName: displayName ?? this.displayName,
      exePath: exePath ?? this.exePath,
      workingDir: workingDir ?? this.workingDir,
      adapterType: adapterType ?? this.adapterType,
      protocol: protocol ?? this.protocol,
      localBindHost: localBindHost ?? this.localBindHost,
      localBindPort: clearLocalBindPort ? null : localBindPort ?? this.localBindPort,
      remoteTargetHost: remoteTargetHost ?? this.remoteTargetHost,
      remoteTargetPort: clearRemoteTargetPort ? null : remoteTargetPort ?? this.remoteTargetPort,
      launchArgs: launchArgs ?? this.launchArgs,
      expectedProcessName: expectedProcessName ?? this.expectedProcessName,
      expectedPorts: expectedPorts ?? this.expectedPorts,
      doctorProfile: doctorProfile ?? this.doctorProfile,
      notes: notes ?? this.notes,
      status: status ?? this.status,
      lastLaunchedAt: lastLaunchedAt ?? this.lastLaunchedAt,
      errorMessage: clearErrorMessage ? null : errorMessage ?? this.errorMessage,
    );
  }
}

// ── v0.3-J backend-aligned models ──────────────────────────────────

class PortCandidateDto {
  const PortCandidateDto({
    required this.protocol,
    required this.port,
    this.processId,
    this.processName,
    this.localAddress,
    this.remoteAddress,
    this.state,
    required this.confidence,
    required this.reason,
  });

  final String protocol;
  final int port;
  final int? processId;
  final String? processName;
  final String? localAddress;
  final String? remoteAddress;
  final String? state;
  final String confidence;
  final String reason;

  factory PortCandidateDto.fromJson(Map<String, Object?> json) {
    return PortCandidateDto(
      protocol: (json['protocol'] as String?) ?? '',
      port: (json['port'] as num?)?.toInt() ?? 0,
      processId: (json['process_id'] as num?)?.toInt(),
      processName: json['process_name'] as String?,
      localAddress: json['local_address'] as String?,
      remoteAddress: json['remote_address'] as String?,
      state: json['state'] as String?,
      confidence: (json['confidence'] as String?) ?? 'low',
      reason: (json['reason'] as String?) ?? '',
    );
  }

  Map<String, Object?> toJson() => {
        'protocol': protocol,
        'port': port,
        'process_id': processId,
        'process_name': processName,
        'local_address': localAddress,
        'remote_address': remoteAddress,
        'state': state,
        'confidence': confidence,
        'reason': reason,
      };
}

class ScanResultDto {
  const ScanResultDto({
    required this.candidates,
    required this.stage,
    required this.scannedAt,
    this.processName,
    this.processId,
  });

  final List<PortCandidateDto> candidates;
  final String stage;
  final double scannedAt;
  final String? processName;
  final int? processId;

  factory ScanResultDto.fromJson(Map<String, Object?> json) {
    final raw = json['candidates'] as List<Object?>? ?? const [];
    final candidates = raw
        .whereType<Map<String, Object?>>()
        .map(PortCandidateDto.fromJson)
        .toList();
    return ScanResultDto(
      candidates: candidates,
      stage: (json['stage'] as String?) ?? 'manual',
      scannedAt: (json['scanned_at'] as num?)?.toDouble() ?? 0.0,
      processName: json['process_name'] as String?,
      processId: (json['process_id'] as num?)?.toInt(),
    );
  }
}

class GameProfileDto {
  const GameProfileDto({
    required this.gameId,
    required this.displayName,
    required this.executablePath,
    this.workingDirectory,
    this.launchArgs,
    this.confirmedTcpPorts = const [],
    this.confirmedUdpPorts = const [],
    this.candidatePorts = const [],
    this.notes,
    required this.createdAt,
    required this.updatedAt,
  });

  final String gameId;
  final String displayName;
  final String executablePath;
  final String? workingDirectory;
  final List<String>? launchArgs;
  final List<int> confirmedTcpPorts;
  final List<int> confirmedUdpPorts;
  final List<PortCandidateDto> candidatePorts;
  final String? notes;
  final double createdAt;
  final double updatedAt;

  factory GameProfileDto.fromJson(Map<String, Object?> json) {
    final tcpRaw = json['confirmed_tcp_ports'] as List<Object?>? ?? const [];
    final udpRaw = json['confirmed_udp_ports'] as List<Object?>? ?? const [];
    final candRaw = json['candidate_ports'] as List<Object?>? ?? const [];
    final launchRaw = json['launch_args'] as List<Object?>?;
    return GameProfileDto(
      gameId: (json['game_id'] as String?) ?? '',
      displayName: (json['display_name'] as String?) ?? '',
      executablePath: (json['executable_path'] as String?) ?? '',
      workingDirectory: json['working_directory'] as String?,
      launchArgs: launchRaw?.map((a) => a.toString()).toList(),
      confirmedTcpPorts: tcpRaw.whereType<num>().map((n) => n.toInt()).toList(),
      confirmedUdpPorts: udpRaw.whereType<num>().map((n) => n.toInt()).toList(),
      candidatePorts: candRaw
          .whereType<Map<String, Object?>>()
          .map(PortCandidateDto.fromJson)
          .toList(),
      notes: json['notes'] as String?,
      createdAt: (json['created_at'] as num?)?.toDouble() ?? 0.0,
      updatedAt: (json['updated_at'] as num?)?.toDouble() ?? 0.0,
    );
  }

  Map<String, Object?> toJson() => {
        'game_id': gameId,
        'display_name': displayName,
        'executable_path': executablePath,
        if (workingDirectory != null) 'working_directory': workingDirectory,
        if (launchArgs != null) 'launch_args': launchArgs,
        'confirmed_tcp_ports': confirmedTcpPorts,
        'confirmed_udp_ports': confirmedUdpPorts,
        'candidate_ports': candidatePorts.map((c) => c.toJson()).toList(),
        if (notes != null) 'notes': notes,
        'created_at': createdAt,
        'updated_at': updatedAt,
      };
}
