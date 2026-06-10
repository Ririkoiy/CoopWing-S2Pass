# Co-opWinG

🌐 Language: 简体中文 | [English](#english)

[使用说明](USAGE.zh-CN.md)

---

## 简体中文

**Co-opWinG** 是一个轻量级、无驱动、面向 Windows 的联机网络辅助工具。

它主要用于帮助部分合作游戏、旧游戏、点对点联机场景改善连接问题，例如 NAT 限制、校园网、CGNAT、P2P 打洞失败、入站 UDP 受限等情况。

Co-opWinG 不安装虚拟网卡，不安装系统驱动，不深度修改系统网络环境。它通过本地适配器、房间连接、UDP/TCP 转发和中继回退来辅助联机。

> 项目状态：**Preview / 实验性**
>
> 当前版本主要用于朋友测试、真实游戏测试和技术反馈，不建议作为稳定生产工具使用。

---

## 当前功能

* Windows 预览版桌面 UI
* 本地后端进程管理
* 创建房间 / 加入房间
* **Bundle 模式**（TCP + UDP + LAN 发现助手，默认）
* **UDP Raw Bridge**（UDP gameplay 流量路径）
* **TCP Relay**（TCP gameplay 流量路径）
* **LAN Discovery Helper**（房间发现辅助，基于 UDP broadcast）
* UDP Only / TCP Only 模式
* 自动 PID 端口检测
* Secondary IP bind 支持
* TCP Relay 可靠帧层：

  * 序号
  * ACK
  * 重传
  * 乱序重排
  * 重复包抑制
  * 半关闭处理
* Joiner → VPS → Creator relay 链路诊断
* 后端健康状态与会话状态展示
* 示例配置文件

代码中部分内部网络核心仍然使用 **S2Pass** 命名。
**Co-opWinG** 是产品对外展示名，**S2Pass** 是内部协议和核心模块名。

---

## Co-opWinG v0.4.1 Preview

本次更新重点：

* 新增 **udp_raw_bridge**，作为 Bundle 模式下的 UDP 游戏流量路径。
* **tcp_relay** 继续负责 TCP 游戏流量。
* **udp_broadcast_forward** 不再作为全局 gameplay broadcast 转发路径。
* **udp_broadcast_forward** 现在仅作为 LAN discovery helper / 房间发现辅助。
* Bundle 模式将 **TCP gameplay**、**Raw UDP gameplay**、**LAN discovery helper** 的诊断和计数分开显示。
* UI 会分别显示本地游戏连接地址与局域网发现辅助地址。
* 这样避免把真实 UDP 游戏流量和发现广播流量混在同一条路径里。

### Co-opWinG v0.4.1 Preview (English)

* Added **udp_raw_bridge** as the Bundle mode UDP gameplay path.
* **tcp_relay** remains responsible for TCP gameplay traffic.
* **udp_broadcast_forward** is no longer used as the global gameplay broadcast path.
* **udp_broadcast_forward** is now treated as LAN discovery helper / room discovery assistance.
* Bundle mode now separates TCP gameplay, raw UDP gameplay, and LAN discovery helper diagnostics.
* UI shows local game connection and LAN discovery helper separately.
* This keeps raw UDP gameplay payloads separate from discovery broadcast traffic.

---

## 名字说明

Co-opWinG 可以理解为：

## 合翼卫

“Co-op” 对应合作联机，“Wing” 有“翼”的含义。

*合翼卫？卫什么啊？何意味啊？*

---

## 这不是什么

Co-opWinG **不是**：

* VPN
* 虚拟局域网网卡
* 系统级隧道驱动
* 万能一键联机修复工具
* 商业中继服务

本项目不包含、不分发第三方游戏文件、破解工具、DRM 绕过工具或任何专有游戏资产。

---

## 预览版限制

当前版本仍然是实验性的：

* 并非所有游戏都支持。
* 仍可能需要手动配置端口。
* TCP Relay 仍为实验功能。
* 高延迟、丢包或低带宽 VPS 可能影响稳定性。
* Minecraft 等 TCP 流量较大的游戏可能需要提高 VPS relay 带宽参数。
* 暂不支持游戏专用 Adapter。
* 某些游戏同时需要 TCP 和 UDP，单独 TCP Relay 可能无法支持。
* Windows Defender 可能因未签名预览版程序弹出警告。

---

## 基本使用方式

典型流程：

1. 启动 Co-opWinG。
2. 等待后端健康状态变为可用。
3. 创建或加入房间。
4. 选择 UDP Experimental、TCP Relay Experimental 或 Local TCP Forward。
5. 配置目标游戏端口。
6. 启动游戏并连接 Co-opWinG 显示的本地地址。
7. 如果失败，查看日志、流量状态和 relay 诊断信息。

详细说明见：[使用说明](USAGE.zh-CN.md)。

---

## 自建服务器 / 中继节点

你可以在一台拥有公网 IP 的 VPS 上运行 `server.py`。

需要开放：

```text
TCP 9000
UDP 9001
```

基础启动方式：

```powershell
uv run python server.py --advertise-host <你的服务器公网 IP>
```

例如：

```powershell
uv run python server.py --advertise-host 1.2.3.4
```

其中：

* `9000/TCP` 用于信令连接
* `9001/UDP` 用于 UDP 注册与中继流量
* `--advertise-host` 应填写客户端能够访问到的公网 IP 或域名

如果测试 TCP Relay 或 Minecraft-like 流量，可能需要提高 relay 带宽参数：

```powershell
uv run python server.py --advertise-host <你的服务器公网 IP> --relay-bytes-per-second 1048576 --relay-burst-bytes 4194304
```

如果仍然出现 relay bandwidth exceeded，可以继续提高：

```powershell
uv run python server.py --advertise-host <你的服务器公网 IP> --relay-bytes-per-second 2097152 --relay-burst-bytes 8388608
```

请同时检查：

* 系统防火墙
* 云服务器安全组
* 入站 TCP 9000
* 入站 UDP 9001
* VPS 带宽与流量限制

---

## 开发环境

推荐环境：

* Windows 10 / Windows 11
* Python 3.10+
* Flutter SDK，启用 Windows desktop 支持
* Git
* PowerShell

### Python 测试

```powershell
python -m unittest discover tests
```

### Flutter 测试

```powershell
cd coopwing_client
flutter test
```

### Flutter Windows 构建

```powershell
cd coopwing_client
flutter build windows
```

---

## 仓库结构

```text
adapters/             Adapter 基础与本地桥接 / relay adapter
backend/              后端 API、会话管理、适配器管理
coopwing_client/      Flutter Windows 客户端
config/               示例配置文件
tests/                Python unittest 测试
tools/                smoke test 与诊断工具
server.py             S2Pass 信令 / 中继服务器
network_core.py       内部 S2Pass 客户端核心
```

---

## 许可证

本项目使用 **GNU General Public License v3.0 only** 许可证。

SPDX 标识：

```text
GPL-3.0-only
```

详情见 `LICENSE` 文件。

---

## 免责声明

Co-opWinG 是一个用于学习、测试和合法多人游戏连接排障的实验性网络工具。

使用者需要自行承担使用责任。请勿使用本项目违反游戏服务条款、绕过 DRM、攻击网络、干扰其他用户，或访问你不拥有、无权测试的系统。

本项目不保证兼容任何特定游戏或网络环境。

---

<a id="english"></a>

# Co-opWinG

🌐 Language: [简体中文](#简体中文) | English

[Usage Guide](USAGE.md)

---

## English

**Co-opWinG** is a lightweight, driver-free Windows networking helper for co-op and older multiplayer games.

It is designed for situations where direct peer-to-peer connections fail because of NAT, campus networks, CGNAT, router restrictions, blocked inbound UDP, or unstable game networking behavior.

Co-opWinG does not install virtual network adapters, system drivers, or deeply modify the system network environment. It uses local adapters, room connections, UDP/TCP forwarding, and relay fallback to help multiplayer connectivity.

> Project status: **Preview / experimental**
>
> This build is intended for friend testing, real-game testing, and technical feedback. It is not recommended as a stable production tool.

---

## Current Features

* Windows preview desktop UI
* Local backend process management
* Room creation / joining
* **Bundle mode** (TCP + UDP + LAN discovery helper, default)
* **UDP Raw Bridge** (UDP gameplay path)
* **TCP Relay** (TCP gameplay path)
* **LAN Discovery Helper** (room discovery helper based on UDP broadcast)
* UDP Only / TCP Only modes
* Automatic PID port detection
* Secondary IP bind support
* Reliable framing for TCP Relay:

  * sequence numbers
  * ACK
  * retransmission
  * reordering
  * duplicate suppression
  * half-close handling
* Joiner → VPS → Creator relay-path diagnostics
* Backend health and session status display
* Example configuration files

Some internal networking code still uses the **S2Pass** name.
**Co-opWinG** is the product-facing name, while **S2Pass** remains the internal protocol/core name.

---

## What This Is Not

Co-opWinG is **not**:

* a VPN
* a virtual LAN adapter
* a system-level tunnel driver
* a universal one-click multiplayer fixer
* a commercial relay service

This project does not include or distribute third-party game files, cracks, DRM bypass tools, or proprietary game assets.

---

## Preview Limitations

This preview build is experimental:

* Not all games are supported.
* Manual port configuration may still be required.
* TCP Relay is still experimental.
* High latency, packet loss, or low-bandwidth VPS nodes may affect stability.
* TCP-heavy games such as Minecraft may require higher VPS relay bandwidth settings.
* No game-specific adapters yet.
* Some games require both TCP and UDP forwarding; TCP Relay alone may not be enough.
* Windows Defender may warn because this preview build is unsigned.

---

## Basic Usage

Typical flow:

1. Start Co-opWinG.
2. Wait for backend health to become ready.
3. Create or join a room.
4. Select UDP Experimental, TCP Relay Experimental, or Local TCP Forward.
5. Configure the target game port.
6. Start the game and connect to the local address shown by Co-opWinG.
7. If something fails, check logs, traffic counters, and relay diagnostics.

For detailed instructions, see [Usage Guide](USAGE.md).

---

## Self-hosting / Relay Node

You can run `server.py` on a VPS with a public IP address.

Required ports:

```text
TCP 9000
UDP 9001
```

Basic startup command:

```powershell
uv run python server.py --advertise-host <your public server IP>
```

Example:

```powershell
uv run python server.py --advertise-host 1.2.3.4
```

Where:

* `9000/TCP` is used for signaling
* `9001/UDP` is used for UDP registration and relay traffic
* `--advertise-host` should be a public IP or domain reachable by clients

For TCP Relay or Minecraft-like traffic, you may need higher relay bandwidth settings:

```powershell
uv run python server.py --advertise-host <your public server IP> --relay-bytes-per-second 1048576 --relay-burst-bytes 4194304
```

If relay bandwidth exceeded warnings still appear, try:

```powershell
uv run python server.py --advertise-host <your public server IP> --relay-bytes-per-second 2097152 --relay-burst-bytes 8388608
```

Also check:

* system firewall
* cloud security group rules
* inbound TCP 9000
* inbound UDP 9001
* VPS bandwidth and traffic limits

---

## Development Setup

Recommended environment:

* Windows 10 / Windows 11
* Python 3.10+
* Flutter SDK with Windows desktop support
* Git
* PowerShell

### Python Tests

```powershell
python -m unittest discover tests
```

### Flutter Tests

```powershell
cd coopwing_client
flutter test
```

### Flutter Windows Build

```powershell
cd coopwing_client
flutter build windows
```

---

## Repository Structure

```text
adapters/             Adapter foundation and local bridge / relay adapters
backend/              Backend API, session manager, adapter manager
coopwing_client/      Flutter Windows client
config/               Example configuration files
tests/                Python unittest suite
tools/                Smoke tests and diagnostics
server.py             S2Pass signaling / relay server
network_core.py       Internal S2Pass client core
```

---

## License

This project is licensed under the **GNU General Public License v3.0 only**.

SPDX identifier:

```text
GPL-3.0-only
```

See the `LICENSE` file for details.

---

## Disclaimer

Co-opWinG is an experimental networking tool for learning, testing, and legitimate multiplayer connectivity troubleshooting.

You are responsible for how you use it. Do not use this project to violate game terms of service, bypass DRM, attack networks, interfere with other users, or access systems you do not own or have permission to test.

This project does not guarantee compatibility with any specific game or network environment.
