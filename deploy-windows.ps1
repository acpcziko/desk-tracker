<#
  Deploys Desk Tracker to the Windows server over WinRM as a native background task (no Docker).
  - Installs Python 3.12 on the server if missing (from bundle\, no internet needed on the server)
  - Creates C:\desk-tracker\venv with the bundled packages
  - Runs the app at startup as a scheduled task "DeskTracker" on the given port
  Passwords are typed by you at the prompts; they are not stored on this PC.

  Usage (from the desk-tracker folder):
    powershell -ExecutionPolicy Bypass -File .\deploy-windows.ps1
#>
param(
    [Parameter(Mandatory)] [string]$Server,
    [Parameter(Mandatory)] [string]$User,
    [string]$RemotePath = "C:\desk-tracker",
    [int]$Port = 8080
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$pyInstaller = "python-3.12.10-amd64.exe"

function Plain([securestring]$s) {
    [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s))
}
function Step($t) { Write-Host ""; Write-Host "== $t" -ForegroundColor Cyan }

foreach ($f in "models\yolo11n.onnx", "bundle\$pyInstaller", "bundle\wheels") {
    if (-not (Test-Path (Join-Path $here $f))) { throw "Missing $f next to this script." }
}

Step "Desk Tracker deploy to $Server (port $Port)"
$cred = Get-Credential -UserName $User -Message "Password for $User on $Server"
$session = New-PSSession -ComputerName $Server -Credential $cred -Authentication Negotiate
Write-Host "Connected."

try {
    $hasConfig = Invoke-Command -Session $session -ArgumentList $RemotePath -ScriptBlock { param($p) Test-Path "$p\config.yaml" }
    if (-not $hasConfig -and -not (Test-Path (Join-Path $here "config.yaml") -PathType Leaf)) {
        throw "Copy config.example.yaml to config.yaml and configure it before installing."
    }
    # --- 1. Stop a running copy (redeploy) ---
    Step "Stopping any running Desk Tracker"
    Invoke-Command -Session $session -ArgumentList $RemotePath -ScriptBlock {
        param($p)
        if (Get-ScheduledTask -TaskName DeskTracker -ErrorAction SilentlyContinue) { Stop-ScheduledTask -TaskName DeskTracker }
        Get-CimInstance Win32_Process |
            Where-Object { $_.CommandLine -and $_.CommandLine -like "*$p*" -and $_.Name -in "python.exe", "cmd.exe" } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        New-Item -ItemType Directory -Force -Path $p, "$p\data", "$p\models", "$p\bundle" | Out-Null
        foreach ($d in "app", "tools") { if (Test-Path "$p\$d") { Remove-Item -Recurse -Force "$p\$d" } }
    }

    # --- 2. Copy files ---
    Step "Copying app files (about 100 MB, may take a few minutes)"
    foreach ($item in "app", "tools", "requirements.txt", "run.cmd", "README.md", ".env.example") {
        Copy-Item -Path (Join-Path $here $item) -Destination $RemotePath -ToSession $session -Recurse -Force
    }
    $modelLocal = Get-Item (Join-Path $here "models\yolo11n.onnx")
    $modelRemoteSize = Invoke-Command -Session $session -ArgumentList $RemotePath -ScriptBlock {
        param($p) $f = Get-Item "$p\models\yolo11n.onnx" -ErrorAction SilentlyContinue; if ($f) { $f.Length } else { 0 }
    }
    if ($modelRemoteSize -ne $modelLocal.Length) {
        Copy-Item -Path $modelLocal.FullName -Destination "$RemotePath\models" -ToSession $session -Force
    } else { Write-Host "AI model already on the server." }
    $remoteHas = Invoke-Command -Session $session -ArgumentList $RemotePath, $pyInstaller -ScriptBlock {
        param($p, $inst)
        $py = $null
        if (Test-Path "C:\Program Files\Python312\python.exe") { $py = "C:\Program Files\Python312\python.exe" }
        @{ config = (Test-Path "$p\config.yaml"); env = (Test-Path "$p\.env"); python = $py;
           wheels = ((Get-ChildItem "$p\bundle\wheels" -Filter *.whl -ErrorAction SilentlyContinue).Count -ge 25) }
    }
    if (-not $remoteHas.config) {
        Copy-Item -Path (Join-Path $here "config.yaml") -Destination $RemotePath -ToSession $session -Force
    } else { Write-Host "Keeping the existing config.yaml on the server." }
    if (-not $remoteHas.wheels) {
        Invoke-Command -Session $session -ArgumentList $RemotePath -ScriptBlock {
            param($p) New-Item -ItemType Directory -Force -Path "$p\bundle\wheels" | Out-Null
        }
        Get-ChildItem (Join-Path $here "bundle\wheels") -File | ForEach-Object {
            Copy-Item -Path $_.FullName -Destination "$RemotePath\bundle\wheels" -ToSession $session -Force
        }
    }

    # --- 3. Python + packages ---
    Step "Python and packages"
    if (-not $remoteHas.python) {
        Write-Host "Installing Python 3.12 on the server..."
        Copy-Item -Path (Join-Path $here "bundle\$pyInstaller") -Destination "$RemotePath\bundle" -ToSession $session -Force
        Invoke-Command -Session $session -ArgumentList $RemotePath, $pyInstaller -ScriptBlock {
            param($p, $inst)
            $proc = Start-Process "$p\bundle\$inst" -ArgumentList "/quiet", "InstallAllUsers=1", "PrependPath=0",
                "Include_test=0", "Include_launcher=0", "Shortcuts=0" -Wait -PassThru
            if ($proc.ExitCode -ne 0) { throw "Python installer failed with code $($proc.ExitCode)" }
        }
    } else { Write-Host "Python 3.12 already installed." }
    $pip = Invoke-Command -Session $session -ArgumentList $RemotePath -ScriptBlock {
        param($p)
        $py = "C:\Program Files\Python312\python.exe"
        if (-not (Test-Path "$p\venv\Scripts\python.exe")) { & $py -m venv "$p\venv" 2>&1 }
        cmd /c "`"$p\venv\Scripts\python.exe`" -m pip install -q --no-index --find-links `"$p\bundle\wheels`" -r `"$p\requirements.txt`" 2>&1"
        "EXIT=$LASTEXITCODE"
    }
    $pip | ForEach-Object { Write-Host $_ }
    if (($pip | Select-Object -Last 1) -ne "EXIT=0") { throw "Package install failed (see above)" }

    # --- 4. Secrets (.env), only asked the first time ---
    Step "Settings"
    if ($remoteHas.env) {
        Write-Host "Keeping the existing .env on the server (delete it there to re-enter passwords)."
    } else {
        Write-Host "NVR login the app uses to read the cameras (a view-only NVR user is recommended)." -ForegroundColor Yellow
        $nvrUser = Read-Host "NVR username"
        $nvrPass = Plain (Read-Host "NVR password" -AsSecureString)
        Write-Host "Login for the dashboard web page." -ForegroundColor Yellow
        $dashUser = Read-Host "Dashboard username [admin]"
        if (-not $dashUser) { $dashUser = "admin" }
        $dashPass = Plain (Read-Host "Dashboard password" -AsSecureString)
        if ([string]::IsNullOrWhiteSpace($dashPass)) { throw "A dashboard password is required." }
        $envText = "NVR_USER=$nvrUser`r`nNVR_PASSWORD=$nvrPass`r`nDASHBOARD_USER=$dashUser`r`nDASHBOARD_PASSWORD=$dashPass`r`n"
        Invoke-Command -Session $session -ArgumentList $RemotePath, $envText -ScriptBlock {
            param($p, $t)
            [IO.File]::WriteAllText("$p\.env", $t)
            icacls "$p\.env" /inheritance:r /grant:r "Administrators:F" "SYSTEM:F" | Out-Null
        }
        $nvrPass = $null; $dashPass = $null; $envText = $null
    }

    # --- 5. Firewall (asks first) ---
    $ruleName = "Desk Tracker $Port"
    $hasRule = Invoke-Command -Session $session -ArgumentList $ruleName -ScriptBlock {
        param($n) [bool](Get-NetFirewallRule -DisplayName $n -ErrorAction SilentlyContinue)
    }
    if (-not $hasRule) {
        $ans = Read-Host "Open TCP port $Port in the server's Windows Firewall so office PCs can reach the dashboard? (y/n)"
        if ($ans -match '^[yY]') {
            Invoke-Command -Session $session -ArgumentList $ruleName, $Port -ScriptBlock {
                param($n, $port)
                New-NetFirewallRule -DisplayName $n -Direction Inbound -Protocol TCP -LocalPort $port -Action Allow -Profile Domain, Private | Out-Null
            }
            Write-Host "Firewall rule '$ruleName' added (Domain/Private networks)."
        }
    }

    # --- 6. Scheduled task: start at boot, run as SYSTEM, keep running ---
    Step "Registering startup task 'DeskTracker' and starting it"
    Invoke-Command -Session $session -ArgumentList $RemotePath, $Port -ScriptBlock {
        param($p, $port)
        $action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c set DESK_TRACKER_PORT=$port&& `"$p\run.cmd`"" -WorkingDirectory $p
        $trigger = New-ScheduledTaskTrigger -AtStartup
        $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
        $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 `
            -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
        Register-ScheduledTask -TaskName DeskTracker -Action $action -Trigger $trigger -Principal $principal `
            -Settings $settings -Description "Desk Tracker (CCTV desk occupancy)" -Force | Out-Null
        Start-ScheduledTask -TaskName DeskTracker
    }

    # --- 7. Check it answers ---
    Step "Checking the dashboard"
    $ok = $false
    for ($i = 0; $i -lt 30 -and -not $ok; $i++) {
        Start-Sleep 2
        $code = Invoke-Command -Session $session -ArgumentList $Port -ScriptBlock {
            param($port)
            try { (Invoke-WebRequest "http://localhost:$port/api/info" -UseBasicParsing -TimeoutSec 3).StatusCode }
            catch { if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { 0 } }
        }
        $ok = $code -eq 401
    }
    if ($ok) {
        Write-Host ""
        Write-Host "Deployed. Open http://${Server}:$Port and log in with the dashboard user." -ForegroundColor Green
        Write-Host "Then go to 'Desk setup' and draw the desks for each room."
    } else {
        Write-Host "The task started but the dashboard is not answering yet. Last log lines:" -ForegroundColor Yellow
    }
    Invoke-Command -Session $session -ArgumentList $RemotePath -ScriptBlock {
        param($p) if (Test-Path "$p\data\server.log") { Get-Content "$p\data\server.log" -Tail 15 }
    } | ForEach-Object { Write-Host "  $_" }
}
finally {
    Remove-PSSession $session
}
