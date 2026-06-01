import 'dart:ui' as ui;

import 'package:flutter/foundation.dart';

enum Language { zh, en }

class Localization extends ChangeNotifier {
  static final Localization _instance = Localization._internal();
  factory Localization() => _instance;
  Localization._internal()
    : _language = languageForLocale(ui.PlatformDispatcher.instance.locale);

  Language _language;
  bool _manualOverride = false;

  Language get language => _language;
  bool get manualOverride => _manualOverride;

  static Language languageForLocale(ui.Locale locale) {
    return locale.languageCode.toLowerCase().startsWith('zh')
        ? Language.zh
        : Language.en;
  }

  void useSystemLocale([ui.Locale? locale]) {
    if (_manualOverride) {
      return;
    }
    final detected = languageForLocale(
      locale ?? ui.PlatformDispatcher.instance.locale,
    );
    if (_language != detected) {
      _language = detected;
      notifyListeners();
    }
  }

  void toggleLanguage() {
    setLanguage(_language == Language.zh ? Language.en : Language.zh);
  }

  void setLanguage(Language lang, {bool manual = true}) {
    if (manual) {
      _manualOverride = true;
    }
    if (_language != lang) {
      _language = lang;
      notifyListeners();
    }
  }

  @visibleForTesting
  void resetForTesting({ui.Locale? locale}) {
    _manualOverride = false;
    _language = languageForLocale(
      locale ?? ui.PlatformDispatcher.instance.locale,
    );
    notifyListeners();
  }

  String get(String key) {
    final translations = _localizedValues[_language];
    return translations?[key] ?? key;
  }

  static const Map<Language, Map<String, String>> _localizedValues = {
    Language.zh: {
      // General UI
      'app_title': 'Co-opWinG',
      'app_subtitle':
          'Developer Preview v0.1 / Generic UDP Relay Technical Preview',
      'version': '版本',
      'home': '首页',
      'my_games': '我的游戏',
      'doctor': '网络诊断',
      'settings': '设置',
      'about': '关于',
      'add_game': '添加游戏',
      'run_diagnostics': '运行网络诊断',
      'recent_games': '最近游戏',
      'my_games_desc': '预览条目仅用于本地 Generic UDP relay 验证。',
      'open': '打开',
      'theme': '主题',
      'developer_mode': '开发者模式',
      'default_relay_server': '默认中继服务器',
      'backend_api_port': '后端 API 端口',
      'backend_type': '后端类型',
      'loading': '加载中',
      'view_report': '查看报告',
      'export_report': '导出报告',
      'open_diagnostics_folder': '打开诊断文件夹',
      'last_report': '上一次诊断报告',
      'no_diagnostics_report': '暂无诊断报告。',
      'report_history': '报告历史',
      'diagnostics_report': '网络诊断报告',
      'close': '关闭',
      'no_zippath': '目录报告无 zipPath。',
      'report_metadata': '报告元数据',
      'system_info': '系统信息',
      'network_interfaces': '网络接口',
      'server_connectivity': '服务器连通性',
      'nat_reachability': 'NAT / 可达性',
      'recommendations': '诊断建议',
      'diagnostics_help': '如果你看不懂，请找开发者或加入群 <占位符>。',

      // Settings screen
      'server_configuration': '服务器配置',
      'server_presets_note': '当前服务器预设来自模拟后端，仅保存在内存中。',
      'default_vps': '默认 Relay/root 主机',
      'default_vps_desc': '用于创建 / 加入房间时的默认 VPS/中继服务器地址。',
      'save': '保存',
      'general_settings': '通用设置',
      'ui_theme': '界面主题',
      'theme_dark_demo': '深色（演示）',
      'log_level': '日志级别',
      'developer_mode_desc': '显示开发者控制台和实验性 UDP 转发模式。',
      'language': '语言',
      'current_language': '当前：简体中文',
      'dev_console_entry': '开发者控制台入口',
      'dev_console_desc': 'DearPyGui 开发者控制台在预览版 0.1 中保持独立。此处仅显示模拟日志。',
      'default_fallback_note':
          '默认备用主机常量集中在 MockBackendClient.defaultRelayHost:',

      // Add Game dialog
      'drag_exe_here': '将游戏 .exe 拖到这里',
      'browse_files_placeholder': '浏览文件仍是当前界面壳的占位功能。',
      'browse_files': '浏览文件',
      'manual_exe_path': '手动输入 .exe 路径用于演示',
      'supported_exe_only': '仅支持 .exe 文件。Co-opWinG 不会修改游戏文件。',
      'cancel': '取消',
      'create_draft': '创建草稿',
      'display_name': '显示名称',
      'mode': '模式',
      'draft_memory_note': '此草稿在保存前仅保留在内存中。不会进行磁盘扫描、游戏平台/商店配置读取或后端调用。',

      // Game card / detail
      'launch_only': '仅启动',
      'diagnostics_only': '仅诊断',
      'udp_experimental': 'UDP 实验模式',
      'adapter_type_launch_only': '仅启动',
      'adapter_type_diagnostics_only': '仅诊断',
      'adapter_type_generic_udp_forward': 'UDP 实验模式',
      'log_level_info': '信息',
      'log_level_debug': '调试',
      'log_level_warn': '警告',
      'log_level_error': '错误',
      'ready': '就绪',
      'running': '运行中',
      'launch': '启动',
      'stop': '停止',
      'run_doctor': '运行诊断',
      'advanced_settings': '高级设置',
      'hidden_by_default': '默认隐藏，仅用于当前模拟界面审阅。',
      'no_exe_path': '未设置可执行文件路径',
      'exp_warning_text': '实验性功能。不保证联机成功。',
      'dev_mode_off_warning': '开发者模式已关闭。UDP 实验性选项保持隐藏。',
      'delete': '删除',

      // Developer Console
      'developer_console': '开发者控制台',
      'mock_event_log': '模拟事件日志',
      'no_mock_events': '暂无模拟事件。',

      // Doctor status
      'doctor_status_idle': '未开始',
      'doctor_status_running': '正在诊断',
      'doctor_status_completed': '已完成',
      'doctor_status_failed': '诊断失败',

      // Failed state dialog / error
      'diagnostics_failed_title': '诊断运行失败',
      'diagnostics_failed_desc': '无法获取网络诊断结果，请检查本地网络或重试。',

      // Connection statuses
      'status_idle': '空闲',
      'status_starting': '正在启动',
      'status_connected': '模拟已连接',
      'status_reconnecting': '重新连接中',
      'status_relay_fallback': '中继备用',
      'status_disconnected': '未连接',
      'status_failed': '失败',
      'status_stopped': '已停止',

      // Disclaimer
      'disclaimer_title': '免责声明',
      'disclaimer_1': '这是开发者预览版 / 技术预览，当前主要验证 Generic UDP relay。',
      'disclaimer_2': '本工具不会修改游戏文件。',
      'disclaimer_3': '本工具不会修改系统网络设置。',
      'disclaimer_4': '网络诊断报告可能包含本机网络信息，分享前请自行检查。',
      'disclaimer_5': '当前版本不支持 TCP，也不保证 LAN 自动发现。',

      // AE Dialogue
      'ae_dialog_title': 'Adobe After Effect 未响应',
      'ae_dialog_body': '预计等待时间：114514 秒',
      'ae_dialog_btn1': '等待应用响应',
      'ae_dialog_btn2': '等待问号响应',

      // Meme sub-texts
      'meme_cat_cable': '网线可能被奶茶咬断啦。',
      'meme_ninja_packets': '忍者数据包还没抵达。',
      'meme_packets_lost': '数据包迷路了，正在找路牌。',
      'meme_nat_sama': 'NAT-sama 今天也很严格。',
      'meme_gremlins_off': '网络精灵今天不上班。',
      'meme_cat_topology': '奶茶正在审查网络拓扑。',
      'meme_route_confirmed': '通路确认，出击许可。',
      'meme_not_your_fault': '这不是你的错，至少不全是。',
      'mock_backend_client': '模拟后端',
      'room_connection': '房间连接',
      'room_connection_desc': '通过本地后端创建或加入测试房间，用于验证 Generic UDP relay。',
      'create_room': '创建房间',
      'join_room': '加入房间',
      'preview_udp_game': '预览 UDP 游戏',
      'relay_preview_local_validation': '中继预览 / 本地验证',
      'relay_only': '仅中继',
      'room_entry_subtitle': '创建或加入 relay 技术预览房间。',
      'preview_status_note':
          '开发者预览版。当前主要验证 Generic UDP relay；不支持 TCP，不保证 LAN 自动发现。',
      'home_short_warning': '当前版本仅验证 Generic UDP relay，不支持 TCP，不保证 LAN 自动发现。',
      'home_chinese_name_easter_egg': '中文名可能是合翼卫？ 卫什么啊，何意味啊。',
      'room_panel_title': '创建 / 加入房间',
      'room_panel_subtitle': '本地后端：自动管理（127.0.0.1:21520）',
      'refresh_health': '刷新后端状态',
      'backend_offline_note': '后端离线。请检查 logs/backend.log 或重启应用。',
      'creating_room': '正在创建房间...',
      'joining_room': '正在加入房间...',
      'refreshing_status': '正在刷新状态...',
      'loading_logs': '正在加载日志...',
      'stopping_session': '正在停止会话...',
      'reset_display_title': '重置本地显示？',
      'reset_display_body': '这只会清空本地界面显示。\n不会停止后端会话。\n如果后端仍在运行，会话可能仍然存在。',
      'reset': '重置',
      'player_name': '玩家名称',
      'player_name_hint': '请输入玩家名称',
      'room_id': '房间 ID',
      'current_session': '当前会话',
      'current_session_summary': '当前会话摘要',
      'copy': '复制',
      'copy_room_id': '复制房间 ID',
      'stop_session': '停止会话',
      'reset_local_display': '重置本地显示',
      'relay_inactivity_note': '30 分钟无 relay 流量会自动断开。',
      'relay_credential_hidden_note': '敏感中继凭证不会显示在界面中。',
      'relay_address_warning':
          'VPS/Relay 地址只供 CoopWing 使用，不应直接填进游戏 connect 命令。',
      'adapter_bind_helper':
          '游戏客户端应连接本机连接地址，例如 connect 127.0.0.1:{adapter_port}',
      'adapter_target_helper': '游戏服务器地址应指向房主本机游戏服务器端口，例如 127.0.0.1:27015',
      'realtime_traffic': '实时流量',
      'cumulative_packets': '累计包数',
      'label_game_to_relay': '游戏 -> Relay',
      'label_relay_to_game': 'Relay -> 游戏',
      'no_game_traffic': '未检测到游戏流量。请确认游戏连接的是本机连接地址。',
      'logs_details': '日志 / 详情',
      'logs_details_subtitle': '会话状态和后端提供的日志',
      'refresh_status': '刷新状态',
      'load_logs': '加载日志',
      'no_session_logs': '暂无会话日志。',
      'advanced_backend_settings': '高级后端设置',
      'advanced_backend_settings_subtitle':
          '后端 HTTP 端点和 relay/root 服务器设置。后端应保持 127.0.0.1。',
      'backend_http_host': '后端 HTTP 主机',
      'backend_http_port': '后端 HTTP 端口',
      'relay_root_host': 'Relay/root 主机',
      'relay_tcp_port': 'Relay TCP 端口',
      'relay_udp_port': 'Relay UDP 端口',
      'game_bind_host': '游戏绑定主机',
      'game_bind_port': '游戏绑定端口',
      'adapter_mode': '适配器模式',
      'adapter_off': '关闭',
      'adapter_udp_experimental': 'UDP 实验模式',
      'force_relay': '强制中继',
      'adapter_target_host': '游戏服务器主机',
      'adapter_target_port': '游戏服务器端口',
      'default_relay_host': '默认 120.27.210.184',
      'default_host_127': '默认 127.0.0.1',
      'default_port_9000': '默认 9000',
      'default_port_9001': '默认 9001',
      'label_role': '角色',
      'label_status': '状态',
      'label_relay_status': 'Relay 状态',
      'label_backend_health': '后端状态',
      'label_session_id': '会话 ID',
      'label_error': '错误',
      'label_adapter': '适配器',
      'label_adapter_bind': '本机连接地址',
      'label_adapter_target': '游戏服务器地址',
      'label_game_to_transport': '游戏 -> 传输',
      'label_transport_to_game': '传输 -> 游戏',
      'label_adapter_error_code': '适配器错误码',
      'label_adapter_error': '适配器错误',
      'backend_health_offline': '离线',
      'backend_health_online_fake': '在线 fake',
      'backend_health_online_mode': '在线 {mode}',
      'relay_ready': '已就绪',
      'relay_running': '运行中',
      'relay_failed': '失败',
      'relay_stopped': '已停止',
      'relay_waiting': '等待中',
      'relay_not_ready': '未就绪',
      'adapter_disabled': '已禁用',
      'adapter_ready': '已就绪',
      'adapter_error': '错误',
      'adapter_stopped_configured': '已停止 / 已配置但未运行',
      'adapter_unknown': '未知',
      'invalid_player_name': '请输入玩家名称。',
      'invalid_port': '端口必须在 0 到 65535 之间。',
      'invalid_room_id': '加入房间需要填写房间 ID。',
      'invalid_game_port': '加入房间的游戏端口必须在 1 到 65535 之间。',
      'invalid_adapter_target_port': '游戏服务器端口必须在 1 到 65535 之间。',
      'backend_starting': '正在启动本地后端...',
      'backend_start_failed': '本地后端启动失败，请查看 logs/backend.log',
      'backend_not_found': '未找到后端可执行文件，安装可能不完整。',
      'backend_launch_error': '无法启动后端进程。',
      'game_server_port': '游戏服务器端口',
      'game_server_port_hint': '例如 27015',
      'invalid_game_server_port': '请填写房主本机游戏服务器端口，例如 27015',
    },
    Language.en: {
      // General UI
      'app_title': 'Co-opWinG',
      'app_subtitle':
          'Developer Preview v0.1 / Generic UDP Relay Technical Preview',
      'version': 'Version',
      'home': 'Home',
      'my_games': 'My Games',
      'doctor': 'Doctor',
      'settings': 'Settings',
      'about': 'About',
      'add_game': 'Add Game',
      'run_diagnostics': 'Run Diagnostics',
      'recent_games': 'Recent Games',
      'my_games_desc':
          'Preview entries are for local Generic UDP relay validation only. '
          'Launch simulates process start; no real game adapter integration yet.',
      'open': 'Open',
      'theme': 'Theme',
      'developer_mode': 'Developer Mode',
      'default_relay_server': 'Default Relay Server',
      'backend_api_port': 'Backend API Port',
      'backend_type': 'Backend Type',
      'loading': 'Loading',
      'view_report': 'View Report',
      'export_report': 'Export Report',
      'open_diagnostics_folder': 'Open Diagnostics Folder',
      'last_report': 'Last Report',
      'no_diagnostics_report': 'No diagnostics report yet.',
      'report_history': 'Report History',
      'diagnostics_report': 'Diagnostics Report',
      'close': 'Close',
      'no_zippath': 'No zipPath for directory reports.',
      'report_metadata': 'Report Metadata',
      'system_info': 'System Info',
      'network_interfaces': 'Network Interfaces',
      'server_connectivity': 'Server Connectivity',
      'nat_reachability': 'NAT / Reachability',
      'recommendations': 'Recommendations',
      'diagnostics_help':
          'If you do not understand the report, ask the developer or join the group <placeholder>.',

      // Settings screen
      'server_configuration': 'Server Configuration',
      'server_presets_note':
          'Server presets come from MockBackendClient and are saved in memory only.',
      'default_vps': 'Default Relay/root host',
      'default_vps_desc':
          'Used as the default VPS/relay server address for create/join room.',
      'save': 'Save',
      'general_settings': 'General Settings',
      'ui_theme': 'UI Theme',
      'theme_dark_demo': 'dark (demo)',
      'log_level': 'Log Level',
      'developer_mode_desc':
          'Show developer console and experimental UDP bridge mode.',
      'language': 'Language',
      'current_language': 'Current: English',
      'dev_console_entry': 'Developer Console Entry',
      'dev_console_desc':
          'DearPyGui developer console remains separate in Preview 0.1. This entry shows mock logs only.',
      'default_fallback_note':
          'Default fallback host constant is centralized in MockBackendClient.defaultRelayHost:',

      // Add Game dialog
      'drag_exe_here': 'Drag your game .exe here',
      'browse_files_placeholder':
          'Browse Files is a placeholder in this UI shell.',
      'browse_files': 'Browse Files',
      'manual_exe_path': 'Manual .exe path for demo',
      'supported_exe_only':
          'Supported: .exe files only. Co-opWinG will not modify game files.',
      'cancel': 'Cancel',
      'create_draft': 'Create Draft',
      'display_name': 'Display name',
      'mode': 'Mode',
      'draft_memory_note':
          'This draft stays in memory only until saved. No disk scan, game platform/store config read, or backend call is performed.',

      // Game card / detail
      'launch_only': 'Launch Only',
      'diagnostics_only': 'Diagnostics Only',
      'udp_experimental': 'UDP Experimental',
      'adapter_type_launch_only': 'Launch Only',
      'adapter_type_diagnostics_only': 'Diagnostics Only',
      'adapter_type_generic_udp_forward': 'UDP Experimental',
      'log_level_info': 'INFO',
      'log_level_debug': 'DEBUG',
      'log_level_warn': 'WARN',
      'log_level_error': 'ERROR',
      'ready': 'Ready',
      'running': 'Running',
      'launch': 'Launch',
      'stop': 'Stop',
      'run_doctor': 'Run Doctor',
      'advanced_settings': 'Advanced Settings',
      'hidden_by_default':
          'Hidden by default. For review only in this mock UI.',
      'no_exe_path': 'No executable path',
      'exp_warning_text':
          'Experimental. Does not guarantee online connectivity.',
      'dev_mode_off_warning':
          'Developer Mode is off. UDP experimental options stay hidden.',
      'delete': 'Delete',

      // Developer Console
      'developer_console': 'Developer Console',
      'mock_event_log': 'Mock Event Log',
      'no_mock_events': 'No mock events yet.',

      // Doctor status
      'doctor_status_idle': 'Idle',
      'doctor_status_running': 'Running',
      'doctor_status_completed': 'Completed',
      'doctor_status_failed': 'Failed',

      // Failed state dialog / error
      'diagnostics_failed_title': 'Diagnostics Run Failed',
      'diagnostics_failed_desc':
          'Failed to retrieve diagnostics results. Please check your local network or try again.',

      // Connection statuses
      'status_idle': 'Idle',
      'status_starting': 'Starting',
      'status_connected': 'Mock Connected',
      'status_reconnecting': 'Reconnecting',
      'status_relay_fallback': 'Relay Fallback',
      'status_disconnected': 'Disconnected',
      'status_failed': 'Failed',
      'status_stopped': 'Stopped',

      // Disclaimer
      'disclaimer_title': 'Disclaimer',
      'disclaimer_1':
          'This is a developer / technical preview focused on Generic UDP relay validation.',
      'disclaimer_2': 'This tool does not modify game files.',
      'disclaimer_3': 'This tool does not modify system network settings.',
      'disclaimer_4':
          'Diagnostic reports may include local network information. Please review before sharing.',
      'disclaimer_5':
          'This build does not support TCP and does not guarantee LAN discovery.',

      // AE Dialogue
      'ae_dialog_title': 'Adobe After Effect is not responding',
      'ae_dialog_body': 'Estimated waiting time: 114514 seconds',
      'ae_dialog_btn1': 'Wait for the app to respond',
      'ae_dialog_btn2': 'Wait for Question Mark to respond',

      // Meme sub-texts
      'meme_cat_cable': 'Maybe Milk Tea chewed the cable.',
      'meme_ninja_packets': 'Aieeee, ninja packets.',
      'meme_packets_lost': 'The packets took a wrong turn.',
      'meme_nat_sama': 'NAT-sama says no.',
      'meme_gremlins_off': 'The network gremlins are off duty.',
      'meme_cat_topology': 'Milk Tea is reviewing the network topology.',
      'meme_route_confirmed': 'Route confirmed. Launch permitted.',
      'meme_not_your_fault': 'Not your fault. Probably.',
      'mock_backend_client': 'MockBackendClient',
      'room_connection': 'Room Connection',
      'room_connection_desc':
          'Create or join a test room through the local backend for Generic UDP relay validation.',
      'create_room': 'Create Room',
      'join_room': 'Join Room',
      'preview_udp_game': 'Preview UDP Game',
      'relay_preview_local_validation': 'Relay preview / local validation',
      'relay_only': 'Relay-only',
      'room_entry_subtitle': 'Create or join a relay technical preview room.',
      'preview_status_note':
          'Developer Preview. Focused on Generic UDP relay validation; no TCP support or LAN discovery guarantee.',
      'home_short_warning':
          'This build only validates Generic UDP relay; no TCP support or LAN discovery guarantee.',
      'home_chinese_name_easter_egg': '中文名可能是合翼卫？ 卫什么啊，何意味啊。',
      'room_panel_title': 'Create / Join Room',
      'room_panel_subtitle':
          'Local backend: managed automatically (127.0.0.1:21520)',
      'refresh_health': 'Refresh health',
      'backend_offline_note':
          'Backend offline. Check logs/backend.log or restart the app.',
      'creating_room': 'Creating room...',
      'joining_room': 'Joining room...',
      'refreshing_status': 'Refreshing status...',
      'loading_logs': 'Loading logs...',
      'stopping_session': 'Stopping session...',
      'reset_display_title': 'Reset local display?',
      'reset_display_body':
          'This only clears the local UI display.\nIt does not stop the backend session.\nIf the backend is still running, the session may still exist there.',
      'reset': 'Reset',
      'player_name': 'Player name',
      'player_name_hint': 'PlayerA',
      'room_id': 'Room ID',
      'current_session': 'Current Session',
      'current_session_summary': 'Current Session Summary',
      'copy': 'Copy',
      'copy_room_id': 'Copy Room ID',
      'stop_session': 'Stop Session',
      'reset_local_display': 'Reset local display',
      'relay_inactivity_note':
          'Rooms disconnect automatically after 30 minutes without relay traffic.',
      'relay_credential_hidden_note':
          'Sensitive relay credentials are not shown in the UI.',
      'relay_address_warning':
          'The VPS/Relay address is for CoopWing only. Do not put it directly into the game connect command.',
      'adapter_bind_helper':
          'Game clients should connect to the local connection address, for example connect 127.0.0.1:{adapter_port}',
      'adapter_target_helper':
          'The game server address should point to the host local game server port, for example 127.0.0.1:27015',
      'realtime_traffic': 'Realtime Traffic',
      'cumulative_packets': 'Cumulative Packets',
      'label_game_to_relay': 'Game -> Relay',
      'label_relay_to_game': 'Relay -> Game',
      'no_game_traffic':
          'No game traffic detected. Make sure the game connects to the local connection address.',
      'logs_details': 'Logs / Details',
      'logs_details_subtitle': 'Session status and backend-provided logs',
      'refresh_status': 'Refresh status',
      'load_logs': 'Load logs',
      'no_session_logs': 'No session logs.',
      'advanced_backend_settings': 'Advanced Backend Settings',
      'advanced_backend_settings_subtitle':
          'Backend HTTP endpoint and relay/root server settings. Keep the backend on 127.0.0.1.',
      'backend_http_host': 'Backend HTTP host',
      'backend_http_port': 'Backend HTTP port',
      'relay_root_host': 'Relay/root host',
      'relay_tcp_port': 'Relay TCP port',
      'relay_udp_port': 'Relay UDP port',
      'game_bind_host': 'Game bind host',
      'game_bind_port': 'Game bind port',
      'adapter_mode': 'Adapter mode',
      'adapter_off': 'Off',
      'adapter_udp_experimental': 'UDP Experimental',
      'force_relay': 'Force Relay',
      'adapter_target_host': 'Game server host',
      'adapter_target_port': 'Game server port',
      'default_relay_host': 'Default 120.27.210.184',
      'default_host_127': 'Default 127.0.0.1',
      'default_port_9000': 'Default 9000',
      'default_port_9001': 'Default 9001',
      'label_role': 'role',
      'label_status': 'status',
      'label_relay_status': 'relay_status',
      'label_backend_health': 'backend_health',
      'label_session_id': 'session_id',
      'label_error': 'error',
      'label_adapter': 'Adapter',
      'label_adapter_bind': 'Local connection address',
      'label_adapter_target': 'Game server address',
      'label_game_to_transport': 'game -> transport',
      'label_transport_to_game': 'transport -> game',
      'label_adapter_error_code': 'adapter_error_code',
      'label_adapter_error': 'adapter_error',
      'backend_health_offline': 'offline',
      'backend_health_online_fake': 'online fake',
      'backend_health_online_mode': 'online {mode}',
      'relay_ready': 'Ready',
      'relay_running': 'Running',
      'relay_failed': 'Failed',
      'relay_stopped': 'Stopped',
      'relay_waiting': 'Waiting',
      'relay_not_ready': 'Not ready',
      'adapter_disabled': 'Disabled',
      'adapter_ready': 'Ready',
      'adapter_error': 'Error',
      'adapter_stopped_configured': 'Stopped / configured but not running',
      'adapter_unknown': 'Unknown',
      'invalid_player_name': 'Player name is required.',
      'invalid_port': 'Port must be between 0 and 65535.',
      'invalid_room_id': 'Room ID is required for Join.',
      'invalid_game_port': 'Game port must be between 1 and 65535 for Join.',
      'invalid_adapter_target_port':
          'Game server port must be between 1 and 65535.',
      'backend_starting': 'Starting local backend...',
      'backend_start_failed':
          'Local backend failed to start. See logs/backend.log.',
      'backend_not_found':
          'Backend executable not found. Installation may be incomplete.',
      'backend_launch_error': 'Cannot start backend process.',
      'game_server_port': 'Game server port',
      'game_server_port_hint': 'e.g. 27015',
      'invalid_game_server_port':
          'Enter the host local game server port, for example 27015.',
    },
  };
}
