<#
  Pushes a new version of the Desk Tracker code to the server and restarts it.
  Keeps the server's config.yaml, .env and data\ (history, desks, tuning) untouched.
  Usage:  powershell -ExecutionPolicy Bypass -File .\update-app.ps1
          add  -Stream 0  to switch the server to the cameras' main (full resolution) stream, -Stream 1 for sub
#>
param(
    [Parameter(Mandatory)] [string]$Server,
    [Parameter(Mandatory)] [string]$User,
    [string]$RemotePath = "C:\desk-tracker",
    [int]$Port = 8080,
    [ValidateSet("", "0", "1")] [string]$Stream = ""
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

$cred = Get-Credential -UserName $User -Message "Password for $User on $Server"
$session = New-PSSession -ComputerName $Server -Credential $cred -Authentication Negotiate
try {
    Write-Host "Stopping Desk Tracker and backing up the current code..."
    Invoke-Command -Session $session -ArgumentList $RemotePath -ScriptBlock {
        param($p)
        Stop-ScheduledTask -TaskName DeskTracker -ErrorAction SilentlyContinue
        Get-CimInstance Win32_Process |
            Where-Object { $_.CommandLine -and $_.CommandLine -like "*$p*" -and $_.Name -in "python.exe", "cmd.exe" } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Start-Sleep 2
        if (Test-Path "$p\app.previous") { Remove-Item -Recurse -Force "$p\app.previous" }
        if (Test-Path "$p\app") { Rename-Item "$p\app" "app.previous" }
        if (Test-Path "$p\tools") { Remove-Item -Recurse -Force "$p\tools" }
    }
    Write-Host "Copying new code..."
    foreach ($item in "app", "tools", "run.cmd", "README.md") {
        Copy-Item -Path (Join-Path $here $item) -Destination $RemotePath -ToSession $session -Recurse -Force
    }
    Invoke-Command -Session $session -ArgumentList $RemotePath, $Stream -ScriptBlock {
        param($p, $stream)
        Get-ChildItem $p -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
        if ($stream -ne "") {
            # nvr.stream in config.yaml (the only 2-space-indented "stream:" key)
            Copy-Item "$p\config.yaml" "$p\config.yaml.bak" -Force
            $lines = Get-Content "$p\config.yaml" | ForEach-Object { $_ -replace '^(  stream:\s*)\d', ('${1}' + $stream) }
            [IO.File]::WriteAllLines("$p\config.yaml", [string[]]$lines)
            Write-Host "config.yaml: nvr.stream set to $stream"
        }
        Start-ScheduledTask -TaskName DeskTracker
    }

    Write-Host "Restarting and waiting for the cameras (up to 60 s)..."
    $result = Invoke-Command -Session $session -ArgumentList $RemotePath, $Port -ScriptBlock {
        param($p, $port)
        $kv = @{}
        Get-Content "$p\.env" | Where-Object { $_ -match '^([A-Z_]+)=(.*)$' } | ForEach-Object { $kv[$Matches[1]] = $Matches[2] }
        $pair = "$($kv['DASHBOARD_USER']):$($kv['DASHBOARD_PASSWORD'])"
        $auth = @{ Authorization = "Basic " + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($pair)) }
        $info = $null
        for ($i = 0; $i -lt 30; $i++) {
            Start-Sleep 2
            try { $info = Invoke-RestMethod "http://localhost:$port/api/info" -Headers $auth -TimeoutSec 3 } catch { continue }
            if (@($info.cameras | Where-Object { -not $_.online }).Count -eq 0) { break }
        }
        $info
    }
    Write-Host ""
    if (-not $result) {
        Write-Host "The new version did not start. Rolling back to the previous code..." -ForegroundColor Red
        Invoke-Command -Session $session -ArgumentList $RemotePath -ScriptBlock {
            param($p)
            Stop-ScheduledTask -TaskName DeskTracker -ErrorAction SilentlyContinue
            Get-CimInstance Win32_Process |
                Where-Object { $_.CommandLine -and $_.CommandLine -like "*$p*" -and $_.Name -in "python.exe", "cmd.exe" } |
                ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
            Start-Sleep 2
            Remove-Item -Recurse -Force "$p\app"; Rename-Item "$p\app.previous" "app"
            Start-ScheduledTask -TaskName DeskTracker
            Get-Content "$p\data\server.log" -Tail 25
        } | ForEach-Object { Write-Host "  $_" }
    } else {
        foreach ($c in $result.cameras) {
            if ($c.online) { Write-Host ("  OK      {0}  ({1} desks)" -f $c.name, $c.desks) -ForegroundColor Green }
            else { Write-Host ("  FAILED  {0}: {1}" -f $c.name, $c.error) -ForegroundColor Red }
        }
        Write-Host "Measuring CPU use of Desk Tracker for 20 s..."
        $cpu = Invoke-Command -Session $session -ArgumentList $RemotePath -ScriptBlock {
            param($p)
            Start-Sleep 15
            # the venv python.exe is only a launcher; the real interpreter is a child process, so match by
            # command line (uvicorn app.main) and add up all of them
            $procs = { Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
                       Where-Object { $_.CommandLine -like "*app.main*" } }
            $cpuSec = { (& $procs | ForEach-Object { $_.KernelModeTime + $_.UserModeTime } | Measure-Object -Sum).Sum / 1e7 }
            $a = & $cpuSec
            Start-Sleep 20
            $b = & $cpuSec
            $mem = (& $procs | Measure-Object WorkingSetSize -Sum).Sum / 1MB
            [pscustomobject]@{ pct = [math]::Round(100 * ($b - $a) / 20 / [Environment]::ProcessorCount, 1)
                               cores = [Environment]::ProcessorCount; mem = [math]::Round($mem) }
        }
        Write-Host ("  CPU: {0}% of the server ({1} cores), memory: {2} MB" -f $cpu.pct, $cpu.cores, $cpu.mem)
        Write-Host "Updated." -ForegroundColor Green
    }
}
finally {
    Remove-PSSession $session
}
