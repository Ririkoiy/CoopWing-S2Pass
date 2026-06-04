param(
    [ValidateSet("fake", "real_core")]
    [string]$Mode = "fake",
    [string]$PythonPath,
    [switch]$SkipPortCheck,
    [switch]$RequireFlutter,
    [switch]$Help
)

$ErrorActionPreference = "Stop"

$BackendPort = 21520
$RootTcpPort = 9000
$RootUdpPort = 9001
$PidFile = Join-Path ([System.IO.Path]::GetTempPath()) "s2pass_preview_pids.json"
$Failures = New-Object System.Collections.Generic.List[string]
$Warnings = New-Object System.Collections.Generic.List[string]
$SelectedPython = $null
$SelectedPythonSource = $null

function Show-Usage {
    Write-Host "Usage: .\tools\check_preview_env.ps1 [-Mode fake|real_core] [-PythonPath <python.exe>] [-SkipPortCheck] [-RequireFlutter] [-Help]"
    Write-Host ""
    Write-Host "Checks the local S2Pass preview environment without starting or stopping processes."
    Write-Host "Default mode is fake. real_core also checks root server ports 9000/9001."
    Write-Host ""
    Write-Host "Python discovery order:"
    Write-Host "  1. explicit -PythonPath"
    Write-Host "  2. .\.venv\Scripts\python.exe"
    Write-Host "  3. .\venv\Scripts\python.exe"
    Write-Host "  4. python on PATH"
    Write-Host "  5. py launcher if it runs successfully"
}

if ($Help) {
    Show-Usage
    exit 0
}

function Add-Failure {
    param([string]$Message)
    $Failures.Add($Message) | Out-Null
    Write-Host "[FAIL] $Message"
}

function Add-Warning {
    param([string]$Message)
    $Warnings.Add($Message) | Out-Null
    Write-Host "[WARN] $Message"
}

function Add-Pass {
    param([string]$Message)
    Write-Host "[PASS] $Message"
}

function Test-RepoRoot {
    $required = @(
        ".\protocol_lock.md",
        ".\server.py",
        ".\network_core.py",
        ".\backend\server.py",
        ".\backend\session_manager.py",
        ".\backend\core_session_runner.py",
        ".\s2pass_flutter_mock"
    )

    $missing = @()
    foreach ($path in $required) {
        if (-not (Test-Path -LiteralPath $path)) {
            $missing += $path
        }
    }

    if ($missing.Count -gt 0) {
        Add-Failure "Run this script from the S2Pass repository root. Missing: $($missing -join ', ')"
        return $false
    }

    Add-Pass "Repository root and required files found."
    return $true
}

function Test-PythonCommand {
    param(
        [string]$Path,
        [string]$Source
    )

    if ($Path -match '\\WindowsApps\\python([0-9.]+)?\.exe$') {
        Add-Warning "Ignoring $Source Python because it appears to be a WindowsApps Store stub: $Path"
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
        Add-Warning "Ignoring $Source Python because it did not run successfully: $versionOutput"
    } catch {
        Add-Warning "Ignoring $Source Python because it failed to start: $($_.Exception.Message)"
    }

    return $null
}

function Find-Python {
    if (-not [string]::IsNullOrWhiteSpace($PythonPath)) {
        if (-not (Test-Path -LiteralPath $PythonPath)) {
            Add-Failure "Explicit -PythonPath does not exist: $PythonPath"
            return $null
        }

        $resolved = (Resolve-Path -LiteralPath $PythonPath).Path
        $result = Test-PythonCommand -Path $resolved -Source "explicit -PythonPath"
        if ($result) {
            return $result
        }
        Add-Failure "Explicit -PythonPath exists but could not run --version: $resolved"
        return $null
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

    return $null
}

function Test-Python {
    $selection = Find-Python
    if (-not $selection) {
        Add-Failure "Python not found. Create .venv with uv or Python, pass -PythonPath explicitly, or fix PATH so python can run."
        return
    }

    $script:SelectedPython = $selection.Path
    $script:SelectedPythonSource = $selection.Source
    Add-Pass "Selected Python: $script:SelectedPython (source: $script:SelectedPythonSource; $($selection.Version))"

    $compileTargets = @(
        "backend/server.py",
        "backend/session_manager.py",
        "backend/core_session_runner.py"
    )
    $output = & $script:SelectedPython -m py_compile @compileTargets 2>&1
    if ($LASTEXITCODE -ne 0) {
        Add-Failure "Python py_compile failed for backend entry points. Fix the reported Python syntax/import issue, then rerun. Output: $output"
        return
    }

    Add-Pass "Python can compile backend entry points."
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

function Test-Ports {
    if ($SkipPortCheck) {
        Add-Warning "Port check skipped by -SkipPortCheck."
        return
    }

    $ports = @($BackendPort)
    if ($Mode -eq "real_core") {
        $ports = @($RootTcpPort, $RootUdpPort, $BackendPort)
    }

    foreach ($port in $ports) {
        if (Test-PortInUse -Port $port) {
            Add-Failure "Port $port is in use. If this is a previous preview, run tools\stop_preview_processes.ps1; otherwise inspect netstat."
        } else {
            Add-Pass "Port $port is available."
        }
    }
}

function Test-Flutter {
    if (-not $RequireFlutter) {
        Add-Pass "Flutter check not required."
        return
    }

    if (-not (Test-Path -LiteralPath ".\s2pass_flutter_mock")) {
        Add-Failure "Flutter project directory s2pass_flutter_mock was not found. Run this script from the S2Pass repository root."
        return
    }

    $flutter = Get-Command flutter -ErrorAction SilentlyContinue
    if (-not $flutter) {
        Add-Failure "flutter not found. Install Flutter SDK or launch backend-only preview."
        return
    }

    Add-Pass "Flutter command found: $($flutter.Source)"
}

function Test-RunnerEnv {
    $value = $env:S2PASS_BACKEND_RUNNER
    if ($Mode -eq "fake" -and $value -eq "real_core") {
        Add-Warning "S2PASS_BACKEND_RUNNER is currently real_core in this shell. launch_preview_fake.ps1 sets the child process to fake, but check your shell if you start backend manually."
        return
    }

    if ($Mode -eq "real_core" -and
        -not [string]::IsNullOrWhiteSpace($value) -and
        $value -ne "real_core" -and
        $value -ne "fake") {
        Add-Warning "S2PASS_BACKEND_RUNNER is '$value'. Expected empty, fake, or real_core for local preview. launch_preview_real_core.ps1 sets the child process explicitly."
        return
    }

    Add-Pass "S2PASS_BACKEND_RUNNER environment is acceptable for $Mode mode."
}

function Test-PidFile {
    if (Test-Path -LiteralPath $PidFile) {
        Add-Warning "Existing preview PID file found; previous preview may still be running. PID file: $PidFile. Suggested fix: tools\stop_preview_processes.ps1"
        return
    }

    Add-Pass "No existing preview PID file found."
}

function Test-GitGeneratedStaging {
    $git = Get-Command git -ErrorAction SilentlyContinue
    if (-not $git) {
        Add-Warning "git not found; skipping generated-file staging warning."
        return
    }

    try {
        $staged = & $git.Source -c core.excludesfile= diff --cached --name-only 2>$null
        if ($LASTEXITCODE -ne 0) {
            Add-Warning "git diff --cached failed; skipping generated-file staging warning."
            return
        }

        $generated = @()
        foreach ($path in $staged) {
            if ($path -match '^s2pass_flutter_mock/(build|\.dart_tool|android|ios|linux|macos|web)(/|$)' -or
                $path -match '^s2pass_flutter_mock/(pubspec\.lock|\.metadata)$') {
                $generated += $path
            }
        }

        if ($generated.Count -gt 0) {
            Add-Warning "Generated/ignored-looking Flutter files appear staged: $($generated -join ', '). Review before committing."
        } else {
            Add-Pass "No staged generated-looking Flutter files detected."
        }
    } catch {
        Add-Warning "Could not inspect staged files with git: $($_.Exception.Message)"
    }
}

Write-Host "S2Pass preview preflight"
Write-Host "Mode: $Mode"
Write-Host "PID file: $PidFile"
Write-Host ""

$repoOk = Test-RepoRoot
if ($repoOk) {
    Test-Python
    Test-Ports
    Test-Flutter
    Test-RunnerEnv
    Test-PidFile
    Test-GitGeneratedStaging
}

Write-Host ""
Write-Host "Preflight summary: failures=$($Failures.Count) warnings=$($Warnings.Count)"
if ($Failures.Count -eq 0) {
    Write-Host "PASS: S2Pass preview environment checks passed."
    exit 0
}

Write-Host "FAIL: Fix the failed checks above, then rerun."
exit 1
