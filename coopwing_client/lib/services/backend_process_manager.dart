import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';

import '../models/backend_api_models.dart';

typedef BackendHealthProbe = Future<HealthStatus?> Function();
typedef BackendExeLocator = String? Function();
typedef BackendDirectoryProvider = String Function();
typedef BackendLogDirectoryProvider = String Function();
typedef BackendProcessStarter =
    Future<BackendProcessHandle> Function(
      String exePath,
      List<String> arguments, {
      required String workingDirectory,
      required Map<String, String> environment,
    });

abstract class BackendProcessHandle {
  int get pid;
  Stream<List<int>> get stdout;
  Stream<List<int>> get stderr;
  Future<int> get exitCode;
  bool kill([ProcessSignal signal]);
}

class _DartProcessHandle implements BackendProcessHandle {
  const _DartProcessHandle(this._process);

  final Process _process;

  @override
  int get pid => _process.pid;

  @override
  Stream<List<int>> get stdout => _process.stdout;

  @override
  Stream<List<int>> get stderr => _process.stderr;

  @override
  Future<int> get exitCode => _process.exitCode;

  @override
  bool kill([ProcessSignal signal = ProcessSignal.sigterm]) {
    return _process.kill(signal);
  }
}

class BackendProcessManager extends ChangeNotifier {
  BackendProcessManager({
    BackendHealthProbe? healthProbe,
    BackendProcessStarter? processStarter,
    BackendExeLocator? backendExeLocator,
    BackendDirectoryProvider? backendDirectoryProvider,
    BackendLogDirectoryProvider? logDirectoryProvider,
  }) : _healthProbe = healthProbe,
       _processStarter = processStarter ?? _defaultProcessStarter,
       _backendExeLocator = backendExeLocator,
       _backendDirectoryProvider = backendDirectoryProvider,
       _logDirectoryProvider = logDirectoryProvider;

  BackendProcessHandle? _process;
  bool _startedByUs = false;
  bool _online = false;
  String? _error;
  bool _starting = false;
  Completer<void>? _startCompleter;

  final BackendHealthProbe? _healthProbe;
  final BackendProcessStarter _processStarter;
  final BackendExeLocator? _backendExeLocator;
  final BackendDirectoryProvider? _backendDirectoryProvider;
  final BackendLogDirectoryProvider? _logDirectoryProvider;

  bool get online => _online;
  bool get startedByUs => _startedByUs;
  String? get error => _error;
  bool get starting => _starting;

  static const String _backendHost = '127.0.0.1';
  static const int _backendPort = 21520;
  static const String _expectedMode = 'real_core';
  static const Duration _healthTimeout = Duration(seconds: 2);
  static const Duration _pollInterval = Duration(milliseconds: 500);
  static const int _maxPolls = 30;

  Future<void> ensureBackendRunning() async {
    if (_online) return;

    final existingStart = _startCompleter;
    if (existingStart != null) {
      _addLog('startup already in progress, awaiting existing start');
      await existingStart.future;
      return;
    }

    final completer = Completer<void>();
    _startCompleter = completer;
    _starting = true;
    _error = null;
    notifyListeners();

    await _ensureBackendRunningLocked(completer);
  }

  Future<void> _ensureBackendRunningLocked(Completer<void> completer) async {
    final existingHealth = await _probeHealth();
    if (existingHealth != null) {
      _addLog('health check found existing backend, reusing');
      _warnIfUnexpectedMode(existingHealth);
      _online = true;
      _finishStart(completer);
      return;
    }

    final exePath = _findBackendExe();
    if (exePath == null) {
      _error = 'backend_not_found';
      _finishStart(completer);
      return;
    }

    IOSink? sink;
    try {
      final logsDir = Directory(_logsDir);
      if (!logsDir.existsSync()) {
        logsDir.createSync(recursive: true);
      }

      final logFile = File('$_logsDir${Platform.pathSeparator}backend.log');
      sink = logFile.openWrite(mode: FileMode.append);
      sink.write(
        '=== CoopWing backend started at ${DateTime.now().toIso8601String()} ===\n',
      );

      _process = await _processStarter(
        exePath,
        [
          '--host',
          _backendHost,
          '--port',
          _backendPort.toString(),
          '--parent-pid',
          pid.toString(),
        ],
        workingDirectory: _backendDir,
        environment: {'S2PASS_BACKEND_RUNNER': _expectedMode},
      );

      _startedByUs = true;
      _addLog('backend process started, PID: ${_process!.pid}');

      final process = _process!;
      process.stdout.transform(utf8.decoder).listen(sink.write);
      process.stderr.transform(utf8.decoder).listen(sink.write);
      process.exitCode.then((code) {
        _addLog('backend process exit code: $code');
        sink?.write(
          '\n=== Backend exited with code $code at ${DateTime.now().toIso8601String()} ===\n',
        );
        sink?.close();
        if (_startedByUs && identical(_process, process)) {
          _online = false;
          _startedByUs = false;
          _process = null;
          notifyListeners();
        }
      });

      for (var i = 0; i < _maxPolls; i++) {
        await Future.delayed(_pollInterval);
        final health = await _probeHealth();
        if (health != null) {
          _warnIfUnexpectedMode(health);
          _online = true;
          _finishStart(completer);
          await sink.flush();
          return;
        }
      }

      _error = 'backend_start_failed';
      await sink.flush();
    } on Exception catch (e) {
      _error = 'backend_launch_error: ${e.toString()}';
    }

    _finishStart(completer);
  }

  Future<HealthStatus?> _probeHealth() async {
    final testProbe = _healthProbe;
    if (testProbe != null) {
      final health = await testProbe();
      return health != null && health.isOnline ? health : null;
    }

    final client = HttpClient();
    try {
      client.connectionTimeout = _healthTimeout;
      final request = await client.get(_backendHost, _backendPort, '/health');
      final response = await request.close().timeout(_healthTimeout);
      final body = await utf8.decoder.bind(response).join();
      if (response.statusCode != 200) return null;
      final decoded = jsonDecode(body);
      if (decoded is! Map) return null;
      final health = HealthStatus.fromJson(
        decoded.map((key, item) => MapEntry(key.toString(), item)),
      );
      return health.isOnline ? health : null;
    } catch (_) {
      return null;
    } finally {
      client.close(force: true);
    }
  }

  String? _findBackendExe() {
    final locator = _backendExeLocator;
    if (locator != null) return locator();
    final exeDir = File(Platform.resolvedExecutable).parent;
    final backendExe = File(
      '${exeDir.path}${Platform.pathSeparator}backend${Platform.pathSeparator}coopwing_backend.exe',
    );
    if (backendExe.existsSync()) return backendExe.path;
    return null;
  }

  String get _backendDir {
    final provider = _backendDirectoryProvider;
    if (provider != null) return provider();
    return '${File(Platform.resolvedExecutable).parent.path}${Platform.pathSeparator}backend';
  }

  String get _logsDir {
    final provider = _logDirectoryProvider;
    if (provider != null) return provider();
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
    unawaited(stop());
    super.dispose();
  }

  void _finishStart(Completer<void> completer) {
    _starting = false;
    if (identical(_startCompleter, completer)) {
      _startCompleter = null;
    }
    if (!completer.isCompleted) {
      completer.complete();
    }
    notifyListeners();
  }

  void _warnIfUnexpectedMode(HealthStatus health) {
    if (health.mode != _expectedMode) {
      _addLog(
        'warning: backend mode is ${health.mode}, expected $_expectedMode',
      );
    }
  }

  void _addLog(String message) {
    debugPrint('[BackendProcessManager] $message');
  }

  static Future<BackendProcessHandle> _defaultProcessStarter(
    String exePath,
    List<String> arguments, {
    required String workingDirectory,
    required Map<String, String> environment,
  }) async {
    return _DartProcessHandle(
      await Process.start(
        exePath,
        arguments,
        workingDirectory: workingDirectory,
        environment: environment,
        includeParentEnvironment: true,
      ),
    );
  }
}
