enum AdapterType {
  launchOnly('launch_only', 'Launch Only'),
  genericUdpForward('generic_udp_forward', 'UDP Adapter Experimental'),
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
      doctorProfile:
          json['doctor_profile'] as Map<String, Object?>? ?? const {},
      notes: json['notes'] as String? ?? '',
      status: ProfileStatus.ready,
    );
  }

  Map<String, Object?> toJson() {
    return {
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
  }

  static List<int> _intList(Object? value) {
    if (value is List) {
      return value.whereType<int>().toList();
    }
    return const [];
  }

  GameProfile copyWith({
    String? profileId,
    String? displayName,
    String? exePath,
    String? workingDir,
    AdapterType? adapterType,
    String? protocol,
    String? localBindHost,
    int? localBindPort,
    bool clearLocalBindPort = false,
    String? remoteTargetHost,
    int? remoteTargetPort,
    bool clearRemoteTargetPort = false,
    String? launchArgs,
    String? expectedProcessName,
    List<int>? expectedPorts,
    Map<String, Object?>? doctorProfile,
    String? notes,
    ProfileStatus? status,
    DateTime? lastLaunchedAt,
    String? errorMessage,
    bool clearErrorMessage = false,
  }) {
    return GameProfile(
      profileId: profileId ?? this.profileId,
      displayName: displayName ?? this.displayName,
      exePath: exePath ?? this.exePath,
      workingDir: workingDir ?? this.workingDir,
      adapterType: adapterType ?? this.adapterType,
      protocol: protocol ?? this.protocol,
      localBindHost: localBindHost ?? this.localBindHost,
      localBindPort: clearLocalBindPort
          ? null
          : localBindPort ?? this.localBindPort,
      remoteTargetHost: remoteTargetHost ?? this.remoteTargetHost,
      remoteTargetPort: clearRemoteTargetPort
          ? null
          : remoteTargetPort ?? this.remoteTargetPort,
      launchArgs: launchArgs ?? this.launchArgs,
      expectedProcessName: expectedProcessName ?? this.expectedProcessName,
      expectedPorts: expectedPorts ?? this.expectedPorts,
      doctorProfile: doctorProfile ?? this.doctorProfile,
      notes: notes ?? this.notes,
      status: status ?? this.status,
      lastLaunchedAt: lastLaunchedAt ?? this.lastLaunchedAt,
      errorMessage: clearErrorMessage
          ? null
          : errorMessage ?? this.errorMessage,
    );
  }
}
