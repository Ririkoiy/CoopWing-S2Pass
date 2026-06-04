# S2Pass Preview Flutter UI

Preview 0.2 Flutter product UI shell / prototype.

This app is intentionally UI-only:

- UI code depends on the `BackendClient` abstraction where practical.
- Uses `MockBackendClient` for in-memory profiles, settings, servers, diagnostics, and logs.
- Does not call the Python backend.
- Does not connect to S2Pass Core.
- Does not construct S2Pass protocol JSON.
- Does not launch real games or run Network Doctor.
- Does not read or write game configuration files, game platform data, or external launcher configurations.

Pages:

- Home / Dashboard
- My Games
- Network Doctor
- Settings
- About
- Add Game dialog
- Game Detail / Profile Card
- Developer Console mock entry when Developer Mode is enabled

The default relay server preset is centralized in
`lib/services/mock_backend_client.dart` as `MockBackendClient.defaultRelayHost`.

## Backend Client Boundary

`lib/services/backend_client.dart` defines the Flutter-side service contract
used by the UI shell. `MockBackendClient` implements this interface and remains
the only instantiated backend client in Preview 0.2. There is no
`HttpBackendClient`, no WebSocket implementation, and no real backend process
launcher in this prototype.

## DTO and API Envelope Boundary

Future backend HTTP responses use a single envelope shape:

```json
{"ok": true, "data": {}}
```

or:

```json
{
  "ok": false,
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable message",
    "details": {}
  }
}
```

Flutter UI widgets should use Dart models with camelCase fields. Backend
snake_case mapping belongs in model/service code such as `toJson`,
`fromJson`, or the future HTTP/WebSocket client. User-facing UI should show
`error.message`; `error.details` is for Developer Console/debug surfaces only.
Tracebacks and internal exception text must not be shown to normal users.

## Reconnection Ownership

- Flutter only reconnects to the local backend HTTP/WebSocket service.
- Flutter does not own protocol-level reconnect.
- Flutter must not re-issue `CREATE_ROOM`, `JOIN_ROOM`, or relay tokens.
- Python Backend owns local backend process/session lifecycle.
- S2Pass Core owns future signaling reconnect, P2P failure detection, relay
  fallback, and transport recovery.
- A user-visible Retry action in Flutter should only call a backend API. The
  backend/Core decides the lower-level action.

Suggested user-visible connection states:

- `idle`
- `starting`
- `connected`
- `reconnecting`
- `relay_fallback`
- `disconnected`
- `failed`
- `stopped`

Preview 0.2 does not implement real reconnect behavior.

## Process Launch / Path Safety

Future Flutter backend launch code must use list-based process APIs:

```dart
Process.start(
  backendExePath,
  [
    '--host',
    '127.0.0.1',
    '--port',
    '21520',
    '--config-dir',
    configDir,
  ],
  workingDirectory: appRootDir,
  runInShell: false,
);
```

Rules:

- Do not concatenate a shell command string.
- Do not manually quote paths.
- Pass the executable path separately.
- Pass each argument as its own list item.
- Assume every path can contain spaces.
- Do not wrap backend startup in `cmd /c` without a specific documented reason.
- Backend-side subprocess calls must also use list arguments and `shell=False`.

Incorrect:

```dart
Process.run('$backendExePath --config "$configPath"', []);
```

Preview 0.2 does not implement a real backend launcher.

## Developer Notes / Easter Eggs

This preview build contains several UI-only interactive easter eggs and developer notes:
- **Ciallo Audio Link**: Located next to the version info on the About page. Clicking it plays `assets/audio/Ciallo.mp4`.
- **Abstract AE Icon**: A small custom violet `AΞ` button at the bottom-right corner of the About page triggers a simulated "Adobe After Effect is not responding" mock dialog.
- **Meme Sub-texts**: Friendly, lighthearted sub-texts (both in Chinese and English) are shown on failed states (like failed diagnostics or connection failure) to improve developer feedback.
