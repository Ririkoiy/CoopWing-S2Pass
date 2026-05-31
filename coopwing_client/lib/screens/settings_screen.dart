import 'package:flutter/material.dart';

import '../models/app_event.dart';
import '../models/app_settings.dart';
import '../models/server_preset.dart';
import '../services/mock_backend_client.dart';
import '../services/localization.dart';
import '../widgets/app_log_panel.dart';
import '../widgets/content_column_page.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({
    super.key,
    required this.settings,
    required this.servers,
    required this.events,
    required this.onSaveSettings,
    required this.onSaveServerHost,
  });

  final AppSettings settings;
  final List<ServerPreset> servers;
  final List<AppEvent> events;
  final Future<void> Function(AppSettings settings) onSaveSettings;
  final Future<void> Function(String serverId, String host) onSaveServerHost;

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  final Map<String, TextEditingController> _serverControllers = {};
  bool _saving = false;

  @override
  void didUpdateWidget(covariant SettingsScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    _syncControllers();
  }

  @override
  void initState() {
    super.initState();
    _syncControllers();
  }

  @override
  void dispose() {
    for (final controller in _serverControllers.values) {
      controller.dispose();
    }
    super.dispose();
  }

  void _syncControllers() {
    for (final server in widget.servers) {
      _serverControllers.putIfAbsent(
        server.serverId,
        () => TextEditingController(text: server.host),
      );
      if (_serverControllers[server.serverId]!.text != server.host) {
        _serverControllers[server.serverId]!.text = server.host;
      }
    }
  }

  Future<void> _toggleDeveloperMode(bool value) async {
    setState(() => _saving = true);
    await widget.onSaveSettings(widget.settings.copyWith(developerMode: value));
    if (mounted) {
      setState(() => _saving = false);
    }
  }

  Future<void> _saveServer(ServerPreset server) async {
    setState(() => _saving = true);
    await widget.onSaveServerHost(
      server.serverId,
      _serverControllers[server.serverId]!.text,
    );
    if (mounted) {
      setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return ContentColumnPage(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(
            Localization().get('settings'),
            style: Theme.of(context).textTheme.headlineMedium,
          ),
          const SizedBox(height: 18),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(18),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    Localization().get('server_configuration'),
                    style: Theme.of(context).textTheme.titleLarge,
                  ),
                  const SizedBox(height: 8),
                  Text(Localization().get('server_presets_note')),
                  const SizedBox(height: 16),
                  for (final server in widget.servers)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 14),
                      child: Row(
                        children: [
                          Expanded(
                            child: TextField(
                              controller: _serverControllers[server.serverId],
                              decoration: InputDecoration(
                                labelText:
                                    '${server.displayName == 'Default VPS' ? Localization().get('default_vps') : server.displayName} (${server.serverId})',
                                helperText:
                                    server.description ==
                                        'Default Preview relay/signaling server preset'
                                    ? Localization().get('default_vps_desc')
                                    : server.description,
                                border: const OutlineInputBorder(),
                              ),
                            ),
                          ),
                          const SizedBox(width: 12),
                          FilledButton.icon(
                            onPressed: _saving
                                ? null
                                : () => _saveServer(server),
                            icon: const Icon(Icons.save),
                            label: Text(Localization().get('save')),
                          ),
                        ],
                      ),
                    ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 16),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(18),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    Localization().get('general_settings'),
                    style: Theme.of(context).textTheme.titleLarge,
                  ),
                  const SizedBox(height: 12),
                  _ReadOnlySetting(
                    label: Localization().get('backend_api_port'),
                    value: '${widget.settings.backendApiPort}',
                  ),
                  _ReadOnlySetting(
                    label: Localization().get('ui_theme'),
                    value: widget.settings.theme == 'dark'
                        ? Localization().get('theme_dark_demo')
                        : widget.settings.theme,
                  ),
                  _ReadOnlySetting(
                    label: Localization().get('log_level'),
                    value: Localization().get(
                      'log_level_${widget.settings.logLevel.toLowerCase()}',
                    ),
                  ),
                  SwitchListTile(
                    contentPadding: EdgeInsets.zero,
                    title: Text(Localization().get('developer_mode')),
                    subtitle: Text(Localization().get('developer_mode_desc')),
                    value: widget.settings.developerMode,
                    onChanged: _saving ? null : _toggleDeveloperMode,
                  ),
                  const Divider(),
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    title: Text(Localization().get('language')),
                    subtitle: Text(Localization().get('current_language')),
                    trailing: FilledButton.tonal(
                      onPressed: () {
                        setState(() {
                          Localization().toggleLanguage();
                        });
                      },
                      child: Text(
                        Localization().language == Language.zh
                            ? 'English'
                            : '中文',
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
          if (widget.settings.developerMode) ...[
            const SizedBox(height: 16),
            Card(
              child: Padding(
                padding: const EdgeInsets.all(18),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      Localization().get('dev_console_entry'),
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                    const SizedBox(height: 8),
                    Text(Localization().get('dev_console_desc')),
                    const SizedBox(height: 6),
                    Text(
                      '💡 ${Localization().get('meme_gremlins_off')}',
                      style: TextStyle(
                        fontSize: 12,
                        fontStyle: FontStyle.italic,
                        color: Theme.of(context).colorScheme.primary,
                      ),
                    ),
                    const SizedBox(height: 12),
                    AppLogPanel(events: widget.events, compact: true),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 16),
            Card(
              child: Padding(
                padding: const EdgeInsets.all(18),
                child: Text(
                  '${Localization().get('default_fallback_note')} ${MockBackendClient.defaultRelayHost}',
                ),
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _ReadOnlySetting extends StatelessWidget {
  const _ReadOnlySetting({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Row(
        children: [
          SizedBox(width: 180, child: Text(label)),
          Text(value, style: const TextStyle(fontFamily: 'monospace')),
        ],
      ),
    );
  }
}
