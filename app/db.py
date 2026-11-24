"""SQLite storage: one row per desk per detection sample."""
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    ts INTEGER NOT NULL,
    camera TEXT NOT NULL,
    desk TEXT NOT NULL,
    occupied INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_samples_ts ON samples(ts);
"""


class DB:
    def __init__(self, path):
        self._path = str(path)
        self._lock = threading.Lock()
        with self._connect() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)
            cols = {r[1] for r in c.execute("PRAGMA table_info(samples)")}
            # score/overlap of the best person at the desk: raw inputs for per-desk auto-tuning
            if "score" not in cols:
                c.execute("ALTER TABLE samples ADD COLUMN score REAL")
            if "overlap" not in cols:
                c.execute("ALTER TABLE samples ADD COLUMN overlap REAL")

    def _connect(self):
        return sqlite3.connect(self._path, timeout=30)

    def add_samples(self, ts: int, camera: str, results: dict[str, tuple[bool, float, float]]) -> None:
        rows = [(ts, camera, desk, int(occ), round(score, 3), round(overlap, 3))
                for desk, (occ, score, overlap) in results.items()]
        if not rows:
            return
        with self._lock, self._connect() as c:
            c.executemany(
                "INSERT INTO samples(ts, camera, desk, occupied, score, overlap) VALUES (?,?,?,?,?,?)", rows)

    def samples(self, start_ts: int, end_ts: int) -> list[tuple[int, str, int]]:
        with self._connect() as c:
            return c.execute(
                "SELECT ts, desk, occupied FROM samples WHERE ts >= ? AND ts < ? ORDER BY ts",
                (start_ts, end_ts),
            ).fetchall()

    def detail(self, start_ts: int, end_ts: int) -> list[tuple[int, str, float, float]]:
        """Samples that carry raw detection values (recorded since auto-tuning was added)."""
        with self._connect() as c:
            return c.execute(
                "SELECT ts, desk, score, overlap FROM samples "
                "WHERE ts >= ? AND ts < ? AND score IS NOT NULL ORDER BY ts",
                (start_ts, end_ts),
            ).fetchall()

    def detail_counts(self, start_ts: int, utc_offset: int) -> list[tuple[str, int, int]]:
        """(desk, local day number, samples with detection values) since start_ts."""
        with self._connect() as c:
            return c.execute(
                "SELECT desk, (ts + ?) / 86400 AS day, COUNT(*) FROM samples "
                "WHERE ts >= ? AND score IS NOT NULL GROUP BY desk, day",
                (utc_offset, start_ts),
            ).fetchall()

    def purge_before(self, ts: int) -> int:
        with self._lock, self._connect() as c:
            return c.execute("DELETE FROM samples WHERE ts < ?", (ts,)).rowcount
