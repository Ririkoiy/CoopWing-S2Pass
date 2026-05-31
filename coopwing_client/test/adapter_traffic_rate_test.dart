import 'package:flutter_test/flutter_test.dart';
import 'package:s2pass_flutter_mock/models/adapter_traffic_rate.dart';
import 'package:s2pass_flutter_mock/models/backend_api_models.dart';

void main() {
  group('AdapterTrafficRateCalculator', () {
    test('computes packet and byte rates from increasing counters', () {
      final calculator = AdapterTrafficRateCalculator();
      final t0 = DateTime(2026, 1, 1, 12);

      final first = calculator.update(
        sessionId: 's_1',
        adapterStatus: 'ready',
        counters: _counters(
          packetsFromGame: 10,
          packetsFromTransport: 20,
          bytesFromGame: 1024,
          bytesFromTransport: 2048,
        ),
        now: t0,
      );

      expect(first.hasBaseline, isFalse);
      expect(first.gameToRelayPacketsPerSecond, 0);

      final second = calculator.update(
        sessionId: 's_1',
        adapterStatus: 'ready',
        counters: _counters(
          packetsFromGame: 20,
          packetsFromTransport: 30,
          bytesFromGame: 3072,
          bytesFromTransport: 4096,
        ),
        now: t0.add(const Duration(seconds: 2)),
      );

      expect(second.hasBaseline, isTrue);
      expect(second.gameToRelayPacketsPerSecond, 5);
      expect(second.relayToGamePacketsPerSecond, 5);
      expect(second.gameToRelayKilobytesPerSecond, 1);
      expect(second.relayToGameKilobytesPerSecond, 1);
    });

    test('session change and counter reset reset the baseline', () {
      final calculator = AdapterTrafficRateCalculator();
      final t0 = DateTime(2026, 1, 1, 12);

      calculator.update(
        sessionId: 's_1',
        adapterStatus: 'ready',
        counters: _counters(packetsFromGame: 10, packetsFromTransport: 10),
        now: t0,
      );

      final sessionChanged = calculator.update(
        sessionId: 's_2',
        adapterStatus: 'ready',
        counters: _counters(packetsFromGame: 20, packetsFromTransport: 20),
        now: t0.add(const Duration(seconds: 1)),
      );
      expect(sessionChanged.hasBaseline, isFalse);

      final reset = calculator.update(
        sessionId: 's_2',
        adapterStatus: 'ready',
        counters: _counters(packetsFromGame: 1, packetsFromTransport: 1),
        now: t0.add(const Duration(seconds: 2)),
      );
      expect(reset.hasBaseline, isFalse);
    });

    test('missing counters or stopped adapter show zero safely', () {
      final calculator = AdapterTrafficRateCalculator();
      final t0 = DateTime(2026, 1, 1, 12);

      expect(
        calculator
            .update(
              sessionId: 's_1',
              adapterStatus: 'ready',
              counters: null,
              now: t0,
            )
            .gameToRelayPacketsPerSecond,
        0,
      );

      final stopped = calculator.update(
        sessionId: 's_1',
        adapterStatus: 'stopped',
        counters: _counters(packetsFromGame: 10, packetsFromTransport: 10),
        now: t0.add(const Duration(seconds: 1)),
      );

      expect(stopped.hasBaseline, isFalse);
      expect(stopped.relayToGamePacketsPerSecond, 0);
    });
  });
}

AdapterCounters _counters({
  required int packetsFromGame,
  required int packetsFromTransport,
  int? bytesFromGame,
  int? bytesFromTransport,
}) {
  return AdapterCounters(
    packetsFromGame: packetsFromGame,
    packetsToTransport: packetsFromGame,
    packetsFromTransport: packetsFromTransport,
    packetsToGame: packetsFromTransport,
    bytesFromGame: bytesFromGame,
    bytesToTransport: bytesFromGame,
    bytesFromTransport: bytesFromTransport,
    bytesToGame: bytesFromTransport,
  );
}
