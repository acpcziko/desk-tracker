<#
  Runs camera connection checks on the server. Prints no passwords.
  Usage:  powershell -ExecutionPolicy Bypass -File .\diagnose.ps1
#>
param(
    [Parameter(Mandatory)] [string]$Server,
    [Parameter(Mandatory)] [string]$User,
    [string]$RemotePath = "C:\desk-tracker"
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

$cred = Get-Credential -UserName $User -Message "Password for $User on $Server"
$session = New-PSSession -ComputerName $Server -Credential $cred -Authentication Negotiate
try {
    Copy-Item (Join-Path $here "tools\rtsp_check.py") -Destination "$RemotePath\tools" -ToSession $session -Force
    Invoke-Command -Session $session -ArgumentList $RemotePath -ScriptBlock {
        param($p)
        Write-Host "== Recorder connectivity and camera authentication"
        Set-Location $p
        cmd /c "venv\Scripts\python.exe tools\rtsp_check.py 2>&1"
        Write-Host ""
        Write-Host "== Last log lines"
        Get-Content "$p\data\server.log" -Tail 12 | ForEach-Object { "  $_" }
    }
}
finally {
    Remove-PSSession $session
}
