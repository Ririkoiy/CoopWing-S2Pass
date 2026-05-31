import 'backend_api_models.dart';

class AdapterTrafficRate {
  const AdapterTrafficRate({
    required this.hasBaseline,
    required this.gameToRelayPacketsPerSecond,
    required this.relayToGamePacketsPerSecond,
    this.gameToRelayKilobytesPerSecond,
    this.relayToGameKilobytesPerSecond,
  });

  final bool hasBaseline;
  final double gameToRelayPacketsPerSecond;
  final double relayToGamePacketsPerSecond;
  final double? gameToRelayKilobytesPerSecond;
  final double? relayToGameKilobytesPerSecond;

  bool get hasByteRates =>
      gameToRelayKilobytesPerSecond != null &&
      relayToGameKilobytesPerSecond != null;

  static const zero = AdapterTrafficRate(
    hasBaseline: false,
    gameToRelayPacketsPerSecond: 0,
    relayToGamePacketsPerSecond: 0,
  );
}

class AdapterTrafficRateCalculator {
  String? _sessionId;
  AdapterCounters? _counters;
  DateTime? _timestamp;

  AdapterTrafficRate update({
    required String sessionId,
    required String adapterStatus,
    required AdapterCounters? counters,
    required DateTime now,
  }) {
    if (counters == null || adapterStatus != 'ready') {
      reset();
      return AdapterTrafficRate.zero;
    }

    final previousSessionId = _sessionId;
    final previousCounters = _counters;
    final previousTimestamp = _timestamp;
    _sessionId = sessionId;
    _counters = counters;
    _timestamp = now;

    if (previousSessionId != sessionId ||
        previousCounters == null ||
        previousTimestamp == null ||
        _countersDecreased(previousCounters, counters)) {
      return AdapterTrafficRate.zero;
    }

    final seconds = now.difference(previousTimestamp).inMilliseconds / 1000;
    if (seconds <= 0) return AdapterTrafficRate.zero;

    final gamePackets =
        (counters.packetsFromGame - previousCounters.packetsFromGame) / seconds;
    final relayPackets =
        (counters.packetsFromTransport -
            previousCounters.packetsFromTransport) /
        seconds;

    double? gameKbps;
    double? relayKbps;
    if (counters.hasByteCounters && previousCounters.hasByteCounters) {
      gameKbps =
          ((counters.bytesFromGame! - previousCounters.bytesFromGame!) / 1024) /
          seconds;
      relayKbps =
          ((counters.bytesFromTransport! -
                  previousCounters.bytesFromTransport!) /
              1024) /
          seconds;
    }

    return AdapterTrafficRate(
      hasBaseline: true,
      gameToRelayPacketsPerSecond: gamePackets,
      relayToGamePacketsPerSecond: relayPackets,
      gameToRelayKilobytesPerSecond: gameKbps,
      relayToGameKilobytesPerSecond: relayKbps,
    );
  }

  void reset() {
    _sessionId = null;
    _counters = null;
    _timestamp = null;
  }

  bool _countersDecreased(AdapterCounters previous, AdapterCounters current) {
    if (current.packetsFromGame < previous.packetsFromGame ||
        current.packetsToTransport < previous.packetsToTransport ||
        current.packetsFromTransport < previous.packetsFromTransport ||
        current.packetsToGame < previous.packetsToGame) {
      return true;
    }
    if (previous.hasByteCounters && current.hasByteCounters) {
      return current.bytesFromGame! < previous.bytesFromGame! ||
          current.bytesToTransport! < previous.bytesToTransport! ||
          current.bytesFromTransport! < previous.bytesFromTransport! ||
          current.bytesToGame! < previous.bytesToGame!;
    }
    return false;
  }
}
