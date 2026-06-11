import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../models/adapter_traffic_rate.dart';
import '../models/backend_api_models.dart';
import '../models/doctor_report.dart';
import '../services/backend_client.dart';
import '../services/localization.dart';

enum _SessionMode { create, join }

enum AdapterMode { bundle, udpOnly, tcpOnly }

typedef _AdapterMode = AdapterMode;

class BackendBridgePanel extends StatefulWidget {
  const BackendBridgePanel({
    super.key,
    required this.client,
    this.defaultServerHost = localPreviewRelayHost,
    required this.onRunDiagnostics,
    this.backendApiPort = defaultBackendHttpPort,
    this.leadingContent,
    this.initialMode,
  });

  static const defaultRelayRootHost = String.fromEnvironment(
    'COOPWING_DEFAULT_RELAY_HOST',
    defaultValue: '120.27.210.184',
  );
  static const localPreviewRelayHost = defaultRelayRootHost;
  static const defaultRelayTcpPort = 9000;
  static const defaultRelayUdpPort = 9001;
  static const defaultBackendHttpHost = '127.0.0.1';
  static const defaultBackendHttpPort = 21520;
  static const defaultGameBindHost = '127.0.0.1';

  final BackendClient client;
  final String defaultServerHost;
  final Future<DoctorReport> Function() onRunDiagnostics;
  final int backendApiPort;
  final Widget? leadingContent;

  /// Pre-select 'create' or 'join' mode. Ignored if null.
  final String? initialMode;

  @override
  State<BackendBridgePanel> createState() => _BackendBridgePanelState();
}

class _BackendBridgePanelState extends State<BackendBridgePanel> {
  late final TextEditingController _serverHostController;
  late final TextEditingController _backendHostController;
  late final TextEditingController _backendPortController;
  final TextEditingController _serverPortController = TextEditingController(
    text: BackendBridgePanel.defaultRelayTcpPort.toString(),
  );
  final TextEditingController _serverUdpPortController = TextEditingController(
    text: BackendBridgePanel.defaultRelayUdpPort.toString(),
  );
  final TextEditingController _playerNameController = TextEditingController();
  final TextEditingController _roomController = TextEditingController();
  final TextEditingController _gameServerHostController = TextEditingController(
    text: BackendBridgePanel.defaultGameBindHost,
  );
  final TextEditingController _gameServerPortController =
      TextEditingController();
  final TextEditingController _adapterTargetHostController =
      TextEditingController(text: BackendBridgePanel.defaultGameBindHost);
  final TextEditingController _adapterTargetPortController =
      TextEditingController();
  final TextEditingController _secondaryIpController = TextEditingController();
  final TextEditingController _secondaryIpInterfaceController =
      TextEditingController();
  final TextEditingController _secondaryIpPrefixController =
      TextEditingController(text: '24');
  final TextEditingController _pidController = TextEditingController();

  HealthStatus _health = HealthStatus.offline();
  LanDiscoveryStatus _lanDiscoveryStatus = LanDiscoveryStatus.stopped();
  List<LanPeerDto> _lanDiscoveryPeers = const [];
  SecondaryIpRecommendation? _secondaryIpRecommendation;
  SessionInfo? _session;
  List<SessionEvent> _events = const [];
  BackendError? _error;
  String? _pendingActionText;
  AdapterTrafficRate _trafficRate = AdapterTrafficRate.zero;
  final AdapterTrafficRateCalculator _trafficRateCalculator =
      AdapterTrafficRateCalculator();
  _SessionMode _mode = _SessionMode.create;
  _AdapterMode _adapterMode = _AdapterMode.bundle;
  bool _forceRelay = true;
  bool _secondaryIpEnabledForNextSession = false;
  Map<String, dynamic>? _secondaryIpStatus;
  List<ProcessPortCandidate> _processPortCandidates = const [];
  ProcessPortCandidate? _selectedProcessPortCandidate;
  String? _processPortHint;
  int _roomTabIndex = 0;
  bool _busy = false;
  bool _waitingForBackend = false;
  bool _pollingSession = false;
  Timer? _sessionPollTimer;
  Timer? _healthRetryTimer;
  int _healthRetryAttempts = 0;

  bool get _backendOnline => _health.isOnline;

  bool get _sessionStatusIsActive {
    return switch (_session?.status) {
      'starting' ||
      'room_created' ||
      'room_joined' ||
      'room_ready' ||
      'relay_ready' ||
      'running' => true,
      _ => false,
    };
  }

  bool get _hasPlayerName => _playerNameController.text.trim().isNotEmpty;

  bool get _hasRoomId => _roomController.text.trim().isNotEmpty;

  bool get _hasValidGamePort {
    final port = int.tryParse(_gameServerPortController.text.trim());
    return port != null && port >= 1 && port <= 65535;
  }

  bool get _canCreate =>
      !_busy &&
      !_sessionStatusIsActive &&
      _hasPlayerName &&
      _hasValidGamePort;

  bool get _canJoin =>
      !_busy &&
      !_sessionStatusIsActive &&
      _hasPlayerName &&
      _hasRoomId;
  bool get _canReadSession => !_busy && _backendOnline && _session != null;
  bool get _canStop => !_busy && _backendOnline && _sessionStatusIsActive;

  @override
  void initState() {
    super.initState();
    _serverHostController = TextEditingController(
      text: widget.defaultServerHost,
    );
    _backendHostController = TextEditingController(
      text: BackendBridgePanel.defaultBackendHttpHost,
    );
    _backendPortController = TextEditingController(
      text: widget.backendApiPort.toString(),
    );
    if (widget.initialMode == 'join') {
      _mode = _SessionMode.join;
    }
    _playerNameController.addListener(_handlePlayerNameChanged);
    _gameServerPortController.addListener(_handlePlayerNameChanged);
    _roomController.addListener(_handlePlayerNameChanged);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _checkHealth();
    });
  }

  @override
  void didUpdateWidget(covariant BackendBridgePanel oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.initialMode != widget.initialMode &&
        widget.initialMode != null &&
        !_busy &&
        !_sessionStatusIsActive) {
      _mode = widget.initialMode == 'join'
          ? _SessionMode.join
          : _SessionMode.create;
    }
    if (oldWidget.backendApiPort != widget.backendApiPort &&
        !_sessionStatusIsActive) {
      _backendPortController.text = widget.backendApiPort.toString();
    }
    if (oldWidget.defaultServerHost != widget.defaultServerHost &&
        !_sessionStatusIsActive &&
        _serverHostController.text == oldWidget.defaultServerHost) {
      _serverHostController.text = widget.defaultServerHost;
    }
  }

  @override
  void dispose() {
    _playerNameController.removeListener(_handlePlayerNameChanged);
    _gameServerPortController.removeListener(_handlePlayerNameChanged);
    _roomController.removeListener(_handlePlayerNameChanged);
    _serverHostController.dispose();
    _backendHostController.dispose();
    _backendPortController.dispose();
    _serverPortController.dispose();
    _serverUdpPortController.dispose();
    _playerNameController.dispose();
    _roomController.dispose();
    _gameServerHostController.dispose();
    _gameServerPortController.dispose();
    _adapterTargetHostController.dispose();
    _adapterTargetPortController.dispose();
    _secondaryIpController.dispose();
    _secondaryIpInterfaceController.dispose();
    _secondaryIpPrefixController.dispose();
    _pidController.dispose();
    _sessionPollTimer?.cancel();
    _healthRetryTimer?.cancel();
    super.dispose();
  }

  void _handlePlayerNameChanged() {
    if (mounted) {
      setState(() {});
    }
  }

  @override
  Widget build(BuildContext context) {
    final session = _session;
    final scheme = Theme.of(context).colorScheme;
    final loc = Localization();

    final roomConnectionCard = Card(
      child: Padding(
        padding: const EdgeInsets.all(18),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            _RoomConnectionHeader(
              health: _health,
              busy: _busy,
              waitingForBackend: _waitingForBackend,
              onRefresh: () => _checkHealth(),
            ),
            if (_pendingActionText != null) ...[
              const SizedBox(height: 10),
              Row(
                children: [
                  const SizedBox(
                    width: 16,
                    height: 16,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  ),
                  const SizedBox(width: 10),
                  Text(
                    _pendingActionText!,
                    style: TextStyle(fontSize: 13, color: scheme.primary),
                  ),
                ],
              ),
            ],
            if (_error != null) ...[
              const SizedBox(height: 10),
              _ErrorBanner(error: _error!),
            ],
            const SizedBox(height: 10),
            // ---- Tab row ----
            Row(
              children: [
                _TabChip(
                  label: loc.get('roomTabLabel'),
                  selected: _roomTabIndex == 0,
                  onTap: () => setState(() => _roomTabIndex = 0),
                ),
                const SizedBox(width: 8),
                _TabChip(
                  label: loc.get('advancedTabLabel'),
                  selected: _roomTabIndex == 1,
                  onTap: () => setState(() => _roomTabIndex = 1),
                ),
              ],
            ),
            const SizedBox(height: 14),
            // ---- Tab content ----
            if (_roomTabIndex == 0) ...[
              // ═══ ROOM TAB ═══
              if (_sessionStatusIsActive && session != null)
                _ActiveSessionCard(
                  session: session,
                  health: _health,
                  canStop: _canStop,
                  onStop: _stopSession,
                  onReset: _confirmResetDisplay,
                  onCopyRoomId: _copyRoomId,
                  onCopyAdapterBind: _copyAdapterBind,
                  onCopyAdapterTarget: _copyAdapterTarget,
                  onCopyRelayRoot: _copyRelayRoot,
                  trafficRate: _trafficRate,
                )
              else ...[
                _ModeSelector(
                  mode: _mode,
                  enabled: !_busy,
                  onChanged: (mode) => setState(() => _mode = mode),
                ),
                const SizedBox(height: 12),
                _ModeForm(
                  mode: _mode,
                  playerNameController: _playerNameController,
                  gameServerPortController: _gameServerPortController,
                  roomController: _roomController,
                  forceRelay: _forceRelay,
                  forceRelayEnabled: !_busy,
                  onForceRelayChanged: (value) {
                    setState(() => _forceRelay = value);
                  },
                  canCreate: _canCreate,
                  canJoin: _canJoin,
                  onCreate: _createSession,
                  onJoin: _joinSession,
                ),
              ],
              if (!_backendOnline && !_busy) ...[
                const SizedBox(height: 10),
                Text(
                  loc.get('backend_offline_note'),
                  style: TextStyle(fontSize: 12, color: scheme.error),
                ),
              ],
            ] else ...[
              _PidPortDetectionSection(
                pidController: _pidController,
                candidates: _processPortCandidates,
                selectedCandidate: _selectedProcessPortCandidate,
                hint: _processPortHint,
                controlsEnabled:
                    _backendOnline && !_busy && !_sessionStatusIsActive,
                onScan: _scanProcessPorts,
                onSelected: (candidate) {
                  setState(() {
                    _selectedProcessPortCandidate = candidate;
                    _processPortHint = null;
                  });
                },
                onApply: _applySelectedProcessPort,
              ),
              const SizedBox(height: 14),
              // ═══ ADVANCED TAB ═══
              _LanDiscoverySection(
                status: _lanDiscoveryStatus,
                peers: _lanDiscoveryPeers,
                backendOnline: _backendOnline,
                busy: _busy,
                onStart: _startLanDiscovery,
                onStop: _stopLanDiscovery,
                onRefreshPeers: _refreshLanDiscoveryPeers,
              ),
              const SizedBox(height: 14),
              _SecondaryIpSetupCard(
                health: _health,
                recommendation: _secondaryIpRecommendation,
                session: session,
                backendApiAddress:
                    '${_backendHostController.text.trim()}:${_backendPortController.text.trim()}',
                armedForNextSession: _secondaryIpEnabledForNextSession,
                controlsEnabled: !_busy && !_sessionStatusIsActive,
                onAutoSelect: _backendOnline ? _autoSelectSecondaryIp : null,
                onEnable: _backendOnline
                    ? _enableSecondaryIpForNextSession
                    : null,
                onRelease: (_backendOnline) ? _releaseSecondaryIp : null,
                secondaryIpStatus: _secondaryIpStatus,
              ),
              const SizedBox(height: 14),
              _AdvancedBackendSettingsSection(
                backendHostController: _backendHostController,
                backendPortController: _backendPortController,
                serverHostController: _serverHostController,
                serverPortController: _serverPortController,
                serverUdpPortController: _serverUdpPortController,
                gameServerHostController: _gameServerHostController,
                gameServerPortController: _gameServerPortController,
                adapterTargetHostController: _adapterTargetHostController,
                secondaryIpController: _secondaryIpController,
                secondaryIpInterfaceController: _secondaryIpInterfaceController,
                secondaryIpPrefixController: _secondaryIpPrefixController,
                adapterMode: _adapterMode,
                onAdapterModeChanged: (mode) {
                  setState(() => _adapterMode = mode);
                },
                controlsEnabled: !_busy && !_sessionStatusIsActive,
              ),
            ],
          ],
        ),
      ),
    );

    final leading = widget.leadingContent;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        // Main row: Game Library (left) + Room Connection (right)
        if (leading != null)
          LayoutBuilder(
            builder: (context, constraints) {
              if (constraints.maxWidth >= 920) {
                return Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Expanded(flex: 35, child: leading),
                    const SizedBox(width: 18),
                    Expanded(flex: 65, child: roomConnectionCard),
                  ],
                );
              }
              return Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  leading,
                  const SizedBox(height: 18),
                  roomConnectionCard,
                ],
              );
            },
          )
        else
          roomConnectionCard,

        // Full-width: Current Session (stopped sessions)
        if (session != null && !_sessionStatusIsActive) ...[
          const SizedBox(height: 12),
          _SessionSummary(
            session: session,
            health: _health,
            onReset: _confirmResetDisplay,
            onCopyRoomId: _copyRoomId,
            onCopyAdapterBind: _copyAdapterBind,
            onCopyAdapterTarget: _copyAdapterTarget,
            onCopyRelayRoot: _copyRelayRoot,
            trafficRate: _trafficRate,
          ),
        ],

        // Full-width: Logs / Details
        const SizedBox(height: 12),
        Card(
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 8),
            child: _LogsDetailsSection(
              health: _health,
              canReadSession: _canReadSession,
              onRefreshStatus: _refreshSession,
              onLoadLogs: _refreshLogs,
              events: _events,
            ),
          ),
        ),
      ],
    );
  }

  // ---------------------------------------------------------------------------
  // Actions — kept identical to preserve state management
  // ---------------------------------------------------------------------------

  Future<void> _checkHealth() async {
    await _run(() async {
      try {
        final health = await widget.client.health();
        if (mounted) {
          setState(() {
            _health = health;
            if (_health.isOnline && _error?.code == 'BACKEND_OFFLINE') {
              _error = null;
            }
          });
        }
        if (_health.isOnline) {
          await _refreshLanDiscoveryStatusSnapshot();
          await _refreshSecondaryIpRecommendationSnapshot();
        }
      } on BackendError catch (error) {
        if (error.code == 'BACKEND_OFFLINE') {
          if (mounted) {
            setState(() {
              _health = HealthStatus.offline();
            });
          }
        }
        rethrow;
      }
    }, requireOnline: false);
  }

  Future<void> _createSession() async {
    await _run(() async {
      try {
        final playerName = _parsePlayerName();
        final gameServerPort = _parseCreateGamePort();
        _adapterTargetPortController.text = gameServerPort.toString();
        final session = await widget.client.createSession(
          serverHost: _serverHostController.text.trim(),
          serverPort: _parsePort(_serverPortController.text),
          serverUdpPort: _parsePort(_serverUdpPortController.text),
          playerName: playerName,
          gameServerPort: gameServerPort,
          bindHost: '127.0.0.1',
          bindPort: 0,
          forceRelay: _forceRelay,
          adapterConfig: _adapterConfigOrNull(includeTargetPort: true),
        );
        _applySessionSnapshot(
          session,
          await widget.client.getSessionLogs(session.sessionId),
        );
        _secondaryIpEnabledForNextSession = false;
      } on BackendError catch (error) {
        throw _actionError('Create room failed', error);
      }
    }, pendingText: Localization().get('creating_room'));
  }

  Future<void> _joinSession() async {
    await _run(() async {
      try {
        final playerName = _parsePlayerName();
        final roomId = _parseRequiredRoomId();
        final gameServerHost = _normalizedGameHost();
        final adapterConfig = _joinAdapterConfig();

        final session = await widget.client.joinSession(
          serverHost: _serverHostController.text.trim(),
          serverPort: _parsePort(_serverPortController.text),
          serverUdpPort: _parsePort(_serverUdpPortController.text),
          roomId: roomId,
          playerName: playerName,
          gameServerHost: gameServerHost,
          forceRelay: _forceRelay,
          adapterConfig: adapterConfig,
        );
        _applySessionSnapshot(
          session,
          await widget.client.getSessionLogs(session.sessionId),
        );
        _secondaryIpEnabledForNextSession = false;
      } on BackendError catch (error) {
        throw _actionError('Join room failed', error);
      }
    }, pendingText: Localization().get('joining_room'));
  }

  Future<void> _refreshSession() async {
    final session = _session;
    if (session == null) return;
    await _run(() async {
      await _refreshSessionSnapshot(session.sessionId);
    }, pendingText: Localization().get('refreshing_status'));
  }

  Future<void> _refreshLogs() async {
    final session = _session;
    if (session == null) return;
    await _run(() async {
      await _refreshSessionSnapshot(session.sessionId);
    }, pendingText: Localization().get('loading_logs'));
  }

  Future<void> _stopSession() async {
    final session = _session;
    if (session == null) return;
    await _run(() async {
      _applySessionSnapshot(
        await widget.client.stopSession(session.sessionId),
        await widget.client.getSessionLogs(session.sessionId),
      );
    }, pendingText: Localization().get('stopping_session'));
  }

  Future<void> _startLanDiscovery() async {
    await _run(() async {
      _lanDiscoveryStatus = await widget.client.startLanDiscovery();
      await _refreshLanDiscoveryPeersSnapshot();
    }, pendingText: Localization().get('lan_discovery_starting'));
  }

  Future<void> _stopLanDiscovery() async {
    await _run(() async {
      _lanDiscoveryStatus = await widget.client.stopLanDiscovery();
      _lanDiscoveryPeers = const [];
    }, pendingText: Localization().get('lan_discovery_stopping'));
  }

  Future<void> _refreshLanDiscoveryPeers() async {
    await _run(() async {
      await _refreshLanDiscoveryPeersSnapshot();
    }, pendingText: Localization().get('lan_discovery_refreshing_peers'));
  }

  Future<void> _scanProcessPorts() async {
    final pid = int.tryParse(_pidController.text.trim());
    if (pid == null || pid <= 0) {
      setState(() {
        _processPortCandidates = const [];
        _selectedProcessPortCandidate = null;
        _processPortHint = Localization().get('pid_port_invalid_pid');
      });
      return;
    }
    await _run(() async {
      final result = await widget.client.scanProcessPorts(pid);
      _processPortCandidates = result.candidates;
      _selectedProcessPortCandidate = result.candidates.isEmpty
          ? null
          : result.candidates.first;
      _processPortHint = result.candidates.isEmpty
          ? Localization().get('pid_port_no_candidates')
          : null;
    }, pendingText: Localization().get('pid_port_scanning'));
  }

  void _applySelectedProcessPort() {
    final candidate = _selectedProcessPortCandidate;
    if (candidate == null) return;
    final loc = Localization();
    if (_adapterMode == _AdapterMode.udpOnly &&
        candidate.protocol.toLowerCase() != 'udp') {
      setState(() {
        _processPortHint = loc.get('pid_port_udp_mismatch');
      });
      return;
    }
    if (_adapterMode == _AdapterMode.tcpOnly &&
        candidate.protocol.toLowerCase() != 'tcp') {
      setState(() {
        _processPortHint = loc.get('pid_port_tcp_mismatch');
      });
      return;
    }

    setState(() {
      final port = candidate.localPort.toString();
      _gameServerPortController.text = port;
      _adapterTargetPortController.text = port;
      _processPortHint = switch (_adapterMode) {
        _AdapterMode.bundle => loc.get('pid_port_applied_bundle'),
        _AdapterMode.udpOnly => loc.get('pid_port_applied_udp'),
        _AdapterMode.tcpOnly => loc.get('pid_port_applied_tcp'),
      };
    });
  }

  Future<void> _autoSelectSecondaryIp() async {
    await _run(() async {
      await _refreshSecondaryIpRecommendationSnapshot();
      _applyRecommendedSecondaryIp();
      _secondaryIpEnabledForNextSession = false;
    }, pendingText: Localization().get('secondaryIpAutoSelecting'));
  }

  void _enableSecondaryIpForNextSession() {
    final loc = Localization();
    setState(() {
      _error = null;
      if (_secondaryIpController.text.trim().isEmpty) {
        _applyRecommendedSecondaryIp();
      }
      if (_secondaryIpController.text.trim().isEmpty) {
        _error = const BackendError(
          code: 'INVALID_INPUT',
          message: 'secondaryIpNoRecommendation',
        );
        _secondaryIpEnabledForNextSession = false;
        return;
      }
      _secondaryIpEnabledForNextSession = true;
      _pendingActionText = loc.get('secondaryIpEnabledForNextSession');
    });
    Future<void>.delayed(const Duration(milliseconds: 700), () {
      if (!mounted) return;
      setState(() {
        if (_pendingActionText == loc.get('secondaryIpEnabledForNextSession')) {
          _pendingActionText = null;
        }
      });
    });
  }

  Future<void> _copyRoomId() async {
    final roomId = _session?.roomId;
    if (roomId == null || roomId.isEmpty) return;
    await _copyText(roomId);
  }

  Future<void> _copyAdapterBind() async {
    final adapterStatus = _session?.adapterStatus;
    final host = adapterStatus?.bindHost;
    final port = adapterStatus?.bindPort;
    if (host == null || port == null) return;
    await _copyText('$host:$port');
  }

  Future<void> _copyAdapterTarget() async {
    final adapterStatus = _session?.adapterStatus;
    final host = adapterStatus?.targetHost;
    final port = adapterStatus?.targetPort;
    if (host == null || port == null) return;
    await _copyText('$host:$port');
  }

  Future<void> _copyRelayRoot() async {
    final session = _session;
    if (session == null) return;
    await _copyText(
      '${session.serverHost} tcp:${session.serverPort} udp:${session.serverUdpPort}',
    );
  }

  Future<void> _copyText(String text) async {
    await Clipboard.setData(ClipboardData(text: text));
  }

  Future<void> _run(
    Future<void> Function() action, {
    String? pendingText,
    bool requireOnline = true,
  }) async {
    if (_busy) return;
    setState(() {
      _busy = true;
      _error = null;
      _pendingActionText = pendingText;
    });
    try {
      if (requireOnline && !_backendOnline) {
        setState(() {
          _waitingForBackend = true;
        });
        try {
          await _refreshHealthBeforeAction();
        } finally {
          if (mounted) {
            setState(() {
              _waitingForBackend = false;
              _pendingActionText = pendingText;
            });
          }
        }
        if (!_backendOnline) {
          throw const BackendError(
            code: 'BACKEND_OFFLINE',
            message: 'BACKEND_OFFLINE',
          );
        }
      }
      await action();
    } on BackendError catch (error) {
      if (error.code == 'SESSION_NOT_FOUND' || error.code == 'ROOM_NOT_FOUND') {
        if (mounted) {
          setState(() {
            _session = null;
            _events = const [];
            _trafficRate = AdapterTrafficRate.zero;
            _trafficRateCalculator.reset();
            _error = null;
          });
        }
      } else {
        if (mounted) {
          setState(() {
            _error = error;
          });
        }
      }
    } catch (error) {
      if (mounted) {
        setState(() {
          _error = BackendError(code: 'UI_ERROR', message: error.toString());
        });
      }
    } finally {
      if (mounted) {
        setState(() {
          _busy = false;
          _pendingActionText = null;
        });
        _syncSessionPolling();
        _syncHealthRetry();
      }
    }
  }

  Future<void> _refreshHealthBeforeAction() async {
    final loc = Localization();
    final startTime = DateTime.now();
    const graceDuration = Duration(seconds: 3);
    const pollInterval = Duration(milliseconds: 500);

    while (true) {
      try {
        final health = await widget.client.health();
        if (health.isOnline) {
          if (mounted) {
            setState(() {
              _health = health;
              if (_error?.code == 'BACKEND_OFFLINE') {
                _error = null;
              }
            });
          }
          return;
        }
      } catch (_) {
        // Suppress errors during grace period polling
      }

      final elapsed = DateTime.now().difference(startTime);
      if (elapsed >= graceDuration) {
        if (mounted) {
          setState(() {
            _health = HealthStatus.offline();
          });
        }
        throw const BackendError(
          code: 'BACKEND_OFFLINE',
          message: 'BACKEND_OFFLINE',
        );
      }

      if (mounted) {
        setState(() {
          _pendingActionText = loc.get('connecting_to_backend');
        });
      }

      await Future<void>.delayed(pollInterval);
    }
  }

  Future<void> _confirmResetDisplay() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) {
        final loc = Localization();
        return AlertDialog(
          title: Text(loc.get('reset_display_title')),
          content: Text(loc.get('reset_display_body')),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(context).pop(false),
              child: Text(loc.get('cancel')),
            ),
            FilledButton(
              onPressed: () => Navigator.of(context).pop(true),
              child: Text(loc.get('reset')),
            ),
          ],
        );
      },
    );
    if (confirmed != true || !mounted) return;
    setState(() {
      _session = null;
      _events = const [];
      _error = null;
      _pendingActionText = null;
      _roomController.clear();
      _serverHostController.text = widget.defaultServerHost;
      _mode = _SessionMode.create;
      _trafficRate = AdapterTrafficRate.zero;
      _trafficRateCalculator.reset();
      _adapterMode = _AdapterMode.bundle;
      _gameServerPortController.clear();
      _adapterTargetPortController.clear();
      _adapterTargetHostController.text = BackendBridgePanel.defaultGameBindHost;
      _selectedProcessPortCandidate = null;
      _processPortCandidates = const [];
      _secondaryIpEnabledForNextSession = false;
    });
    _syncSessionPolling();
  }

  Future<void> _refreshSessionSnapshot(String sessionId) async {
    final status = await widget.client.getSessionStatus(sessionId);
    final events = await widget.client.getSessionLogs(sessionId);
    _applySessionSnapshot(status, events);
  }

  Future<void> _refreshLanDiscoveryStatusSnapshot() async {
    try {
      _lanDiscoveryStatus = await widget.client.getLanDiscoveryStatus();
      if (!_lanDiscoveryStatus.running) {
        _lanDiscoveryPeers = const [];
      }
    } catch (_) {
      _lanDiscoveryStatus = LanDiscoveryStatus.stopped();
      _lanDiscoveryPeers = const [];
    }
  }

  Future<void> _refreshLanDiscoveryPeersSnapshot() async {
    try {
      final response = await widget.client.getLanDiscoveryPeers();
      _lanDiscoveryPeers = response.peers;
      if (!response.running && _lanDiscoveryStatus.running) {
        await _refreshLanDiscoveryStatusSnapshot();
      }
    } catch (_) {
      // Keep existing peers or clear them
    }
  }

  Future<void> _refreshSecondaryIpRecommendationSnapshot() async {
    try {
      _secondaryIpRecommendation = await widget.client
          .getSecondaryIpRecommendation();
    } catch (error) {
      _secondaryIpRecommendation = SecondaryIpRecommendation.unavailable(
        backendAdmin: _health.backendAdmin,
        reason: 'recommendation_failed',
        warning: error.toString(),
      );
    }
  }

  void _applyRecommendedSecondaryIp() {
    final recommendation = _secondaryIpRecommendation;
    if (recommendation == null || !recommendation.available) return;
    final recommendedIp = recommendation.recommendedIp;
    if (recommendedIp == null || recommendedIp.isEmpty) return;
    _secondaryIpController.text = recommendedIp;
    final interfaceIndex = recommendation.interfaceIndex;
    if (interfaceIndex != null) {
      _secondaryIpInterfaceController.text = interfaceIndex.toString();
    }
    final prefixLength = recommendation.prefixLength;
    if (prefixLength != null) {
      _secondaryIpPrefixController.text = prefixLength.toString();
    }
  }

  Future<void> _refreshSecondaryIpStatusSnapshot() async {
    if (!_backendOnline) return;
    try {
      _secondaryIpStatus = await widget.client.getSecondaryIpStatus();
    } catch (_) {
      _secondaryIpStatus = null;
    }
  }

  Future<void> _releaseSecondaryIp() async {
    if (_busy) return;
    setState(() {
      _busy = true;
      _pendingActionText = Localization().get('secondaryIpReleasing');
    });
    try {
      await widget.client.releaseSecondaryIp();
      await _refreshSecondaryIpStatusSnapshot();
      await _refreshSecondaryIpRecommendationSnapshot();
    } on BackendError catch (error) {
      setState(() {
        _error = error;
      });
    } catch (error) {
      setState(() {
        _error = BackendError(
          code: 'RELEASE_FAILED',
          message: error.toString(),
        );
      });
    } finally {
      setState(() {
        _busy = false;
        _pendingActionText = null;
      });
    }
  }

  Future<void> _pollSessionSnapshot() async {
    if (_busy || _pollingSession) return;
    final session = _session;
    if (session == null || !_sessionStatusNeedsPolling(session.status)) {
      _syncSessionPolling();
      return;
    }

    _pollingSession = true;
    try {
      final status = await widget.client.getSessionStatus(session.sessionId);
      final events = await widget.client.getSessionLogs(session.sessionId);
      if (!mounted || _session?.sessionId != session.sessionId) return;
      setState(() {
        _applySessionSnapshot(status, events);
      });
    } on BackendError catch (error) {
      if (!mounted) return;
      setState(() {
        if (error.code == 'SESSION_NOT_FOUND' || error.code == 'ROOM_NOT_FOUND') {
          _session = null;
          _events = const [];
          _trafficRate = AdapterTrafficRate.zero;
          _trafficRateCalculator.reset();
          _error = null;
        } else {
          if (error.code == 'BACKEND_OFFLINE') {
            unawaited(_checkHealth());
          }
          _error = error;
        }
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _error = BackendError(code: 'UI_ERROR', message: error.toString());
      });
    } finally {
      _pollingSession = false;
      _syncSessionPolling();
    }
  }

  void _applySessionSnapshot(SessionInfo session, List<SessionEvent> events) {
    _events = events;
    _session = _deriveDisplaySession(session, events);
    final adapterStatus = _session?.adapterStatus;
    _trafficRate = _trafficRateCalculator.update(
      sessionId: _session!.sessionId,
      adapterStatus: adapterStatus?.status ?? '',
      counters: adapterStatus?.counters,
      now: DateTime.now(),
    );

    // Keep join-field room-id in sync with the resolved display room-id.
    // During create the field is hidden; the value is prefill for a later
    // join after stop.
    final displayRoomId = _session?.roomId;
    if (displayRoomId != null && displayRoomId.isNotEmpty) {
      _roomController.text = displayRoomId;
    }

    _syncSessionPolling();
  }

  void _syncHealthRetry() {
    if (!mounted) return;
    if (_backendOnline) {
      _healthRetryTimer?.cancel();
      _healthRetryTimer = null;
      _healthRetryAttempts = 0;
      return;
    }
    if (_healthRetryTimer != null) return;
    if (_healthRetryAttempts >= 5) return;
    _healthRetryTimer = Timer(const Duration(seconds: 1), () {
      _healthRetryTimer = null;
      if (!mounted || _backendOnline) return;
      _healthRetryAttempts += 1;
      if (!_busy) {
        unawaited(_checkHealth());
      }
    });
  }

  void _syncSessionPolling() {
    if (_busy) return;
    final active = _session;
    if (_backendOnline &&
        active != null &&
        _sessionStatusNeedsPolling(active.status)) {
      _sessionPollTimer ??= Timer.periodic(
        const Duration(seconds: 1),
        (_) => _pollSessionSnapshot(),
      );
      return;
    }

    _sessionPollTimer?.cancel();
    _sessionPollTimer = null;
  }

  /// Resolve the room-id that should appear in the Room ID card, copy
  /// button, and join prefill.
  ///
  /// Priority:
  /// 1. *confirmed* room_id from room_created / room_joined log events
  ///    (this is the real room-id assigned by the S2Pass server).
  /// 2. null — for a create session that is still waiting for the first
  ///    room_created event (the UI shows a pending / "-" state).
  /// 3. session.room_id from the backend status response — used only after
  ///    the session has reached a terminal state or when the logs already
  ///    confirmed it via event data.
  static String? _resolveDisplayRoomId(
    SessionInfo session,
    List<SessionEvent> events,
  ) {
    // 1. Log-confirmed room_id (real Core / server room_id).
    final confirmed = _confirmedRoomIdFromEvents(events);
    if (confirmed != null) return confirmed;

    // 2. Active create session that has not yet received a room_created
    //    event → show pending / "-" so no stale backend-generated id leaks.
    if (session.role == 'create' &&
        _sessionStatusNeedsPolling(session.status) &&
        !_hasConfirmedCreateRoom(events)) {
      return null;
    }

    // 3. Join sessions and terminal sessions: the backend status room_id
    //    was either entered by the user (join) or updated by
    //    _emit_event when room_created fired (terminal create).
    return session.roomId;
  }

  SessionInfo _deriveDisplaySession(
    SessionInfo session,
    List<SessionEvent> events,
  ) {
    final roomId = _resolveDisplayRoomId(session, events);
    final eventStatus = _statusFromEvents(events);
    final status = _preferredStatus(session.status, eventStatus);

    return SessionInfo(
      sessionId: session.sessionId,
      role: session.role,
      status: status,
      roomId: roomId,
      playerName: session.playerName,
      serverHost: session.serverHost,
      serverPort: session.serverPort,
      serverUdpPort: session.serverUdpPort,
      adapterHost: session.adapterHost,
      adapterPort: session.adapterPort,
      gameServerHost: session.gameServerHost,
      gameServerPort: session.gameServerPort,
      forceRelay: session.forceRelay,
      createdAt: session.createdAt,
      updatedAt: session.updatedAt,
      stats: session.stats,
      adapterStatus: session.adapterStatus,
      error: session.error,
      playerId: session.playerId,
      protocolVersion: session.protocolVersion,
      maxPlayers: session.maxPlayers,
      participantCount: session.participantCount,
      participants: session.participants,
      hostPlayerId: session.hostPlayerId,
      lastRoomEvent: session.lastRoomEvent,
      roomReady: session.roomReady,
      roomClosed: session.roomClosed,
      relayReady: session.relayReady,
      relayTokenAvailable: session.relayTokenAvailable,
      relayTargetHost: session.relayTargetHost,
      relayTargetPort: session.relayTargetPort,
      peerEndpointHost: session.peerEndpointHost,
      peerEndpointPort: session.peerEndpointPort,
      peerEndpointSource: session.peerEndpointSource,
      serverTime: session.serverTime,
      secondaryIpEnabled: session.secondaryIpEnabled,
      secondaryIpFallbackUsed: session.secondaryIpFallbackUsed,
      secondaryIpWarning: session.secondaryIpWarning,
      backendAdmin: session.backendAdmin,
      secondaryIpBindAddress: session.secondaryIpBindAddress,
      secondaryIpInterfaceIndex: session.secondaryIpInterfaceIndex,
      secondaryIpInterfaceAlias: session.secondaryIpInterfaceAlias,
      adapterBindMode: session.adapterBindMode,
    );
  }

  // ---------------------------------------------------------------------------
  // Validation — kept identical
  // ---------------------------------------------------------------------------

  String _parsePlayerName() {
    final playerName = _playerNameController.text.trim();
    if (playerName.isEmpty) {
      throw const BackendError(
        code: 'INVALID_INPUT',
        message: 'invalid_player_name',
      );
    }
    return playerName;
  }

  int _parseCreateGamePort() {
    final text = _gameServerPortController.text.trim();
    if (text.isEmpty) {
      throw const BackendError(
        code: 'INVALID_INPUT',
        message: 'invalid_game_server_port',
      );
    }
    final parsed = int.tryParse(text);
    if (parsed == null || parsed < 1 || parsed > 65535) {
      throw const BackendError(
        code: 'INVALID_INPUT',
        message: 'invalid_game_server_port',
      );
    }
    return parsed;
  }

  int _parsePort(String value) {
    final parsed = int.tryParse(value.trim());
    if (parsed == null || parsed < 0 || parsed > 65535) {
      throw const BackendError(code: 'INVALID_INPUT', message: 'invalid_port');
    }
    return parsed;
  }

  String _parseRequiredRoomId() {
    final roomId = _roomController.text.trim();
    if (roomId.isEmpty) {
      throw const BackendError(
        code: 'INVALID_INPUT',
        message: 'invalid_room_id',
      );
    }
    return roomId;
  }

  String _normalizedGameHost() {
    final host = _gameServerHostController.text.trim();
    return host.isEmpty ? BackendBridgePanel.defaultGameBindHost : host;
  }

  String _normalizedAdapterTargetHost() {
    final host = _adapterTargetHostController.text.trim();
    return host.isEmpty ? BackendBridgePanel.defaultGameBindHost : host;
  }

  int _parseAdapterTargetPort() {
    final text = _adapterTargetPortController.text.trim();
    if (text.isEmpty) {
      throw const BackendError(
        code: 'INVALID_INPUT',
        message: 'invalid_game_server_port',
      );
    }
    final parsed = int.tryParse(text);
    if (parsed == null || parsed < 1 || parsed > 65535) {
      throw const BackendError(
        code: 'INVALID_INPUT',
        message: 'invalid_game_server_port',
      );
    }
    return parsed;
  }

  int? _parseSecondaryIpPrefix() {
    final text = _secondaryIpPrefixController.text.trim();
    if (text.isEmpty) return null;
    final parsed = int.tryParse(text);
    if (parsed == null || parsed < 1 || parsed > 32) {
      throw const BackendError(
        code: 'INVALID_INPUT',
        message: 'invalid_secondary_ip_prefix',
      );
    }
    return parsed;
  }

  SecondaryIpRequestConfig? _secondaryIpRequestOrNull() {
    if (!_secondaryIpEnabledForNextSession) return null;
    final ip = _secondaryIpController.text.trim();
    if (ip.isEmpty) return null;
    final interfaceHint = _secondaryIpInterfaceController.text.trim();
    return SecondaryIpRequestConfig(
      ipAddress: ip,
      interfaceHint: interfaceHint.isEmpty ? null : interfaceHint,
      prefixLength: _parseSecondaryIpPrefix(),
    );
  }

  AdapterConfig? _adapterConfigOrNull({required bool includeTargetPort}) {
    final targetHost = _normalizedAdapterTargetHost();
    final secondaryIpRequest = _secondaryIpRequestOrNull();
    int? targetPort;
    if (includeTargetPort) {
      final portText = _adapterTargetPortController.text.trim();
      targetPort = portText.isNotEmpty ? _parseAdapterTargetPort() : null;
    }
    _adapterTargetHostController.text = targetHost;
    if (targetPort != null) {
      _adapterTargetPortController.text = targetPort.toString();
    }
    return switch (_adapterMode) {
      _AdapterMode.bundle => AdapterConfig.bundle(
        targetHost: targetHost,
        targetPort: targetPort!,
        secondaryIpRequest: secondaryIpRequest,
      ),
      _AdapterMode.udpOnly => AdapterConfig.udpExperimental(
        targetHost: targetHost,
        targetPort: targetPort,
        secondaryIpRequest: secondaryIpRequest,
      ),
      _AdapterMode.tcpOnly => AdapterConfig.tcpForward(
        targetHost: targetHost,
        targetPort: targetPort,
        secondaryIpRequest: secondaryIpRequest,
      ),
    };
  }

  AdapterConfig _joinAdapterConfig() {
    final targetHost = _normalizedAdapterTargetHost();
    final secondaryIpRequest = _secondaryIpRequestOrNull();
    _adapterTargetHostController.text = targetHost;
    return switch (_adapterMode) {
      _AdapterMode.bundle => AdapterConfig.bundle(
        targetHost: targetHost,
        targetPort: 0,
        secondaryIpRequest: secondaryIpRequest,
      ),
      _AdapterMode.udpOnly => AdapterConfig.udpExperimental(
        targetHost: targetHost,
        secondaryIpRequest: secondaryIpRequest,
      ),
      _AdapterMode.tcpOnly => AdapterConfig.tcpForward(
        targetHost: targetHost,
        secondaryIpRequest: secondaryIpRequest,
      ),
    };
  }

  BackendError _actionError(String action, BackendError error) {
    if (error.code == 'BACKEND_OFFLINE' || error.code == 'INVALID_INPUT') {
      return error;
    }
    return BackendError(
      code: error.code,
      message: '$action: ${error.message}',
      details: error.details,
    );
  }
}

// =============================================================================
// Sub-widgets
// =============================================================================

class _RoomConnectionHeader extends StatelessWidget {
  const _RoomConnectionHeader({
    required this.health,
    required this.busy,
    this.waitingForBackend = false,
    required this.onRefresh,
  });

  final HealthStatus health;
  final bool busy;
  final bool waitingForBackend;
  final VoidCallback onRefresh;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final loc = Localization();
    return Row(
      children: [
        Icon(Icons.meeting_room_outlined, color: scheme.primary),
        const SizedBox(width: 10),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                loc.get('room_panel_title'),
                style: Theme.of(context).textTheme.titleLarge,
              ),
              const SizedBox(height: 4),
              Text(
                loc.get('room_panel_subtitle'),
                style: TextStyle(
                  color: scheme.onSurfaceVariant,
                  fontFamily: 'monospace',
                ),
              ),
            ],
          ),
        ),
        _HealthBadge(health: health, waitingForBackend: waitingForBackend),
        const SizedBox(width: 8),
        IconButton(
          tooltip: loc.get('refresh_health'),
          onPressed: busy ? null : onRefresh,
          icon: const Icon(Icons.refresh),
        ),
      ],
    );
  }
}

class _ModeSelector extends StatelessWidget {
  const _ModeSelector({
    required this.mode,
    required this.enabled,
    required this.onChanged,
  });

  final _SessionMode mode;
  final bool enabled;
  final ValueChanged<_SessionMode> onChanged;

  @override
  Widget build(BuildContext context) {
    final loc = Localization();
    return SegmentedButton<_SessionMode>(
      segments: [
        ButtonSegment<_SessionMode>(
          value: _SessionMode.create,
          icon: const Icon(Icons.add_circle_outline),
          label: Text(loc.get('create_room')),
        ),
        ButtonSegment<_SessionMode>(
          value: _SessionMode.join,
          icon: const Icon(Icons.login),
          label: Text(loc.get('join_room')),
        ),
      ],
      selected: {mode},
      onSelectionChanged: enabled ? (values) => onChanged(values.single) : null,
    );
  }
}

class _ModeForm extends StatelessWidget {
  const _ModeForm({
    required this.mode,
    required this.playerNameController,
    required this.gameServerPortController,
    required this.roomController,
    required this.forceRelay,
    required this.forceRelayEnabled,
    required this.onForceRelayChanged,
    required this.canCreate,
    required this.canJoin,
    required this.onCreate,
    required this.onJoin,
  });

  final _SessionMode mode;
  final TextEditingController playerNameController;
  final TextEditingController gameServerPortController;
  final TextEditingController roomController;
  final bool forceRelay;
  final bool forceRelayEnabled;
  final ValueChanged<bool> onForceRelayChanged;
  final bool canCreate;
  final bool canJoin;
  final VoidCallback onCreate;
  final VoidCallback onJoin;

  static const double _fieldWidth = 200;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final loc = Localization();
    final isCreate = mode == _SessionMode.create;
    final enabled = isCreate ? canCreate : canJoin;
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        border: Border.all(color: scheme.outlineVariant),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(
                isCreate ? Icons.add_circle_outline : Icons.login,
                color: scheme.primary,
              ),
              const SizedBox(width: 8),
              Text(
                isCreate ? loc.get('create_room') : loc.get('join_room'),
                style: Theme.of(context).textTheme.titleMedium,
              ),
            ],
          ),
          const SizedBox(height: 12),
          if (isCreate)
            Wrap(
              key: const Key('create-basic-fields'),
              spacing: 10,
              runSpacing: 10,
              children: [
                SizedBox(
                  width: _fieldWidth,
                  child: _Field(
                    controller: playerNameController,
                    label: loc.get('player_name'),
                    hintText: loc.get('player_name_hint'),
                  ),
                ),
                SizedBox(
                  width: _fieldWidth,
                  child: _Field(
                    key: const Key('create-game-port-field'),
                    controller: gameServerPortController,
                    label: loc.get('shared_game_port'),
                    number: true,
                    helperText: loc.get('game_server_port_hint'),
                  ),
                ),
              ],
            )
          else ...[
            Wrap(
              spacing: 10,
              runSpacing: 10,
              children: [
                SizedBox(
                  width: _fieldWidth,
                  child: _Field(
                    controller: playerNameController,
                    label: loc.get('player_name'),
                    hintText: loc.get('player_name_hint'),
                  ),
                ),
                SizedBox(
                  width: _fieldWidth,
                  child: _Field(
                    key: const Key('join-room-id-field'),
                    controller: roomController,
                    label: loc.get('room_id'),
                  ),
                ),
              ],
            ),
          ],
          const SizedBox(height: 12),
          CheckboxListTile(
            value: forceRelay,
            onChanged: forceRelayEnabled
                ? (value) => onForceRelayChanged(value ?? true)
                : null,
            contentPadding: EdgeInsets.zero,
            dense: true,
            controlAffinity: ListTileControlAffinity.leading,
            title: Text(loc.get('force_relay')),
          ),
          const SizedBox(height: 12),
          Align(
            alignment: Alignment.centerLeft,
            child: FilledButton.icon(
              onPressed: enabled ? (isCreate ? onCreate : onJoin) : null,
              icon: Icon(isCreate ? Icons.add_circle_outline : Icons.login),
              label: Text(
                isCreate ? loc.get('create_room') : loc.get('join_room'),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _ActiveSessionCard extends StatelessWidget {
  const _ActiveSessionCard({
    required this.session,
    required this.health,
    required this.canStop,
    required this.onStop,
    required this.onReset,
    required this.onCopyRoomId,
    required this.onCopyAdapterBind,
    required this.onCopyAdapterTarget,
    required this.onCopyRelayRoot,
    required this.trafficRate,
  });

  final SessionInfo session;
  final HealthStatus health;
  final bool canStop;
  final VoidCallback onStop;
  final VoidCallback onReset;
  final VoidCallback onCopyRoomId;
  final VoidCallback onCopyAdapterBind;
  final VoidCallback onCopyAdapterTarget;
  final VoidCallback onCopyRelayRoot;
  final AdapterTrafficRate trafficRate;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final loc = Localization();
    final adapterStatus = session.adapterStatus;
    final adapterBind = _adapterBindAddress(adapterStatus);
    final adapterTarget = _adapterTargetAddress(adapterStatus);
    final localGameConn = _localGameConnectionAddress(adapterStatus);
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        border: Border.all(color: scheme.outlineVariant),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(Icons.meeting_room_outlined, color: scheme.primary),
              const SizedBox(width: 8),
              Text(
                loc.get('current_session'),
                style: Theme.of(context).textTheme.titleMedium,
              ),
            ],
          ),
          const SizedBox(height: 12),
          Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: scheme.primaryContainer.withValues(alpha: 0.36),
              borderRadius: BorderRadius.circular(8),
            ),
            child: Row(
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        loc.get('room_id'),
                        style: Theme.of(context).textTheme.labelLarge,
                      ),
                      const SizedBox(height: 3),
                      SelectableText(
                        session.roomId ?? '-',
                        style: Theme.of(context).textTheme.headlineSmall,
                      ),
                    ],
                  ),
                ),
                IconButton(
                  tooltip: loc.get('copy_room_id'),
                  onPressed: session.roomId == null ? null : onCopyRoomId,
                  icon: const Icon(Icons.copy),
                ),
              ],
            ),
          ),
          if (localGameConn != null) ...[
            const SizedBox(height: 10),
            _HighlightDatum(
              label: loc.get('local_game_connection'),
              value: localGameConn,
              icon: Icons.videogame_asset_outlined,
              onCopy: onCopyAdapterBind,
            ),
            const SizedBox(height: 8),
            _HelperText(text: loc.get('local_game_connection_helper')),
            if (adapterStatus?.adapterType == 'bundle') ...[
              const SizedBox(height: 4),
              const _BundleGameplaySummary(),
            ],
          ],
          if (adapterStatus?.adapterType == 'bundle') ...[
            const SizedBox(height: 10),
            _HighlightDatum(
              label: loc.get('lan_discovery_helper'),
              value: adapterStatus?.discoveryHelperConnectionAddress ?? loc.get('disabled_unavailable'),
              icon: Icons.radar_outlined,
              onCopy: adapterStatus?.discoveryHelperConnectionAddress != null
                  ? () => Clipboard.setData(ClipboardData(text: adapterStatus!.discoveryHelperConnectionAddress!))
                  : null,
            ),
            const SizedBox(height: 8),
            _HelperText(text: loc.get('lan_discovery_helper_helper')),
          ],
          if (_hasParticipantSummary(session)) ...[
            const SizedBox(height: 10),
            _SessionParticipantsSummary(session: session),
          ],
          if (adapterBind != null && adapterBind != localGameConn) ...[
            const SizedBox(height: 10),
            _HighlightDatum(
              label: loc.get('label_adapter_bind'),
              value: adapterBind,
              icon: Icons.videogame_asset_outlined,
              onCopy: onCopyAdapterBind,
            ),
            const SizedBox(height: 8),
            _HelperText(
              text: loc
                  .get('adapter_bind_helper')
                  .replaceAll(
                    '{adapter_port}',
                    adapterStatus!.bindPort.toString(),
                  ),
            ),
          ],
          if (adapterTarget != null) ...[
            const SizedBox(height: 10),
            _HighlightDatum(
              label: loc.get('label_adapter_target'),
              value: adapterTarget,
              icon: Icons.dns_outlined,
              onCopy: onCopyAdapterTarget,
            ),
            const SizedBox(height: 8),
            _HelperText(text: loc.get('adapter_target_helper')),
          ],
          if (session.error != null) ...[
            const SizedBox(height: 10),
            _SessionErrorNotice(error: session.error!),
          ],
          const SizedBox(height: 10),
          _SessionDetailsExpansion(session: session, health: health),
          if (_hasSecondaryIpInfo(session)) ...[
            const SizedBox(height: 10),
            _SecondaryIpDetails(session: session),
          ],
          if (adapterStatus != null) ...[
            const SizedBox(height: 10),
            _AdapterStatusDetails(
              session: session,
              adapterStatus: adapterStatus,
              trafficRate: trafficRate,
              onCopyRelayRoot: onCopyRelayRoot,
            ),
          ],
          const SizedBox(height: 10),
          Text(
            loc.get('relay_inactivity_note'),
            style: Theme.of(
              context,
            ).textTheme.bodySmall?.copyWith(color: scheme.onSurfaceVariant),
          ),
          const SizedBox(height: 4),
          Text(
            loc.get('relay_credential_hidden_note'),
            style: Theme.of(
              context,
            ).textTheme.bodySmall?.copyWith(color: scheme.onSurfaceVariant),
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 10,
            runSpacing: 10,
            children: [
              FilledButton.icon(
                onPressed: canStop ? onStop : null,
                icon: const Icon(Icons.stop_circle_outlined),
                label: Text(loc.get('stop_session')),
              ),
              OutlinedButton.icon(
                onPressed: onReset,
                icon: const Icon(Icons.clear_all),
                label: Text(loc.get('reset_local_display')),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _BundleGameplaySummary extends StatelessWidget {
  const _BundleGameplaySummary();

  @override
  Widget build(BuildContext context) {
    final loc = Localization();
    final scheme = Theme.of(context).colorScheme;
    final style = TextStyle(fontSize: 12, color: scheme.onSurfaceVariant);
    return Padding(
      padding: const EdgeInsets.only(left: 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _BundleSummaryLine(label: loc.get('tcp_relay_label'), style: style),
          const SizedBox(height: 2),
          _BundleSummaryLine(label: loc.get('udp_gameplay_label'), style: style),
        ],
      ),
    );
  }
}

class _BundleSummaryLine extends StatelessWidget {
  const _BundleSummaryLine({required this.label, required this.style});

  final String label;
  final TextStyle style;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Icon(
          Icons.arrow_right,
          size: 16,
          color: Theme.of(context).colorScheme.primary,
        ),
        Text(label, style: style),
      ],
    );
  }
}

class _SessionErrorNotice extends StatelessWidget {
  const _SessionErrorNotice({required this.error});

  final BackendError error;

  @override
  Widget build(BuildContext context) {
    final loc = Localization();
    final scheme = Theme.of(context).colorScheme;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: scheme.errorContainer.withValues(alpha: 0.28),
        border: Border.all(color: scheme.error.withValues(alpha: 0.36)),
        borderRadius: BorderRadius.circular(8),
      ),
      child: _Datum(label: loc.get('label_error'), value: error.message),
    );
  }
}

class _SessionSummary extends StatelessWidget {
  const _SessionSummary({
    required this.session,
    required this.health,
    required this.onReset,
    required this.onCopyRoomId,
    required this.onCopyAdapterBind,
    required this.onCopyAdapterTarget,
    required this.onCopyRelayRoot,
    required this.trafficRate,
  });

  final SessionInfo session;
  final HealthStatus health;
  final VoidCallback onReset;
  final VoidCallback onCopyRoomId;
  final VoidCallback onCopyAdapterBind;
  final VoidCallback onCopyAdapterTarget;
  final VoidCallback onCopyRelayRoot;
  final AdapterTrafficRate trafficRate;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final loc = Localization();
    final adapterStatus = session.adapterStatus;
    final adapterBind = _adapterBindAddress(adapterStatus);
    final adapterTarget = _adapterTargetAddress(adapterStatus);
    final localGameConn = _localGameConnectionAddress(adapterStatus);
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        border: Border.all(color: scheme.outlineVariant),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(Icons.meeting_room_outlined, color: scheme.primary),
              const SizedBox(width: 8),
              Text(
                loc.get('current_session_summary'),
                style: Theme.of(context).textTheme.titleMedium,
              ),
            ],
          ),
          const SizedBox(height: 12),
          _HighlightDatum(
            label: loc.get('room_id'),
            value: session.roomId ?? '-',
            icon: Icons.meeting_room_outlined,
            onCopy: session.roomId == null ? null : onCopyRoomId,
          ),
          if (localGameConn != null) ...[
            const SizedBox(height: 10),
            _HighlightDatum(
              label: loc.get('local_game_connection'),
              value: localGameConn,
              icon: Icons.videogame_asset_outlined,
              onCopy: onCopyAdapterBind,
            ),
            const SizedBox(height: 8),
            _HelperText(text: loc.get('local_game_connection_helper')),
            if (adapterStatus?.adapterType == 'bundle') ...[
              const SizedBox(height: 4),
              const _BundleGameplaySummary(),
            ],
          ],
          if (adapterStatus?.adapterType == 'bundle') ...[
            const SizedBox(height: 10),
            _HighlightDatum(
              label: loc.get('lan_discovery_helper'),
              value: adapterStatus?.discoveryHelperConnectionAddress ?? loc.get('disabled_unavailable'),
              icon: Icons.radar_outlined,
              onCopy: adapterStatus?.discoveryHelperConnectionAddress != null
                  ? () => Clipboard.setData(ClipboardData(text: adapterStatus!.discoveryHelperConnectionAddress!))
                  : null,
            ),
            const SizedBox(height: 8),
            _HelperText(text: loc.get('lan_discovery_helper_helper')),
          ],
          if (_hasParticipantSummary(session)) ...[
            const SizedBox(height: 10),
            _SessionParticipantsSummary(session: session),
          ],
          if (adapterBind != null && adapterBind != localGameConn) ...[
            const SizedBox(height: 10),
            _HighlightDatum(
              label: loc.get('label_adapter_bind'),
              value: adapterBind,
              icon: Icons.videogame_asset_outlined,
              onCopy: onCopyAdapterBind,
            ),
            const SizedBox(height: 8),
            _HelperText(
              text: loc
                  .get('adapter_bind_helper')
                  .replaceAll(
                    '{adapter_port}',
                    adapterStatus!.bindPort.toString(),
                  ),
            ),
          ],
          if (adapterTarget != null) ...[
            const SizedBox(height: 10),
            _HighlightDatum(
              label: loc.get('label_adapter_target'),
              value: adapterTarget,
              icon: Icons.dns_outlined,
              onCopy: onCopyAdapterTarget,
            ),
            const SizedBox(height: 8),
            _HelperText(text: loc.get('adapter_target_helper')),
          ],
          if (session.error != null) ...[
            const SizedBox(height: 10),
            _SessionErrorNotice(error: session.error!),
          ],
          const SizedBox(height: 10),
          _SessionDetailsExpansion(session: session, health: health),
          if (_hasSecondaryIpInfo(session)) ...[
            const SizedBox(height: 10),
            _SecondaryIpDetails(session: session),
          ],
          if (adapterStatus != null) ...[
            const SizedBox(height: 10),
            _AdapterStatusDetails(
              session: session,
              adapterStatus: adapterStatus,
              trafficRate: trafficRate,
              onCopyRelayRoot: onCopyRelayRoot,
            ),
          ],
          const SizedBox(height: 10),
          Wrap(
            spacing: 10,
            runSpacing: 10,
            children: [
              OutlinedButton.icon(
                onPressed: session.roomId == null ? null : onCopyRoomId,
                icon: const Icon(Icons.copy),
                label: Text(loc.get('copy_room_id')),
              ),
              OutlinedButton.icon(
                onPressed: onReset,
                icon: const Icon(Icons.clear_all),
                label: Text(loc.get('reset_local_display')),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _SessionParticipantsSummary extends StatelessWidget {
  const _SessionParticipantsSummary({required this.session});

  final SessionInfo session;

  @override
  Widget build(BuildContext context) {
    final loc = Localization();
    final players = _playersValue(session);
    final participants = session.participants;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (players != null)
          _Datum(label: loc.get('playersLabel'), value: players),
        if (players != null) const SizedBox(height: 12),
        Text(
          loc.get('participantsLabel'),
          style: Theme.of(context).textTheme.titleSmall,
        ),
        const SizedBox(height: 8),
        if (participants.isEmpty)
          _HelperText(text: loc.get('noParticipantsLabel'))
        else
          Column(
            children: participants
                .map(
                  (participant) => _ParticipantRow(
                    participant: participant,
                    currentPlayerId: session.playerId,
                    hostPlayerId: session.hostPlayerId,
                  ),
                )
                .toList(growable: false),
          ),
      ],
    );
  }
}

class _SessionDetailsExpansion extends StatelessWidget {
  const _SessionDetailsExpansion({required this.session, required this.health});

  final SessionInfo session;
  final HealthStatus health;

  @override
  Widget build(BuildContext context) {
    final loc = Localization();
    final peerEndpoint = _peerEndpointValue(session);
    final relayTarget = _relayTargetValue(session);
    return Material(
      color: Colors.transparent,
      child: ExpansionTile(
        initiallyExpanded: false,
        tilePadding: EdgeInsets.zero,
        childrenPadding: const EdgeInsets.only(top: 8, bottom: 8),
        title: Text(loc.get('session_details_title')),
        children: [
          Wrap(
            spacing: 10,
            runSpacing: 10,
            children: [
              _Datum(label: loc.get('label_role'), value: session.role),
              _Datum(label: loc.get('label_status'), value: session.status),
              _Datum(
                label: loc.get('label_relay_status'),
                value: _relayStatusLabel(session.status),
              ),
              _Datum(
                label: loc.get('label_backend_health'),
                value: _backendHealthLabel(health),
              ),
              _Datum(
                label: loc.get('label_session_id'),
                value: session.sessionId,
              ),
              if (session.error != null)
                _Datum(
                  label: loc.get('label_error'),
                  value: session.error!.message,
                ),
              if (session.protocolVersion != null)
                _Datum(
                  label: loc.get('protocolVersionLabel'),
                  value: _protocolLabel(session.protocolVersion!),
                ),
              if (session.maxPlayers != null)
                _Datum(
                  label: loc.get('maxPlayersLabel'),
                  value: session.maxPlayers.toString(),
                ),
              if (session.participantCount != null)
                _Datum(
                  label: loc.get('participantCountLabel'),
                  value: session.participantCount.toString(),
                ),
              if (session.hostPlayerId != null)
                _Datum(
                  label: loc.get('hostPlayerIdLabel'),
                  value: session.hostPlayerId!,
                ),
              _Datum(
                label: loc.get('roomReadyLabel'),
                value: _yesNo(session.roomReady),
              ),
              _Datum(
                label: loc.get('roomClosedLabel'),
                value: _yesNo(session.roomClosed),
              ),
              _Datum(
                label: loc.get('relayReadyLabel'),
                value: _yesNo(session.relayReady),
              ),
              if (session.lastRoomEvent != null)
                _Datum(
                  label: loc.get('lastRoomEventLabel'),
                  value: session.lastRoomEvent!,
                ),
              _Datum(
                label: loc.get('peerEndpointLabel'),
                value: peerEndpoint ?? loc.get('peerEndpointUnavailable'),
              ),
              if (relayTarget != null)
                _Datum(label: loc.get('relayTargetLabel'), value: relayTarget),
            ],
          ),
        ],
      ),
    );
  }
}

class _ParticipantRow extends StatelessWidget {
  const _ParticipantRow({
    required this.participant,
    required this.currentPlayerId,
    required this.hostPlayerId,
  });

  final ParticipantDto participant;
  final String? currentPlayerId;
  final String? hostPlayerId;

  @override
  Widget build(BuildContext context) {
    final loc = Localization();
    final scheme = Theme.of(context).colorScheme;
    final isHost = participant.isHost || participant.playerId == hostPlayerId;
    final isCurrent =
        currentPlayerId != null && participant.playerId == currentPlayerId;
    final title = participant.playerName.isNotEmpty ? participant.playerName : '-';
    return Container(
      width: double.infinity,
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        border: Border.all(color: scheme.outlineVariant),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Wrap(
        spacing: 8,
        runSpacing: 6,
        crossAxisAlignment: WrapCrossAlignment.center,
        children: [
          Text(title, style: Theme.of(context).textTheme.bodyMedium),
          if (isHost)
            Chip(
              label: Text(loc.get('hostLabel')),
              visualDensity: VisualDensity.compact,
            ),
          if (isCurrent)
            Chip(
              label: Text(loc.get('youLabel')),
              visualDensity: VisualDensity.compact,
            ),
        ],
      ),
    );
  }
}

class _SecondaryIpDetails extends StatelessWidget {
  const _SecondaryIpDetails({required this.session});

  final SessionInfo session;

  @override
  Widget build(BuildContext context) {
    final loc = Localization();
    final interfaceValue = _secondaryIpInterfaceValue(session);
    final gameTarget =
        _adapterTargetAddress(session.adapterStatus) ??
        (session.gameServerPort == null
            ? '-'
            : '${session.gameServerHost}:${session.gameServerPort}');
    return Wrap(
      spacing: 10,
      runSpacing: 10,
      children: [
        _Datum(
          label: loc.get('backendAdminLabel'),
          value: _yesNo(session.backendAdmin),
        ),
        _Datum(
          label: loc.get('secondaryIpBindStatusLabel'),
          value: _secondaryIpBindStatusLabel(session),
        ),
        _Datum(
          label: loc.get('secondaryIpTargetInterfaceLabel'),
          value: interfaceValue,
        ),
        _Datum(
          label: loc.get('secondaryIpBindAddressLabel'),
          value: session.secondaryIpBindAddress ?? '-',
        ),
        _Datum(
          label: loc.get('adapterBindModeLabel'),
          value: session.adapterBindMode,
        ),
        _Datum(label: loc.get('gameTargetAddressLabel'), value: gameTarget),
        if (session.secondaryIpWarning != null &&
            session.secondaryIpWarning!.isNotEmpty)
          _Datum(
            label: loc.get('secondaryIpWarningLabel'),
            value: session.secondaryIpWarning!,
          ),
      ],
    );
  }
}

class _SecondaryIpSetupCard extends StatelessWidget {
  const _SecondaryIpSetupCard({
    required this.health,
    required this.recommendation,
    required this.session,
    required this.backendApiAddress,
    required this.armedForNextSession,
    required this.controlsEnabled,
    required this.onAutoSelect,
    required this.onEnable,
    this.onRelease,
    this.secondaryIpStatus,
  });

  final HealthStatus health;
  final SecondaryIpRecommendation? recommendation;
  final SessionInfo? session;
  final String backendApiAddress;
  final bool armedForNextSession;
  final bool controlsEnabled;
  final VoidCallback? onAutoSelect;
  final VoidCallback? onEnable;
  final VoidCallback? onRelease;
  final Map<String, dynamic>? secondaryIpStatus;

  @override
  Widget build(BuildContext context) {
    final loc = Localization();
    final scheme = Theme.of(context).colorScheme;
    final rec = recommendation;
    final activeSession = session;
    final sipStatus = secondaryIpStatus;
    final interfaceValue = _recommendationInterfaceValue(rec);
    final recommendedIp = rec?.recommendedIp ?? '-';
    final adapterBind =
        activeSession?.secondaryIpBindAddress ??
        _adapterBindAddress(activeSession?.adapterStatus) ??
        '-';
    final allocated = sipStatus?['allocated'] == true;
    final allocatedIp = sipStatus?['allocated_ip'] as String?;
    final bindMode = sipStatus?['bind_mode'] as String? ?? '-';
    final source = sipStatus?['source'] as String? ?? '-';
    final lastError = sipStatus?['last_error'] as String?;

    final statusLabel = allocated
        ? loc.get('secondaryIpAllocatedStatus')
        : (activeSession == null
              ? (armedForNextSession
                    ? loc.get('secondaryIpReadyToRequest')
                    : loc.get('secondaryIpNotRequested'))
              : _secondaryIpBindStatusLabel(activeSession));
    final warning =
        lastError ?? activeSession?.secondaryIpWarning ?? rec?.warning;

    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        border: Border.all(color: scheme.outlineVariant),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(Icons.add_link, color: scheme.primary),
              const SizedBox(width: 8),
              Text(
                loc.get('secondaryIpCardTitle'),
                style: Theme.of(context).textTheme.titleMedium,
              ),
            ],
          ),
          const SizedBox(height: 8),
          _HelperText(text: loc.get('secondaryIpSafetyNote')),
          const SizedBox(height: 12),
          Wrap(
            spacing: 10,
            runSpacing: 10,
            children: [
              _Datum(
                label: loc.get('backendAdminLabel'),
                value: _yesNo(rec?.backendAdmin ?? health.backendAdmin),
              ),
              _Datum(
                label: loc.get('secondaryIpDefaultInterfaceLabel'),
                value: interfaceValue,
              ),
              _Datum(
                label: loc.get('recommendedAddressLabel'),
                value: recommendedIp,
              ),
              _Datum(
                label: loc.get('secondaryIpBindStatusLabel'),
                value: statusLabel,
              ),
              if (allocated && allocatedIp != null) ...[
                _Datum(
                  label: loc.get('secondaryIpAllocatedAddressLabel'),
                  value: allocatedIp,
                ),
                _Datum(label: loc.get('adapterBindModeLabel'), value: bindMode),
                _Datum(label: loc.get('secondaryIpSourceLabel'), value: source),
              ],
              _Datum(
                label: loc.get('backendApiAddressLabel'),
                value: backendApiAddress,
              ),
              _Datum(label: loc.get('label_adapter_bind'), value: adapterBind),
              if (activeSession?.secondaryIpBindAddress != null)
                _Datum(
                  label: loc.get('adapterBindModeLabel'),
                  value: activeSession!.adapterBindMode,
                ),
              if (warning != null && warning.isNotEmpty)
                _Datum(
                  label: loc.get('secondaryIpWarningLabel'),
                  value: warning,
                ),
            ],
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 10,
            runSpacing: 10,
            children: [
              if (allocated)
                OutlinedButton.icon(
                  onPressed: controlsEnabled ? onRelease : null,
                  icon: const Icon(Icons.link_off),
                  label: Text(loc.get('releaseSecondaryIpButton')),
                )
              else ...[
                OutlinedButton.icon(
                  onPressed: controlsEnabled ? onAutoSelect : null,
                  icon: const Icon(Icons.route_outlined),
                  label: Text(loc.get('autoSelectInterfaceButton')),
                ),
                FilledButton.icon(
                  onPressed: controlsEnabled ? onEnable : null,
                  icon: const Icon(Icons.add_circle_outline),
                  label: Text(loc.get('enableSecondaryIpButton')),
                ),
              ],
            ],
          ),
        ],
      ),
    );
  }
}

String _recommendationInterfaceValue(
  SecondaryIpRecommendation? recommendation,
) {
  if (recommendation == null || !recommendation.available) return '-';
  final alias = recommendation.interfaceAlias;
  final index = recommendation.interfaceIndex;
  final ip = recommendation.interfaceIp;
  final prefix = recommendation.prefixLength;
  final String name;
  if ((alias == null || alias.isEmpty) && index == null) {
    name = '-';
  } else if (alias == null || alias.isEmpty) {
    name = 'ifIndex $index';
  } else if (index == null) {
    name = alias;
  } else {
    name = '$alias (ifIndex $index)';
  }
  if (ip == null || prefix == null) return name;
  return '$name $ip/$prefix';
}

String _secondaryIpBindStatusLabel(SessionInfo session) {
  final loc = Localization();
  if (session.secondaryIpEnabled) {
    return loc.get('secondaryIpAssigned');
  }
  if (session.secondaryIpFallbackUsed ||
      (session.secondaryIpWarning?.isNotEmpty ?? false)) {
    return loc.get('secondaryIpFailed');
  }
  return loc.get('secondaryIpDisabled');
}

String _secondaryIpInterfaceValue(SessionInfo session) {
  final alias = session.secondaryIpInterfaceAlias;
  final index = session.secondaryIpInterfaceIndex;
  if ((alias == null || alias.isEmpty) && index == null) {
    return '-';
  }
  if (alias == null || alias.isEmpty) {
    return 'ifIndex $index';
  }
  if (index == null) {
    return alias;
  }
  return '$alias (ifIndex $index)';
}

class _AdapterStatusDetails extends StatelessWidget {
  const _AdapterStatusDetails({
    required this.session,
    required this.adapterStatus,
    required this.trafficRate,
    required this.onCopyRelayRoot,
  });

  final SessionInfo session;
  final AdapterStatus adapterStatus;
  final AdapterTrafficRate trafficRate;
  final VoidCallback onCopyRelayRoot;

  @override
  Widget build(BuildContext context) {
    final counters = adapterStatus.counters;
    final loc = Localization();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Wrap(
          spacing: 10,
          runSpacing: 10,
          children: [
            _Datum(
              label: loc.get('relay_root_host'),
              value: session.serverHost,
              onCopy: onCopyRelayRoot,
            ),
            _Datum(
              label: loc.get('relay_tcp_port'),
              value: session.serverPort.toString(),
            ),
            _Datum(
              label: loc.get('relay_udp_port'),
              value: session.serverUdpPort.toString(),
            ),
          ],
        ),
        const SizedBox(height: 8),
        _HelperText(text: loc.get('relay_address_warning')),
        const SizedBox(height: 10),
        Wrap(
          spacing: 10,
          runSpacing: 10,
          children: [
            _Datum(
              label: loc.get('label_adapter'),
              value: _adapterStatusLabel(adapterStatus),
            ),
          ],
        ),
        if (counters != null) ...[
          const SizedBox(height: 12),
          _TrafficDetails(
            counters: counters,
            trafficRate: trafficRate,
            adapterStatus: adapterStatus,
          ),
        ],
        if (adapterStatus.error != null) ...[
          const SizedBox(height: 10),
          Wrap(
            spacing: 10,
            runSpacing: 10,
            children: [
              _Datum(
                label: loc.get('label_adapter_error_code'),
                value: adapterStatus.error!.code,
              ),
              _Datum(
                label: loc.get('label_adapter_error'),
                value: adapterStatus.error!.message,
              ),
            ],
          ),
        ],
        () {
          final diag = adapterStatus.payloadDiagnostics;
          final rulesList = diag != null ? diag['rules'] : null;
          final List<Map<String, Object?>> rules = [];
          if (rulesList is List) {
            for (final item in rulesList) {
              if (item is Map) {
                rules.add(item.map((k, v) => MapEntry(k.toString(), v)));
              }
            }
          }
          if (rules.isEmpty) return const SizedBox.shrink();

          return Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const SizedBox(height: 12),
              ExpansionTile(
                title: Text(loc.get('advanced_diagnostics_title')),
                children: rules.map((rule) {
                  final id = rule['id'] as String? ?? '-';
                  final kind = rule['kind'] as String? ?? '-';
                  final running = rule['running'] as bool? ?? false;
                  final bindHost = rule['local_bind_host'] as String? ?? rule['bind_host'] ?? '-';
                  final bindPort = rule['local_bind_port'] as int? ?? rule['bind_port'] as int? ?? 0;
                  final targetHost = rule['remote_target_host'] as String? ?? rule['target_host'] ?? '-';
                  final targetPort = rule['remote_target_port'] as int? ?? rule['target_port'] as int? ?? 0;
                  final stats = rule['stats'] as Map?;
                  final packetsFromGame = stats != null ? stats['packets_from_game'] : null;
                  final packetsFromTransport = stats != null ? stats['packets_from_transport'] : null;

                  return Padding(
                    padding: const EdgeInsets.symmetric(vertical: 4, horizontal: 8),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: [
                            Icon(
                              running ? Icons.check_circle_outline : Icons.error_outline,
                              color: running ? Colors.green : Colors.red,
                              size: 16,
                            ),
                            const SizedBox(width: 8),
                            Text(
                              '$id ($kind)',
                              style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 13),
                            ),
                          ],
                        ),
                        const SizedBox(height: 4),
                        Padding(
                          padding: const EdgeInsets.only(left: 24),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                'Bind: $bindHost:$bindPort',
                                style: TextStyle(fontSize: 12, color: Theme.of(context).colorScheme.onSurfaceVariant),
                              ),
                              if (targetPort > 0)
                                Text(
                                  'Target: $targetHost:$targetPort',
                                  style: TextStyle(fontSize: 12, color: Theme.of(context).colorScheme.onSurfaceVariant),
                                ),
                              if (packetsFromGame != null || packetsFromTransport != null)
                                Text(
                                  'Packets: ${packetsFromGame ?? 0} sent, ${packetsFromTransport ?? 0} received',
                                  style: TextStyle(fontSize: 12, color: Theme.of(context).colorScheme.onSurfaceVariant),
                                ),
                            ],
                          ),
                        ),
                        const Divider(),
                      ],
                    ),
                  );
                }).toList(),
              ),
            ],
          );
        }(),
      ],
    );
  }
}

class _TrafficDetails extends StatelessWidget {
  const _TrafficDetails({
    required this.counters,
    required this.trafficRate,
    required this.adapterStatus,
  });

  final AdapterCounters counters;
  final AdapterTrafficRate trafficRate;
  final AdapterStatus adapterStatus;

  @override
  Widget build(BuildContext context) {
    final loc = Localization();
    final isBundle = adapterStatus.adapterType == 'bundle';

    if (isBundle) {
      return _BundleTrafficDetails(
        counters: counters,
        trafficRate: trafficRate,
        adapterStatus: adapterStatus,
      );
    }

    final hasTraffic =
        counters.packetsFromGame > 0 || counters.packetsFromTransport > 0;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          loc.get('realtime_traffic'),
          style: Theme.of(context).textTheme.titleSmall,
        ),
        const SizedBox(height: 8),
        Wrap(
          spacing: 10,
          runSpacing: 10,
          children: [
            _Datum(
              label: loc.get('label_game_to_relay'),
              value: _formatTrafficRate(
                trafficRate.gameToRelayPacketsPerSecond,
                trafficRate.gameToRelayKilobytesPerSecond,
              ),
            ),
            _Datum(
              label: loc.get('label_relay_to_game'),
              value: _formatTrafficRate(
                trafficRate.relayToGamePacketsPerSecond,
                trafficRate.relayToGameKilobytesPerSecond,
              ),
            ),
          ],
        ),
        const SizedBox(height: 10),
        Text(
          loc.get('cumulative_packets'),
          style: Theme.of(context).textTheme.titleSmall,
        ),
        const SizedBox(height: 8),
        Wrap(
          spacing: 10,
          runSpacing: 10,
          children: [
            _Datum(
              label: loc.get('label_game_to_relay'),
              value: counters.packetsFromGame.toString(),
            ),
            _Datum(
              label: loc.get('label_relay_to_game'),
              value: counters.packetsFromTransport.toString(),
            ),
            _Datum(
              label: loc.get('label_game_to_transport'),
              value:
                  '${counters.packetsFromGame}/${counters.packetsToTransport}',
            ),
            _Datum(
              label: loc.get('label_transport_to_game'),
              value:
                  '${counters.packetsFromTransport}/${counters.packetsToGame}',
            ),
          ],
        ),
        if (!hasTraffic) ...[
          const SizedBox(height: 8),
          _HelperText(text: loc.get('no_game_traffic')),
        ],
      ],
    );
  }
}

class _BundleTrafficDetails extends StatelessWidget {
  const _BundleTrafficDetails({
    required this.counters,
    required this.trafficRate,
    required this.adapterStatus,
  });

  final AdapterCounters counters;
  final AdapterTrafficRate trafficRate;
  final AdapterStatus adapterStatus;

  @override
  Widget build(BuildContext context) {
    final loc = Localization();
    final tcpRelayCounters = adapterStatus.getRuleCounters('tcp_relay');
    final udpRawCounters = adapterStatus.getRuleCounters('udp_raw_bridge');
    final broadcastCounters = adapterStatus.getRuleCounters('udp_broadcast_forward');

    final tcpRunning = adapterStatus.isRuleRunning('tcp_relay');
    final udpRunning = adapterStatus.isRuleRunning('udp_raw_bridge');
    final broadcastRunning = adapterStatus.isRuleRunning('udp_broadcast_forward');

    // Use rule-level counters if available, fallback to top-level
    final hasTcpTraffic = tcpRelayCounters != null &&
        (tcpRelayCounters.packetsFromGame > 0 || tcpRelayCounters.packetsFromTransport > 0);
    final hasUdpTraffic = udpRawCounters != null &&
        (udpRawCounters.packetsFromGame > 0 || udpRawCounters.packetsFromTransport > 0);
    final hasBroadcastTraffic = broadcastCounters != null &&
        (broadcastCounters.packetsFromGame > 0 || broadcastCounters.packetsFromTransport > 0);

    final hasAnyGameplayTraffic = hasTcpTraffic || hasUdpTraffic ||
        (counters.packetsFromGame > 0 || counters.packetsFromTransport > 0);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          loc.get('gameplay_traffic'),
          style: Theme.of(context).textTheme.titleSmall,
        ),
        const SizedBox(height: 8),

        // TCP gameplay
        if (tcpRelayCounters != null || tcpRunning) ...[
          KeyedSubtree(
            key: const Key('bundle-tcp-gameplay-section'),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  loc.get('tcp_gameplay'),
                  style: Theme.of(context).textTheme.labelLarge,
                ),
                const SizedBox(height: 4),
                if (tcpRelayCounters != null && hasTcpTraffic)
                  Wrap(
                    spacing: 10,
                    runSpacing: 10,
                    children: [
                      _Datum(
                        label: loc.get('label_game_to_relay'),
                        value: tcpRelayCounters.packetsFromGame.toString(),
                      ),
                      _Datum(
                        label: loc.get('label_relay_to_game'),
                        value: tcpRelayCounters.packetsFromTransport.toString(),
                      ),
                    ],
                  )
                else if (tcpRunning)
                  _HelperText(
                    text: '${loc.get('tcp_gameplay_status')}: running / ready',
                  )
                else
                  _HelperText(text: '${loc.get('tcp_gameplay_status')}: no traffic'),
              ],
            ),
          ),
          const SizedBox(height: 10),
        ],

        // UDP gameplay
        if (udpRawCounters != null || udpRunning) ...[
          KeyedSubtree(
            key: const Key('bundle-udp-gameplay-section'),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  loc.get('udp_gameplay'),
                  style: Theme.of(context).textTheme.labelLarge,
                ),
                const SizedBox(height: 2),
                Text(
                  loc.get('raw_udp_gameplay'),
                  style: Theme.of(context).textTheme.bodySmall,
                ),
                const SizedBox(height: 4),
                if (udpRawCounters != null && hasUdpTraffic)
                  Wrap(
                    spacing: 10,
                    runSpacing: 10,
                    children: [
                      _Datum(
                        label: loc.get('label_game_to_relay'),
                        value: udpRawCounters.packetsFromGame.toString(),
                      ),
                      _Datum(
                        label: loc.get('label_relay_to_game'),
                        value: udpRawCounters.packetsFromTransport.toString(),
                      ),
                    ],
                  )
                else if (udpRunning)
                  _HelperText(
                    text: '${loc.get('udp_gameplay_status')}: running / ready',
                  )
                else
                  _HelperText(text: '${loc.get('udp_gameplay_status')}: no traffic'),
              ],
            ),
          ),
          const SizedBox(height: 10),
        ],

        // Top-level gameplay total (only if rule counters aren't showing everything)
        if (counters.packetsFromGame > 0 || counters.packetsFromTransport > 0) ...[
          Text(
            loc.get('realtime_traffic'),
            style: Theme.of(context).textTheme.labelLarge,
          ),
          const SizedBox(height: 4),
          Wrap(
            spacing: 10,
            runSpacing: 10,
            children: [
              _Datum(
                label: loc.get('label_game_to_relay'),
                value: _formatTrafficRate(
                  trafficRate.gameToRelayPacketsPerSecond,
                  trafficRate.gameToRelayKilobytesPerSecond,
                ),
              ),
              _Datum(
                label: loc.get('label_relay_to_game'),
                value: _formatTrafficRate(
                  trafficRate.relayToGamePacketsPerSecond,
                  trafficRate.relayToGameKilobytesPerSecond,
                ),
              ),
            ],
          ),
          const SizedBox(height: 10),
        ],

        // LAN discovery helper
        if (broadcastCounters != null || broadcastRunning) ...[
          const Divider(),
          const SizedBox(height: 8),
          KeyedSubtree(
            key: const Key('bundle-discovery-helper-section'),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  loc.get('discovery_helper_traffic'),
                  style: Theme.of(context).textTheme.titleSmall,
                ),
                const SizedBox(height: 8),
                if (broadcastCounters != null && hasBroadcastTraffic)
                  Wrap(
                    spacing: 10,
                    runSpacing: 10,
                    children: [
                      _Datum(
                        label: loc.get('label_game_to_relay'),
                        value: broadcastCounters.packetsFromGame.toString(),
                      ),
                      _Datum(
                        label: loc.get('label_relay_to_game'),
                        value: broadcastCounters.packetsFromTransport.toString(),
                      ),
                    ],
                  )
                else
                  _HelperText(
                    text: 'LAN discovery: ${broadcastRunning ? "running" : "stopped"}',
                  ),
              ],
            ),
          ),
          const SizedBox(height: 8),
        ],

        if (!hasAnyGameplayTraffic) ...[
          _HelperText(text: loc.get('no_game_traffic')),
        ],
      ],
    );
  }
}

String _formatTrafficRate(double packetsPerSecond, double? kilobytesPerSecond) {
  final packets = packetsPerSecond.isFinite ? packetsPerSecond : 0;
  final packetText = '${packets.toStringAsFixed(0)} pkt/s';
  if (kilobytesPerSecond == null || !kilobytesPerSecond.isFinite) {
    return packetText;
  }
  return '$packetText, ${kilobytesPerSecond.toStringAsFixed(1)} KB/s';
}

String _formatLastSeenAge(num seconds) {
  final safeSeconds = seconds < 0 ? 0 : seconds;
  if (safeSeconds < 10) {
    return '${safeSeconds.toStringAsFixed(1)}s ago';
  }
  return '${safeSeconds.round()}s ago';
}

bool _hasParticipantSummary(SessionInfo session) {
  return _playersValue(session) != null || session.participants.isNotEmpty;
}

bool _hasSecondaryIpInfo(SessionInfo session) {
  return session.secondaryIpEnabled ||
      session.secondaryIpFallbackUsed ||
      (session.secondaryIpWarning != null &&
          session.secondaryIpWarning!.isNotEmpty);
}

String _protocolLabel(int version) {
  final loc = Localization();
  if (version == 2) {
    return 'v2 ${loc.get('relayOnlyLabel')}';
  }
  return 'v$version';
}

String? _playersValue(SessionInfo session) {
  final count = session.participantCount ?? session.participants.length;
  final maxPlayers = session.maxPlayers;
  if (count == 0 && maxPlayers == null) return null;
  if (maxPlayers == null) return '$count';
  return '$count / $maxPlayers';
}

String? _relayTargetValue(SessionInfo session) {
  final host = session.relayTargetHost;
  final port = session.relayTargetPort;
  if (host == null || host.isEmpty || port == null || port <= 0) {
    return null;
  }
  return '$host:$port';
}

String? _peerEndpointValue(SessionInfo session) {
  final host = session.peerEndpointHost;
  final port = session.peerEndpointPort;
  if (host != null && host.isNotEmpty && port != null && port > 0) {
    return '$host:$port';
  }
  return session.adapterStatus?.peerEndpointAddress;
}

String _yesNo(bool value) {
  return Localization().get(value ? 'yesLabel' : 'noLabel');
}

class _HighlightDatum extends StatelessWidget {
  const _HighlightDatum({
    required this.label,
    required this.value,
    required this.icon,
    this.onCopy,
  });

  final String label;
  final String value;
  final IconData icon;
  final VoidCallback? onCopy;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      width: 220,
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: scheme.primaryContainer.withValues(alpha: 0.28),
        border: Border.all(color: scheme.primary.withValues(alpha: 0.36)),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Row(
        children: [
          Icon(icon, size: 18, color: scheme.primary),
          const SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(label, style: Theme.of(context).textTheme.labelMedium),
                const SizedBox(height: 3),
                SelectableText(
                  value,
                  maxLines: 1,
                  style: Theme.of(context).textTheme.bodyMedium,
                ),
              ],
            ),
          ),
          if (onCopy != null)
            IconButton(
              tooltip: Localization().get('copy'),
              onPressed: onCopy,
              icon: const Icon(Icons.copy),
              visualDensity: VisualDensity.compact,
            ),
        ],
      ),
    );
  }
}

class _HelperText extends StatelessWidget {
  const _HelperText({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Text(
      text,
      style: Theme.of(
        context,
      ).textTheme.bodySmall?.copyWith(color: scheme.onSurfaceVariant),
    );
  }
}

class _LanDiscoverySection extends StatelessWidget {
  const _LanDiscoverySection({
    required this.status,
    required this.peers,
    required this.backendOnline,
    required this.busy,
    required this.onStart,
    required this.onStop,
    required this.onRefreshPeers,
  });

  final LanDiscoveryStatus status;
  final List<LanPeerDto> peers;
  final bool backendOnline;
  final bool busy;
  final VoidCallback onStart;
  final VoidCallback onStop;
  final VoidCallback onRefreshPeers;

  @override
  Widget build(BuildContext context) {
    final loc = Localization();
    final running = status.running;
    return Material(
      color: Colors.transparent,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      loc.get('lan_discovery_title'),
                      style: Theme.of(context).textTheme.titleMedium,
                    ),
                    const SizedBox(height: 4),
                    _HelperText(text: loc.get('lan_discovery_note')),
                  ],
                ),
              ),
              const SizedBox(width: 12),
              Chip(
                avatar: Icon(
                  Icons.circle,
                  size: 10,
                  color: running ? Colors.teal : Colors.redAccent,
                ),
                label: Text(
                  running
                      ? loc.get('lan_discovery_running')
                      : loc.get('lan_discovery_stopped'),
                ),
                visualDensity: VisualDensity.compact,
              ),
            ],
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 10,
            runSpacing: 10,
            children: [
              _Datum(
                label: loc.get('label_status'),
                value: running
                    ? loc.get('lan_discovery_running')
                    : loc.get('lan_discovery_stopped'),
              ),
              if (running && (status.peerId?.isNotEmpty ?? false))
                _Datum(
                  label: loc.get('lan_discovery_local_peer_id'),
                  value: status.peerId!,
                ),
              _Datum(
                label: loc.get('lan_discovery_instance_name'),
                value: status.instanceName.isEmpty ? '-' : status.instanceName,
              ),
              _Datum(
                label: loc.get('lan_discovery_service_port'),
                value: status.servicePort > 0 ? '${status.servicePort}' : '-',
              ),
              _Datum(
                label: loc.get('lan_discovery_broadcast_port'),
                value: status.broadcastPort > 0
                    ? '${status.broadcastPort}'
                    : '-',
              ),
              _Datum(
                label: loc.get('lan_discovery_peer_count'),
                value: '${status.peerCount}',
              ),
            ],
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 10,
            runSpacing: 10,
            children: [
              FilledButton.icon(
                onPressed: backendOnline && !busy && !running ? onStart : null,
                icon: const Icon(Icons.wifi_tethering),
                label: Text(loc.get('lan_discovery_start')),
              ),
              OutlinedButton.icon(
                onPressed: backendOnline && !busy && running ? onStop : null,
                icon: const Icon(Icons.stop_circle_outlined),
                label: Text(loc.get('lan_discovery_stop')),
              ),
              OutlinedButton.icon(
                onPressed: backendOnline && !busy ? onRefreshPeers : null,
                icon: const Icon(Icons.sync),
                label: Text(loc.get('lan_discovery_refresh_peers')),
              ),
            ],
          ),
          const SizedBox(height: 12),
          Text(
            loc.get('lan_discovery_peers'),
            style: Theme.of(context).textTheme.titleSmall,
          ),
          const SizedBox(height: 8),
          if (peers.isEmpty)
            _HelperText(text: loc.get('lan_discovery_no_peers'))
          else
            Column(
              children: peers
                  .map((peer) => _LanPeerRow(peer: peer))
                  .toList(growable: false),
            ),
        ],
      ),
    );
  }
}

class _LanPeerRow extends StatelessWidget {
  const _LanPeerRow({required this.peer});

  final LanPeerDto peer;

  @override
  Widget build(BuildContext context) {
    final loc = Localization();
    final scheme = Theme.of(context).colorScheme;
    final title = peer.name.isEmpty ? peer.host : peer.name;
    final address = '${peer.host}:${peer.port}';
    return Container(
      width: double.infinity,
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        border: Border.all(color: scheme.outlineVariant),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title, style: Theme.of(context).textTheme.titleSmall),
          const SizedBox(height: 6),
          Wrap(
            spacing: 10,
            runSpacing: 6,
            children: [
              Text(address),
              Text('${loc.get('version')}: ${peer.version}'),
              Text(
                '${loc.get('lan_discovery_last_seen')}: '
                '${_formatLastSeenAge(peer.lastSeenAgeSeconds)}',
              ),
            ],
          ),
          if (peer.peerId.isNotEmpty) ...[
            const SizedBox(height: 6),
            SelectableText(
              'peer_id: ${peer.peerId}',
              style: Theme.of(
                context,
              ).textTheme.bodySmall?.copyWith(color: scheme.onSurfaceVariant),
            ),
          ],
        ],
      ),
    );
  }
}

class _LogsDetailsSection extends StatelessWidget {
  const _LogsDetailsSection({
    required this.health,
    required this.canReadSession,
    required this.onRefreshStatus,
    required this.onLoadLogs,
    required this.events,
  });

  final HealthStatus health;
  final bool canReadSession;
  final VoidCallback onRefreshStatus;
  final VoidCallback onLoadLogs;
  final List<SessionEvent> events;

  @override
  Widget build(BuildContext context) {
    final loc = Localization();
    return Material(
      color: Colors.transparent,
      child: ExpansionTile(
        initiallyExpanded: false,
        tilePadding: EdgeInsets.zero,
        childrenPadding: const EdgeInsets.only(top: 8, bottom: 8),
        title: Text(loc.get('logs_details')),
        subtitle: Text(loc.get('logs_details_subtitle')),
        children: [
          Wrap(
            spacing: 10,
            runSpacing: 10,
            children: [
              _Datum(
                label: loc.get('label_backend_health'),
                value: health.isOnline
                    ? '${health.backend} / ${health.mode}'
                    : loc.get('backend_health_offline'),
              ),
              _Datum(
                label: loc.get('backendAdminLabel'),
                value: _yesNo(health.backendAdmin),
              ),
            ],
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 10,
            runSpacing: 10,
            children: [
              OutlinedButton.icon(
                onPressed: canReadSession ? onRefreshStatus : null,
                icon: const Icon(Icons.sync),
                label: Text(loc.get('refresh_status')),
              ),
              OutlinedButton.icon(
                onPressed: canReadSession ? onLoadLogs : null,
                icon: const Icon(Icons.receipt_long),
                label: Text(loc.get('load_logs')),
              ),
            ],
          ),
          const SizedBox(height: 12),
          _LogList(events: events),
        ],
      ),
    );
  }
}

class _PidPortDetectionSection extends StatelessWidget {
  const _PidPortDetectionSection({
    required this.pidController,
    required this.candidates,
    required this.selectedCandidate,
    required this.hint,
    required this.controlsEnabled,
    required this.onScan,
    required this.onSelected,
    required this.onApply,
  });

  final TextEditingController pidController;
  final List<ProcessPortCandidate> candidates;
  final ProcessPortCandidate? selectedCandidate;
  final String? hint;
  final bool controlsEnabled;
  final VoidCallback onScan;
  final ValueChanged<ProcessPortCandidate> onSelected;
  final VoidCallback onApply;

  @override
  Widget build(BuildContext context) {
    final loc = Localization();
    final scheme = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        border: Border.all(color: scheme.outlineVariant),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            loc.get('pid_port_title'),
            style: Theme.of(context).textTheme.titleMedium,
          ),
          const SizedBox(height: 4),
          Text(
            loc.get('pid_port_note'),
            style: Theme.of(
              context,
            ).textTheme.bodySmall?.copyWith(color: scheme.onSurfaceVariant),
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 10,
            runSpacing: 10,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              SizedBox(
                width: 180,
                child: _Field(
                  controller: pidController,
                  label: loc.get('pid_port_pid'),
                  number: true,
                  enabled: controlsEnabled,
                ),
              ),
              FilledButton.icon(
                onPressed: controlsEnabled ? onScan : null,
                icon: const Icon(Icons.search),
                label: Text(loc.get('pid_port_scan')),
              ),
              OutlinedButton.icon(
                onPressed: controlsEnabled && selectedCandidate != null
                    ? onApply
                    : null,
                icon: const Icon(Icons.check),
                label: Text(loc.get('pid_port_apply')),
              ),
            ],
          ),
          if (candidates.isNotEmpty) ...[
            const SizedBox(height: 12),
            ...candidates.map(
              (candidate) => Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: InkWell(
                  onTap: controlsEnabled ? () => onSelected(candidate) : null,
                  borderRadius: BorderRadius.circular(8),
                  child: Container(
                    padding: const EdgeInsets.all(10),
                    decoration: BoxDecoration(
                      border: Border.all(
                        color: identical(candidate, selectedCandidate)
                            ? scheme.primary
                            : scheme.outlineVariant,
                      ),
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Icon(
                          identical(candidate, selectedCandidate)
                              ? Icons.radio_button_checked
                              : Icons.radio_button_off,
                          color: identical(candidate, selectedCandidate)
                              ? scheme.primary
                              : scheme.onSurfaceVariant,
                        ),
                        const SizedBox(width: 10),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                '${candidate.protocol.toUpperCase()} '
                                '${candidate.localAddress}:${candidate.localPort}',
                                style: Theme.of(context).textTheme.titleSmall,
                              ),
                              const SizedBox(height: 3),
                              Text(
                                [
                                  if (candidate.state != null) candidate.state!,
                                  candidate.confidence,
                                ].join(' / '),
                              ),
                              const SizedBox(height: 3),
                              Text(
                                candidate.reason,
                                style: Theme.of(context).textTheme.bodySmall,
                              ),
                            ],
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              ),
            ),
          ],
          if (hint != null) ...[
            const SizedBox(height: 8),
            Text(
              hint!,
              style: Theme.of(
                context,
              ).textTheme.bodySmall?.copyWith(color: scheme.primary),
            ),
          ],
        ],
      ),
    );
  }
}

class _AdvancedBackendSettingsSection extends StatelessWidget {
  const _AdvancedBackendSettingsSection({
    required this.backendHostController,
    required this.backendPortController,
    required this.serverHostController,
    required this.serverPortController,
    required this.serverUdpPortController,
    required this.gameServerHostController,
    required this.gameServerPortController,
    required this.adapterTargetHostController,
    required this.secondaryIpController,
    required this.secondaryIpInterfaceController,
    required this.secondaryIpPrefixController,
    required this.adapterMode,
    required this.onAdapterModeChanged,
    required this.controlsEnabled,
  });

  final TextEditingController backendHostController;
  final TextEditingController backendPortController;
  final TextEditingController serverHostController;
  final TextEditingController serverPortController;
  final TextEditingController serverUdpPortController;
  final TextEditingController gameServerHostController;
  final TextEditingController gameServerPortController;
  final TextEditingController adapterTargetHostController;
  final TextEditingController secondaryIpController;
  final TextEditingController secondaryIpInterfaceController;
  final TextEditingController secondaryIpPrefixController;
  final _AdapterMode adapterMode;
  final ValueChanged<_AdapterMode> onAdapterModeChanged;
  final bool controlsEnabled;

  @override
  Widget build(BuildContext context) {
    final loc = Localization();
    return Material(
      color: Colors.transparent,
      child: ExpansionTile(
        initiallyExpanded: false,
        tilePadding: EdgeInsets.zero,
        childrenPadding: const EdgeInsets.only(top: 8, bottom: 8),
        title: Text(loc.get('advanced_backend_settings')),
        subtitle: Text(
          '${loc.get('advanced_backend_settings_subtitle')} ${loc.get('relay_inactivity_note')}',
        ),
        children: [
          Wrap(
            spacing: 10,
            runSpacing: 10,
            children: [
              SizedBox(
                width: 190,
                child: _Field(
                  controller: backendHostController,
                  label: loc.get('backend_http_host'),
                  enabled: false,
                ),
              ),
              SizedBox(
                width: 150,
                child: _Field(
                  controller: backendPortController,
                  label: loc.get('backend_http_port'),
                  number: true,
                  enabled: false,
                ),
              ),
              SizedBox(
                width: 260,
                child: _Field(
                  controller: serverHostController,
                  label: loc.get('relay_root_host'),
                  helperText: loc.get('default_relay_host'),
                  enabled: controlsEnabled,
                ),
              ),
              SizedBox(
                width: 130,
                child: _Field(
                  controller: serverPortController,
                  label: loc.get('relay_tcp_port'),
                  number: true,
                  helperText: loc.get('default_port_9000'),
                  enabled: controlsEnabled,
                ),
              ),
              SizedBox(
                width: 130,
                child: _Field(
                  controller: serverUdpPortController,
                  label: loc.get('relay_udp_port'),
                  number: true,
                  helperText: loc.get('default_port_9001'),
                  enabled: controlsEnabled,
                ),
              ),
              SizedBox(
                width: 210,
                child: _Field(
                  controller: gameServerHostController,
                  label: loc.get('game_bind_host'),
                  helperText: loc.get('default_host_127'),
                  enabled: controlsEnabled,
                ),
              ),
              SizedBox(
                width: 170,
                child: _Field(
                  key: const Key('advanced-game-port-field'),
                  controller: gameServerPortController,
                  label: loc.get('game_bind_port'),
                  number: true,
                  helperText: loc.get('game_server_port_hint'),
                  enabled: controlsEnabled,
                ),
              ),
              SizedBox(
                width: 210,
                child: DropdownButtonFormField<_AdapterMode>(
                  key: ValueKey(adapterMode),
                  initialValue: adapterMode,
                  isExpanded: true,
                  decoration: InputDecoration(
                    labelText: loc.get('adapter_mode'),
                    isDense: true,
                    border: const OutlineInputBorder(),
                  ),
                  items: [
                    DropdownMenuItem<_AdapterMode>(
                      value: _AdapterMode.bundle,
                      child: Text(loc.get('adapter_bundle')),
                    ),
                    DropdownMenuItem<_AdapterMode>(
                      value: _AdapterMode.udpOnly,
                      child: Text(loc.get('adapter_udp_only')),
                    ),
                    DropdownMenuItem<_AdapterMode>(
                      value: _AdapterMode.tcpOnly,
                      child: Text(loc.get('adapter_tcp_only')),
                    ),
                  ],
                  onChanged: controlsEnabled
                      ? (mode) {
                          if (mode != null) {
                            onAdapterModeChanged(mode);
                          }
                        }
                      : null,
                ),
              ),
              if (adapterMode == _AdapterMode.tcpOnly)
                SizedBox(
                  width: 430,
                  child: _HelperText(text: loc.get('adapter_tcp_only_helper')),
                ),
              if (adapterMode == _AdapterMode.bundle)
                SizedBox(
                  width: 430,
                  child: _HelperText(text: loc.get('adapter_bundle_helper')),
                ),
              SizedBox(
                width: 210,
                child: _Field(
                  controller: adapterTargetHostController,
                  label: loc.get('adapter_target_host'),
                  helperText: loc.get('default_host_127'),
                  enabled: controlsEnabled,
                ),
              ),
              SizedBox(
                width: 210,
                child: _Field(
                  controller: secondaryIpController,
                  label: loc.get('secondary_ip_address'),
                  helperText: loc.get('secondary_ip_address_hint'),
                  enabled: controlsEnabled,
                ),
              ),
              SizedBox(
                width: 180,
                child: _Field(
                  controller: secondaryIpInterfaceController,
                  label: loc.get('secondary_ip_interface'),
                  helperText: loc.get('secondary_ip_interface_hint'),
                  enabled: controlsEnabled,
                ),
              ),
              SizedBox(
                width: 130,
                child: _Field(
                  controller: secondaryIpPrefixController,
                  label: loc.get('secondary_ip_prefix'),
                  number: true,
                  helperText: loc.get('default_prefix_24'),
                  enabled: controlsEnabled,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

String _backendHealthLabel(HealthStatus health) {
  final loc = Localization();
  if (!health.isOnline) {
    return loc.get('backend_health_offline');
  }
  if (health.isFakeMode) {
    return loc.get('backend_health_online_fake');
  }
  return loc
      .get('backend_health_online_mode')
      .replaceAll('{mode}', health.mode);
}

String _relayStatusLabel(String status) {
  final loc = Localization();
  return switch (status) {
    'relay_ready' => loc.get('relay_ready'),
    'running' => loc.get('relay_running'),
    'failed' => loc.get('relay_failed'),
    'stopped' => loc.get('relay_stopped'),
    'starting' ||
    'room_created' ||
    'room_joined' ||
    'room_ready' => loc.get('relay_waiting'),
    _ => loc.get('relay_not_ready'),
  };
}

String _adapterStatusLabel(AdapterStatus status) {
  final loc = Localization();
  if (!status.enabled) return loc.get('adapter_disabled');
  return switch (status.status) {
    'ready' => loc.get('adapter_ready'),
    'error' => loc.get('adapter_error'),
    'stopped' => loc.get('adapter_stopped_configured'),
    'disabled' => loc.get('adapter_disabled'),
    _ => status.status.isEmpty ? loc.get('adapter_unknown') : status.status,
  };
}

String? _localGameConnectionAddress(AdapterStatus? status) {
  final diagAddr = status?.localGameConnectionAddress;
  if (diagAddr != null) return diagAddr;
  final bindHost = status?.bindHost;
  final bindPort = status?.bindPort;
  if (status?.status != 'ready' ||
      bindHost == null ||
      bindPort == null ||
      bindPort <= 0) {
    return null;
  }
  return '$bindHost:$bindPort';
}

String? _adapterBindAddress(AdapterStatus? status) {
  final bindHost = status?.bindHost;
  final bindPort = status?.bindPort;
  if (status?.status != 'ready' ||
      bindHost == null ||
      bindPort == null ||
      bindPort <= 0) {
    return null;
  }
  return '$bindHost:$bindPort';
}

String? _adapterTargetAddress(AdapterStatus? status) {
  final targetHost = status?.targetHost;
  final targetPort = status?.targetPort;
  if (targetHost == null || targetPort == null || targetPort <= 0) {
    return null;
  }
  return '$targetHost:$targetPort';
}

bool _sessionStatusNeedsPolling(String status) {
  return switch (status) {
    'starting' ||
    'room_created' ||
    'room_joined' ||
    'room_ready' ||
    'relay_ready' ||
    'running' => true,
    _ => false,
  };
}

String? _confirmedRoomIdFromEvents(List<SessionEvent> events) {
  String? roomId;
  for (final event in events) {
    if (event.type != 'room_created' && event.type != 'room_joined') {
      continue;
    }
    final value = event.data['room_id'];
    if (value == null) continue;
    final candidate = value.toString().trim();
    if (candidate.isNotEmpty) {
      roomId = candidate;
    }
  }
  return roomId;
}

bool _hasConfirmedCreateRoom(List<SessionEvent> events) {
  return events.any(
    (event) =>
        event.type == 'room_created' &&
        (event.data['room_id']?.toString().trim().isNotEmpty ?? false),
  );
}

String? _statusFromEvents(List<SessionEvent> events) {
  String? status;
  for (final event in events) {
    status = switch (event.type) {
      'session_starting' => 'starting',
      'room_created' => 'room_created',
      'room_joined' => 'room_joined',
      'room_ready' => 'room_ready',
      'room_closed' => 'closed',
      'relay_ready' => 'relay_ready',
      'session_running' => 'running',
      'session_stopping' => 'stopping',
      'session_stopped' => 'stopped',
      'session_failed' => 'failed',
      _ => status,
    };
  }
  return status;
}

String _preferredStatus(String status, String? eventStatus) {
  if (eventStatus == null) return status;
  if (_statusRank(eventStatus) >= _statusRank(status)) {
    return eventStatus;
  }
  return status;
}

int _statusRank(String status) {
  return switch (status) {
    'idle' => 0,
    'starting' => 1,
    'room_created' || 'room_joined' => 2,
    'relay_ready' => 3,
    'running' => 4,
    'stopping' => 5,
    'stopped' || 'failed' => 6,
    _ => -1,
  };
}

// =============================================================================
// Leaf widgets
// =============================================================================

class _Field extends StatelessWidget {
  const _Field({
    super.key,
    required this.controller,
    required this.label,
    this.number = false,
    this.helperText,
    this.hintText,
    this.enabled = true,
  });

  final TextEditingController controller;
  final String label;
  final bool number;
  final String? helperText;
  final String? hintText;
  final bool enabled;

  @override
  Widget build(BuildContext context) {
    return TextField(
      controller: controller,
      enabled: enabled,
      keyboardType: number ? TextInputType.number : TextInputType.text,
      decoration: InputDecoration(
        labelText: label,
        hintText: hintText,
        helperText: helperText,
        isDense: true,
        border: const OutlineInputBorder(),
      ),
    );
  }
}

class _HealthBadge extends StatelessWidget {
  const _HealthBadge({required this.health, this.waitingForBackend = false});

  final HealthStatus health;
  final bool waitingForBackend;

  @override
  Widget build(BuildContext context) {
    final loc = Localization();
    final Color color;
    final String label;
    if (waitingForBackend) {
      color = Colors.orange;
      label = loc.get('status_reconnecting');
    } else {
      color = health.isOnline ? Colors.teal : Colors.redAccent;
      label = _backendHealthLabel(health);
    }
    return Chip(
      avatar: Icon(Icons.circle, size: 10, color: color),
      label: Text(label),
      visualDensity: VisualDensity.compact,
    );
  }
}

class _ErrorBanner extends StatelessWidget {
  const _ErrorBanner({required this.error});

  final BackendError error;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final loc = Localization();
    final message = loc.get(error.message);
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: scheme.errorContainer.withValues(alpha: 0.42),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Text(
        '${error.code}: $message',
        style: TextStyle(color: scheme.onErrorContainer),
      ),
    );
  }
}

class _Datum extends StatelessWidget {
  const _Datum({required this.label, required this.value, this.onCopy});

  final String label;
  final String value;
  final VoidCallback? onCopy;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      width: 190,
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        border: Border.all(color: scheme.outlineVariant),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(label, style: Theme.of(context).textTheme.labelMedium),
                const SizedBox(height: 3),
                Text(
                  value,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: Theme.of(context).textTheme.bodyMedium,
                ),
              ],
            ),
          ),
          if (onCopy != null)
            IconButton(
              tooltip: Localization().get('copy'),
              onPressed: onCopy,
              icon: const Icon(Icons.copy),
              visualDensity: VisualDensity.compact,
            ),
        ],
      ),
    );
  }
}

class _LogList extends StatelessWidget {
  const _LogList({required this.events});

  final List<SessionEvent> events;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      constraints: const BoxConstraints(minHeight: 84, maxHeight: 220),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: scheme.surfaceContainerHighest.withValues(alpha: 0.32),
        borderRadius: BorderRadius.circular(8),
      ),
      child: events.isEmpty
          ? Text(
              Localization().get('no_session_logs'),
              style: TextStyle(color: scheme.onSurfaceVariant),
            )
          : ListView.separated(
              shrinkWrap: true,
              itemCount: events.length,
              separatorBuilder: (_, _) => const Divider(height: 10),
              itemBuilder: (context, index) {
                final event = events[index];
                return Text(
                  '${event.type}: ${event.message}',
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                );
              },
            ),
    );
  }
}

class _TabChip extends StatelessWidget {
  const _TabChip({
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(6),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
        decoration: BoxDecoration(
          color: selected
              ? scheme.primaryContainer
              : scheme.surfaceContainerHighest,
          borderRadius: BorderRadius.circular(6),
          border: Border.all(
            color: selected ? scheme.primary : scheme.outlineVariant,
          ),
        ),
        child: Text(
          label,
          style: TextStyle(
            fontWeight: selected ? FontWeight.w600 : FontWeight.w400,
            color: selected
                ? scheme.onPrimaryContainer
                : scheme.onSurfaceVariant,
            fontSize: 13,
          ),
        ),
      ),
    );
  }
}
