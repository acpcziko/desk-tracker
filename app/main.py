"""Desk Tracker: reads NVR cameras, detects people at desks, serves the dashboard."""
import logging
import os
import secrets
import threading
import time
from contextlib import asynccontextmanager
from datetime import date, timedelta

import cv2
from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, reports
from .camera import Camera
from .db import DB
from .detector import PersonDetector
from .tuning import DETECTOR_FLOOR, Tuning
from .zones import Zones

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("tracker")

if os.name == "nt" and config.load().get("low_priority", True):
    # Leave CPU time available for other processes while decoding video.
    import ctypes
    BELOW_NORMAL_PRIORITY_CLASS = 0x4000
    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)

STATIC = config.BASE_DIR / "app" / "static"


class Tracker:
    def __init__(self):
        self.cfg = config.load()
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        det = self.cfg["detection"]
        self.schedule = reports.Schedule(self.cfg)
        self.db = DB(config.DATA_DIR / "desk_tracker.db")
        self.zones = Zones(config.DATA_DIR / "zones.json")
        self.tuning = Tuning(config.DATA_DIR / "tuning.json", self.cfg)
        # detector reports weak boxes too; each desk applies its own (possibly tuned) confidence
        self.detector = PersonDetector(config.MODEL_PATH, min(det["confidence"], DETECTOR_FLOOR), det["cpu_threads"])
        self.cameras = {
            c["id"]: Camera(c["id"], c["name"], config.camera_url(self.cfg, c), det["interval_seconds"])
            for c in self.cfg["cameras"]
        }
        self.live: dict[str, dict] = {}
        self._stop = threading.Event()
        self._last_purge = 0.0
        self._tune_lock = threading.Lock()

    def start(self):
        for cam in self.cameras.values():
            cam.start()
        threading.Thread(target=self._loop, name="tracker", daemon=True).start()

    def stop(self):
        self._stop.set()
        for cam in self.cameras.values():
            cam.stop()

    def analyse(self, cam_id: str, frame):
        h, w = frame.shape[:2]
        det = self.cfg["detection"]
        # only look where desks are, in tiles, so high-resolution detail isn't thrown away
        boxes = self.detector.detect_region(frame, self.zones.roi(cam_id, w, h), det["tile_size"], 0.2, det["max_tiles"])
        results = self.zones.occupancy(cam_id, w, h, boxes, self.tuning.params)
        occ = {desk: r[0] for desk, r in results.items()}
        self.live[cam_id] = {"ts": time.time(), "boxes": boxes, "occupied": occ}
        return boxes, occ, results

    def _loop(self):
        interval = self.cfg["detection"]["interval_seconds"]
        while not self._stop.is_set():
            started = time.time()
            if self.schedule.tracking_now():
                for cam_id, cam in self.cameras.items():
                    frame = cam.latest(max_age=interval * 3)
                    if frame is None:
                        continue  # camera offline: no sample, shows up as lower coverage
                    try:
                        _, _, results = self.analyse(cam_id, frame)
                        self.db.add_samples(int(started), cam_id, results)
                    except Exception:
                        log.exception("[%s] analysis failed", cam_id)
            if started - self._last_purge > 3600:
                cutoff = started - self.cfg["retention_days"] * 86400
                removed = self.db.purge_before(int(cutoff))
                if removed:
                    log.info("purged %d samples older than %d days", removed, self.cfg["retention_days"])
                self._last_purge = started
            self._maybe_tune()
            self._stop.wait(max(0.5, interval - (time.time() - started)))


    def desk_ids(self) -> list[str]:
        return [d["id"] for c in self.cfg["cameras"] for d in self.zones.desks(c["id"])]

    def tune_now(self) -> dict:
        with self._tune_lock:
            return self.tuning.run(self.db, self.desk_ids(), self.schedule)

    def _maybe_tune(self):
        det = self.cfg["detection"]
        if not det["auto_tune"]:
            return
        now = self.schedule.now()
        last = self.tuning.last_run
        if now.hour == det["auto_tune_hour"] and (last is None or time.time() - last > 12 * 3600):
            try:
                summary = self.tune_now()
                log.info("nightly auto-tune: %s", summary)
            except Exception:
                log.exception("auto-tune failed")


tracker: Tracker | None = None


@asynccontextmanager
async def lifespan(_app):
    global tracker
    if not os.environ.get("DASHBOARD_PASSWORD", "").strip():
        raise RuntimeError("Set DASHBOARD_PASSWORD before starting Desk Tracker.")
    tracker = Tracker()
    tracker.start()
    yield
    tracker.stop()


security = HTTPBasic(auto_error=False)


def require_login(creds: HTTPBasicCredentials | None = Depends(security)):
    password = os.environ.get("DASHBOARD_PASSWORD")
    user = os.environ.get("DASHBOARD_USER", "admin")
    if password and password.strip() and creds and secrets.compare_digest(creds.username.encode(), user.encode()) \
            and secrets.compare_digest(creds.password.encode(), password.encode()):
        return
    raise HTTPException(401, "Login required", headers={"WWW-Authenticate": 'Basic realm="Desk Tracker"'})


app = FastAPI(title="Desk Tracker", lifespan=lifespan, dependencies=[Depends(require_login)])
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _camera_list():
    return [{"id": c["id"], "name": c["name"]} for c in tracker.cfg["cameras"]]


def _parse_date(s: str | None) -> date:
    if not s:
        return tracker.schedule.now().date()
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/info")
def info():
    sch = tracker.cfg["schedule"]
    cams = []
    for c in _camera_list():
        cam = tracker.cameras[c["id"]]
        live = tracker.live.get(c["id"], {})
        occ = live.get("occupied", {})
        cams.append({
            **c, "online": cam.online, "error": cam.error,
            "frame_age": round(cam.frame_age, 1) if cam.frame_age is not None else None,
            "desks": len(tracker.zones.desks(c["id"])),
            "occupied_now": sum(occ.values()),
        })
    return {
        "cameras": cams,
        "schedule": {k: sch[k] for k in ("timezone", "workdays", "start", "end")},
        "tracking": tracker.schedule.tracking_now(),
        "today": tracker.schedule.now().date().isoformat(),
        "interval_seconds": tracker.cfg["detection"]["interval_seconds"],
    }


@app.get("/api/day")
def day(date: str | None = None):
    return reports.day_report(tracker.cfg, tracker.db, tracker.zones, _camera_list(), _parse_date(date))


@app.get("/api/range")
def range_(start: str, end: str):
    s, e = _parse_date(start), _parse_date(end)
    if e < s or (e - s) > timedelta(days=62):
        raise HTTPException(400, "range must be 1-62 days")
    return reports.range_report(tracker.cfg, tracker.db, tracker.zones, _camera_list(), s, e)


@app.get("/api/export.csv")
def export_csv(start: str, end: str):
    rep = range_(start, end)
    body = reports.to_csv(rep, tracker.schedule.tz)
    return Response("﻿" + body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="desk-time_{start}_{end}.csv"'})


@app.get("/api/snapshot/{cam_id}")
def snapshot(cam_id: str, overlay: int = 1):
    cam = tracker.cameras.get(cam_id)
    if not cam:
        raise HTTPException(404, "unknown camera")
    frame = cam.latest(max_age=60)
    if frame is None:
        raise HTTPException(503, cam.error or "no frame yet")
    boxes, occ = [], {}
    if overlay:
        boxes, occ, _ = tracker.analyse(cam_id, frame)
    # main-stream frames are large: send the browser at most 1600 px wide
    scale = min(1.0, 1600 / frame.shape[1])
    if scale < 1.0:
        frame = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        boxes = [(int(a * scale), int(b * scale), int(c * scale), int(d * scale), s) for a, b, c, d, s in boxes]
    if overlay:
        frame = tracker.zones.draw(cam_id, frame, boxes, occ, tracker.cfg["detection"]["confidence"])
    ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return Response(jpg.tobytes(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/zones")
def get_zones():
    return tracker.zones.all()


class DeskIn(BaseModel):
    id: str
    name: str = ""
    employee: str = ""
    points: list[list[float]]


class ZonesIn(BaseModel):
    desks: list[DeskIn]


@app.put("/api/zones/{cam_id}")
def put_zones(cam_id: str, body: ZonesIn):
    if cam_id not in tracker.cameras:
        raise HTTPException(404, "unknown camera")
    try:
        tracker.zones.set_camera(cam_id, [d.model_dump() for d in body.desks])
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.get("/api/tuning")
def get_tuning():
    st = tracker.tuning.status()
    st["enabled"] = tracker.cfg["detection"]["auto_tune"]
    # live count of full days collected, so "learning" progress is current before the nightly run
    det, sch = tracker.cfg["detection"], tracker.schedule
    ws, we = sch.work_window(sch.now().date())
    full_day = 0.5 * (we - ws).total_seconds() / det["interval_seconds"]
    offset = int(sch.now().utcoffset().total_seconds())
    since = int(time.time() - det["auto_tune_window_days"] * 86400)
    days: dict[str, int] = {}
    for desk, _, n in tracker.db.detail_counts(since, offset):
        if n >= full_day:
            days[desk] = days.get(desk, 0) + 1
    for desk_id in tracker.desk_ids():
        entry = st["desks"].setdefault(desk_id, {"mode": "auto", "status": "learning"})
        entry["data_days"] = days.get(desk_id, 0)
    return st


@app.post("/api/tuning/run")
def run_tuning():
    return tracker.tune_now()


class ModeIn(BaseModel):
    mode: str


@app.put("/api/tuning/{desk_id}")
def set_tuning_mode(desk_id: str, body: ModeIn):
    try:
        tracker.tuning.set_mode(desk_id, body.mode)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}
