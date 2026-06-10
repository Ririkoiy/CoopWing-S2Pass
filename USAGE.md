# Co-opWinG Usage Guide

🌐 Language: English | [简体中文](USAGE.zh-CN.md)

> This document applies to the Co-opWinG preview version.
> Features, UI, and packaging may change over time.

---

## 1. Before You Start

Co-opWinG is an experimental networking helper designed to improve connectivity for some co-op games, older multiplayer games, and peer-to-peer connection scenarios.

The current version is mainly intended for:

* LAN / remote multiplayer troubleshooting
* UDP bridging and relay fallback testing
* Windows preview testing

It is not a VPN or a virtual LAN tool. It does not install a virtual network adapter or system driver.

---

## 2. Suitable Use Cases

Co-opWinG may be useful when:

* The game communicates through TCP/UDP ports
* Direct connection fails, but both players can access the same relay server
* One player is behind a campus network, CGNAT, or strict NAT
* The game supports connecting through a specified address or local port
* You know, or can test, which ports the game uses

Co-opWinG is not guaranteed to work with:

* Games that strongly depend on official matchmaking servers
* Fully encrypted games with non-configurable connection targets
* Games that require virtual LAN broadcast discovery
* Complex games that require protocol-level parsing or modification
* Game scenarios where third-party networking tools are explicitly prohibited

---

## 3. Basic Concepts

### Room

Co-opWinG uses a “room” to establish a connection between players.

Usually, one player creates a room, and the other player joins by entering the room code.

### Backend

The Co-opWinG UI connects to a local backend. The backend handles network sessions, adapters, and relay-related logic.

Under normal usage, users do not need to operate the backend manually.

### Adapter

An adapter connects a game’s local network traffic to Co-opWinG.

### Relay

If direct P2P connection fails, Co-opWinG can try to forward traffic through a relay server.

Relay performance depends on server location, bandwidth, latency, and both players’ network environments.

---

## 4. Typical Usage Flow

### Host Side

1. Start Co-opWinG.
2. Wait until the backend status becomes available.
3. Go to the room page.
4. Click create room.
5. Send the room code to the other player.
6. Configure the game or adapter settings.
7. Start the game or local bridge.
8. Watch the connection status and traffic indicators.

### Join Side

1. Start Co-opWinG.
2. Wait until the backend status becomes available.
3. Go to the room page.
4. Enter the room code provided by the host.
5. Click join room.
6. Configure the game or adapter settings.
7. Start the game or local bridge.
8. Watch the connection status and traffic indicators.

---

## 5. Testing Suggestions

For the first test, it is recommended not to start with a complex game immediately.

Suggested testing order:

1. Both computers can start Co-opWinG normally.
2. Both players can create / join a room successfully.
3. Backend status is normal.
4. Adapter status is normal.
5. Traffic counters are changing.
6. Then test the actual game.

---

## 6. FAQ

### What should I do if the backend does not start?

You can try:

* Restart Co-opWinG
* Check whether antivirus software blocked it
* Check Windows Firewall prompts
* Make sure the program folder was not moved and no required files are missing
* Check whether an old backend process is still running, or try using `Stop-CoopWing-Backend.bat`

### What should I do if creating or joining a room fails?

You can check:

* Whether both players are using the same server configuration
* Whether the room code is correct
* Whether the network can access the relay / signaling server
* Whether the firewall is blocking the program
* Whether the backend status is normal

### What should I do if the game does not connect successfully?

You can check:

* Whether the game port is correct
* Whether the game actually uses a UDP or TCP local port
* Whether the game allows manually connecting to an address
* Whether the adapter configuration matches the game port
* Whether both players have started the corresponding adapter
* Whether a firewall is blocking the connection

### Why is there traffic, but the game still does not connect?

Possible reasons include:

* The game requires multiple ports
* The game requires a protocol-aware adapter
* The game still performs official server verification
* The game uses dynamic ports
* The traffic direction does not match the configuration
* The game itself does not support this connection method

---

## 7. Windows Firewall Prompt

Windows may show a firewall prompt the first time the program runs.

If you want to test LAN or remote multiplayer connectivity, you usually need to allow Co-opWinG or its related backend program to access the network.

If access is not allowed, you may encounter:

* Room creation failure
* Room joining failure
* Local port receiving no data
* Adapter showing no traffic
* Game connection failure

---

## 8. Information to Provide When Reporting Issues

When submitting an issue, please provide as much of the following information as possible:

* Windows version
* Co-opWinG version
* Whether you created a room or joined a room
* Both players’ network environments, such as home broadband, campus network, mobile hotspot, or CGNAT
* The game or test tool used
* Configured ports
* Whether traffic changes were observed
* Whether relay fallback was triggered
* Steps to reproduce
* Logs or screenshots

---

## 9. Current Limitations

The current preview version still has the following limitations:

* Mainly targets Windows
* Not all games are supported
* Manual port configuration may be required
* Relay quality depends on the server and network environment
* UI and wording are still being adjusted
* Some features are still mainly intended for testing

The current version is a preview build for testing, not a stable release.
