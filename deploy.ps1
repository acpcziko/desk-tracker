<#
  Deploys Desk Tracker to the Windows server over WinRM and starts it with Docker.
  Passwords are typed by you at the prompts; they are not stored on this PC.

  Usage (from the desk-tracker folder):
    powershell -ExecutionPolicy Bypass -File .\deploy.ps1
#>
param(
    [Parameter(Mandatory)] [string]$Server,
    [Parameter(Mandatory)] [string]$User,
    [string]$RemotePath = "C:\desk-tracker",
    [int]$Port = 8080
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

function Plain([securestring]$s) {
    [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s))
}

Write-Host "== Desk Tracker deploy to $Server (port $Port) ==" -ForegroundColor Cyan
$cred = Get-Credential -UserName $User -Message "Password for $User on $Server"

Write-Host "Connecting over WinRM..."
$session = New-PSSession -ComputerName $Server -Credential $cred -Authentication Negotiate

try {
    # --- 1. Check Docker on the server ---
    $docker = Invoke-Command -Session $session -ScriptBlock {
        $exe = (Get-Command docker -ErrorAction SilentlyContinue).Source
        if (-not $exe) { $exe = "C:\Program Files\Docker\Docker\resources\bin\docker.exe" }
        if (-not (Test-Path $exe)) { return @{ ok = $false; msg = "docker.exe not found on the server" } }
        $os = & $exe info --format "{{.OSType}}" 2>&1
        if ($LASTEXITCODE -ne 0) { return @{ ok = $false; msg = "Docker engine not reachable: $os" } }
        & $exe compose version *> $null
        @{ ok = $true; exe = $exe; os = "$os".Trim(); compose = ($LASTEXITCODE -eq 0) }
    }
    if (-not $docker.ok) { throw $docker.msg }
    Write-Host "Docker found, engine OS type: $($docker.os)"
    if ($docker.os -ne "linux") {
        throw "Docker is in Windows-containers mode. Switch Docker to Linux containers on the server, then run this again."
    }

    # --- 2. Copy the app ---
    $hasConfig = Invoke-Command -Session $session -ArgumentList $RemotePath -ScriptBlock { param($p) Test-Path "$p\config.yaml" }
    if (-not $hasConfig -and -not (Test-Path (Join-Path $here "config.yaml") -PathType Leaf)) {
        throw "Copy config.example.yaml to config.yaml and configure it before installing."
    }
    Write-Host "Copying files to $RemotePath ..."
    Invoke-Command -Session $session -ArgumentList $RemotePath -ScriptBlock {
        param($p)
        New-Item -ItemType Directory -Force -Path $p, "$p\data" | Out-Null
        # replace code, keep data\ (history, desk zones) and .env
        foreach ($d in "app", "tools") { if (Test-Path "$p\$d") { Remove-Item -Recurse -Force "$p\$d" } }
    }
    foreach ($item in "app", "tools", "Dockerfile", "docker-compose.yml", "requirements.txt", ".dockerignore", ".env.example", "README.md") {
        Copy-Item -Path (Join-Path $here $item) -Destination $RemotePath -ToSession $session -Recurse -Force
    }
    if (-not $hasConfig) {
        Copy-Item -Path (Join-Path $here "config.yaml") -Destination $RemotePath -ToSession $session -Force
    } else {
        Write-Host "Keeping the existing config.yaml on the server."
    }

    # --- 3. Secrets (.env), only asked the first time ---
    $hasEnv = Invoke-Command -Session $session -ArgumentList $RemotePath -ScriptBlock { param($p) Test-Path "$p\.env" }
    if ($hasEnv) {
        Write-Host "Keeping the existing .env on the server (delete it there to re-enter passwords)."
        Invoke-Command -Session $session -ArgumentList $RemotePath, $Port -ScriptBlock {
            param($p, $port)
            $lines = Get-Content "$p\.env" | Where-Object { $_ -notmatch '^DESK_TRACKER_PORT=' }
            Set-Content "$p\.env" -Value ($lines + "DESK_TRACKER_PORT=$port") -Encoding ASCII
        }
    } else {
        Write-Host ""
        Write-Host "NVR login the app uses to read the cameras (a view-only NVR user is recommended)." -ForegroundColor Yellow
        $nvrUser = Read-Host "NVR username"
        $nvrPass = Plain (Read-Host "NVR password" -AsSecureString)
        Write-Host "Login for the dashboard web page." -ForegroundColor Yellow
        $dashUser = Read-Host "Dashboard username [admin]"
        if (-not $dashUser) { $dashUser = "admin" }
        $dashPass = Plain (Read-Host "Dashboard password" -AsSecureString)
        if ([string]::IsNullOrWhiteSpace($dashPass)) { throw "A dashboard password is required." }
        $envText = "NVR_USER=$nvrUser`nNVR_PASSWORD=$nvrPass`nDASHBOARD_USER=$dashUser`nDASHBOARD_PASSWORD=$dashPass`nDESK_TRACKER_PORT=$Port`n"
        Invoke-Command -Session $session -ArgumentList $RemotePath, $envText -ScriptBlock {
            param($p, $t)
            [IO.File]::WriteAllText("$p\.env", $t)
            # only Administrators and SYSTEM may read the secrets file
            icacls "$p\.env" /inheritance:r /grant:r "Administrators:F" "SYSTEM:F" | Out-Null
        }
        $nvrPass = $null; $dashPass = $null; $envText = $null
    }

    # --- 4. Firewall (asks first) ---
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

    # --- 5. Build and start ---
    Write-Host ""
    Write-Host "Building and starting the container. The first build downloads the AI model and can take 5-15 minutes..." -ForegroundColor Cyan
    $result = Invoke-Command -Session $session -ArgumentList $RemotePath, $docker.exe, $docker.compose -ScriptBlock {
        param($p, $exe, $hasCompose)
        Set-Location $p
        if ($hasCompose) { cmd /c "`"$exe`" compose up -d --build 2>&1" }
        else { cmd /c "docker-compose up -d --build 2>&1" }
        "EXIT=$LASTEXITCODE"
    }
    $result | ForEach-Object { Write-Host $_ }
    if (($result | Select-Object -Last 1) -ne "EXIT=0") { throw "docker compose failed (see output above)" }

    # --- 6. Check it answers ---
    Write-Host "Waiting for the dashboard to come up..."
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
        Write-Host "Container started but the dashboard is not answering yet. Logs:" -ForegroundColor Yellow
        Invoke-Command -Session $session -ArgumentList $docker.exe -ScriptBlock { param($exe) cmd /c "`"$exe`" logs --tail 40 desk-tracker 2>&1" }
    }
}
finally {
    Remove-PSSession $session
}
