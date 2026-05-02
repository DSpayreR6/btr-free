from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from .btrfs import Snapshot

DB_PATH = Path.home() / '.cache' / 'btr-free' / 'cache.db'
_TTL = timedelta(days=7)


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS snapshots (
            subvol_id     INTEGER PRIMARY KEY,
            path          TEXT    NOT NULL,
            grp           TEXT    NOT NULL,
            rfer          INTEGER NOT NULL,
            excl          INTEGER NOT NULL,
            snapshot_date TEXT,
            cached_at     TEXT    NOT NULL
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS timing (
            fingerprint    TEXT PRIMARY KEY,
            sec_per_subvol REAL NOT NULL,
            measured_at    TEXT NOT NULL
        )
    ''')
    conn.commit()
    return conn


def needs_refresh() -> bool:
    conn = _conn()
    row = conn.execute('SELECT MIN(cached_at) FROM snapshots').fetchone()
    conn.close()
    if not row or row[0] is None:
        return True
    return datetime.now() - datetime.fromisoformat(row[0]) > _TTL


def save(snapshots: list[Snapshot]) -> None:
    """Cache only snapshots older than today (their data never changes)."""
    today = date.today().isoformat()
    now   = datetime.now().isoformat()
    conn  = _conn()
    for s in snapshots:
        snap_date = s.otime.date().isoformat() if s.otime else None
        if snap_date and snap_date >= today:
            continue
        conn.execute(
            '''INSERT OR REPLACE INTO snapshots
               (subvol_id, path, grp, rfer, excl, snapshot_date, cached_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (s.subvol_id, s.path, s.group, s.rfer, s.excl, snap_date, now),
        )
    conn.commit()
    conn.close()


def get_estimated_duration(fingerprint: str, subvol_count: int) -> float | None:
    """Returns estimated rescan duration in seconds, or None if no data."""
    conn = _conn()
    row  = conn.execute(
        'SELECT sec_per_subvol FROM timing WHERE fingerprint = ?', (fingerprint,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return row[0] * subvol_count


def save_timing(fingerprint: str, duration_sec: float, subvol_count: int) -> None:
    if subvol_count <= 0:
        return
    conn = _conn()
    conn.execute(
        'INSERT OR REPLACE INTO timing (fingerprint, sec_per_subvol, measured_at) VALUES (?, ?, ?)',
        (fingerprint, duration_sec / subvol_count, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def load_all() -> list[Snapshot]:
    conn = _conn()
    rows = conn.execute(
        'SELECT subvol_id, path, grp, rfer, excl, snapshot_date FROM snapshots'
    ).fetchall()
    conn.close()
    result: list[Snapshot] = []
    for subvol_id, path, grp, rfer, excl, snap_date in rows:
        otime = None
        if snap_date:
            try:
                otime = datetime.fromisoformat(snap_date + 'T00:00:00')
            except ValueError:
                pass
        result.append(Snapshot(subvol_id=subvol_id, path=path, group=grp,
                                rfer=rfer, excl=excl, otime=otime))
    return result
