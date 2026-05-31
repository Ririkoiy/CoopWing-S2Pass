import 'package:flutter/material.dart';

import '../models/doctor_report.dart';
import '../services/backend_client.dart';
import '../services/localization.dart';
import '../widgets/content_column_page.dart';
import '../widgets/status_chip.dart';

class NetworkDoctorScreen extends StatefulWidget {
  const NetworkDoctorScreen({super.key, required this.client});

  final BackendClient client;

  @override
  State<NetworkDoctorScreen> createState() => _NetworkDoctorScreenState();
}

class _NetworkDoctorScreenState extends State<NetworkDoctorScreen> {
  DoctorStatus _status = DoctorStatus.idle;
  DoctorReport? _report;
  List<DoctorReport> _reports = const [];

  @override
  void initState() {
    super.initState();
    _loadReports();
  }

  Future<void> _loadReports() async {
    final status = _doctorStatusFromBackendValue(
      await widget.client.getDoctorStatus(),
    );
    final reports = await widget.client.getDoctorReports();
    if (!mounted) {
      return;
    }
    setState(() {
      _status = status;
      _reports = reports;
      _report = reports.isEmpty ? null : reports.first;
    });
  }

  DoctorStatus _doctorStatusFromBackendValue(String value) {
    return DoctorStatus.values.firstWhere(
      (status) => status.label == value,
      orElse: () => DoctorStatus.idle,
    );
  }

  Future<void> _run() async {
    setState(() => _status = DoctorStatus.running);
    try {
      final report = await widget.client.runDoctor();
      final reports = await widget.client.getDoctorReports();
      setState(() {
        _report = report;
        _reports = reports;
        _status = DoctorStatus.completed;
      });
    } catch (_) {
      setState(() => _status = DoctorStatus.failed);
    }
  }

  void _viewReport() {
    final report = _report;
    if (report == null) {
      return;
    }
    showDialog<void>(
      context: context,
      builder: (context) => _ReportDialog(report: report),
    );
  }

  void _placeholder(String action) {
    final msg = Localization().language == Language.zh
        ? '$action 在预览版 0.2 中仅为演示占位。'
        : '$action is a placeholder in Preview 0.2.';
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  @override
  Widget build(BuildContext context) {
    return ContentColumnPage(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  Localization().get('doctor'),
                  style: Theme.of(context).textTheme.headlineMedium,
                ),
              ),
              StatusChip.doctor(_status),
            ],
          ),
          const SizedBox(height: 10),
          Text(
            '${Localization().get('disclaimer_5')} ${Localization().get('disclaimer_3')}',
          ),
          const SizedBox(height: 18),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(18),
              child: Wrap(
                spacing: 12,
                runSpacing: 12,
                children: [
                  FilledButton.icon(
                    onPressed: _status == DoctorStatus.running ? null : _run,
                    icon: const Icon(Icons.play_arrow),
                    label: Text(Localization().get('run_diagnostics')),
                  ),
                  OutlinedButton.icon(
                    onPressed: _report == null ? null : _viewReport,
                    icon: const Icon(Icons.article_outlined),
                    label: Text(Localization().get('view_report')),
                  ),
                  OutlinedButton.icon(
                    onPressed: () =>
                        _placeholder(Localization().get('export_report')),
                    icon: const Icon(Icons.ios_share),
                    label: Text(Localization().get('export_report')),
                  ),
                  OutlinedButton.icon(
                    onPressed: () => _placeholder(
                      Localization().get('open_diagnostics_folder'),
                    ),
                    icon: const Icon(Icons.folder_open),
                    label: Text(Localization().get('open_diagnostics_folder')),
                  ),
                ],
              ),
            ),
          ),
          if (_status == DoctorStatus.failed) ...[
            const SizedBox(height: 18),
            Card(
              color: Theme.of(context).colorScheme.errorContainer,
              child: Padding(
                padding: const EdgeInsets.all(18),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Icon(
                          Icons.error_outline,
                          color: Theme.of(context).colorScheme.error,
                        ),
                        const SizedBox(width: 8),
                        Text(
                          Localization().get('diagnostics_failed_title'),
                          style: Theme.of(context).textTheme.titleMedium
                              ?.copyWith(
                                color: Theme.of(
                                  context,
                                ).colorScheme.onErrorContainer,
                                fontWeight: FontWeight.bold,
                              ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 8),
                    Text(
                      Localization().get('diagnostics_failed_desc'),
                      style: TextStyle(
                        color: Theme.of(context).colorScheme.onErrorContainer,
                      ),
                    ),
                    const SizedBox(height: 8),
                    Text(
                      '💡 ${Localization().get('meme_cat_cable')}',
                      style: TextStyle(
                        fontStyle: FontStyle.italic,
                        color: Theme.of(context).colorScheme.error,
                        fontWeight: FontWeight.w500,
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ],
          const SizedBox(height: 18),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(18),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    Localization().get('last_report'),
                    style: Theme.of(context).textTheme.titleLarge,
                  ),
                  const SizedBox(height: 10),
                  Text(
                    _report?.summary ??
                        Localization().get('no_diagnostics_report'),
                  ),
                  if (_report != null) ...[
                    const SizedBox(height: 8),
                    Text(
                      '${Localization().language == Language.zh ? '创建时间' : 'Created'}: ${_report!.createdAt}',
                      style: const TextStyle(fontFamily: 'monospace'),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      '${Localization().language == Language.zh ? '类型' : 'Type'}: ${_report!.reportType.backendValue}  ${Localization().language == Language.zh ? '文件' : 'File'}: ${_report!.filename}',
                      style: const TextStyle(fontFamily: 'monospace'),
                    ),
                    if (_report!.zipPath == null)
                      Padding(
                        padding: const EdgeInsets.only(top: 4),
                        child: Text(Localization().get('no_zippath')),
                      ),
                  ],
                ],
              ),
            ),
          ),
          const SizedBox(height: 6),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 8),
            child: Text(
              Localization().get('diagnostics_help'),
              style: TextStyle(
                fontSize: 12,
                color: Theme.of(
                  context,
                ).colorScheme.onSurfaceVariant.withValues(alpha: 0.75),
              ),
            ),
          ),
          const SizedBox(height: 12),
          _ReportsList(
            reports: _reports,
            onOpen: (report) {
              setState(() => _report = report);
              _viewReport();
            },
          ),
          const SizedBox(height: 18),
          const _PrivacyNotice(),
        ],
      ),
    );
  }
}

class _ReportDialog extends StatelessWidget {
  const _ReportDialog({required this.report});

  final DoctorReport report;

  @override
  Widget build(BuildContext context) {
    return Dialog(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 760, maxHeight: 720),
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(
                      Localization().get('diagnostics_report'),
                      style: Theme.of(context).textTheme.headlineSmall,
                    ),
                  ),
                  IconButton(
                    tooltip: Localization().get('close'),
                    onPressed: () => Navigator.of(context).pop(),
                    icon: const Icon(Icons.close),
                  ),
                ],
              ),
              const SizedBox(height: 12),
              Expanded(
                child: SingleChildScrollView(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      _ReportSection(
                        title: Localization().get('report_metadata'),
                        items: [
                          'filename: ${report.filename}',
                          'created_at: ${report.createdAt.toIso8601String()}',
                          'size_bytes: ${report.sizeBytes?.toString() ?? 'unknown'}',
                          'report_type: ${report.reportType.backendValue}',
                          'summary_path: ${report.summaryPath}',
                          'zip_path: ${report.zipPath ?? '(none)'}',
                        ],
                      ),
                      _ReportSection(
                        title: Localization().get('system_info'),
                        items: report.systemInfo,
                      ),
                      _ReportSection(
                        title: Localization().get('network_interfaces'),
                        items: report.networkInterfaces,
                      ),
                      _ReportSection(
                        title: Localization().get('server_connectivity'),
                        items: report.serverConnectivity,
                      ),
                      _ReportSection(
                        title: Localization().get('nat_reachability'),
                        items: report.natReachability,
                      ),
                      _ReportSection(
                        title: Localization().get('recommendations'),
                        items: report.recommendations,
                      ),
                    ],
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _ReportsList extends StatelessWidget {
  const _ReportsList({required this.reports, required this.onOpen});

  final List<DoctorReport> reports;
  final ValueChanged<DoctorReport> onOpen;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(18),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              Localization().get('report_history'),
              style: Theme.of(context).textTheme.titleLarge,
            ),
            const SizedBox(height: 10),
            for (final report in reports.take(3))
              ListTile(
                contentPadding: EdgeInsets.zero,
                leading: Icon(
                  report.reportType == ReportType.zip
                      ? Icons.folder_zip
                      : Icons.folder_outlined,
                ),
                title: Text(report.filename),
                subtitle: Text(
                  '${report.reportType.backendValue} - ${report.sizeBytes?.toString() ?? 'unknown'} bytes',
                ),
                trailing: TextButton(
                  onPressed: () => onOpen(report),
                  child: Text(Localization().get('view_report')),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _ReportSection extends StatelessWidget {
  const _ReportSection({required this.title, required this.items});

  final String title;
  final List<String> items;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(title, style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 8),
            for (final item in items)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 3),
                child: Text(item),
              ),
          ],
        ),
      ),
    );
  }
}

class _PrivacyNotice extends StatelessWidget {
  const _PrivacyNotice();

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: scheme.secondaryContainer.withValues(alpha: 0.3),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: scheme.secondary.withValues(alpha: 0.35)),
      ),
      child: Text(Localization().get('disclaimer_4')),
    );
  }
}
