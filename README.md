# Co-opWinG

🌐 Language: 简体中文 | [English](#english)

---

<a id="简体中文"></a>

[使用说明](USAGE.zh-CN.md)

**Co-opWinG** 是一个轻量级、无驱动、面向 Windows 的联机网络辅助工具，主要用于帮助部分合作游戏、旧游戏或点对点联机场景改善连接问题。

它面向的典型问题包括：NAT 限制、校园网、CGNAT、路由器限制、P2P 打洞失败，以及部分游戏自身联机机制不稳定等。

Co-opWinG 尝试提供一种比完整虚拟局域网工具更轻量的方案：不安装虚拟网卡，不安装系统驱动，不深度修改系统网络环境，而是通过本地适配器、房间连接、UDP/TCP 转发与中继回退来完成联机辅助。

> 名字说明

Co-opWinG 是当前项目的对外展示名。

中文名可以暂时理解为：

## 合翼卫

“Co-op” 对应合作联机，“Wing” 有“翼”的含义。于是我决定给它取名：合翼卫！

*合翼卫？卫什么啊？何意味啊？*

> 项目状态：**预览版 / 实验性**
>
> 当前版本主要用于早期测试、朋友测试和技术反馈，不建议作为稳定生产工具使用。

---

## Co-opWinG 能做什么

Co-opWinG 提供一套本地网络桥接和中继回退机制，用于辅助基于 TCP/UDP 端口通信的游戏或工具。

当前重点：

* Windows 优先的预览版构建
* 本地后端进程管理
* 创建房间 / 加入房间流程
* UDP 传输与中继回退
* 本地 UDP 桥接适配器
* 游戏配置档 / 适配器基础结构
* Flutter 桌面端基础 UI
* 后端健康状态与会话状态展示

代码中部分内部网络核心仍然使用 **S2Pass** 命名。这是当前阶段的有意保留。
**Co-opWinG** 是产品对外展示名，**S2Pass** 是内部协议和核心模块名，除非未来进行明确、受控的重命名，否则不会随意改动。

---

## 为什么会有这个项目

许多旧游戏或合作游戏仍然依赖脆弱的点对点联机假设。但在真实网络环境中，这些假设经常失败：

* 对称 NAT
* CGNAT
* 校园网或公寓网络限制
* 入站 UDP 被阻断
* P2P 打洞失败
* 路由器行为不一致
* 游戏没有清晰暴露联机端口
* 游戏自身联机机制不稳定

完整的虚拟局域网工具可以解决其中一部分问题，但通常需要虚拟网卡、驱动、管理员权限，或者较复杂的配置流程。

Co-opWinG 尝试的是另一条路线：

> 保持轻量、显式、基于游戏配置档。
> 不使用虚拟网卡。
> 优先使用本地适配器和中继回退。
> 在直接 P2P 失败时，提供可控的替代连接路径。

---

## 当前功能

### Windows 预览 UI

当前 Flutter 桌面 UI 包含：

* 首页与后端健康状态展示
* 创建房间 / 加入房间流程
* 后端桥接状态面板
* 适配器流量速率显示
* 游戏 / 适配器预览结构
* 本地后端进程启动与关闭处理
* 基础本地化支持

### 后端

后端当前提供：

* 面向前端的 HTTP API
* 会话管理
* 适配器管理
* 真实核心运行器集成
* 用于 UI 测试的 Fake Runner 模式
* 后端进程生命周期管理

### 网络核心

内部 S2Pass 核心提供：

* TCP 信令服务器
* UDP 注册
* 创建房间 / 加入房间流程
* P2P 失败检测
* 中继回退
* 中继转发
* Keepalive / 心跳机制
* 本地 smoke test 工具

### 适配器基础

当前适配器相关内容包括：

* 游戏配置档模型
* 适配器基类
* 启动适配器
* 本地 UDP 桥接适配器
* UDP mini-game 测试工具
* 适配器管理器测试

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

当前预览版是实验性的，存在重要限制：

* 并非所有游戏都支持。
* 仍需手动配置端口。
* TCP 转发支持仍在开发中。
* 某些游戏未来可能需要协议感知型适配器。
* 中继性能取决于 VPS 和网络环境。
* 校园网、CGNAT、严格防火墙等环境仍可能导致失败。
* UI 仍处于预览阶段，部分表述可能偏开发者。
* 当前打包流程主要面向 Windows。

当前版本仅用于测试，而不是用于关键生产环境。

---

## 后续方向

近期目标：

* 改进 Windows 预览版打包流程
* 改进适配器配置流程
* 在 UI 中接入通用 UDP 转发
* 增加通用 TCP 转发
* 改进日志与诊断导出
* 改进后端崩溃恢复和状态展示
* 增加基于游戏配置档的配置流程

未来想法：

* 进程端口检测向导
* 自动生成候选游戏配置档
* 更好的中继节点配置
* 社区中继节点模式
* 更多适配器类型
* 更完整的故障排查报告

计划中的 **进程端口检测** 功能用于帮助用户在游戏启动、进入多人菜单、创建房间、开始游戏等阶段观察目标进程使用的端口，并生成候选配置档。
它只应提供建议，不自动启用高风险转发行为。

---

## 基本使用方式

具体流程可能随预览版本变化。

当前典型预览流程：

1. 启动 Co-opWinG。
2. 等待后端健康状态变为可用。
3. 创建或加入房间。
4. 配置对应游戏或适配器设置。
5. 启动游戏或本地桥接。
6. 与另一名玩家测试连接。
7. 如果失败，查看日志和流量状态。

详细说明见：[使用说明](USAGE.zh-CN.md)。

---

## 开发环境

本项目目前包含 Python 后端 / 网络核心，以及 Flutter Windows UI。

### 推荐环境

* Windows 10 / Windows 11
* Python 3.10+
* Flutter SDK，启用 Windows desktop 支持
* Git
* PowerShell

### Python 测试

在项目根目录运行：

```powershell
python -m unittest discover
```

部分 smoke test 可能需要打开本地端口或启动子进程。

### Flutter 测试

进入 Flutter mock 目录：

```powershell
cd coopwing_client
flutter test
```

### Flutter Windows 构建

```powershell
cd coopwing_client
flutter build windows
```

打包脚本和 spec 文件可能会随着预览版流程继续调整。

---

## 仓库结构

重要目录和文件：

```text
adapters/                  适配器基础与本地桥接适配器
backend/                   后端 API、会话管理、适配器管理
s2pass_flutter_mock/       Flutter Windows 预览 UI
tests/                     Python unittest 测试
tools/                     smoke test 与诊断工具
server.py                  S2Pass 信令 / 中继服务器
network_core.py            内部 S2Pass 客户端核心
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

## 项目状态

Co-opWinG 当前处于预览开发阶段。

当前目标是产出一个可用于受控测试的 Windows 预览包，然后逐步改进适配器支持、诊断能力、打包流程和用户配置体验。

欢迎提交贡献、bug 报告和测试反馈，尤其是包含以下信息的反馈：

* 操作系统版本
* 网络环境
* 使用的游戏或测试工具
* 适配器设置
* 日志
* 复现步骤
* 期望结果
* 实际结果

如有相关问题，请尽量提供具体信息。

---

## 自建服务器 / 贡献中继节点

如果你想贡献一份力，或者只是搭建自己的 Co-opWinG / S2Pass 服务器，可以在一台拥有公网 IP 的服务器上运行 `server.py`。

服务器需要开放以下端口：

```text
TCP 9000
UDP 9001
```

基本启动方式：

```powershell
python server.py --advertise-host <你的服务器公网 IP>
```

例如：

```powershell
python server.py --advertise-host 1.2.3.4
```

其中：

* `9000/TCP` 用于信令连接
* `9001/UDP` 用于 UDP 注册与中继流量
* `--advertise-host` 应填写客户端能够访问到的公网 IP 或域名

如果服务器位于云厂商环境，请同时检查：

* 系统防火墙
* 云服务器安全组
* 入站 TCP 9000
* 入站 UDP 9001

如果你只是自己和朋友测试，可以先使用自己的 VPS。
如果你希望提供公共节点，请注意带宽、滥用风险、日志、服务器成本和当地网络服务相关规定。

---

<a id="english"></a>

# Co-opWinG

🌐 Language: [简体中文](#简体中文) | English

[Usage Guide](USAGE.md)

**Co-opWinG** is a lightweight, driver-free Windows networking helper for co-op and older multiplayer games.

It is designed for situations where direct peer-to-peer connections fail because of NAT, campus networks, CGNAT, router restrictions, or unstable game networking behavior. Co-opWinG tries to provide a simpler alternative to full virtual LAN tools: no virtual network adapter, no system driver installation, and no deep system network modification.

> Project status: **Preview / experimental**
>
> This project is under active development. The current release is mainly intended for early testing, friend testing, and technical feedback.

---

## What Co-opWinG Does

Co-opWinG provides a local networking bridge and relay-based fallback system for games or tools that communicate through TCP/UDP ports.

Current focus:

* Windows-first preview build
* Local backend process management
* Room create / join workflow
* UDP transport and relay fallback
* Local UDP bridge adapter
* Game profile / adapter foundation
* Basic Flutter desktop UI
* Backend health and session state display

The internal networking core is still named **S2Pass** in parts of the codebase. This is intentional for now. **Co-opWinG** is the product-facing name; **S2Pass** remains the internal protocol/core name unless a future scoped rename is performed.

---

## Why This Exists

Many older or co-op games still rely on fragile peer-to-peer networking assumptions. In real-world environments, those assumptions often fail:

* symmetric NAT
* CGNAT
* campus or apartment network restrictions
* blocked inbound UDP
* unstable P2P hole punching
* inconsistent router behavior
* games that expose ports poorly or not at all

Full virtual LAN tools can solve some of these problems, but they often require virtual adapters, drivers, elevated permissions, or more setup than casual players want.

Co-opWinG explores a different path:

> Keep the tool lightweight, explicit, and game-profile based.
> Avoid virtual adapters.
> Prefer local adapters and relay fallback when direct P2P fails.

---

## Current Features

### Windows Preview UI

The current Flutter desktop UI includes:

* Home page with backend health display
* Room creation and joining flow
* Backend bridge panel
* Adapter traffic rate display
* Game / adapter preview structure
* Local backend process startup and shutdown handling
* Basic localization support

### Backend

The backend provides:

* HTTP API for frontend communication
* Session management
* Adapter manager
* Real core runner integration
* Fake runner mode for UI testing
* Backend process lifecycle handling

### Networking Core

The internal S2Pass core provides:

* TCP signaling server
* UDP registration
* Room create / join flow
* P2P failure detection
* Relay fallback
* Relay forwarding
* Keepalive / heartbeat behavior
* Local smoke-test tooling

### Adapter Foundation

Current adapter-related work includes:

* Game profile model
* Adapter base structure
* Launch adapter
* Local UDP bridge adapter
* UDP mini-game test tools
* Adapter manager tests

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

This preview build is experimental and has important limitations:

* Not all games are supported.
* Manual port configuration may still be required.
* TCP forwarding support is still under development.
* Some games may require protocol-aware adapters in the future.
* Relay performance depends on the VPS/network environment.
* Campus networks, CGNAT, and strict firewalls may still cause failures.
* The UI is still a preview and may expose developer-oriented wording.
* Packaging is currently Windows-focused.

Use this release for testing, not for production-critical use.

---

## Planned Direction

Near-term goals:

* Improve Windows preview packaging
* Improve adapter configuration flow
* Add generic UDP forwarding support in UI
* Add generic TCP forwarding support
* Improve logs and diagnostics export
* Improve backend crash recovery and status reporting
* Add profile-based game configuration

Future ideas:

* Process port detection wizard
* Suggested game profile generation
* Better relay node configuration
* Community relay node mode
* More adapter types for different game networking patterns
* Better troubleshooting reports

The planned **Process Port Detection** feature is intended to help users discover which ports a game process uses during launch, multiplayer menu entry, room creation, and game start. It should suggest candidate profiles, not automatically enable risky forwarding behavior.

---

## Basic Usage

The exact usage flow may change between preview builds.

Typical preview flow:

1. Start Co-opWinG.
2. Wait for backend health to become ready.
3. Create or join a room.
4. Configure the relevant game / adapter settings.
5. Start the game or local bridge.
6. Test connectivity with another player.
7. Check logs and traffic indicators if something fails.

For detailed instructions, see [Usage Guide](USAGE.md).

---

## Development Setup

This project currently contains both Python backend/core code and a Flutter Windows UI.

### Requirements

Recommended development environment:

* Windows 10 / Windows 11
* Python 3.10+
* Flutter SDK with Windows desktop support
* Git
* PowerShell

### Python Tests

Run from the project root:

```powershell
python -m unittest discover
```

Some smoke tools may require opening local ports or starting subprocesses.

### Flutter Tests

Run from the Flutter mock directory:

```powershell
cd coopwing_client
flutter test
```

### Flutter Windows Build

```powershell
cd coopwing_client
flutter build windows
```

Packaging scripts and specs may change as the preview release process evolves.

---

## Repository Structure

Important directories and files:

```text
adapters/                  Adapter foundation and local bridge adapters
backend/                   Backend API, session manager, adapter manager
s2pass_flutter_mock/       Flutter Windows preview UI
tests/                     Python unittest suite
tools/                     Smoke tests and diagnostic tools
server.py                  S2Pass signaling / relay server
network_core.py            Internal S2Pass client core
```

---

## Development Notes

This project favors small, test-backed changes and clear module boundaries.

Important principles:

* Keep networking behavior predictable.
* Avoid hidden duplicate logic.
* Avoid fallback piles that only “work by accident.”
* Keep UI logic separate from networking state machines.
* Prefer explicit adapter boundaries.
* Preserve smoke tests and diagnostics.
* Treat real network behavior as hostile until tested.

Networking code is especially good at lying politely before exploding, so tests and smoke tools are part of the project design rather than decoration.

---

## Release Notes

This preview is intended for early testing.

Before publishing a release, check:

* Python tests pass
* Flutter tests pass
* Backend starts correctly
* UI can start or connect to backend
* Create / join flow works
* Adapter panel displays expected state
* Packaging output does not include temporary build junk
* License file is included
* README and usage guide are included

Recommended release type:

> GitHub Pre-release

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

---

## Project Status

Co-opWinG is currently in preview development.

The immediate goal is to produce a usable Windows preview package for controlled testing, then gradually improve adapter support, diagnostics, packaging, and user-facing configuration.

Contributions, bug reports, and test feedback are welcome, especially when they include:

* operating system version
* network environment
* game or test tool used
* adapter settings
* logs
* steps to reproduce
* expected result
* actual result

---

## Self-hosting / Contributing a Relay Node

If you want to contribute to the project or run your own Co-opWinG / S2Pass server, you can run `server.py` on a server with a public IP address.

The server needs the following ports open:

```text
TCP 9000
UDP 9001
```

Basic startup command:

```powershell
python server.py --advertise-host <your public server IP>
```

Example:

```powershell
python server.py --advertise-host 1.2.3.4
```

Where:

* `9000/TCP` is used for signaling
* `9001/UDP` is used for UDP registration and relay traffic
* `--advertise-host` should be the public IP address or domain name reachable by clients

If the server is hosted on a cloud provider, also check:

* system firewall
* cloud security group rules
* inbound TCP 9000
* inbound UDP 9001

For private testing, a personal VPS is enough.
For public relay nodes, please consider bandwidth, abuse risk, logging, server cost, and applicable network service regulations.
