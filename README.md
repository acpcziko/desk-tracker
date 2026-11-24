# Desk Tracker

Desk Tracker measures desk occupancy from RTSP cameras. It runs YOLO11n on the CPU and stores occupancy samples in SQLite. The dashboard shows live snapshots, daily and weekly reports, and a CSV export. Camera frames are processed in memory.

## Configure a deployment

1. Copy `config.example.yaml` to `config.yaml`. Enter your recorder hostname, RTSP path, camera channels, and local work schedule. Give each camera a stable ID so its desk assignments survive configuration changes.
2. Copy `.env.example` to `.env` for a local or Docker installation. Enter credentials for a read-only camera account and set a unique dashboard password. The Windows deployment scripts prompt for credentials on first installation.
3. Keep `config.yaml`, `.env`, and `data/` private. The data directory contains desk assignments, employee names, and occupancy history. Access the dashboard over HTTPS through a reverse proxy when connecting over a network. Limit direct access to the application port.

### Docker

With Docker configured for Linux containers, run the following command.

```sh
docker compose up --build -d
```

Open `http://localhost:8080` on the host. Set `DESK_TRACKER_PORT` in `.env` to choose a different port. The first build downloads the model and dependencies.

### Windows

The native installation requires Python 3.12 for Windows, its dependency wheels, and the exported ONNX model. From a build environment with Python, Ultralytics, and ONNX installed, prepare the bundle.

```sh
python tools/export_model.py models/yolo11n.onnx
pip download -r requirements.txt --only-binary=:all: --platform win_amd64 --python-version 3.12 --implementation cp -d bundle/wheels
curl -L -o bundle/python-3.12.10-amd64.exe https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe
```

From a Windows machine with WinRM access to the target, run the installer.

```powershell
.\deploy-windows.ps1 -Server "server.example.invalid" -User "deploy-user"
```

Replace the example values with the intended host and account. The installer prompts before opening the firewall and registers a startup task. Use a dedicated application host. Pass `-Port` if you use a custom dashboard port.

| Script | Purpose |
| --- | --- |
| `deploy-windows.ps1` | Install the native application and startup task. |
| `deploy.ps1` | Deploy to a remote Docker host running Linux containers. |
| `update-app.ps1` | Update the native application and check startup. Pass `-Stream 0` or `-Stream 1` to change the configured stream. |
| `update-login.ps1` | Change the credentials used by the native application. |
| `diagnose.ps1` | Check connectivity and camera authentication using the deployed configuration. |
| `measure-cpu.ps1` | Report CPU and memory usage. |

## Use the dashboard

In **Desk setup**, draw a polygon around each seating area, assign a name, and save. The **Live** view overlays these areas and detections on the current frame. **Daily** shows first and last detection, breaks, and seated time during the configured work hours. **Weekly** summarizes daily totals and exports CSV.

Short absences are grouped using `away_grace_minutes`. Missing camera samples reduce the reported coverage. Per-desk auto-tuning adjusts detection thresholds after enough workdays have been recorded. Configure the schedule, thresholds, tuning options, and retention period in `config.yaml`. Back up the private `data/` directory to preserve desk assignments and history.
