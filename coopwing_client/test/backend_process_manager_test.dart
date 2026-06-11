import 'dart:async';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:s2pass_flutter_mock/models/backend_api_models.dart';
import 'package:s2pass_flutter_mock/services/backend_process_manager.dart';

void main() {
  test('concurrent ensureBackendRunning shares one startup', () async {
    final tempDir = await Directory.systemTemp.createTemp('coopwing_bpm_test_');

    var healthCalls = 0;
    var starts = 0;
    final firstHealth = Completer<HealthStatus?>();
    final process = _FakeBackendProcess();
    final manager = BackendProcessManager(
      backendExeLocator: () => 'coopwing_backend.exe',
      backendDirectoryProvider: () => tempDir.path,
      logDirectoryProvider: () => tempDir.path,
      healthProbe: () async {
        healthCalls += 1;
        if (healthCalls == 1) return firstHealth.future;
        return _onlineHealth();
      },
      processStarter:
          (
            exePath,
            arguments, {
            required workingDirectory,
            required environment,
          }) async {
            starts += 1;
            return process;
          },
    );
    addTearDown(() async {
      await manager.stop();
      manager.dispose();
      await Future<void>.delayed(const Duration(milliseconds: 20));
      await tempDir.delete(recursive: true);
    });

    final first = manager.ensureBackendRunning();
    final second = manager.ensureBackendRunning();
    await Future<void>.delayed(Duration.zero);
    firstHealth.complete(null);

    await Future.wait([first, second]);

    expect(starts, 1);
    expect(manager.online, isTrue);
    expect(manager.startedByUs, isTrue);
  });

  test('stop terminates only process started by manager', () async {
    final manager = BackendProcessManager(
      healthProbe: () async => _onlineHealth(),
      processStarter:
          (
            exePath,
            arguments, {
            required workingDirectory,
            required environment,
          }) {
            fail('Existing healthy backend should be reused');
          },
    );
    addTearDown(manager.dispose);

    await manager.ensureBackendRunning();
    await manager.stop();

    expect(manager.online, isTrue);
    expect(manager.startedByUs, isFalse);
  });

  test('stop sends termination to owned process', () async {
    final tempDir = await Directory.systemTemp.createTemp('coopwing_bpm_stop_');

    var healthCalls = 0;
    final process = _FakeBackendProcess();
    final manager = BackendProcessManager(
      backendExeLocator: () => 'coopwing_backend.exe',
      backendDirectoryProvider: () => tempDir.path,
      logDirectoryProvider: () => tempDir.path,
      healthProbe: () async {
        healthCalls += 1;
        return healthCalls == 1 ? null : _onlineHealth();
      },
      processStarter:
          (
            exePath,
            arguments, {
            required workingDirectory,
            required environment,
          }) async {
            return process;
          },
    );
    addTearDown(() async {
      await manager.stop();
      manager.dispose();
      await Future<void>.delayed(const Duration(milliseconds: 20));
      await tempDir.delete(recursive: true);
    });

    await manager.ensureBackendRunning();
    await manager.stop();

    expect(process.signals, contains(ProcessSignal.sigterm));
    expect(manager.online, isFalse);
    expect(manager.startedByUs, isFalse);
  });

  test('child process exits but health probe still online', () async {
    final tempDir = await Directory.systemTemp.createTemp('coopwing_bpm_exit_health_');
    var healthCalls = 0;
    final process = _FakeBackendProcess();
    final manager = BackendProcessManager(
      backendExeLocator: () => 'coopwing_backend.exe',
      backendDirectoryProvider: () => tempDir.path,
      logDirectoryProvider: () => tempDir.path,
      healthProbe: () async {
        healthCalls += 1;
        return healthCalls == 1 ? null : _onlineHealth();
      },
      processStarter:
          (
            exePath,
            arguments, {
            required workingDirectory,
            required environment,
          }) async {
            return process;
          },
    );
    addTearDown(() async {
      manager.dispose();
      await Future<void>.delayed(const Duration(milliseconds: 20));
      await tempDir.delete(recursive: true);
    });

    await manager.ensureBackendRunning();
    expect(manager.online, isTrue);
    expect(manager.startedByUs, isTrue);

    process.completeExit(0);
    await Future<void>.delayed(const Duration(milliseconds: 50));

    expect(manager.online, isTrue);
    expect(manager.startedByUs, isFalse);
  });
}

HealthStatus _onlineHealth() {
  return const HealthStatus(
    status: 'ok',
    version: '0.1.0',
    uptimeSeconds: 1,
    backend: 's2pass',
    mode: 'real_core',
  );
}

class _FakeBackendProcess implements BackendProcessHandle {
  final Completer<int> _exit = Completer<int>();
  final List<ProcessSignal> signals = [];

  @override
  int get pid => 12345;

  @override
  Stream<List<int>> get stdout => const Stream<List<int>>.empty();

  @override
  Stream<List<int>> get stderr => const Stream<List<int>>.empty();

  @override
  Future<int> get exitCode => _exit.future;

  @override
  bool kill([ProcessSignal signal = ProcessSignal.sigterm]) {
    signals.add(signal);
    if (!_exit.isCompleted) {
      _exit.complete(0);
    }
    return true;
  }

  void completeExit(int code) {
    if (!_exit.isCompleted) {
      _exit.complete(code);
    }
  }
}
