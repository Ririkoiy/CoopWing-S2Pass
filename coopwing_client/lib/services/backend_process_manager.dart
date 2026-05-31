import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';

class BackendProcessManager extends ChangeNotifier {
  Process? _process;
  bool _startedByUs = false;
  bool _online = false;
  String? _error;
  bool _starting = false;
  Completer<void>? _startCompleter;

  bool get online => _online;
  bool get startedByUs => _startedByUs;
  String? get error => _error;
  bool get starting => _starting;

  static const String _backendHost = '127.0.0.1';
  static const int _backendPort = 21520;
  static const Duration _healthTimeout = Duration(seconds: 2);
  static const Duration _pollInterval = Duration(milliseconds: 500);
  static const int _maxPolls = 30;

  Future<void> ensureBackendRunning() async {
    if (_online) return;

    // If a start is already in progress, await that same start
    if (_startCompleter != null) {
      _addLog('startup already in progress, awaiting existing start');
      await _startCompleter!.future;
      return;
    }

    // Check health before committing to a start
    if (await _checkHealth()) {
      _addLog('health check found existing backend, reusing');
      _online = true;
      notifyListeners();
      return;
    }

    // Commit to starting — lock BEFORE any async work
    _startCompleter = Completer<void>();
    _starting = true;
    _error = null;
    notifyListeners();

    final exePath = _findBackendExe();
    if (exePath == null) {
      _error = 'backend_not_found';
      _finishStart();
      return;
    }

    try {
      final logsDir = Directory(_logsDir);
      if (!logsDir.existsSync()) {
        logsDir.createSync(recursive: true);
      }

      final logFile = File('$_logsDir${Platform.pathSeparator}backend.log');
      final sink = logFile.openWrite(mode: FileMode.append);

      sink.write(
        '=== CoopWing backend started at ${DateTime.now().toIso8601String()} ===\n',
      );

      _process = await Process.start(
        exePath,
        ['--host', _backendHost, '--port', _backendPort.toString()],
        workingDirectory: _backendDir,
        environment: {'S2PASS_BACKEND_RUNNER': 'real_core'},
        includeParentEnvironment: true,
      );

      _startedByUs = true;
      _addLog('backend process started, PID: ${_process!.pid}');

      _process!.stdout.transform(utf8.decoder).listen((data) {
        sink.write(data);
      });
      _process!.stderr.transform(utf8.decoder).listen((data) {
        sink.write(data);
      });

      _process!.exitCode.then((code) {
        _addLog('backend process exit code: $code');
        sink.write('\n=== Backend exited with code $code at ${DateTime.now().toIso8601String()} ===\n');
        sink.close();
        if (_startedByUs) {
          _online = false;
          _startedByUs = false;
          _process = null;
          notifyListeners();
        }
      });

      for (var i = 0; i < _maxPolls; i++) {
        await Future.delayed(_pollInterval);
        if (await _checkHealth()) {
          _online = true;
          _finishStart();
          await sink.flush();
          return;
        }
      }

      _error = 'backend_start_failed';
      await sink.flush();
    } on Exception catch (e) {
      _error = 'backend_launch_error: ${e.toString()}';
    }

    _finishStart();
  }

  Future<bool> _checkHealth() async {
    try {
      final client = HttpClient();
      client.connectionTimeout = _healthTimeout;
      final request = await client.get(_backendHost, _backendPort, '/health');
      final response = await request.close().timeout(_healthTimeout);
      final body = await utf8.decoder.bind(response).join();
      client.close(force: true);
      return response.statusCode == 200 && body.contains('"status"');
    } catch (_) {
      return false;
    }
  }

  String? _findBackendExe() {
    final exeDir = File(Platform.resolvedExecutable).parent;
    final backendExe = File(
      '${exeDir.path}${Platform.pathSeparator}backend${Platform.pathSeparator}coopwing_backend.exe',
    );
    if (backendExe.existsSync()) return backendExe.path;
    return null;
  }

  String get _backendDir {
    return '${File(Platform.resolvedExecutable).parent.path}${Platform.pathSeparator}backend';
  }

  String get _logsDir {
    return '${File(Platform.resolvedExecutable).parent.path}${Platform.pathSeparator}logs';
  }

  Future<void> stop() async {
    final proc = _process;
    if (proc == null || !_startedByUs) return;

    _startedByUs = false;
    _addLog('backend stop requested, PID: ${proc.pid}');
    proc.kill(ProcessSignal.sigterm);

    try {
      await proc.exitCode.timeout(const Duration(seconds: 3));
      _addLog('backend stopped gracefully');
    } on TimeoutException {
      _addLog('backend did not exit gracefully, force killing');
      proc.kill(ProcessSignal.sigkill);
    }

    _process = null;
    _online = false;
    notifyListeners();
  }

  @override
  void dispose() {
    stop();
    super.dispose();
  }

  void _finishStart() {
    _starting = false;
    final c = _startCompleter;
    _startCompleter = null;
    if (c != null && !c.isCompleted) {
      c.complete();
    }
    notifyListeners();
  }

  void _addLog(String message) {
    debugPrint('[BackendProcessManager] $message');
  }
}
