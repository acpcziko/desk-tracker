"""Turn raw desk samples into seated sessions, breaks and daily totals."""
import csv
import io
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


class Schedule:
    def __init__(self, cfg: dict):
        sch = cfg["schedule"]
        self.tz = ZoneInfo(sch["timezone"])
        self.workdays = {d.lower()[:3] for d in sch["workdays"]}
        self.start = _hm(sch["start"])
        self.end = _hm(sch["end"])
        self.margin = timedelta(minutes=sch["track_margin_minutes"])
        self.track_always = bool(sch["track_always"])

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def is_workday(self, d: date) -> bool:
        return WEEKDAYS[d.weekday()] in self.workdays

    def work_window(self, d: date) -> tuple[datetime, datetime]:
        return datetime.combine(d, self.start, self.tz), datetime.combine(d, self.end, self.tz)

    def track_window(self, d: date) -> tuple[datetime, datetime]:
        ws, we = self.work_window(d)
        return ws - self.margin, we + self.margin

    def tracking_now(self) -> bool:
        if self.track_always:
            return True
        now = self.now()
        if not self.is_workday(now.date()):
            return False
        ts, te = self.track_window(now.date())
        return ts <= now < te


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def desk_day(samples: list[tuple[int, int]], work: tuple[float, float], now: float, det: dict) -> dict:
    """samples: sorted (ts, occupied) for one desk over the tracking window of one day."""
    interval = det["interval_seconds"]
    grace = det["away_grace_minutes"] * 60
    min_session = det["min_session_minutes"] * 60

    sessions: list[list[float]] = []
    for ts, occ in samples:
        if not occ:
            continue
        if sessions and ts - sessions[-1][1] <= grace:
            sessions[-1][1] = ts + interval
        else:
            sessions.append([ts, ts + interval])
    sessions = [s for s in sessions if s[1] - s[0] >= min_session]

    w0, w1 = work
    elapsed_end = min(w1, now)
    work_seconds = max(0.0, elapsed_end - w0)
    seated_total = sum(e - s for s, e in sessions)
    seated_work = sum(_overlap(s, e, w0, elapsed_end) for s, e in sessions)
    breaks = [(sessions[i][1], sessions[i + 1][0]) for i in range(len(sessions) - 1)]
    sampled = sum(1 for ts, _ in samples if w0 <= ts < elapsed_end)
    expected = work_seconds / interval if work_seconds else 0

    return {
        "first_in": sessions[0][0] if sessions else None,
        "last_out": sessions[-1][1] if sessions else None,
        "seated_seconds": round(seated_total),
        "seated_work_seconds": round(seated_work),
        "work_seconds": round(work_seconds),
        "seated_pct": round(100 * seated_work / work_seconds, 1) if work_seconds else None,
        "breaks": len(breaks),
        "break_seconds": round(sum(b - a for a, b in breaks)),
        "longest_break_seconds": round(max((b - a for a, b in breaks), default=0)),
        "coverage_pct": round(min(100.0, 100 * sampled / expected), 1) if expected else None,
        "sessions": [[round(s), round(e)] for s, e in sessions],
    }


def day_report(cfg: dict, db, zones, cameras: list[dict], d: date) -> dict:
    sch = Schedule(cfg)
    t0, t1 = sch.track_window(d)
    w0, w1 = sch.work_window(d)
    rows = db.samples(int(t0.timestamp()), int(t1.timestamp()))
    by_desk: dict[str, list] = defaultdict(list)
    for ts, desk, occ in rows:
        by_desk[desk].append((ts, occ))

    now = sch.now().timestamp()
    work = (w0.timestamp(), w1.timestamp())
    desks = []
    for cam in cameras:
        for z in zones.desks(cam["id"]):
            stats = desk_day(by_desk.get(z["id"], []), work, now, cfg["detection"])
            desks.append({
                "desk_id": z["id"], "desk": z["name"], "employee": z["employee"],
                "camera": cam["id"], "room": cam["name"], **stats,
            })
    return {
        "date": d.isoformat(),
        "workday": sch.is_workday(d),
        "work_start": work[0], "work_end": work[1],
        "track_start": t0.timestamp(), "track_end": t1.timestamp(),
        "desks": desks,
    }


def range_report(cfg, db, zones, cameras, start: date, end: date) -> dict:
    days = []
    d = start
    while d <= end and len(days) < 62:
        days.append(day_report(cfg, db, zones, cameras, d))
        d += timedelta(days=1)
    return {"days": days}


def to_csv(rep: dict, tz: ZoneInfo) -> str:
    def clock(ts):
        return datetime.fromtimestamp(ts, tz).strftime("%H:%M") if ts else ""

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "room", "desk", "employee", "first_in", "last_out", "hours_at_desk_in_work_hours",
                "hours_at_desk_total", "pct_of_work_hours", "breaks", "break_minutes",
                "longest_break_minutes", "camera_coverage_pct"])
    for day in rep["days"]:
        for r in day["desks"]:
            w.writerow([day["date"], r["room"], r["desk"], r["employee"], clock(r["first_in"]), clock(r["last_out"]),
                        round(r["seated_work_seconds"] / 3600, 2), round(r["seated_seconds"] / 3600, 2),
                        r["seated_pct"] if r["seated_pct"] is not None else "", r["breaks"],
                        round(r["break_seconds"] / 60), round(r["longest_break_seconds"] / 60),
                        r["coverage_pct"] if r["coverage_pct"] is not None else ""])
    return buf.getvalue()
