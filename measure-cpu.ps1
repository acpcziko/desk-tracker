<#
  Measures Desk Tracker's CPU and memory use on the server (and total server CPU for comparison).
  Usage:  powershell -ExecutionPolicy Bypass -File .\measure-cpu.ps1
#>
param(
    [Parameter(Mandatory)] [string]$Server,
    [Parameter(Mandatory)] [string]$User,
    [int]$Seconds = 30
)
$ErrorActionPreference = "Stop"

$cred = Get-Credential -UserName $User -Message "Password for $User on $Server"
Write-Host "Measuring for $Seconds s..."
$r = Invoke-Command -ComputerName $Server -Credential $cred -Authentication Negotiate -ArgumentList $Seconds -ScriptBlock {
    param($sec)
    $procs = { Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*app.main*" } }
    $cpuSec = { (& $procs | ForEach-Object { $_.KernelModeTime + $_.UserModeTime } | Measure-Object -Sum).Sum / 1e7 }
    $a = & $cpuSec
    $total = (Get-Counter '\Processor(_Total)\% Processor Time' -SampleInterval $sec -MaxSamples 1).CounterSamples[0].CookedValue
    $b = & $cpuSec
    $p = & $procs
    [pscustomobject]@{
        app   = [math]::Round(100 * ($b - $a) / $sec / [Environment]::ProcessorCount, 1)
        total = [math]::Round($total, 1)
        cores = [Environment]::ProcessorCount
        mem   = [math]::Round(($p | Measure-Object WorkingSetSize -Sum).Sum / 1MB)
        prio  = ($p | ForEach-Object { (Get-Process -Id $_.ProcessId).PriorityClass } | Select-Object -Unique) -join ","
    }
}
Write-Host ("  Desk Tracker: {0}% of the server CPU ({1} cores), {2} MB memory, priority {3}" -f $r.app, $r.cores, $r.mem, $r.prio)
Write-Host ("  Whole server: {0}% CPU" -f $r.total)
