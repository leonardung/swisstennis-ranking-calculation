"""Loading times: the UI's page views (measured by the browser) and the API's response times (by
the server), kept PERF_DAYS days in data/perf.sqlite and summarised per page as avg, p50, p95
and p99. Samples are buffered and written in batches."""

import sqlite3
import threading
import time
from pathlib import Path

import pandas as pd

PERF_DAYS = 90
# a batch is written once it has this many samples or is this old (seconds)
FLUSH_SIZE = 200
FLUSH_AGE = 60


class PerfLog:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.buffer: list[tuple[float, str, str, float]] = []
        self.flushed = time.time()
        with self._connect() as db:
            db.execute("create table if not exists samples (time real not null, kind text not null, page text not null, ms real not null)")
            db.execute("create index if not exists samples_time on samples (time)")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=5)

    def add(self, kind: str, page: str, ms: float) -> None:
        now = time.time()
        with self.lock:
            self.buffer.append((now, kind, page, ms))
            if len(self.buffer) >= FLUSH_SIZE or now - self.flushed >= FLUSH_AGE:
                self._flush(now)

    def flush(self) -> None:
        with self.lock:
            self._flush(time.time())

    def _flush(self, now: float) -> None:
        """Write the buffer and drop the samples older than PERF_DAYS (the lock held)."""
        with self._connect() as db:
            db.executemany("insert into samples values (?, ?, ?, ?)", self.buffer)
            db.execute("delete from samples where time < ?", (now - PERF_DAYS * 86400,))
        self.buffer = []
        self.flushed = now

    def summary(self, days: float) -> list[dict]:
        """Per kind and page over the last `days`: the number of samples, avg, p50, p95 and p99
        (ms), the most frequent first."""
        with self.lock:
            self._flush(time.time())
            with self._connect() as db:
                rows = pd.read_sql_query(
                    "select kind, page, ms from samples where time >= ?", db, params=(time.time() - days * 86400,)
                )
        g = rows.groupby(["kind", "page"])["ms"]
        out = pd.DataFrame(
            {"n": g.size(), "avg": g.mean(), "p50": g.quantile(0.5), "p95": g.quantile(0.95), "p99": g.quantile(0.99)}
        ).reset_index()
        out = out.sort_values(["kind", "n"], ascending=[True, False])
        return [
            {"kind": r.kind, "page": r.page, "n": int(r.n), **{k: round(float(getattr(r, k))) for k in ("avg", "p50", "p95", "p99")}}
            for r in out.itertuples()
        ]
