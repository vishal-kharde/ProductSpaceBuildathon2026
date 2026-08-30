import json
import logging
import sqlite3
from pathlib import Path
from config import settings
from safety import sanitize_json

log = logging.getLogger("bidlens.db")


def conn():
    path = Path(settings.db_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, timeout=15)
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init_db():
    c = conn()
    c.execute("CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, requirements TEXT, result_json TEXT NOT NULL)")
    c.commit(); c.close()
    log.info("Database ready: %s", Path(settings.db_path).resolve())


def save_run(requirements: str, result: dict):
    safe = sanitize_json(result)
    payload = json.dumps(safe, allow_nan=False, separators=(",", ":"))
    c = conn()
    c.execute("INSERT INTO runs(requirements, result_json) VALUES (?, ?)", (requirements, payload))
    c.commit(); run_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    c.close()
    log.info("Saved run id=%s", run_id)
    return run_id


def latest_run():
    c = conn(); row = c.execute("SELECT id, created_at, requirements, result_json FROM runs ORDER BY id DESC LIMIT 1").fetchone(); c.close()
    if not row: return None
    try:
        result = sanitize_json(json.loads(row[3]))
    except json.JSONDecodeError as exc:
        log.exception("Corrupt latest result in DB id=%s", row[0])
        return {"id": row[0], "created_at": row[1], "requirements": row[2], "result": None, "error": f"Corrupt stored result: {exc}"}
    return {"id": row[0], "created_at": row[1], "requirements": row[2], "result": result}


def reset_db():
    c = conn()
    c.execute("DELETE FROM runs")
    try: c.execute("DELETE FROM sqlite_sequence WHERE name='runs'")
    except sqlite3.OperationalError: pass
    c.commit(); c.close()
    log.info("Database reset")
