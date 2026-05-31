import 'package:flutter/material.dart';

import '../models/game_profile.dart';
import '../services/backend_client.dart';
import '../services/mock_backend_client.dart';
import '../services/localization.dart';

class AddGameDialog extends StatefulWidget {
  const AddGameDialog({super.key, required this.client});

  final BackendClient client;

  @override
  State<AddGameDialog> createState() => _AddGameDialogState();
}

class _AddGameDialogState extends State<AddGameDialog> {
  final TextEditingController _pathController = TextEditingController();
  final TextEditingController _nameController = TextEditingController();

  GameProfile? _draft;
  String? _error;
  bool _busy = false;

  @override
  void dispose() {
    _pathController.dispose();
    _nameController.dispose();
    super.dispose();
  }

  Future<void> _createDraft() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final draft = await widget.client.createProfileDraftFromExe(
        _pathController.text,
      );
      setState(() {
        _draft = draft;
        _nameController.text = draft.displayName;
      });
    } on MockBackendException catch (error) {
      setState(() => _error = '${error.code}: ${error.message}');
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _save() async {
    final draft = _draft;
    if (draft == null) {
      return;
    }
    setState(() => _busy = true);
    final saved = await widget.client.saveProfile(
      draft.copyWith(displayName: _nameController.text.trim()),
    );
    if (mounted) {
      Navigator.of(context).pop(saved);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Dialog(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 720),
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(
                      Localization().get('add_game'),
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
              const SizedBox(height: 16),
              if (_draft == null)
                _buildDropStep(context)
              else
                _buildConfirmStep(),
              if (_error != null) ...[
                const SizedBox(height: 12),
                Text(
                  _error!,
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
                const SizedBox(height: 4),
                Text(
                  '💡 ${Localization().get('meme_not_your_fault')}',
                  style: TextStyle(
                    fontSize: 12,
                    fontStyle: FontStyle.italic,
                    color: Theme.of(context).colorScheme.error.withValues(alpha: 0.8),
                  ),
                ),
              ],
              const SizedBox(height: 20),
              Row(
                mainAxisAlignment: MainAxisAlignment.end,
                children: [
                  TextButton(
                    onPressed: _busy ? null : () => Navigator.of(context).pop(),
                    child: Text(Localization().get('cancel')),
                  ),
                  const SizedBox(width: 12),
                  FilledButton.icon(
                    onPressed: _busy
                        ? null
                        : _draft == null
                        ? _createDraft
                        : _save,
                    icon: Icon(
                      _draft == null ? Icons.arrow_forward : Icons.save,
                    ),
                    label: Text(
                      _draft == null
                          ? Localization().get('create_draft')
                          : Localization().get('save'),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildDropStep(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Container(
          height: 210,
          decoration: BoxDecoration(
            color: scheme.surfaceContainerHighest.withValues(alpha: 0.55),
            borderRadius: BorderRadius.circular(8),
            border: Border.all(
              color: scheme.primary.withValues(alpha: 0.45),
              width: 1.4,
            ),
          ),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(Icons.file_upload_outlined, size: 42, color: scheme.primary),
              const SizedBox(height: 12),
              Text(
                Localization().get('drag_exe_here'),
                style: Theme.of(context).textTheme.titleMedium,
              ),
              const SizedBox(height: 8),
              Text(Localization().get('browse_files_placeholder')),
              const SizedBox(height: 14),
              OutlinedButton.icon(
                // TODO: Wire to a desktop file picker/drop plugin in a future
                // integration pass. Preview 0.2 keeps this as UI-only mock UX.
                onPressed: () {
                  _pathController.text =
                      r'C:\Games\ExampleGame\ExampleGame.exe';
                },
                icon: const Icon(Icons.folder_open),
                label: Text(Localization().get('browse_files')),
              ),
            ],
          ),
        ),
        const SizedBox(height: 16),
        TextField(
          controller: _pathController,
          decoration: InputDecoration(
            labelText: Localization().get('manual_exe_path'),
            hintText: r'C:\Games\ExampleGame\ExampleGame.exe',
            border: const OutlineInputBorder(),
          ),
          onSubmitted: (_) => _createDraft(),
        ),
        const SizedBox(height: 10),
        Text(
          Localization().get('supported_exe_only'),
        ),
      ],
    );
  }

  Widget _buildConfirmStep() {
    final draft = _draft!;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        TextField(
          controller: _nameController,
          decoration: InputDecoration(
            labelText: Localization().get('display_name'),
            border: const OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 14),
        _ReadOnlyRow(
          label: Localization().get('mode'),
          value: Localization().get('adapter_type_${draft.adapterType.backendValue}'),
        ),
        _ReadOnlyRow(label: 'exe_path', value: draft.exePath),
        _ReadOnlyRow(label: 'working_dir', value: draft.workingDir),
        _ReadOnlyRow(label: 'local_bind_host', value: draft.localBindHost),
        const SizedBox(height: 10),
        Text(
          Localization().get('draft_memory_note'),
        ),
      ],
    );
  }
}

class _ReadOnlyRow extends StatelessWidget {
  const _ReadOnlyRow({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Row(
        children: [
          SizedBox(width: 130, child: Text(label)),
          Expanded(
            child: Tooltip(
              message: value,
              child: Text(
                value.isEmpty ? '(empty)' : value,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontFamily: 'monospace'),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
