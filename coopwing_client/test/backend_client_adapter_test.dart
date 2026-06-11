import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:s2pass_flutter_mock/models/backend_api_models.dart';
import 'package:s2pass_flutter_mock/services/http_backend_client.dart';
import 'package:s2pass_flutter_mock/services/mock_backend_client.dart';

void main() {
  group('v2 session models', () {
    test('ParticipantDto parses player fields with safe fallback', () {
      final participant = ParticipantDto.fromJson({
        'player_id': 'p_alice',
        'player_name': 'Alice',
        'is_host': true,
      });

      expect(participant.playerId, 'p_alice');
      expect(participant.playerName, 'Alice');
      expect(participant.isHost, isTrue);
      expect(participant.toJson(), {
        'player_id': 'p_alice',
        'player_name': 'Alice',
        'is_host': true,
      });

      final fallback = ParticipantDto.fromJson({
        'player_id': 42,
        'player_name': false,
        'is_host': 'yes',
      });
      expect(fallback.playerId, isEmpty);
      expect(fallback.playerName, isEmpty);
      expect(fallback.isHost, isFalse);
    });

    test('SessionInfo parses v2 multi-peer status fields', () {
      final session = SessionInfo.fromJson(
        _sessionJson(
          extra: {
            'player_id': 'p_bob',
            'protocol_version': 2,
            'max_players': 4,
            'participant_count': 3,
            'participants': [
              {'player_id': 'p_alice', 'player_name': 'Alice', 'is_host': true},
              {'player_id': 'p_bob', 'player_name': 'Bob', 'is_host': false},
              {
                'player_id': 'p_carol',
                'player_name': 'Carol',
                'is_host': false,
              },
            ],
            'host_player_id': 'p_alice',
            'last_room_event': 'participant_joined',
            'room_ready': true,
            'room_closed': false,
            'relay_ready': true,
            'relay_token_available': true,
            'relay_target_host': '127.0.0.1',
            'relay_target_port': 9001,
            'peer_endpoint_host': '198.51.100.44',
            'peer_endpoint_port': 42001,
            'peer_endpoint_source': 'session_diagnostics',
            'server_time': 1716192000.5,
            'secondary_ip_enabled': true,
            'secondary_ip_fallback_used': false,
            'secondary_ip_warning': 'secondary IP active',
            'relay_token': 'must_not_be_exposed',
          },
        ),
      );

      expect(session.playerId, 'p_bob');
      expect(session.protocolVersion, 2);
      expect(session.maxPlayers, 4);
      expect(session.participantCount, 3);
      expect(session.participants, hasLength(3));
      expect(session.participants.first.playerName, 'Alice');
      expect(session.participants.first.isHost, isTrue);
      expect(session.hostPlayerId, 'p_alice');
      expect(session.lastRoomEvent, 'participant_joined');
      expect(session.roomReady, isTrue);
      expect(session.roomClosed, isFalse);
      expect(session.relayReady, isTrue);
      expect(session.relayTokenAvailable, isTrue);
      expect(session.relayTargetHost, '127.0.0.1');
      expect(session.relayTargetPort, 9001);
      expect(session.peerEndpointHost, '198.51.100.44');
      expect(session.peerEndpointPort, 42001);
      expect(session.peerEndpointSource, 'session_diagnostics');
      expect(session.serverTime, 1716192000.5);
      expect(session.secondaryIpEnabled, isTrue);
      expect(session.secondaryIpFallbackUsed, isFalse);
      expect(session.secondaryIpWarning, 'secondary IP active');
      expect(session.toJson().containsKey('relay_token'), isFalse);
    });

    test('SessionInfo missing v2 fields falls back safely', () {
      final session = SessionInfo.fromJson(_sessionJson());

      expect(session.playerId, isNull);
      expect(session.protocolVersion, isNull);
      expect(session.maxPlayers, isNull);
      expect(session.participantCount, isNull);
      expect(session.participants, isEmpty);
      expect(session.hostPlayerId, isNull);
      expect(session.lastRoomEvent, isNull);
      expect(session.roomReady, isFalse);
      expect(session.roomClosed, isFalse);
      expect(session.relayReady, isFalse);
      expect(session.relayTokenAvailable, isFalse);
      expect(session.relayTargetHost, isNull);
      expect(session.relayTargetPort, isNull);
      expect(session.peerEndpointHost, isNull);
      expect(session.peerEndpointPort, isNull);
      expect(session.peerEndpointSource, isNull);
      expect(session.serverTime, isNull);
      expect(session.secondaryIpEnabled, isFalse);
      expect(session.secondaryIpFallbackUsed, isFalse);
      expect(session.secondaryIpWarning, isNull);
    });

    test('MockBackendClient returns secondary IP status fields', () async {
      final client = MockBackendClient();
      addTearDown(client.dispose);

      final session = await client.createSession(
        serverHost: '127.0.0.1',
        serverPort: 9000,
        serverUdpPort: 9001,
        playerName: 'Alice',
        gameServerPort: 27015,
        bindHost: '127.0.0.1',
        bindPort: 0,
        adapterConfig: AdapterConfig.udpExperimental(
          bindHost: '192.168.1.250',
          targetHost: '127.0.0.1',
          targetPort: 27015,
        ),
      );

      expect(session.secondaryIpEnabled, isTrue);
      expect(session.secondaryIpFallbackUsed, isFalse);
      expect(session.secondaryIpWarning, isNull);
      expect(session.toJson()['secondary_ip_enabled'], isTrue);
      expect(session.toJson()['secondary_ip_fallback_used'], isFalse);
    });
  });

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

    test('ready adapter_status parses discovery_helper_connection if present', () {
      final session = SessionInfo.fromJson(
        _sessionJson(
          adapterStatus: {
            'enabled': true,
            'status': 'ready',
            'adapter_type': 'bundle',
            'bind_host': '127.0.0.1',
            'bind_port': 40100,
            'payload_diagnostics': {
              'discovery_helper_connection': {
                'host': '127.0.0.1',
                'port': 40101,
                'udp_available': true,
              }
            }
          },
        ),
      );

      final status = session.adapterStatus!;
      expect(status.discoveryHelperConnection, isNotNull);
      expect(status.discoveryHelperConnection!['host'], '127.0.0.1');
      expect(status.discoveryHelperConnection!['port'], 40101);
      expect(status.discoveryHelperConnectionAddress, '127.0.0.1:40101');
    });

    test('ready adapter_status discovery_helper_connection handles missing/disabled gracefully', () {
      final sessionDisabled = SessionInfo.fromJson(
        _sessionJson(
          adapterStatus: {
            'enabled': true,
            'status': 'ready',
            'adapter_type': 'bundle',
            'payload_diagnostics': {
              'discovery_helper_connection': {
                'host': '127.0.0.1',
                'port': 40101,
                'udp_available': false,
              }
            }
          },
        ),
      );
      expect(sessionDisabled.adapterStatus!.discoveryHelperConnectionAddress, isNull);

      final sessionMissing = SessionInfo.fromJson(
        _sessionJson(
          adapterStatus: {
            'enabled': true,
            'status': 'ready',
            'adapter_type': 'bundle',
            'payload_diagnostics': {}
          },
        ),
      );
      expect(sessionMissing.adapterStatus!.discoveryHelperConnection, isNull);
      expect(sessionMissing.adapterStatus!.discoveryHelperConnectionAddress, isNull);
    });
  });

  group('lan discovery models', () {
    test('status parses backend fields', () {
      final status = LanDiscoveryStatus.fromJson({
        'running': true,
        'peer_id': 'peer_local',
        'instance_name': 'Co-opWinG Host',
        'service_port': 21520,
        'broadcast_port': 37020,
        'peer_count': 2,
      });

      expect(status.running, isTrue);
      expect(status.peerId, 'peer_local');
      expect(status.instanceName, 'Co-opWinG Host');
      expect(status.servicePort, 21520);
      expect(status.broadcastPort, 37020);
      expect(status.peerCount, 2);
      expect(status.toJson()['service_port'], 21520);
    });

    test('peers response parses peer list and last seen age', () {
      final response = LanDiscoveryPeersResponse.fromJson({
        'running': true,
        'peers': [
          {
            'peer_id': 'peer_neighbor',
            'name': 'Nearby Co-opWinG',
            'host': '192.168.1.23',
            'port': 21520,
            'version': '0.3.0',
            'last_seen_age_seconds': 1.1,
          },
        ],
      });

      expect(response.running, isTrue);
      expect(response.peers, hasLength(1));
      expect(response.peers.single.peerId, 'peer_neighbor');
      expect(response.peers.single.name, 'Nearby Co-opWinG');
      expect(response.peers.single.host, '192.168.1.23');
      expect(response.peers.single.port, 21520);
      expect(response.peers.single.version, '0.3.0');
      expect(response.peers.single.lastSeenAgeSeconds, 1.1);
      expect(response.toJson()['peers'], isA<List<Object?>>());
    });
  });

  group('adapter_config request bodies', () {
    test('HTTP client parses v2 participants from session response', () async {
      final session = await _withSessionResponseServer(
        _sessionJson(
          extra: {
            'protocol_version': 2,
            'participant_count': 3,
            'max_players': 4,
            'participants': [
              {'player_id': 'p_alice', 'player_name': 'Alice', 'is_host': true},
              {'player_id': 'p_bob', 'player_name': 'Bob', 'is_host': false},
              {
                'player_id': 'p_carol',
                'player_name': 'Carol',
                'is_host': false,
              },
            ],
            'relay_token_available': true,
            'relay_token': 'must_not_be_exposed',
          },
        ),
        (client) {
          return client.createSession(
            serverHost: '127.0.0.1',
            serverPort: 9000,
            serverUdpPort: 9001,
            playerName: 'Alice',
            gameServerPort: 27015,
            bindHost: '127.0.0.1',
            bindPort: 0,
          );
        },
      );

      expect(session.participants, hasLength(3));
      expect(session.participants.map((item) => item.playerName).toList(), [
        'Alice',
        'Bob',
        'Carol',
      ]);
      expect(session.relayTokenAvailable, isTrue);
      expect(session.toJson().containsKey('relay_token'), isFalse);
    });

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

    test('UDP Only adds expected adapter_config to create request', () async {
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
    });

    test('Bundle adds expected adapter_config to create request', () async {
      final capture = await _withCaptureServer((client) {
        return client.createSession(
          serverHost: '127.0.0.1',
          serverPort: 9000,
          serverUdpPort: 9001,
          playerName: 'Alice',
          gameServerPort: 27015,
          bindHost: '127.0.0.1',
          bindPort: 0,
          adapterConfig: AdapterConfig.bundle(targetPort: 27015),
        );
      });

      expect(capture.body['adapter_config'], {
        'enabled': true,
        'adapter_type': 'bundle',
        'bind_host': '127.0.0.1',
        'bind_port': 0,
        'target_host': '127.0.0.1',
        'target_port': 27015,
      });
    });

    test('Bundle no-target adds expected adapter_config to join request', () async {
      final capture = await _withCaptureServer((client) {
        return client.joinSession(
          serverHost: '127.0.0.1',
          serverPort: 9000,
          serverUdpPort: 9001,
          roomId: 'ABC234',
          playerName: 'Bob',
          gameServerHost: '127.0.0.1',
          adapterConfig: AdapterConfig.bundle(targetPort: 0),
        );
      });

      expect(capture.body.containsKey('game_server_port'), isFalse);
      expect(
        (capture.body['adapter_config'] as Map<String, Object?>)['adapter_type'],
        'bundle',
      );
      expect(
        (capture.body['adapter_config'] as Map<String, Object?>)['target_port'],
        0,
      );
    });

    test('UDP Only adds expected adapter_config to join request', () async {
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
    });

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

  group('lan discovery http client', () {
    test('calls status start stop and peers endpoints', () async {
      final status = {
        'running': false,
        'peer_id': null,
        'instance_name': 'Co-opWinG Host',
        'service_port': 21520,
        'broadcast_port': 37020,
        'peer_count': 0,
      };
      final peers = {'running': false, 'peers': <Object?>[]};

      var capture = await _withLanCapture(
        status,
        (client) => client.getLanDiscoveryStatus(),
      );
      expect(capture.method, 'GET');
      expect(capture.path, '/api/lan-discovery/status');
      expect(capture.bodyText, isEmpty);

      capture = await _withLanCapture(
        status,
        (client) => client.startLanDiscovery(),
      );
      expect(capture.method, 'POST');
      expect(capture.path, '/api/lan-discovery/start');
      expect(capture.bodyText, isEmpty);

      capture = await _withLanCapture(
        status,
        (client) => client.stopLanDiscovery(),
      );
      expect(capture.method, 'POST');
      expect(capture.path, '/api/lan-discovery/stop');
      expect(capture.bodyText, isEmpty);

      capture = await _withLanCapture(
        peers,
        (client) => client.getLanDiscoveryPeers(),
      );
      expect(capture.method, 'GET');
      expect(capture.path, '/api/lan-discovery/peers');
      expect(capture.bodyText, isEmpty);
    });
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

  // ── v0.3-J game profile HTTP client tests ──
  group('v0.3-J game profile HTTP client', () {
    _gameApiHttpTests();
  });
}

// ── v0.3-J game API test helpers / data ──────────────────────────────

void _gameApiHttpTests() {
  test('listGames calls GET /api/games and parses games', () async {
    final r = await _captureGameCall({
      'games': [_sampleGame],
    }, action: (c) => c.listGames());
    expect(r.request.method, 'GET');
    expect(r.request.path, '/api/games');
    final games = r.data;
    expect(games.length, 1);
    expect(games[0].gameId, 'abc123');
    expect(games[0].displayName, 'Test Game');
    expect(games[0].confirmedTcpPorts, [27015]);
  });

  test('createGame sends POST /api/games with correct JSON', () async {
    final r = await _captureGameCall(
      _sampleGame,
      statusCode: 201,
      action: (c) => c.createGame(
        displayName: 'Test Game',
        executablePath: r'C:\game\test.exe',
        workingDirectory: r'C:\game',
      ),
    );
    expect(r.request.method, 'POST');
    expect(r.request.path, '/api/games');
    final body = jsonDecode(r.request.bodyText) as Map<String, Object?>;
    expect(body['display_name'], 'Test Game');
    expect(body['executable_path'], r'C:\game\test.exe');
    expect(body['working_directory'], r'C:\game');
    final game = r.data;
    expect(game.gameId, 'abc123');
  });

  test('getGame calls GET /api/games/{id}', () async {
    final r = await _captureGameCall(
      _sampleGame,
      action: (c) => c.getGame('abc123'),
    );
    expect(r.request.method, 'GET');
    expect(r.request.path, '/api/games/abc123');
    expect(r.data.gameId, 'abc123');
  });

  test('deleteGame calls DELETE /api/games/{id}', () async {
    final r = await _captureGameCall({
      'deleted': true,
      'game_id': 'abc123',
    }, action: (c) => c.deleteGame('abc123'));
    expect(r.request.method, 'DELETE');
    expect(r.request.path, '/api/games/abc123');
  });

  test(
    'scanGamePorts sends POST with stage and includeLowConfidence',
    () async {
      final r = await _captureGameCall(
        _sampleScan,
        action: (c) => c.scanGamePorts(
          'abc123',
          stage: 'lobby',
          includeLowConfidence: true,
        ),
      );
      expect(r.request.method, 'POST');
      expect(r.request.path, '/api/games/abc123/scan-ports');
      final body = jsonDecode(r.request.bodyText) as Map<String, Object?>;
      expect(body['stage'], 'lobby');
      expect(body['include_low_confidence'], true);
      final scan = r.data;
      expect(scan.candidates.length, 2);
      expect(scan.candidates[0].protocol, 'tcp');
      expect(scan.candidates[0].port, 27015);
      expect(scan.candidates[0].confidence, 'high');
      expect(scan.stage, 'lobby');
    },
  );

  test('scanGamePorts with processId sends it', () async {
    final r = await _captureGameCall(
      _sampleScan,
      action: (c) =>
          c.scanGamePorts('abc123', stage: 'in_game', processId: 9999),
    );
    final body = jsonDecode(r.request.bodyText) as Map<String, Object?>;
    expect(body['process_id'], 9999);
    expect(body['stage'], 'in_game');
    expect(body['include_low_confidence'], false);
  });

  test('scanProcessPorts sends PID and parses candidates', () async {
    final r = await _captureGameCall(
      _sampleProcessPortScan,
      action: (c) => c.scanProcessPorts(4321),
    );

    expect(r.request.method, 'POST');
    expect(r.request.path, '/process-ports/scan');
    final body = jsonDecode(r.request.bodyText) as Map<String, Object?>;
    expect(body, {'pid': 4321});
    expect(r.data.pid, 4321);
    expect(r.data.candidates, hasLength(2));
    expect(r.data.candidates.first.protocol, 'tcp');
    expect(r.data.candidates.first.localPort, 27015);
    expect(r.data.candidates.last.protocol, 'udp');
  });

  test('confirmGamePorts sends POST with tcp_ports and udp_ports', () async {
    final r = await _captureGameCall(
      _sampleGame,
      action: (c) => c.confirmGamePorts(
        'abc123',
        tcpPorts: [27015, 27016],
        udpPorts: [27015, 27017],
      ),
    );
    expect(r.request.method, 'POST');
    expect(r.request.path, '/api/games/abc123/confirm-ports');
    final body = jsonDecode(r.request.bodyText) as Map<String, Object?>;
    expect(body['tcp_ports'], [27015, 27016]);
    expect(body['udp_ports'], [27015, 27017]);
    expect(r.data.gameId, 'abc123');
    expect(r.data.confirmedTcpPorts, [27015]);
  });

  test('non-2xx surfaces BackendError', () async {
    try {
      await _withGameServer(
        {
          'error': {'code': 'INTERNAL_ERROR', 'message': 'boom'},
        },
        statusCode: 500,
        action: (c) => c.listGames(),
      );
      fail('expected BackendError');
    } on BackendError catch (e) {
      expect(e.code, 'INTERNAL_ERROR');
    }
  });

  test('connection refused surfaces BackendError offline', () async {
    final client = HttpBackendClient(
      baseUrl: 'http://127.0.0.1:1',
      timeout: const Duration(milliseconds: 200),
    );
    try {
      await client.listGames();
      fail('expected BackendError');
    } on BackendError catch (e) {
      expect(e.code, 'BACKEND_OFFLINE');
    }
    client.dispose();
  });

  test('no UnimplementedError for any game API method', () async {
    final client = HttpBackendClient(
      baseUrl: 'http://127.0.0.1:1',
      timeout: const Duration(milliseconds: 100),
    );
    final actions = <Future<Object?> Function()>[
      () => client.listGames(),
      () => client.createGame(displayName: 'x', executablePath: 'y'),
      () => client.getGame('id'),
      () => client.deleteGame('id'),
      () => client.scanGamePorts('id'),
      () => client.confirmGamePorts('id', tcpPorts: [], udpPorts: []),
    ];
    for (final action in actions) {
      try {
        await action();
      } on UnimplementedError {
        fail('UnimplementedError found');
      } on Object {
        /* expected — backend offline */
      }
    }
    client.dispose();
  });

  group('Bundle traffic display', () {
    test('AdapterStatus parses Bundle rules safely', () {
      final status = AdapterStatus.fromJson({
        'enabled': true,
        'status': 'ready',
        'adapter_type': 'bundle',
        'bind_host': '127.0.0.1',
        'bind_port': 40001,
        'target_host': '127.0.0.1',
        'target_port': 27015,
        'counters': {
          'packets_from_game': 100,
          'packets_to_transport': 100,
          'packets_from_transport': 80,
          'packets_to_game': 80,
        },
        'payload_diagnostics': {
          'rules': [
            {
              'id': 'tcp_relay',
              'kind': 'tcp_relay',
              'running': true,
              'stats': {
                'packets_from_game': 50,
                'packets_to_transport': 50,
                'packets_from_transport': 40,
                'packets_to_game': 40,
              },
            },
            {
              'id': 'udp_raw',
              'kind': 'udp_raw_bridge',
              'running': true,
              'stats': {
                'packets_from_game': 50,
                'packets_to_transport': 50,
                'packets_from_transport': 40,
                'packets_to_game': 40,
              },
            },
            {
              'id': 'broadcast',
              'kind': 'udp_broadcast_forward',
              'running': true,
              'stats': {
                'packets_from_game': 10,
                'packets_to_transport': 10,
                'packets_from_transport': 5,
                'packets_to_game': 5,
              },
            },
          ],
        },
      });

      expect(status.rules, hasLength(3));
      expect(status.getRuleByKind('tcp_relay'), isNotNull);
      expect(status.getRuleByKind('udp_raw_bridge'), isNotNull);
      expect(status.getRuleByKind('udp_broadcast_forward'), isNotNull);
      expect(status.isRuleRunning('tcp_relay'), isTrue);
      expect(status.isRuleRunning('udp_raw_bridge'), isTrue);
      expect(status.isRuleRunning('udp_broadcast_forward'), isTrue);

      final tcpCounters = status.getRuleCounters('tcp_relay');
      expect(tcpCounters, isNotNull);
      expect(tcpCounters!.packetsFromGame, 50);
      expect(tcpCounters.packetsFromTransport, 40);

      final udpCounters = status.getRuleCounters('udp_raw_bridge');
      expect(udpCounters, isNotNull);
      expect(udpCounters!.packetsFromGame, 50);
      expect(udpCounters.packetsFromTransport, 40);

      final broadcastCounters = status.getRuleCounters('udp_broadcast_forward');
      expect(broadcastCounters, isNotNull);
      expect(broadcastCounters!.packetsFromGame, 10);
      expect(broadcastCounters.packetsFromTransport, 5);
    });

    test('AdapterStatus handles missing rules gracefully', () {
      final status = AdapterStatus.fromJson({
        'enabled': true,
        'status': 'ready',
        'adapter_type': 'bundle',
        'bind_host': '127.0.0.1',
        'bind_port': 40001,
      });

      expect(status.rules, isEmpty);
      expect(status.getRuleByKind('tcp_relay'), isNull);
      expect(status.getRuleCounters('tcp_relay'), isNull);
      expect(status.isRuleRunning('tcp_relay'), isFalse);
    });

    test('AdapterStatus handles missing stats in rule', () {
      final status = AdapterStatus.fromJson({
        'enabled': true,
        'status': 'ready',
        'adapter_type': 'bundle',
        'payload_diagnostics': {
          'rules': [
            {
              'id': 'tcp_relay',
              'kind': 'tcp_relay',
              'running': true,
            },
          ],
        },
      });

      expect(status.rules, hasLength(1));
      expect(status.isRuleRunning('tcp_relay'), isTrue);
      expect(status.getRuleCounters('tcp_relay'), isNull);
    });

    test('Bundle discovery_helper_connection parsed safely', () {
      final status = AdapterStatus.fromJson({
        'enabled': true,
        'status': 'ready',
        'adapter_type': 'bundle',
        'payload_diagnostics': {
          'discovery_helper_connection': {
            'host': '127.0.0.1',
            'port': 50002,
            'udp_available': true,
          },
        },
      });

      expect(status.discoveryHelperConnection, isNotNull);
      expect(status.discoveryHelperConnectionAddress, '127.0.0.1:50002');
    });

    test('Bundle local_game_connection parsed safely', () {
      final status = AdapterStatus.fromJson({
        'enabled': true,
        'status': 'ready',
        'adapter_type': 'bundle',
        'payload_diagnostics': {
          'local_game_connection': {
            'host': '127.0.0.1',
            'port': 40001,
            'tcp_available': true,
            'udp_available': true,
          },
        },
      });

      expect(status.localGameConnection, isNotNull);
      expect(status.localGameConnectionAddress, '127.0.0.1:40001');
    });

    test('Top-level counters aggregate gameplay traffic only', () {
      // This test documents that backend aggregates tcp_relay + udp_raw_bridge
      // but NOT udp_broadcast_forward into top-level counters
      final status = AdapterStatus.fromJson({
        'enabled': true,
        'status': 'ready',
        'adapter_type': 'bundle',
        'counters': {
          'packets_from_game': 100,
          'packets_to_transport': 100,
          'packets_from_transport': 80,
          'packets_to_game': 80,
        },
        'payload_diagnostics': {
          'rules': [
            {
              'id': 'tcp_relay',
              'kind': 'tcp_relay',
              'running': true,
              'stats': {
                'packets_from_game': 50,
                'packets_to_transport': 50,
                'packets_from_transport': 40,
                'packets_to_game': 40,
              },
            },
            {
              'id': 'udp_raw',
              'kind': 'udp_raw_bridge',
              'running': true,
              'stats': {
                'packets_from_game': 50,
                'packets_to_transport': 50,
                'packets_from_transport': 40,
                'packets_to_game': 40,
              },
            },
            {
              'id': 'broadcast',
              'kind': 'udp_broadcast_forward',
              'running': true,
              'stats': {
                'packets_from_game': 1000,
                'packets_to_transport': 1000,
                'packets_from_transport': 500,
                'packets_to_game': 500,
              },
            },
          ],
        },
      });

      // Top-level should be tcp_relay + udp_raw_bridge = 50+50=100
      expect(status.counters!.packetsFromGame, 100);
      expect(status.counters!.packetsFromTransport, 80);

      // Discovery helper has 1000 packets but should NOT be in top-level
      final broadcastCounters = status.getRuleCounters('udp_broadcast_forward');
      expect(broadcastCounters!.packetsFromGame, 1000);
    });

    test('Rule-level counters override empty top-level counters', () {
      // This prevents "game works but UI shows no packets" bug
      final status = AdapterStatus.fromJson({
        'enabled': true,
        'status': 'ready',
        'adapter_type': 'bundle',
        'counters': {
          'packets_from_game': 0,
          'packets_to_transport': 0,
          'packets_from_transport': 0,
          'packets_to_game': 0,
        },
        'payload_diagnostics': {
          'rules': [
            {
              'id': 'udp_raw',
              'kind': 'udp_raw_bridge',
              'running': true,
              'stats': {
                'packets_from_game': 100,
                'packets_to_transport': 100,
                'packets_from_transport': 80,
                'packets_to_game': 80,
              },
            },
          ],
        },
      });

      // UI should show UDP gameplay traffic from rule-level counters
      final udpCounters = status.getRuleCounters('udp_raw_bridge');
      expect(udpCounters, isNotNull);
      expect(udpCounters!.packetsFromGame, greaterThan(0));
      expect(status.counters!.packetsFromGame, 0);
    });

    test('TCP gameplay status shown when rule running but no traffic yet', () {
      final status = AdapterStatus.fromJson({
        'enabled': true,
        'status': 'ready',
        'adapter_type': 'bundle',
        'payload_diagnostics': {
          'rules': [
            {
              'id': 'tcp_relay',
              'kind': 'tcp_relay',
              'running': true,
              'stats': {
                'packets_from_game': 0,
                'packets_to_transport': 0,
                'packets_from_transport': 0,
                'packets_to_game': 0,
              },
            },
          ],
        },
      });

      expect(status.isRuleRunning('tcp_relay'), isTrue);
      final tcpCounters = status.getRuleCounters('tcp_relay');
      expect(tcpCounters, isNotNull);
      expect(tcpCounters!.packetsFromGame, 0);
      // UI should show "TCP gameplay: running / ready" instead of hiding it
    });
  });
}

Future<T> _withGameServer<T>(
  Map<String, Object?> response, {
  int statusCode = 200,
  required Future<T> Function(HttpBackendClient client) action,
}) async {
  final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
  final subscription = server.listen((request) async {
    request.response.statusCode = statusCode;
    request.response.headers.contentType = ContentType.json;
    request.response.write(jsonEncode(response));
    await request.response.close();
  });
  final client = HttpBackendClient(
    baseUrl: 'http://127.0.0.1:${server.port}',
    timeout: const Duration(seconds: 5),
  );
  try {
    return await action(client);
  } finally {
    client.dispose();
    await subscription.cancel();
    await server.close(force: true);
  }
}

Future<_CapturedGameCall<T>> _captureGameCall<T>(
  Map<String, Object?> response, {
  int statusCode = 200,
  required Future<T> Function(HttpBackendClient client) action,
}) async {
  final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
  final captured = Completer<_CapturedSimpleRequest>();
  final subscription = server.listen((request) async {
    final text = await utf8.decoder.bind(request).join();
    captured.complete(
      _CapturedSimpleRequest(request.method, request.uri.path, text),
    );
    request.response.statusCode = statusCode;
    request.response.headers.contentType = ContentType.json;
    request.response.write(jsonEncode(response));
    await request.response.close();
  });
  final client = HttpBackendClient(
    baseUrl: 'http://127.0.0.1:${server.port}',
    timeout: const Duration(seconds: 5),
  );
  try {
    final data = await action(client);
    return _CapturedGameCall(await captured.future, data);
  } finally {
    client.dispose();
    await subscription.cancel();
    await server.close(force: true);
  }
}

class _CapturedGameCall<T> {
  const _CapturedGameCall(this.request, this.data);
  final _CapturedSimpleRequest request;
  final T data;
}

final _sampleGame = {
  'game_id': 'abc123',
  'display_name': 'Test Game',
  'executable_path': r'C:\game\test.exe',
  'working_directory': r'C:\game',
  'launch_args': ['-windowed'],
  'confirmed_tcp_ports': [27015],
  'confirmed_udp_ports': [27015],
  'candidate_ports': <Map<String, Object?>>[
    {
      'protocol': 'tcp',
      'port': 27015,
      'confidence': 'high',
      'reason': 'TCP LISTEN',
    },
  ],
  'notes': 'test',
  'created_at': 100.0,
  'updated_at': 200.0,
};

final _sampleScan = {
  'candidates': [
    {
      'protocol': 'tcp',
      'port': 27015,
      'process_id': 999,
      'process_name': 'hl2',
      'local_address': '0.0.0.0',
      'confidence': 'high',
      'reason': 'TCP LISTEN on 0.0.0.0:27015',
    },
    {
      'protocol': 'udp',
      'port': 27015,
      'process_id': 999,
      'process_name': 'hl2',
      'local_address': '0.0.0.0',
      'confidence': 'high',
      'reason': 'UDP bound 0.0.0.0:27015',
    },
  ],
  'stage': 'lobby',
  'scanned_at': 300.0,
  'process_name': 'hl2',
  'process_id': 999,
};

final _sampleProcessPortScan = {
  'pid': 4321,
  'candidates': [
    {
      'pid': 4321,
      'protocol': 'tcp',
      'local_address': '0.0.0.0',
      'local_port': 27015,
      'state': 'Listen',
      'confidence': 'high',
      'reason': 'TCP LISTEN on 0.0.0.0:27015',
    },
    {
      'pid': 4321,
      'protocol': 'udp',
      'local_address': '0.0.0.0',
      'local_port': 27016,
      'confidence': 'high',
      'reason': 'UDP bound 0.0.0.0:27016',
    },
  ],
};

// ── helpers used by original adapter / HTTP session tests ────────────

Future<_CapturedRequest> _withCaptureServer(
  Future<Object?> Function(HttpBackendClient client) action,
) async {
  final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
  final captured = Completer<_CapturedRequest>();
  final subscription = server.listen((request) async {
    final bodyText = await utf8.decoder.bind(request).join();
    final body =
        (bodyText.isNotEmpty
            ? jsonDecode(bodyText) as Map<String, Object?>?
            : null) ??
        {};
    captured.complete(_CapturedRequest(request.uri.path, body));
    request.response.statusCode = 201;
    request.response.headers.contentType = ContentType.json;
    request.response.write(
      jsonEncode({
        'session_id': 's_test',
        'role': 'create',
        'status': 'running',
        'room_id': 'TESTRM',
        'player_name': 'Test',
        'server_host': '127.0.0.1',
        'server_port': 9000,
        'server_udp_port': 9001,
        'adapter_host': '127.0.0.1',
        'adapter_port': 0,
        'game_server_host': '127.0.0.1',
        'game_server_port': 27015,
        'force_relay': true,
        'created_at': 1.0,
        'updated_at': 1.0,
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
    return captured.future;
  } finally {
    client.dispose();
    await subscription.cancel();
    await server.close(force: true);
  }
}

Future<BackendError> _withErrorServer(
  Future<Object?> Function(HttpBackendClient client) action,
) async {
  final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
  final completer = Completer<BackendError>();
  final subscription = server.listen((request) async {
    request.response.statusCode = 400;
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
  } on BackendError catch (e) {
    completer.complete(e);
  }
  client.dispose();
  await subscription.cancel();
  await server.close(force: true);
  return completer.future;
}

// ── helper used by Dart boundary tests ──────────────────────────────

Future<_CapturedSimpleRequest> _withLanCapture(
  Map<String, Object?> response,
  Future<Object?> Function(HttpBackendClient client) action,
) async {
  final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
  final captured = Completer<_CapturedSimpleRequest>();
  final subscription = server.listen((request) async {
    final text = await utf8.decoder.bind(request).join();
    captured.complete(
      _CapturedSimpleRequest(request.method, request.uri.path, text),
    );
    request.response.headers.contentType = ContentType.json;
    request.response.write(jsonEncode(response));
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

Map<String, Object?> _sessionJson({
  Map<String, Object?>? adapterStatus,
  Map<String, Object?>? extra,
}) {
  final map = <String, Object?>{
    'session_id': 's_test',
    'role': 'create',
    'status': 'running',
    'room_id': 'TESTRM',
    'player_name': 'Test',
    'server_host': '127.0.0.1',
    'server_port': 9000,
    'server_udp_port': 9001,
    'adapter_host': '127.0.0.1',
    'adapter_port': 0,
    'game_server_host': '127.0.0.1',
    'game_server_port': 27015,
    'force_relay': true,
    'created_at': 1.0,
    'updated_at': 1.0,
  };
  if (adapterStatus != null) {
    map['adapter_status'] = adapterStatus;
  }
  if (extra != null) {
    map.addAll(extra);
  }
  return map;
}

Future<SessionInfo> _withSessionResponseServer(
  Map<String, Object?> response,
  Future<Object?> Function(HttpBackendClient client) action,
) async {
  final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
  final subscription = server.listen((request) async {
    request.response.statusCode = 200;
    request.response.headers.contentType = ContentType.json;
    request.response.write(jsonEncode(response));
    await request.response.close();
  });
  final client = HttpBackendClient(
    baseUrl: 'http://127.0.0.1:${server.port}',
    timeout: const Duration(seconds: 5),
  );
  try {
    return await action(client) as SessionInfo;
  } finally {
    client.dispose();
    await subscription.cancel();
    await server.close(force: true);
  }
}

String _joined(List<String> parts, String sep) => parts.join(sep);

// ── original helpers (preserved) ────────────────────────────────────

class _CapturedRequest {
  const _CapturedRequest(this.path, this.body);
  final String path;
  final Map<String, Object?> body;
}

class _CapturedSimpleRequest {
  const _CapturedSimpleRequest(this.method, this.path, this.bodyText);
  final String method;
  final String path;
  final String bodyText;
}
