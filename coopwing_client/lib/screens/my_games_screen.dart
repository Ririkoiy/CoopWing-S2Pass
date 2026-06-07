import 'package:desktop_drop/desktop_drop.dart';
import 'package:flutter/material.dart';

import '../models/game_profile.dart';
import '../services/backend_client.dart';
import '../services/localization.dart';
import '../widgets/content_column_page.dart';

@visibleForTesting
bool disableDesktopDropForTesting = false;

class ExecutablePathDraft {
  const ExecutablePathDraft({
    required this.executablePath,
    required this.displayName,
    required this.workingDirectory,
  });

  final String executablePath;
  final String displayName;
  final String workingDirectory;
}

String cleanExecutablePathInput(String value) {
  var path = value.trim();
  if (path.length >= 2 &&
      ((path.startsWith('"') && path.endsWith('"')) ||
          (path.startsWith("'") && path.endsWith("'")))) {
    path = path.substring(1, path.length - 1).trim();
  }
  return path;
}

bool isExeExecutablePath(String value) =>
    cleanExecutablePathInput(value).toLowerCase().endsWith('.exe');

int _lastPathSeparator(String path) {
  final slash = path.lastIndexOf('/');
  final backslash = path.lastIndexOf('\\');
  return slash > backslash ? slash : backslash;
}

ExecutablePathDraft? executablePathDraftFromPath(String value) {
  final clean = cleanExecutablePathInput(value);
  if (clean.isEmpty || !isExeExecutablePath(clean)) {
    return null;
  }
  final separator = _lastPathSeparator(clean);
  final filename = separator >= 0 ? clean.substring(separator + 1) : clean;
  final displayName = filename.substring(0, filename.length - 4);
  final workingDirectory = separator >= 0 ? clean.substring(0, separator) : '';
  return ExecutablePathDraft(
    executablePath: clean,
    displayName: displayName,
    workingDirectory: workingDirectory,
  );
}

class MyGamesScreen extends StatefulWidget {
  const MyGamesScreen({super.key, required this.client});

  final BackendClient client;

  @override
  State<MyGamesScreen> createState() => _MyGamesScreenState();
}

class _MyGamesScreenState extends State<MyGamesScreen> {
  List<GameProfileDto> _games = [];
  bool _loading = true;
  String? _error;
  bool _draggingExecutable = false;
  final Set<String> _selectedCandidateKeys = {};
  final Set<String> _expandedGameIds = {};

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  Future<void> _refresh() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      _games = await widget.client.listGames();
    } catch (e) {
      _error = e.toString();
      _games = [];
    }
    if (mounted) setState(() => _loading = false);
  }

  String _candidateKey(PortCandidateDto c) => '${c.protocol}:${c.port}';

  // 鈹€鈹€ build 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

  @override
  Widget build(BuildContext context) {
    final loc = Localization();
    final theme = Theme.of(context);
    final content = AnimatedContainer(
      duration: const Duration(milliseconds: 120),
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        border: Border.all(
          color: _draggingExecutable
              ? theme.colorScheme.primary
              : Colors.transparent,
          width: 1.5,
        ),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _buildHeader(loc, theme),
          const SizedBox(height: 18),
          if (_error != null) _MessageBanner(message: _error!, isError: true),
          if (_loading)
            const Center(
              child: Padding(
                padding: EdgeInsets.all(32),
                child: CircularProgressIndicator(),
              ),
            )
          else if (_games.isEmpty)
            _buildEmptyState(loc, theme)
          else
            ..._games.map((g) => _buildGameCard(g, loc, theme)),
          const SizedBox(height: 24),
          Text(
            loc.get('port_detection_note'),
            style: TextStyle(
              fontSize: 12,
              color: theme.colorScheme.onSurfaceVariant,
            ),
          ),
          const SizedBox(height: 4),
          Text(
            loc.get('port_detection_no_mutate'),
            style: TextStyle(
              fontSize: 12,
              color: theme.colorScheme.onSurfaceVariant,
              fontStyle: FontStyle.italic,
            ),
          ),
        ],
      ),
    );

    return ContentColumnPage(
      child: disableDesktopDropForTesting
          ? content
          : DropTarget(
              onDragDone: _handlePageDrop,
              onDragEntered: (_) => setState(() => _draggingExecutable = true),
              onDragExited: (_) => setState(() => _draggingExecutable = false),
              child: content,
            ),
    );
  }

  Widget _buildHeader(Localization loc, ThemeData theme) {
    return Row(
      children: [
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(loc.get('my_games'), style: theme.textTheme.headlineMedium),
              const SizedBox(height: 4),
              Text(
                loc.get('my_games_desc'),
                style: TextStyle(color: theme.colorScheme.onSurfaceVariant),
              ),
            ],
          ),
        ),
        FilledButton.tonalIcon(
          onPressed: _openAddGame,
          icon: const Icon(Icons.add),
          label: Text(loc.get('add_game')),
        ),
      ],
    );
  }

  Widget _buildEmptyState(Localization loc, ThemeData theme) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 48),
      child: Center(
        child: Column(
          children: [
            Icon(
              Icons.sports_esports_outlined,
              size: 64,
              color: theme.colorScheme.onSurfaceVariant.withAlpha(120),
            ),
            const SizedBox(height: 16),
            Text(
              loc.get('no_games_yet'),
              style: theme.textTheme.titleMedium?.copyWith(
                color: theme.colorScheme.onSurfaceVariant,
              ),
            ),
            const SizedBox(height: 8),
            Text(
              loc.get('no_games_yet_desc'),
              style: TextStyle(color: theme.colorScheme.onSurfaceVariant),
            ),
            const SizedBox(height: 8),
            Text(
              loc.get('drag_exe_here'),
              style: TextStyle(
                fontSize: 13,
                color: theme.colorScheme.onSurfaceVariant,
              ),
            ),
            const SizedBox(height: 20),
            OutlinedButton.icon(
              onPressed: _openAddGame,
              icon: const Icon(Icons.add),
              label: Text(loc.get('add_game')),
            ),
          ],
        ),
      ),
    );
  }

  // 鈹€鈹€ game card 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

  Widget _buildGameCard(
    GameProfileDto game,
    Localization loc,
    ThemeData theme,
  ) {
    final scheme = theme.colorScheme;
    final expanded = _expandedGameIds.contains(game.gameId);

    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      decoration: BoxDecoration(
        border: Border.all(color: scheme.outlineVariant),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Column(
        children: [
          ListTile(
            leading: Container(
              width: 42,
              height: 42,
              decoration: BoxDecoration(
                color: scheme.primaryContainer,
                borderRadius: BorderRadius.circular(8),
              ),
              child: const Icon(Icons.sports_esports),
            ),
            title: Text(
              game.displayName,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
            subtitle: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  game.executablePath,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    fontSize: 11,
                    fontFamily: 'monospace',
                    color: scheme.onSurfaceVariant,
                  ),
                ),
                const SizedBox(height: 2),
                Wrap(
                  spacing: 6,
                  runSpacing: 2,
                  children: [
                    if (game.confirmedTcpPorts.isNotEmpty)
                      _tag('TCP: ${game.confirmedTcpPorts.join(', ')}', scheme),
                    if (game.confirmedUdpPorts.isNotEmpty)
                      _tag('UDP: ${game.confirmedUdpPorts.join(', ')}', scheme),
                    if (game.candidatePorts.isNotEmpty)
                      _tag('Candidates: ${game.candidatePorts.length}', scheme),
                  ],
                ),
              ],
            ),
            trailing: IconButton(
              tooltip: expanded ? 'Collapse' : 'Expand',
              icon: Icon(expanded ? Icons.expand_less : Icons.expand_more),
              onPressed: () => setState(() {
                if (expanded) {
                  _expandedGameIds.remove(game.gameId);
                } else {
                  _expandedGameIds.add(game.gameId);
                }
              }),
            ),
            onTap: () => setState(() {
              if (expanded) {
                _expandedGameIds.remove(game.gameId);
              } else {
                _expandedGameIds.add(game.gameId);
              }
            }),
          ),
          if (expanded) _buildExpandedSection(game, loc, theme),
        ],
      ),
    );
  }

  Widget _tag(String label, ColorScheme scheme) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
      decoration: BoxDecoration(
        color: scheme.secondaryContainer,
        borderRadius: BorderRadius.circular(4),
      ),
      child: Text(label, style: const TextStyle(fontSize: 10)),
    );
  }

  Widget _buildExpandedSection(
    GameProfileDto game,
    Localization loc,
    ThemeData theme,
  ) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(18, 0, 18, 14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const Divider(),
          // scan controls
          Row(
            children: [
              Expanded(
                child: DropdownButtonFormField<String>(
                  initialValue: 'manual',
                  decoration: InputDecoration(
                    labelText: loc.get('scan_stage'),
                    border: const OutlineInputBorder(),
                    isDense: true,
                    contentPadding: const EdgeInsets.symmetric(
                      horizontal: 10,
                      vertical: 8,
                    ),
                  ),
                  items: ['manual', 'launch', 'menu', 'lobby', 'in_game']
                      .map(
                        (s) => DropdownMenuItem(
                          value: s,
                          child: Text(loc.get('stage_$s')),
                        ),
                      )
                      .toList(),
                  onChanged: (_) {},
                ),
              ),
              const SizedBox(width: 10),
              OutlinedButton.icon(
                onPressed: () => _scanPorts(game.gameId),
                icon: const Icon(Icons.wifi_tethering, size: 18),
                label: Text(loc.get('scan_ports')),
              ),
            ],
          ),
          const SizedBox(height: 12),
          // candidates
          if (game.candidatePorts.isNotEmpty) ...[
            Text(loc.get('port_candidates'), style: theme.textTheme.titleSmall),
            const SizedBox(height: 6),
            ...game.candidatePorts.map((c) {
              final key = _candidateKey(c);
              final confidenceColor = c.confidence == 'high'
                  ? Colors.green
                  : c.confidence == 'medium'
                  ? Colors.orange
                  : Colors.grey;
              return CheckboxListTile(
                dense: true,
                contentPadding: EdgeInsets.zero,
                value: _selectedCandidateKeys.contains(key),
                onChanged: (v) => setState(() {
                  if (v == true) {
                    _selectedCandidateKeys.add(key);
                  } else {
                    _selectedCandidateKeys.remove(key);
                  }
                }),
                title: Row(
                  children: [
                    Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 5,
                        vertical: 1,
                      ),
                      decoration: BoxDecoration(
                        color: c.protocol == 'tcp'
                            ? Colors.blue.withAlpha(30)
                            : Colors.purple.withAlpha(30),
                        borderRadius: BorderRadius.circular(3),
                      ),
                      child: Text(
                        c.protocol.toUpperCase(),
                        style: const TextStyle(
                          fontSize: 10,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                    ),
                    const SizedBox(width: 8),
                    Text(
                      ':${c.port}',
                      style: const TextStyle(fontFamily: 'monospace'),
                    ),
                    const SizedBox(width: 8),
                    Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 4,
                        vertical: 0,
                      ),
                      decoration: BoxDecoration(
                        color: confidenceColor.withAlpha(30),
                        borderRadius: BorderRadius.circular(3),
                      ),
                      child: Text(
                        '${loc.get('confidence_${c.confidence}')} (${c.confidence})',
                        style: TextStyle(fontSize: 10, color: confidenceColor),
                      ),
                    ),
                  ],
                ),
                subtitle: Text(
                  c.reason,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontSize: 11),
                ),
              );
            }),
            const SizedBox(height: 10),
            FilledButton.icon(
              onPressed: () => _confirmSelected(game.gameId),
              icon: const Icon(Icons.check, size: 18),
              label: Text(loc.get('save_ports')),
            ),
          ] else
            Text(
              loc.get('no_candidates'),
              style: TextStyle(color: theme.colorScheme.onSurfaceVariant),
            ),
          const SizedBox(height: 12),
          // delete
          Align(
            alignment: Alignment.centerLeft,
            child: TextButton.icon(
              onPressed: () => _deleteGame(game.gameId, game.displayName),
              icon: const Icon(
                Icons.delete_outline,
                size: 18,
                color: Colors.red,
              ),
              label: Text(
                loc.get('delete_game'),
                style: const TextStyle(color: Colors.red),
              ),
            ),
          ),
        ],
      ),
    );
  }

  // 鈹€鈹€ actions 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

  Future<void> _openAddGame([String? executablePath]) async {
    final game = await showDialog<GameProfileDto>(
      context: context,
      builder: (_) => AddGameForm(
        client: widget.client,
        initialExecutablePath: executablePath,
      ),
    );
    if (game != null) _refresh();
  }

  void _handlePageDrop(DropDoneDetails details) {
    setState(() => _draggingExecutable = false);
    final draft = _draftFromDroppedFiles(details.files);
    if (draft == null) {
      setState(() => _error = Localization().get('invalid_exe_path'));
      return;
    }
    setState(() => _error = null);
    _openAddGame(draft.executablePath);
  }

  Future<void> _scanPorts(String gameId) async {
    setState(() {
      _error = null;
    });
    try {
      await widget.client.scanGamePorts(gameId);
    } catch (e) {
      setState(() => _error = e.toString());
    }
    _refresh();
  }

  Future<void> _confirmSelected(String gameId) async {
    final tcp = <int>[];
    final udp = <int>[];
    for (final key in _selectedCandidateKeys) {
      final parts = key.split(':');
      final proto = parts[0];
      final port = int.tryParse(parts[1]) ?? 0;
      if (proto == 'tcp') {
        tcp.add(port);
      } else {
        udp.add(port);
      }
    }
    if (tcp.isEmpty && udp.isEmpty) return;
    try {
      await widget.client.confirmGamePorts(
        gameId,
        tcpPorts: tcp,
        udpPorts: udp,
      );
      _selectedCandidateKeys.clear();
    } catch (e) {
      setState(() => _error = e.toString());
    }
    _refresh();
  }

  Future<void> _deleteGame(String gameId, String name) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('${Localization().get('delete_game')}: $name'),
        content: Text('${Localization().get('delete_game')} "$name"?'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: Text(Localization().get('cancel')),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: Text(Localization().get('delete_game')),
          ),
        ],
      ),
    );
    if (ok == true) {
      await widget.client.deleteGame(gameId);
      _refresh();
    }
  }
}

// 鈹€鈹€ Add Game Form 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

ExecutablePathDraft? _draftFromDroppedFiles(List<dynamic> files) {
  for (final file in files) {
    final path = file.path;
    if (path is String) {
      final draft = executablePathDraftFromPath(path);
      if (draft != null) {
        return draft;
      }
    }
  }
  return null;
}

class AddGameForm extends StatefulWidget {
  const AddGameForm({
    super.key,
    required this.client,
    this.initialExecutablePath,
  });

  final BackendClient client;
  final String? initialExecutablePath;

  @override
  State<AddGameForm> createState() => _AddGameFormState();
}

class _AddGameFormState extends State<AddGameForm> {
  final _nameCtrl = TextEditingController();
  final _pathCtrl = TextEditingController();
  final _workDirCtrl = TextEditingController();
  final _argsCtrl = TextEditingController();
  final _notesCtrl = TextEditingController();
  bool _busy = false;
  String? _error;
  String? _lastAutoName;
  String? _lastAutoWorkDir;

  @override
  void initState() {
    super.initState();
    final initial = widget.initialExecutablePath;
    if (initial != null) {
      final draft = executablePathDraftFromPath(initial);
      if (draft != null) {
        _fillFromExecutableDraft(draft);
      }
    }
  }

  @override
  void dispose() {
    _nameCtrl.dispose();
    _pathCtrl.dispose();
    _workDirCtrl.dispose();
    _argsCtrl.dispose();
    _notesCtrl.dispose();
    super.dispose();
  }

  void _applyExecutablePath({bool showErrors = false}) {
    final path = cleanExecutablePathInput(_pathCtrl.text);
    if (path.isEmpty) {
      return;
    }
    final draft = executablePathDraftFromPath(path);
    if (draft == null) {
      if (showErrors) {
        setState(() => _error = Localization().get('invalid_exe_path'));
      }
      return;
    }

    setState(() {
      _fillFromExecutableDraft(draft);
      _error = null;
    });
  }

  void _fillFromExecutableDraft(ExecutablePathDraft draft) {
    if (_pathCtrl.text != draft.executablePath) {
      _pathCtrl.text = draft.executablePath;
      _pathCtrl.selection = TextSelection.collapsed(
        offset: draft.executablePath.length,
      );
    }
    if (_nameCtrl.text.trim().isEmpty || _nameCtrl.text == _lastAutoName) {
      _nameCtrl.text = draft.displayName;
      _lastAutoName = draft.displayName;
    }
    if (_workDirCtrl.text.trim().isEmpty ||
        _workDirCtrl.text == _lastAutoWorkDir) {
      _workDirCtrl.text = draft.workingDirectory;
      _lastAutoWorkDir = draft.workingDirectory;
    }
  }

  void _handleDialogDrop(DropDoneDetails details) {
    final draft = _draftFromDroppedFiles(details.files);
    if (draft == null) {
      setState(() => _error = Localization().get('invalid_exe_path'));
      return;
    }
    setState(() {
      _fillFromExecutableDraft(draft);
      _error = null;
    });
  }

  Future<void> _create() async {
    _applyExecutablePath(showErrors: true);
    final executablePath = cleanExecutablePathInput(_pathCtrl.text);
    if (_nameCtrl.text.trim().isEmpty || executablePath.isEmpty) return;
    if (!isExeExecutablePath(executablePath)) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final game = await widget.client.createGame(
        displayName: _nameCtrl.text.trim(),
        executablePath: executablePath,
        workingDirectory: _workDirCtrl.text.trim().isEmpty
            ? null
            : _workDirCtrl.text.trim(),
        launchArgs: _argsCtrl.text.trim().isEmpty
            ? null
            : _argsCtrl.text.trim().split(RegExp(r'\s+')),
        notes: _notesCtrl.text.trim().isEmpty ? null : _notesCtrl.text.trim(),
      );
      if (mounted) Navigator.of(context).pop(game);
    } catch (e) {
      setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final loc = Localization();
    final dialogBody = ConstrainedBox(
      constraints: const BoxConstraints(maxWidth: 580),
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(
                      loc.get('add_game'),
                      style: Theme.of(context).textTheme.headlineSmall,
                    ),
                  ),
                  IconButton(
                    tooltip: loc.get('close'),
                    icon: const Icon(Icons.close),
                    onPressed: () => Navigator.of(context).pop(),
                  ),
                ],
              ),
              const SizedBox(height: 16),
              TextField(
                controller: _nameCtrl,
                decoration: InputDecoration(
                  labelText: loc.get('game_name'),
                  border: const OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 14),
              TextField(
                controller: _pathCtrl,
                onChanged: (_) => _applyExecutablePath(),
                decoration: InputDecoration(
                  labelText: loc.get('executable_path'),
                  hintText: r'C:\Games\MyGame\MyGame.exe',
                  helperText: loc.get('manual_exe_path'),
                  border: const OutlineInputBorder(),
                  suffixIcon: TextButton(
                    onPressed: () => _applyExecutablePath(showErrors: true),
                    child: Text(loc.get('apply_exe_path')),
                  ),
                ),
              ),
              const SizedBox(height: 8),
              Text(
                loc.get('exe_path_autofill_note'),
                style: TextStyle(
                  fontSize: 12,
                  color: Theme.of(context).colorScheme.onSurfaceVariant,
                ),
              ),
              const SizedBox(height: 14),
              TextField(
                controller: _workDirCtrl,
                decoration: InputDecoration(
                  labelText: loc.get('working_directory'),
                  border: const OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 14),
              TextField(
                controller: _argsCtrl,
                decoration: InputDecoration(
                  labelText: loc.get('launch_args'),
                  border: const OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 14),
              TextField(
                controller: _notesCtrl,
                maxLines: 2,
                decoration: InputDecoration(
                  labelText: loc.get('notes'),
                  border: const OutlineInputBorder(),
                ),
              ),
              if (_error != null) ...[
                const SizedBox(height: 12),
                _MessageBanner(message: _error!, isError: true),
              ],
              const SizedBox(height: 20),
              Row(
                mainAxisAlignment: MainAxisAlignment.end,
                children: [
                  TextButton(
                    onPressed: _busy ? null : () => Navigator.of(context).pop(),
                    child: Text(loc.get('cancel')),
                  ),
                  const SizedBox(width: 12),
                  FilledButton.icon(
                    onPressed: _busy ? null : _create,
                    icon: const Icon(Icons.add),
                    label: Text(loc.get('add_game')),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
    return Dialog(
      child: disableDesktopDropForTesting
          ? dialogBody
          : DropTarget(onDragDone: _handleDialogDrop, child: dialogBody),
    );
  }
}

// 鈹€鈹€ shared widgets 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

class _MessageBanner extends StatelessWidget {
  const _MessageBanner({required this.message, this.isError = false});
  final String message;
  final bool isError;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: isError
            ? scheme.errorContainer.withAlpha(120)
            : scheme.primaryContainer.withAlpha(120),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Row(
        children: [
          Icon(
            isError ? Icons.error_outline : Icons.info_outline,
            size: 18,
            color: isError ? scheme.error : scheme.primary,
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              message,
              style: TextStyle(
                fontSize: 13,
                color: isError
                    ? scheme.onErrorContainer
                    : scheme.onPrimaryContainer,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
