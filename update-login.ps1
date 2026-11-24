<#
  Changes the NVR login (and optionally the dashboard password) used by Desk Tracker on the server,
  restarts it, and shows whether each camera connects. Passwords are typed by you at the prompts.

  Usage:  powershell -ExecutionPolicy Bypass -File .\update-login.ps1
#>
param(
    [Parameter(Mandatory)] [string]$Server,
    [Parameter(Mandatory)] [string]$User,
    [string]$RemotePath = "C:\desk-tracker",
    [int]$Port = 8080
)
$ErrorActionPreference = "Stop"

function Plain([securestring]$s) {
    [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s))
}

$cred = Get-Credential -UserName $User -Message "Password for $User on $Server"
$session = New-PSSession -ComputerName $Server -Credential $cred -Authentication Negotiate
Write-Host "Connected to $Server."

try {
    $set = @{}
    Write-Host ""
    Write-Host "New NVR login (the view-only account)." -ForegroundColor Yellow
    $set["NVR_USER"] = Read-Host "NVR username"
    $set["NVR_PASSWORD"] = Plain (Read-Host "NVR password" -AsSecureString)
    if ((Read-Host "Also set a new dashboard password? (y/n)") -match '^[yY]') {
        $set["DASHBOARD_PASSWORD"] = Plain (Read-Host "New dashboard password" -AsSecureString)
        if ([string]::IsNullOrWhiteSpace($set["DASHBOARD_PASSWORD"])) { throw "A dashboard password is required." }
    }

    Write-Host "Updating settings and restarting Desk Tracker..."
    Invoke-Command -Session $session -ArgumentList $RemotePath, $set -ScriptBlock {
        param($p, $set)
        $envFile = "$p\.env"
        $lines = @(Get-Content $envFile | Where-Object { $_ -notmatch "^($($set.Keys -join '|'))=" })
        foreach ($k in $set.Keys) { $lines += "$k=$($set[$k])" }
        [IO.File]::WriteAllText($envFile, ($lines -join "`r`n") + "`r`n")

        Stop-ScheduledTask -TaskName DeskTracker -ErrorAction SilentlyContinue
        Get-CimInstance Win32_Process |
            Where-Object { $_.CommandLine -and $_.CommandLine -like "*$p*" -and $_.Name -in "python.exe", "cmd.exe" } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Start-ScheduledTask -TaskName DeskTracker
    }
    $set = $null

    Write-Host "Waiting for the cameras to connect (up to 60 s)..."
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
            if (@($info.cameras | Where-Object { -not $_.online -and $_.error -eq "connecting" }).Count -eq 0) { break }
        }
        $info
    }
    Write-Host ""
    if (-not $result) {
        Write-Host "The app did not answer. Check C:\desk-tracker\data\server.log on the server." -ForegroundColor Red
    } else {
        foreach ($c in $result.cameras) {
            if ($c.online) { Write-Host ("  OK      {0}" -f $c.name) -ForegroundColor Green }
            else { Write-Host ("  FAILED  {0}: {1}" -f $c.name, $c.error) -ForegroundColor Red }
        }
    }
}
finally {
    Remove-PSSession $session
}
