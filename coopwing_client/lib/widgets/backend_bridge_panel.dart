import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../models/adapter_traffic_rate.dart';
import '../models/backend_api_models.dart';
import '../models/doctor_report.dart';
import '../services/backend_client.dart';
import '../services/localization.dart';

enum _SessionMode { create, join }

enum _AdapterMode { off, udpExperimental, tcpRelay, tcpForward }

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

  HealthStatus _health = HealthStatus.offline();
  SessionInfo? _session;
  List<SessionEvent> _events = const [];
  BackendError? _error;
  String? _pendingActionText;
  AdapterTrafficRate _trafficRate = AdapterTrafficRate.zero;
  final AdapterTrafficRateCalculator _trafficRateCalculator =
      AdapterTrafficRateCalculator();
  _SessionMode _mode = _SessionMode.create;
  _AdapterMode _adapterMode = _AdapterMode.udpExperimental;
  bool _forceRelay = true;
  bool _busy = false;
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
      'relay_ready' ||
      'running' => true,
      _ => false,
    };
  }

  bool get _hasPlayerName => _playerNameController.text.trim().isNotEmpty;

  bool get _hasGameServerPort =>
      _gameServerPortController.text.trim().isNotEmpty;

  bool get _hasRoomId => _roomController.text.trim().isNotEmpty;

  bool get _canCreate =>
      !_busy &&
      _backendOnline &&
      !_sessionStatusIsActive &&
      _hasPlayerName &&
      _hasGameServerPort;

  bool get _canJoin =>
      !_busy &&
      _backendOnline &&
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
              onRefresh: () => _checkHealth(),
            ),
            const SizedBox(height: 16),
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
            if (!_backendOnline) ...[
              const SizedBox(height: 10),
              Text(
                loc.get('backend_offline_note'),
                style: TextStyle(fontSize: 12, color: scheme.error),
              ),
            ],
            if (_pendingActionText != null) ...[
              const SizedBox(height: 12),
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
              const SizedBox(height: 14),
              _ErrorBanner(error: _error!),
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
        const SizedBox(height: 12),
        Card(
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 8),
            child: _AdvancedBackendSettingsSection(
              backendHostController: _backendHostController,
              backendPortController: _backendPortController,
              serverHostController: _serverHostController,
              serverPortController: _serverPortController,
              serverUdpPortController: _serverUdpPortController,
              gameServerHostController: _gameServerHostController,
              gameServerPortController: _gameServerPortController,
              adapterTargetHostController: _adapterTargetHostController,
              adapterMode: _adapterMode,
              onAdapterModeChanged: (mode) {
                setState(() => _adapterMode = mode);
              },
              controlsEnabled: !_busy && !_sessionStatusIsActive,
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
      _health = await widget.client.health();
      if (_health.isOnline && _error?.code == 'BACKEND_OFFLINE') {
        _error = null;
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

        final session = await widget.client.joinSession(
          serverHost: _serverHostController.text.trim(),
          serverPort: _parsePort(_serverPortController.text),
          serverUdpPort: _parsePort(_serverUdpPortController.text),
          roomId: roomId,
          playerName: playerName,
          gameServerHost: gameServerHost,
          forceRelay: _forceRelay,
          adapterConfig: _adapterConfigOrNull(includeTargetPort: false),
        );
        _applySessionSnapshot(
          session,
          await widget.client.getSessionLogs(session.sessionId),
        );
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
        await _refreshHealthBeforeAction();
        if (!_backendOnline) {
          throw const BackendError(
            code: 'BACKEND_OFFLINE',
            message: 'BACKEND_OFFLINE',
          );
        }
      }
      await action();
    } on BackendError catch (error) {
      if (error.code == 'BACKEND_OFFLINE') {
        _health = HealthStatus.offline();
      }
      _error = error;
    } catch (error) {
      _error = BackendError(code: 'UI_ERROR', message: error.toString());
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
    try {
      _health = await widget.client.health();
      if (_health.isOnline && _error?.code == 'BACKEND_OFFLINE') {
        _error = null;
      }
    } on BackendError catch (error) {
      if (error.code == 'BACKEND_OFFLINE') {
        _health = HealthStatus.offline();
        return;
      }
      rethrow;
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
    });
    _syncSessionPolling();
  }

  Future<void> _refreshSessionSnapshot(String sessionId) async {
    final status = await widget.client.getSessionStatus(sessionId);
    final events = await widget.client.getSessionLogs(sessionId);
    _applySessionSnapshot(status, events);
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
        if (error.code == 'BACKEND_OFFLINE') {
          _health = HealthStatus.offline();
        }
        _error = error;
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

  AdapterConfig? _adapterConfigOrNull({required bool includeTargetPort}) {
    if (_adapterMode == _AdapterMode.off) return null;
    final targetHost = _normalizedAdapterTargetHost();
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
      _AdapterMode.udpExperimental => AdapterConfig.udpExperimental(
        targetHost: targetHost,
        targetPort: targetPort,
      ),
      _AdapterMode.tcpRelay => AdapterConfig.tcpRelay(
        targetHost: targetHost,
        targetPort: targetPort,
      ),
      _AdapterMode.tcpForward => AdapterConfig.tcpForward(
        targetHost: targetHost,
        targetPort: targetPort,
      ),
      _AdapterMode.off => null,
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
    required this.onRefresh,
  });

  final HealthStatus health;
  final bool busy;
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
        _HealthBadge(health: health),
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
                    controller: gameServerPortController,
                    label: loc.get('game_server_port'),
                    hintText: loc.get('game_server_port_hint'),
                    number: true,
                  ),
                ),
              ],
            )
          else ...[
            SizedBox(
              width: 360,
              child: _Field(
                controller: playerNameController,
                label: loc.get('player_name'),
                hintText: loc.get('player_name_hint'),
              ),
            ),
            const SizedBox(height: 10),
            SizedBox(
              width: 240,
              child: _Field(
                controller: roomController,
                label: loc.get('room_id'),
              ),
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
          if (adapterBind != null) ...[
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
          const SizedBox(height: 10),
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
            ],
          ),
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
          Wrap(
            spacing: 10,
            runSpacing: 10,
            children: [
              _HighlightDatum(
                label: loc.get('room_id'),
                value: session.roomId ?? '-',
                icon: Icons.meeting_room_outlined,
                onCopy: session.roomId == null ? null : onCopyRoomId,
              ),
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
            ],
          ),
          if (adapterBind != null) ...[
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
          _TrafficDetails(counters: counters, trafficRate: trafficRate),
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
      ],
    );
  }
}

class _TrafficDetails extends StatelessWidget {
  const _TrafficDetails({required this.counters, required this.trafficRate});

  final AdapterCounters counters;
  final AdapterTrafficRate trafficRate;

  @override
  Widget build(BuildContext context) {
    final loc = Localization();
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

String _formatTrafficRate(double packetsPerSecond, double? kilobytesPerSecond) {
  final packets = packetsPerSecond.isFinite ? packetsPerSecond : 0;
  final packetText = '${packets.toStringAsFixed(0)} pkt/s';
  if (kilobytesPerSecond == null || !kilobytesPerSecond.isFinite) {
    return packetText;
  }
  return '$packetText, ${kilobytesPerSecond.toStringAsFixed(1)} KB/s';
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
                  initialValue: adapterMode,
                  isExpanded: true,
                  decoration: InputDecoration(
                    labelText: loc.get('adapter_mode'),
                    isDense: true,
                    border: const OutlineInputBorder(),
                  ),
                  items: [
                    DropdownMenuItem<_AdapterMode>(
                      value: _AdapterMode.off,
                      child: Text(loc.get('adapter_off')),
                    ),
                    DropdownMenuItem<_AdapterMode>(
                      value: _AdapterMode.udpExperimental,
                      child: Text(loc.get('adapter_udp_experimental')),
                    ),
                    DropdownMenuItem<_AdapterMode>(
                      value: _AdapterMode.tcpRelay,
                      child: Text(loc.get('adapter_tcp_relay')),
                    ),
                    DropdownMenuItem<_AdapterMode>(
                      value: _AdapterMode.tcpForward,
                      child: Text(loc.get('adapter_tcp_forward')),
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
              if (adapterMode == _AdapterMode.tcpRelay)
                SizedBox(
                  width: 430,
                  child: _HelperText(text: loc.get('adapter_tcp_relay_helper')),
                ),
              SizedBox(
                width: 210,
                child: _Field(
                  controller: adapterTargetHostController,
                  label: loc.get('adapter_target_host'),
                  helperText: loc.get('default_host_127'),
                  enabled: controlsEnabled && adapterMode != _AdapterMode.off,
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
    'starting' || 'room_created' || 'room_joined' => loc.get('relay_waiting'),
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
  const _HealthBadge({required this.health});

  final HealthStatus health;

  @override
  Widget build(BuildContext context) {
    final color = health.isOnline ? Colors.teal : Colors.redAccent;
    final label = _backendHealthLabel(health);
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
