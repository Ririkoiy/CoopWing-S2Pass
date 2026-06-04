import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:s2pass_flutter_mock/models/backend_api_models.dart';
import 'package:s2pass_flutter_mock/services/http_backend_client.dart';

void main() {
  group('adapter_status parsing', () {
    test('absent adapter_status parses as unconfigured null', () {
      final session = SessionInfo.fromJson(_sessionJson());

      expect(session.adapterStatus, isNull);
    });

    test('disabled adapter_status parses successfully', () {
      final session = SessionInfo.fromJson(
        _sessionJson(adapterStatus: {'enabled': false, 'status': 'disabled'}),
      );

      expect(session.adapterStatus, isNotNull);
      expect(session.adapterStatus!.enabled, isFalse);
      expect(session.adapterStatus!.status, 'disabled');
    });

    test('ready adapter_status parses counters and ignores extras', () {
      final session = SessionInfo.fromJson(
        _sessionJson(
          adapterStatus: {
            'enabled': true,
            'status': 'ready',
            'adapter_type': 'local_udp_bridge',
            'bind_host': '127.0.0.1',
            'bind_port': 40100,
            'target_host': '127.0.0.1',
            'target_port': 40200,
            'counters': {
              'packets_from_game': 6,
              'packets_to_transport': 6,
              'packets_from_transport': 5,
              'packets_to_game': 5,
              'bytes_from_game': 600,
              'bytes_to_transport': 600,
              'bytes_from_transport': 500,
              'bytes_to_game': 500,
              'ignored_counter': 99,
            },
            'error': null,
            'ignored_field': 'safe',
          },
        ),
      );

      final status = session.adapterStatus!;
      expect(status.enabled, isTrue);
      expect(status.status, 'ready');
      expect(status.adapterType, 'local_udp_bridge');
      expect(status.bindHost, '127.0.0.1');
      expect(status.bindPort, 40100);
      expect(status.targetHost, '127.0.0.1');
      expect(status.targetPort, 40200);
      expect(status.counters!.packetsFromGame, 6);
      expect(status.counters!.packetsToTransport, 6);
      expect(status.counters!.packetsFromTransport, 5);
      expect(status.counters!.packetsToGame, 5);
      expect(status.counters!.bytesFromGame, 600);
      expect(status.counters!.bytesToTransport, 600);
      expect(status.counters!.bytesFromTransport, 500);
      expect(status.counters!.bytesToGame, 500);
    });

    test(
      'error adapter_status parses backend-local error code and message',
      () {
        final session = SessionInfo.fromJson(
          _sessionJson(
            adapterStatus: {
              'enabled': true,
              'status': 'error',
              'adapter_type': 'local_udp_bridge',
              'bind_host': '127.0.0.1',
              'bind_port': 40100,
              'target_host': '127.0.0.1',
              'target_port': 40200,
              'counters': {
                'packets_from_game': 0,
                'packets_to_transport': 0,
                'packets_from_transport': 0,
                'packets_to_game': 0,
              },
              'error': {
                'code': 'ADAPTER_BIND_FAILED',
                'message': 'Failed to bind UDP socket to 127.0.0.1:40100',
              },
            },
          ),
        );

        expect(session.adapterStatus!.status, 'error');
        expect(session.adapterStatus!.error!.code, 'ADAPTER_BIND_FAILED');
        expect(
          session.adapterStatus!.error!.message,
          'Failed to bind UDP socket to 127.0.0.1:40100',
        );
      },
    );
  });

  group('adapter_config request bodies', () {
    test('Adapter Off omits adapter_config from create request', () async {
      final capture = await _withCaptureServer((client) {
        return client.createSession(
          serverHost: '127.0.0.1',
          serverPort: 9000,
          serverUdpPort: 9001,
          playerName: 'Alice',
          gameServerPort: 27015,
          bindHost: '127.0.0.1',
          bindPort: 0,
        );
      });

      expect(capture.path, '/sessions/create');
      expect(capture.body['game_server_port'], 27015);
      expect(capture.body['force_relay'], isTrue);
      expect(capture.body.containsKey('adapter_config'), isFalse);
    });

    test(
      'UDP Experimental adds expected adapter_config to create request',
      () async {
        final capture = await _withCaptureServer((client) {
          return client.createSession(
            serverHost: '127.0.0.1',
            serverPort: 9000,
            serverUdpPort: 9001,
            playerName: 'Alice',
            gameServerPort: 27015,
            bindHost: '127.0.0.1',
            bindPort: 0,
            adapterConfig: AdapterConfig.udpExperimental(
              targetHost: '127.0.0.1',
              targetPort: 27015,
            ),
          );
        });

        expect(capture.path, '/sessions/create');
        expect(capture.body['game_server_port'], 27015);
        expect(capture.body['adapter_config'], {
          'enabled': true,
          'adapter_type': 'local_udp_bridge',
          'bind_host': '127.0.0.1',
          'bind_port': 0,
          'target_host': '127.0.0.1',
          'target_port': 27015,
        });
        expect(
          (capture.body['adapter_config']!
              as Map<String, Object?>)['adapter_type'],
          isNot('tcp_forward'),
        );
      },
    );

    test(
      'UDP Experimental adds expected adapter_config to join request',
      () async {
        final capture = await _withCaptureServer((client) {
          return client.joinSession(
            serverHost: '127.0.0.1',
            serverPort: 9000,
            serverUdpPort: 9001,
            roomId: 'ABC234',
            playerName: 'Bob',
            gameServerHost: '127.0.0.1',
            adapterConfig: const AdapterConfig(
              enabled: true,
              adapterType: 'local_udp_bridge',
              bindHost: '127.0.0.1',
              bindPort: 0,
              targetHost: '127.0.0.1',
            ),
          );
        });

        expect(capture.path, '/sessions/join');
        expect(capture.body.containsKey('game_server_port'), isFalse);
        expect(capture.body['force_relay'], isTrue);
        expect(capture.body['adapter_config'], {
          'enabled': true,
          'adapter_type': 'local_udp_bridge',
          'bind_host': '127.0.0.1',
          'bind_port': 0,
          'target_host': '127.0.0.1',
        });
      },
    );

    test('TCP Relay adds expected adapter_config to create request', () async {
      final capture = await _withCaptureServer((client) {
        return client.createSession(
          serverHost: '127.0.0.1',
          serverPort: 9000,
          serverUdpPort: 9001,
          playerName: 'Alice',
          gameServerPort: 25565,
          bindHost: '127.0.0.1',
          bindPort: 0,
          adapterConfig: AdapterConfig.tcpRelay(
            targetHost: '127.0.0.1',
            targetPort: 25565,
          ),
        );
      });

      expect(capture.path, '/sessions/create');
      expect(capture.body['game_server_port'], 25565);
      expect(capture.body['adapter_config'], {
        'enabled': true,
        'adapter_type': 'tcp_relay',
        'bind_host': '127.0.0.1',
        'bind_port': 0,
        'target_host': '127.0.0.1',
        'target_port': 25565,
      });
    });

    test('TCP Relay adds expected adapter_config to join request', () async {
      final capture = await _withCaptureServer((client) {
        return client.joinSession(
          serverHost: '127.0.0.1',
          serverPort: 9000,
          serverUdpPort: 9001,
          roomId: 'ABC234',
          playerName: 'Bob',
          gameServerHost: '127.0.0.1',
          adapterConfig: AdapterConfig.tcpRelay(),
        );
      });

      expect(capture.path, '/sessions/join');
      expect(capture.body['adapter_config'], {
        'enabled': true,
        'adapter_type': 'tcp_relay',
        'bind_host': '127.0.0.1',
        'bind_port': 0,
        'target_host': '127.0.0.1',
      });
    });

    test(
      'TCP Forward adds expected adapter_config to create request',
      () async {
        final capture = await _withCaptureServer((client) {
          return client.createSession(
            serverHost: '127.0.0.1',
            serverPort: 9000,
            serverUdpPort: 9001,
            playerName: 'Alice',
            gameServerPort: 25565,
            bindHost: '127.0.0.1',
            bindPort: 0,
            adapterConfig: AdapterConfig.tcpForward(
              targetHost: '127.0.0.1',
              targetPort: 25565,
            ),
          );
        });

        expect(capture.path, '/sessions/create');
        expect(capture.body['game_server_port'], 25565);
        expect(capture.body['adapter_config'], {
          'enabled': true,
          'adapter_type': 'tcp_forward',
          'bind_host': '127.0.0.1',
          'bind_port': 0,
          'target_host': '127.0.0.1',
          'target_port': 25565,
        });
        expect(
          (capture.body['adapter_config']!
              as Map<String, Object?>)['target_port'],
          isNot(25566),
        );
      },
    );

    test('Create request can opt out of force relay', () async {
      final capture = await _withCaptureServer((client) {
        return client.createSession(
          serverHost: '127.0.0.1',
          serverPort: 9000,
          serverUdpPort: 9001,
          playerName: 'Alice',
          gameServerPort: 27015,
          bindHost: '127.0.0.1',
          bindPort: 0,
          forceRelay: false,
        );
      });

      expect(capture.path, '/sessions/create');
      expect(capture.body['force_relay'], isFalse);
    });

    test('Join request can opt out of force relay', () async {
      final capture = await _withCaptureServer((client) {
        return client.joinSession(
          serverHost: '127.0.0.1',
          serverPort: 9000,
          serverUdpPort: 9001,
          roomId: 'ABC234',
          playerName: 'Bob',
          gameServerHost: '127.0.0.1',
          forceRelay: false,
        );
      });

      expect(capture.path, '/sessions/join');
      expect(capture.body['force_relay'], isFalse);
    });
  });

  test('HTTP 400 validation errors preserve backend error code', () async {
    final error = await _withErrorServer((client) {
      return client.createSession(
        serverHost: '127.0.0.1',
        serverPort: 9000,
        serverUdpPort: 9001,
        playerName: 'Alice',
        gameServerPort: 27015,
        bindHost: '127.0.0.1',
        bindPort: 0,
      );
    });

    expect(error.code, 'INVALID_REQUEST');
    expect(error.message, 'game_server_port required');
    expect(error.code, isNot('BACKEND_OFFLINE'));
  });

  test(
    'Dart boundary keeps protocol and UDP socket details out of Flutter',
    () {
      final contents = Directory('lib')
          .listSync(recursive: true)
          .whereType<File>()
          .where((file) => file.path.endsWith('.dart'))
          .map((file) => file.readAsStringSync())
          .join('\n');

      expect(contents, isNot(contains(_joined(['relay', 'token'], '_'))));
      expect(contents, isNot(contains(_joined(['CREATE', 'ROOM'], '_'))));
      expect(contents, isNot(contains(_joined(['JOIN', 'ROOM'], '_'))));
      expect(contents, isNot(contains(_joined(['RELAY', 'ENABLED'], '_'))));
      expect(
        contents,
        isNot(contains(_joined(['Raw', 'Datagram', 'Socket'], ''))),
      );
      expect(contents, isNot(contains(_joined(['Datagram', 'Socket'], ''))));
    },
  );
}

String _joined(List<String> parts, String separator) => parts.join(separator);

Map<String, Object?> _sessionJson({Map<String, Object?>? adapterStatus}) {
  return {
    'session_id': 's_test',
    'role': 'create',
    'status': 'running',
    'room_id': 'ABC234',
    'player_name': 'Alice',
    'server_host': '127.0.0.1',
    'server_port': 9000,
    'server_udp_port': 9001,
    'adapter_host': '127.0.0.1',
    'adapter_port': 0,
    'game_server_host': '127.0.0.1',
    'game_server_port': 40100,
    'force_relay': true,
    'created_at': 1,
    'updated_at': 2,
    'stats': {},
    if (adapterStatus != null) 'adapter_status': adapterStatus,
  };
}

Future<_CapturedRequest> _withCaptureServer(
  Future<SessionInfo> Function(HttpBackendClient client) action,
) async {
  final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
  final captured = Completer<_CapturedRequest>();
  final subscription = server.listen((request) async {
    final text = await utf8.decoder.bind(request).join();
    final decoded = jsonDecode(text) as Map<String, Object?>;
    captured.complete(_CapturedRequest(request.uri.path, decoded));
    request.response.headers.contentType = ContentType.json;
    request.response.write(jsonEncode(_sessionJson()));
    await request.response.close();
  });
  final client = HttpBackendClient(
    baseUrl: 'http://127.0.0.1:${server.port}',
    timeout: const Duration(seconds: 5),
  );

  try {
    await action(client);
    return captured.future;
  } finally {
    client.dispose();
    await subscription.cancel();
    await server.close(force: true);
  }
}

Future<BackendError> _withErrorServer(
  Future<SessionInfo> Function(HttpBackendClient client) action,
) async {
  final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
  final subscription = server.listen((request) async {
    await utf8.decoder.bind(request).join();
    request.response.statusCode = HttpStatus.badRequest;
    request.response.headers.contentType = ContentType.json;
    request.response.write(
      jsonEncode({
        'error': {
          'code': 'INVALID_REQUEST',
          'message': 'game_server_port required',
        },
      }),
    );
    await request.response.close();
  });
  final client = HttpBackendClient(
    baseUrl: 'http://127.0.0.1:${server.port}',
    timeout: const Duration(seconds: 5),
  );

  try {
    await action(client);
    fail('Expected BackendError');
  } on BackendError catch (error) {
    return error;
  } finally {
    client.dispose();
    await subscription.cancel();
    await server.close(force: true);
  }
}

class _CapturedRequest {
  const _CapturedRequest(this.path, this.body);

  final String path;
  final Map<String, Object?> body;
}
