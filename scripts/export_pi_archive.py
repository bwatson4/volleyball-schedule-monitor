"""Read-only Pi archive export to stdout; run via SSH, never installs anything.

PDFs and operational metadata only: no environment file or email credentials.
SQLite is opened immutable so even journal/shared-memory files cannot be created.
Refuse a live WAL rather than export a potentially stale comparison snapshot.
"""
import base64
from contextlib import closing
import hashlib
import json
import sqlite3
import sys
from pathlib import Path


def export(root):
    root = Path(root).resolve()
    pdf_dir = root / "pdfs"
    if not pdf_dir.is_dir():
        raise ValueError(f"PDF archive not found: {pdf_dir}")
    result = {"archive_path": str(pdf_dir), "pdfs": [], "history": {}, "settings": {}, "state": {}}
    for path in sorted(pdf_dir.glob("*.pdf")):
        data = path.read_bytes()
        result["pdfs"].append({"filename": path.name, "sha256": hashlib.sha256(data).hexdigest(),
                               "bytes": len(data), "base64": base64.b64encode(data).decode("ascii")})
    db_path = root / "history.sqlite3"
    wal = root / "history.sqlite3-wal"
    if wal.exists() and wal.stat().st_size:
        raise ValueError("Live SQLite WAL present; refusing a stale immutable history export")
    if db_path.is_file():
        before = hashlib.sha256(db_path.read_bytes()).hexdigest()
        with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN")
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ("schedule_revision", "parsed_game", "parsed_pool_team"):
                if table in tables:
                    result["history"][table] = [dict(row) for row in db.execute(f"SELECT * FROM {table}")]
        if before != hashlib.sha256(db_path.read_bytes()).hexdigest() or (wal.exists() and wal.stat().st_size):
            raise ValueError("History changed during export; retry for a stable read-only snapshot")
    for filename, key, fields in [
        ("settings.json", "settings", ("team_names", "team_name", "gyms", "schedule_match_text")),
        ("schedule_state.json", "state", ("candidate", "completed", "last_failure")),
    ]:
        path = root / filename
        if path.is_file():
            data = json.loads(path.read_text())
            result[key] = {field: data[field] for field in fields if field in data}
    result["pdf_count"] = len(result["pdfs"])
    if {p.name for p in pdf_dir.glob("*.pdf")} != {p["filename"] for p in result["pdfs"]}:
        raise ValueError("PDF inventory changed during export; retry")
    return result


if __name__ == "__main__":
    print(json.dumps(export(sys.argv[1] if len(sys.argv) > 1 else "/var/lib/volleyball-schedule-monitor")))
