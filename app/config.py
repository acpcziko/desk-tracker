"""Configuration loading: config.yaml for settings, environment (.env) for secrets."""
import os
from pathlib import Path
from urllib.parse import quote

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env reader for running without Docker. Never overrides real env vars."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")

CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", BASE_DIR / "config.yaml"))
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
MODEL_PATH = Path(os.environ.get("MODEL_PATH", BASE_DIR / "models" / "yolo11n.onnx"))

DEFAULTS = {
    "nvr": {"host": "", "rtsp_port": 554, "rtsp_path": "/unicast/c{channel}/s{stream}/live", "stream": 1},
    "cameras": [],
    "schedule": {
        "timezone": "UTC",
        "workdays": ["mon", "tue", "wed", "thu", "fri"],
        "start": "09:00",
        "end": "17:00",
        "track_margin_minutes": 60,
        "track_always": False,
    },
    "detection": {
        "interval_seconds": 5,
        "confidence": 0.35,
        "min_overlap": 0.25,
        "away_grace_minutes": 3,
        "min_session_minutes": 1,
        "cpu_threads": 2,
        "tile_size": 960,             # high-res frames are split into tiles of about this many pixels
        "max_tiles": 6,               # per camera per check
        "auto_tune": True,            # nightly per-desk tuning of confidence/min_overlap
        "auto_tune_min_days": 2,      # full workdays of data a desk needs before it is tuned
        "auto_tune_window_days": 7,   # history used for each tuning run
        "auto_tune_hour": 2,          # local hour the nightly run happens
    },
    "retention_days": 90,
    "low_priority": True,             # run below normal CPU priority (Windows)
}


def load() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        user = yaml.safe_load(f) or {}
    cfg = {}
    for key, default in DEFAULTS.items():
        if isinstance(default, dict):
            cfg[key] = {**default, **(user.get(key) or {})}
        else:
            cfg[key] = user.get(key, default)
    return cfg


def camera_url(cfg: dict, cam: dict) -> str:
    """Build the RTSP URL. `url` on a camera overrides everything (useful for testing with a file)."""
    if cam.get("url"):
        return cam["url"]
    nvr = cfg["nvr"]
    user = quote(os.environ.get("NVR_USER", ""), safe="")
    password = quote(os.environ.get("NVR_PASSWORD", ""), safe="")
    path = nvr["rtsp_path"].format(channel=cam["channel"], stream=cam.get("stream", nvr["stream"]))
    return f"rtsp://{user}:{password}@{nvr['host']}:{nvr['rtsp_port']}{path}"
