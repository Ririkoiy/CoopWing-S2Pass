param(
    [switch]$Help
)

$ErrorActionPreference = "Stop"

$PidFile = Join-Path ([System.IO.Path]::GetTempPath()) "s2pass_preview_pids.json"

function Show-Usage {
    Write-Host "Usage: .\tools\stop_preview_processes.ps1 [-Help]"
    Write-Host ""
    Write-Host "Stops only preview processes recorded in the temp PID file."
    Write-Host "PID file: $PidFile"
}

if ($Help) {
    Show-Usage
    exit 0
}

function Assert-RepoRoot {
    $required = @(
        ".\protocol_lock.md",
        ".\backend\server.py",
        ".\network_core.py"
    )
    foreach ($path in $required) {
        if (-not (Test-Path -LiteralPath $path)) {
            Write-Error "Run this script from the S2Pass repository root. Missing required path: $path"
            exit 1
        }
    }
}

function Read-PidEntries {
    if (-not (Test-Path -LiteralPath $PidFile)) {
        return @()
    }

    $raw = Get-Content -Raw -LiteralPath $PidFile
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return @()
    }

    try {
        return @(ConvertFrom-Json -InputObject $raw)
    } catch {
        Write-Error "PID file is not valid JSON: $PidFile"
        exit 1
    }
}

function Get-ProcessCommandLine {
    param([int]$ProcessId)

    try {
        $cim = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop
        return $cim.CommandLine
    } catch {
        return $null
    }
}

function Test-TrackedCommandMatches {
    param(
        [object]$Entry,
        [string]$CommandLine
    )

    if ([string]::IsNullOrWhiteSpace($CommandLine)) {
        return $true
    }

    $role = [string]$Entry.role
    if ($role -eq "backend_fake" -or $role -eq "backend_real_core") {
        return ($CommandLine.IndexOf("backend.server", [System.StringComparison]::OrdinalIgnoreCase) -ge 0)
    }
    if ($role -eq "root_server") {
        return ($CommandLine.IndexOf("server.py", [System.StringComparison]::OrdinalIgnoreCase) -ge 0)
    }
    return $false
}

function Test-ProcessLooksRelated {
    param([object]$Entry)

    $processId = [int]$Entry.pid
    $proc = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if (-not $proc) {
        Write-Host "$($Entry.name) PID $processId is not running; skipping."
        return $false
    }

    $commandLine = Get-ProcessCommandLine -ProcessId $processId
    if (-not (Test-TrackedCommandMatches -Entry $Entry -CommandLine $commandLine)) {
        Write-Host "$($Entry.name) PID $processId appears unrelated; skipping."
        if ($commandLine) {
            Write-Host "  Command line: $commandLine"
        }
        return $false
    }

    return $true
}

function Stop-TrackedProcess {
    param([object]$Entry)

    $processId = [int]$Entry.pid
    Write-Host "Stopping $($Entry.name) (PID $processId)..."

    try {
        Stop-Process -Id $processId -ErrorAction Stop
    } catch {
        Write-Host "  Non-force stop failed or process already exited: $($_.Exception.Message)"
    }

    try {
        Wait-Process -Id $processId -Timeout 3 -ErrorAction SilentlyContinue
    } catch {
    }

    $stillRunning = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($stillRunning) {
        Write-Host "  Still running; force-stopping PID $processId only."
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
        try {
            Wait-Process -Id $processId -Timeout 3 -ErrorAction SilentlyContinue
        } catch {
        }
    }

    $stillRunning = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($stillRunning) {
        Write-Host "  WARNING: PID $processId is still running."
        return $false
    }

    Write-Host "  Stopped."
    return $true
}

Assert-RepoRoot

Write-Host "Stopping S2Pass local preview processes."
Write-Host "PID file: $PidFile"

$entries = Read-PidEntries
if ($entries.Count -eq 0) {
    Remove-Item -LiteralPath $PidFile -ErrorAction SilentlyContinue
    Write-Host "No tracked S2Pass preview processes found."
    exit 0
}

$stopped = 0
$skipped = 0
$failed = 0

foreach ($entry in $entries) {
    if (-not (Test-ProcessLooksRelated -Entry $entry)) {
        $skipped += 1
        continue
    }

    if (Stop-TrackedProcess -Entry $entry) {
        $stopped += 1
    } else {
        $failed += 1
    }
}

if ($failed -eq 0) {
    Remove-Item -LiteralPath $PidFile -ErrorAction SilentlyContinue
    Write-Host "Removed PID file."
} else {
    Write-Host "Keeping PID file because $failed process(es) did not stop cleanly."
}

Write-Host "Summary: stopped=$stopped skipped=$skipped failed=$failed"

if ($failed -ne 0) {
    exit 1
}
