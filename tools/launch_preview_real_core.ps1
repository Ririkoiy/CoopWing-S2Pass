param(
    [string]$PythonPath,
    [switch]$Help
)

$ErrorActionPreference = "Stop"

$RootHost = "127.0.0.1"
$RootTcpPort = 9000
$RootUdpPort = 9001
$BackendHost = "127.0.0.1"
$BackendPort = 21520
$PidFile = Join-Path ([System.IO.Path]::GetTempPath()) "s2pass_preview_pids.json"

function Show-Usage {
    Write-Host "Usage: .\tools\launch_preview_real_core.ps1 [-PythonPath <python.exe>] [-Help]"
    Write-Host ""
    Write-Host "Starts server.py plus the local backend in real_core mode."
    Write-Host "If -PythonPath is provided, it is used only for this launch."
    Write-Host "Run from the repository root."
}

if ($Help) {
    Show-Usage
    exit 0
}

function Invoke-Preflight {
    $scriptPath = ".\tools\check_preview_env.ps1"
    if (-not (Test-Path -LiteralPath $scriptPath)) {
        Write-Error "Preflight script not found: $scriptPath. Run this script from the S2Pass repository root."
        exit 1
    }

    Write-Host "Running preflight checks..."
    $preflightArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $scriptPath, "-Mode", "real_core")
    if (-not [string]::IsNullOrWhiteSpace($PythonPath)) {
        $preflightArgs += @("-PythonPath", $PythonPath)
    }
    & powershell @preflightArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Preflight failed. Fix the failed checks above, then rerun launch_preview_real_core.ps1."
        exit $LASTEXITCODE
    }
    Write-Host ""
}

function Assert-RepoRoot {
    $required = @(
        ".\protocol_lock.md",
        ".\server.py",
        ".\network_core.py",
        ".\backend\server.py",
        ".\backend\session_manager.py",
        ".\backend\core_session_runner.py",
        ".\s2pass_flutter_mock"
    )
    foreach ($path in $required) {
        if (-not (Test-Path -LiteralPath $path)) {
            Write-Error "Run this script from the S2Pass repository root. Missing required path: $path"
            exit 1
        }
    }
}

function Test-PythonCommand {
    param(
        [string]$Path,
        [string]$Source
    )

    if ($Path -match '\\WindowsApps\\python([0-9.]+)?\.exe$') {
        Write-Host "Ignoring $Source Python because it appears to be a WindowsApps Store stub: $Path"
        return $null
    }

    try {
        $versionOutput = & $Path --version 2>&1
        if ($LASTEXITCODE -eq 0) {
            return [pscustomobject]@{
                Path = $Path
                Source = $Source
                Version = ($versionOutput -join " ").Trim()
            }
        }
        Write-Host "Ignoring $Source Python because it did not run successfully: $versionOutput"
    } catch {
        Write-Host "Ignoring $Source Python because it failed to start: $($_.Exception.Message)"
    }

    return $null
}

function Find-Python {
    if (-not [string]::IsNullOrWhiteSpace($PythonPath)) {
        if (-not (Test-Path -LiteralPath $PythonPath)) {
            Write-Error "Explicit -PythonPath does not exist: $PythonPath"
            exit 1
        }

        $resolved = (Resolve-Path -LiteralPath $PythonPath).Path
        $result = Test-PythonCommand -Path $resolved -Source "explicit -PythonPath"
        if ($result) {
            return $result
        }
        Write-Error "Explicit -PythonPath exists but could not run --version: $resolved"
        exit 1
    }

    $candidates = @(
        @{ Path = ".\.venv\Scripts\python.exe"; Source = ".venv" },
        @{ Path = ".\venv\Scripts\python.exe"; Source = "venv" }
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate.Path) {
            $resolved = (Resolve-Path -LiteralPath $candidate.Path).Path
            $result = Test-PythonCommand -Path $resolved -Source $candidate.Source
            if ($result) {
                return $result
            }
        }
    }

    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) {
        $result = Test-PythonCommand -Path $cmd.Source -Source "PATH"
        if ($result) {
            return $result
        }
    }

    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        $result = Test-PythonCommand -Path $pyLauncher.Source -Source "py launcher"
        if ($result) {
            return $result
        }
    }

    Write-Error "Python not found. Create .venv with uv or Python, pass -PythonPath explicitly, or fix PATH so python can run."
    exit 1
}

function Test-PortInUse {
    param([int]$Port)

    $tcpPattern = "^\s*TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+\d+"
    $udpPattern = "^\s*UDP\s+\S+:$Port\s+"
    $lines = & netstat -ano 2>$null
    foreach ($line in $lines) {
        if ($line -match $tcpPattern -or $line -match $udpPattern) {
            return $true
        }
    }
    return $false
}

function Assert-PortFree {
    param([int]$Port)

    if (Test-PortInUse -Port $Port) {
        Write-Error "Port $Port is in use. If this is a previous preview, run tools\stop_preview_processes.ps1; otherwise inspect netstat."
        exit 1
    }
}

function Clear-StalePidFileOrFail {
    if (-not (Test-Path -LiteralPath $PidFile)) {
        return
    }

    $raw = Get-Content -Raw -LiteralPath $PidFile
    if ([string]::IsNullOrWhiteSpace($raw)) {
        Remove-Item -LiteralPath $PidFile -ErrorAction SilentlyContinue
        return
    }

    try {
        $entries = @(ConvertFrom-Json -InputObject $raw)
    } catch {
        Write-Error "PID file exists but is not valid JSON: $PidFile. Inspect it before launching."
        exit 1
    }

    $running = @()
    foreach ($entry in $entries) {
        $proc = Get-Process -Id ([int]$entry.pid) -ErrorAction SilentlyContinue
        if ($proc) {
            $running += $entry
        }
    }

    if ($running.Count -gt 0) {
        Write-Error "Existing preview PID file found; previous preview may still be running. Run tools\stop_preview_processes.ps1 before launching again. PID file: $PidFile"
        exit 1
    }

    Write-Host "Removing stale preview PID file: $PidFile"
    Remove-Item -LiteralPath $PidFile -ErrorAction SilentlyContinue
}

function Save-PidEntries {
    param([object[]]$Entries)

    $Entries | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $PidFile -Encoding UTF8
}

function Stop-StartedProcesses {
    param([object[]]$Entries)

    foreach ($entry in @($Entries)) {
        $processId = [int]$entry.pid
        $proc = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if (-not $proc) {
            continue
        }
        Write-Host "Stopping started process $($entry.name) (PID $processId)..."
        Stop-Process -Id $processId -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 800
        $stillRunning = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($stillRunning) {
            Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
        }
    }
    Remove-Item -LiteralPath $PidFile -ErrorAction SilentlyContinue
}

function Wait-TcpPort {
    param(
        [string]$HostName,
        [int]$Port,
        [int]$TimeoutSeconds = 15
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $client = New-Object System.Net.Sockets.TcpClient
        try {
            $async = $client.BeginConnect($HostName, $Port, $null, $null)
            if ($async.AsyncWaitHandle.WaitOne(500, $false) -and $client.Connected) {
                $client.EndConnect($async)
                return
            }
        } catch {
        } finally {
            $client.Close()
        }
        Start-Sleep -Milliseconds 500
    }

    throw "Root server did not open TCP $Port. Check server.py startup and port conflict."
}

function Start-RootServer {
    param([string]$PythonPath)

    return Start-Process -FilePath $PythonPath `
        -ArgumentList @("-u", "server.py", "--advertise-host", $RootHost) `
        -WorkingDirectory (Get-Location).Path `
        -WindowStyle Hidden `
        -PassThru
}

function Start-BackendRealCore {
    param([string]$PythonPath)

    $oldRunner = $env:S2PASS_BACKEND_RUNNER
    try {
        $env:S2PASS_BACKEND_RUNNER = "real_core"
        return Start-Process -FilePath $PythonPath `
            -ArgumentList @("-m", "backend.server", "--host", $BackendHost, "--port", [string]$BackendPort) `
            -WorkingDirectory (Get-Location).Path `
            -WindowStyle Hidden `
            -PassThru
    } finally {
        if ($null -eq $oldRunner) {
            Remove-Item Env:S2PASS_BACKEND_RUNNER -ErrorAction SilentlyContinue
        } else {
            $env:S2PASS_BACKEND_RUNNER = $oldRunner
        }
    }
}

function Wait-HealthMode {
    param(
        [string]$ExpectedMode,
        [int]$TimeoutSeconds = 15
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $url = "http://$BackendHost`:$BackendPort/health"
    $modeMismatch = $null
    while ((Get-Date) -lt $deadline) {
        try {
            $health = Invoke-RestMethod -Uri $url -Method Get -TimeoutSec 2
            if ($health.status -eq "ok" -and $health.mode -eq $ExpectedMode) {
                return $health
            }
            if ($health.status -eq "ok") {
                $modeMismatch = $health.mode
                break
            }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }

    if ($null -ne $modeMismatch) {
        throw "Backend health returned mode=$modeMismatch, expected $ExpectedMode. Check S2PASS_BACKEND_RUNNER and restart backend."
    }

    throw "Backend /health did not become ready. Check backend process logs and port $BackendPort."
}

Invoke-Preflight
Assert-RepoRoot
Clear-StalePidFileOrFail

Write-Host "S2Pass local preview (real_core mode)"
Write-Host "Repository root: $((Get-Location).Path)"
Write-Host "PID file: $PidFile"

$pythonSelection = Find-Python
$python = $pythonSelection.Path
Write-Host "Selected Python: $python (source: $($pythonSelection.Source); $($pythonSelection.Version))"

Assert-PortFree -Port $RootTcpPort
Assert-PortFree -Port $RootUdpPort
Assert-PortFree -Port $BackendPort

$entries = @()
try {
    Write-Host "Starting root server on TCP $RootTcpPort / UDP $RootUdpPort, advertise host $RootHost..."
    $rootServer = Start-RootServer -PythonPath $python
    $entries += [pscustomobject]@{
        name = "root_server"
        pid = $rootServer.Id
        command = "$python -u server.py --advertise-host $RootHost"
        role = "root_server"
        cwd = (Get-Location).Path
        started_at = (Get-Date).ToString("o")
    }
    Save-PidEntries -Entries $entries

    Wait-TcpPort -HostName $RootHost -Port $RootTcpPort

    Write-Host "Starting backend real_core mode on $BackendHost`:$BackendPort..."
    $backend = Start-BackendRealCore -PythonPath $python
    $entries += [pscustomobject]@{
        name = "backend"
        pid = $backend.Id
        command = "$python -m backend.server --host $BackendHost --port $BackendPort (S2PASS_BACKEND_RUNNER=real_core)"
        role = "backend_real_core"
        cwd = (Get-Location).Path
        started_at = (Get-Date).ToString("o")
    }
    Save-PidEntries -Entries $entries

    $health = Wait-HealthMode -ExpectedMode "real_core"

    Write-Host ""
    Write-Host "S2Pass Local Preview (real_core) is ready."
    Write-Host "Root server PID: $($rootServer.Id)"
    Write-Host "Root server ports: TCP $RootTcpPort / UDP $RootUdpPort"
    Write-Host "Backend PID: $($backend.Id)"
    Write-Host "Backend health URL: http://$BackendHost`:$BackendPort/health"
    Write-Host "Backend health mode: $($health.mode)"
    Write-Host ""
    Write-Host "Flutter launch command:"
    Write-Host "  cd .\s2pass_flutter_mock"
    Write-Host "  flutter run -d windows"
    Write-Host ""
    Write-Host "Stop command:"
    Write-Host "  .\tools\stop_preview_processes.ps1"
} catch {
    Write-Host "Launch failed: $($_.Exception.Message)"
    Stop-StartedProcesses -Entries $entries
    exit 1
}
