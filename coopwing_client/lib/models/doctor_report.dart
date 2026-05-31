enum DoctorStatus {
  idle('idle'),
  running('running'),
  completed('completed'),
  failed('failed');

  const DoctorStatus(this.label);

  final String label;
}

enum ReportType {
  directory('directory'),
  zip('zip');

  const ReportType(this.backendValue);

  final String backendValue;
}

class DoctorReport {
  const DoctorReport({
    required this.filename,
    required this.createdAt,
    required this.sizeBytes,
    required this.reportType,
    required this.summaryPath,
    required this.zipPath,
    required this.summary,
    required this.systemInfo,
    required this.networkInterfaces,
    required this.serverConnectivity,
    required this.natReachability,
    required this.recommendations,
  });

  final String filename;
  final DateTime createdAt;
  final int? sizeBytes;
  final ReportType reportType;
  final String summaryPath;
  final String? zipPath;
  final String summary;
  final List<String> systemInfo;
  final List<String> networkInterfaces;
  final List<String> serverConnectivity;
  final List<String> natReachability;
  final List<String> recommendations;

  factory DoctorReport.fromJson(Map<String, Object?> json) {
    final reportTypeValue = json['report_type'] as String? ?? 'directory';
    return DoctorReport(
      filename: json['filename'] as String? ?? '',
      createdAt:
          DateTime.tryParse(json['created_at'] as String? ?? '') ??
          DateTime.fromMillisecondsSinceEpoch(0),
      sizeBytes: json['size_bytes'] as int?,
      reportType: ReportType.values.firstWhere(
        (type) => type.backendValue == reportTypeValue,
        orElse: () => ReportType.directory,
      ),
      summaryPath: json['summary_path'] as String? ?? '',
      zipPath: json['zip_path'] as String?,
      summary: json['summary'] as String? ?? '',
      systemInfo: _stringList(json['system_info']),
      networkInterfaces: _stringList(json['network_interfaces']),
      serverConnectivity: _stringList(json['server_connectivity']),
      natReachability: _stringList(json['nat_reachability']),
      recommendations: _stringList(json['recommendations']),
    );
  }

  Map<String, Object?> toJson() {
    return {
      'filename': filename,
      'created_at': createdAt.toIso8601String(),
      'size_bytes': sizeBytes,
      'report_type': reportType.backendValue,
      'summary_path': summaryPath,
      'zip_path': zipPath,
      'summary': summary,
      'system_info': systemInfo,
      'network_interfaces': networkInterfaces,
      'server_connectivity': serverConnectivity,
      'nat_reachability': natReachability,
      'recommendations': recommendations,
    };
  }

  static List<String> _stringList(Object? value) {
    if (value is List) {
      return value.whereType<String>().toList();
    }
    return const [];
  }
}
