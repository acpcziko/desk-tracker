"""Per-desk automatic tuning of the detection thresholds (confidence, min_overlap).

Self-training, no labelled data needed:
  1. A desk's recent raw samples are turned into seated sessions with the usual rules (short absences bridged,
     short blips dropped). Those sessions are the best estimate of when someone was really sitting there.
  2. Every candidate (confidence, min_overlap) pair is scored by how often its raw per-sample decision disagrees
     with that estimate: missed samples inside sessions (flicker) plus hits outside them (false alarms).
     Seated time that a candidate adds or removes compared with the estimate weighs 3x, so thresholds that
     let a neighbour leaning in create phantom sessions are rejected.
  3. The pair with the lowest cost wins (ties go to the pair closest to the defaults).
The estimate is always built from the default thresholds, so tuning cannot feed on its own earlier output.
"""
import json
import logging
import threading
import time
from collections import defaultdict
from datetime import datetime

import numpy as np

log = logging.getLogger("tuning")

CONF_GRID = np.round(np.arange(0.20, 0.501, 0.05), 2)
OVERLAP_GRID = np.round(np.arange(0.10, 0.451, 0.05), 2)
DETECTOR_FLOOR = float(CONF_GRID.min())  # detector must report boxes down to this score


def sessions_mask(ts: np.ndarray, occ: np.ndarray, interval: float, grace: float, min_session: float) -> np.ndarray:
    """Boolean per sample: inside a smoothed seated session (same rules as the reports)."""
    mask = np.zeros(len(ts), bool)
    t = ts[occ]
    if not len(t):
        return mask
    brk = np.diff(t) - interval > grace          # absence longer than the grace period ends a session
    starts = np.r_[t[0], t[1:][brk]]
    ends = np.r_[t[:-1][brk], t[-1]] + interval
    keep = ends - starts >= min_session          # blips shorter than a minimum session are dropped
    delta = np.zeros(len(ts) + 1, int)
    np.add.at(delta, np.searchsorted(ts, starts[keep]), 1)
    np.add.at(delta, np.searchsorted(ts, ends[keep]), -1)
    return np.cumsum(delta)[:-1] > 0


SESSION_WEIGHT = 3  # seated time added or lost counts 3x a single wrong sample


def best_thresholds(ts, score, overlap, ref, default, interval, grace, min_session) -> tuple[float, float, float]:
    """Grid search; returns (confidence, min_overlap, raw agreement 0..1).

    cost = samples whose raw decision disagrees with the reference sessions
         + SESSION_WEIGHT x samples where the resulting seated sessions differ from the reference
    The first term removes flicker; the second stops thresholds that create phantom seated time
    (e.g. a neighbour leaning in) or lose real seated time.
    """
    n = len(ref)
    best = None
    for c in CONF_GRID:
        hit_c = score >= c - 1e-9
        for o in OVERLAP_GRID:
            occ = hit_c & (overlap >= o - 1e-9)
            wrong = np.count_nonzero(occ != ref)
            sess_wrong = np.count_nonzero(sessions_mask(ts, occ, interval, grace, min_session) != ref)
            # tiny pull towards the defaults so equal results don't drift
            cost = wrong + SESSION_WEIGHT * sess_wrong + 0.002 * n * (
                abs(c - default["confidence"]) / 0.3 + abs(o - default["min_overlap"]) / 0.35)
            if best is None or cost < best[0]:
                best = (cost, float(c), float(o), wrong)
    _, c, o, wrong = best
    return c, o, 1 - wrong / n


class Tuning:
    def __init__(self, path, cfg: dict):
        det = cfg["detection"]
        self.cfg = cfg
        self.default = {"confidence": det["confidence"], "min_overlap": det["min_overlap"]}
        self._path = path
        self._lock = threading.Lock()
        self._data: dict = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        self.last_run: float | None = self._data.pop("_last_run", None)

    # --- used on every detection ---
    def params(self, desk_id: str) -> dict:
        e = self._data.get(desk_id)
        if e and e.get("mode", "auto") == "auto" and "confidence" in e:
            return {"confidence": e["confidence"], "min_overlap": e["min_overlap"]}
        return self.default

    # --- dashboard ---
    def status(self) -> dict:
        with self._lock:
            return {"default": self.default, "last_run": self.last_run,
                    "min_days": self.cfg["detection"]["auto_tune_min_days"],
                    "desks": json.loads(json.dumps(self._data))}

    def set_mode(self, desk_id: str, mode: str) -> None:
        if mode not in ("auto", "default"):
            raise ValueError("mode must be 'auto' or 'default'")
        with self._lock:
            self._data.setdefault(desk_id, {})["mode"] = mode
            self._save()

    def _save(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps({**self._data, "_last_run": self.last_run}, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    # --- nightly run ---
    def run(self, db, desk_ids: list[str], schedule) -> dict:
        det = self.cfg["detection"]
        interval = det["interval_seconds"]
        grace = det["away_grace_minutes"] * 60
        min_session = det["min_session_minutes"] * 60
        now = time.time()
        rows = db.detail(int(now - det["auto_tune_window_days"] * 86400), int(now))

        by_desk: dict[str, list] = defaultdict(list)
        for ts, desk, score, overlap in rows:
            by_desk[desk].append((ts, score, overlap))

        # a "full day" for a desk = at least half of the working hours were sampled
        work_start, work_end = schedule.work_window(schedule.now().date())
        full_day = 0.5 * (work_end - work_start).total_seconds() / interval

        summary = {"tuned": 0, "learning": 0}
        with self._lock:
            for desk_id in desk_ids:
                e = self._data.setdefault(desk_id, {"mode": "auto"})
                data = by_desk.get(desk_id, [])
                per_day: dict = defaultdict(int)
                for ts, _, _ in data:
                    per_day[datetime.fromtimestamp(ts, schedule.tz).date()] += 1
                days = sum(1 for n in per_day.values() if n >= full_day)
                e["data_days"] = days
                if days < det["auto_tune_min_days"]:
                    e["status"] = "learning"
                    summary["learning"] += 1
                    continue

                arr = np.array(data, dtype=float)
                ts, score, overlap = arr[:, 0], arr[:, 1], arr[:, 2]
                # the reference always comes from the default thresholds (smoothed), never from earlier tuned
                # values, so a desk cannot drift by re-learning from its own mistakes night after night
                c, o = self.default["confidence"], self.default["min_overlap"]
                occ = (score >= c - 1e-9) & (overlap >= o - 1e-9)
                ref = sessions_mask(ts, occ, interval, grace, min_session)
                if ref.mean() >= 0.02:
                    c, o, agreement = best_thresholds(ts, score, overlap, ref, self.default, interval, grace, min_session)
                if ref.mean() < 0.02:
                    e["status"] = "unused"  # nobody sat there enough to learn from
                    summary["learning"] += 1
                    continue
                e.update({"confidence": c, "min_overlap": o, "agreement": round(agreement, 3),
                          "status": "tuned", "updated": now})
                summary["tuned"] += 1
                log.info("tuned %s: confidence=%.2f min_overlap=%.2f agreement=%.1f%% (%d days)",
                         desk_id, c, o, 100 * agreement, days)
            self.last_run = now
            self._save()
        return summary
