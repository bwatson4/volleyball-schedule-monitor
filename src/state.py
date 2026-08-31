"""Atomic, human-readable state for resumable processing."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from utils import atomic_json_write

def now(): return datetime.now(timezone.utc).isoformat()

class ScheduleState:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = self._load()
    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {"version": 1}
            return data if isinstance(data, dict) else {"version": 1}
        except (OSError, json.JSONDecodeError):
            try: self.path.replace(self.path.with_suffix(self.path.suffix + ".corrupt"))
            except OSError: pass
            return {"version": 1}
    def save(self): atomic_json_write(self.path, self.data)
    def run_started(self): self.data["last_attempted_run"] = now(); self.save()
    def set_failure(self, stage, exc): self.data["last_failure"] = {"stage":stage,"message":str(exc),"at":now()}; self.save()
    def begin_candidate(self, digest, pdf_path, source_url):
        candidate = self.data.get("candidate", {})
        if candidate.get("hash") != digest:
            candidate = {"hash":digest,"pdf_path":str(pdf_path),"source_url":source_url,"detected_at":now(),"parsed":False,"calendar":False,"email":False}
            self.data["candidate"] = candidate; self.save()
        return candidate
    def mark_stage(self, stage, value=True): self.data["candidate"][stage] = value; self.save()
    def complete_if_ready(self):
        candidate = self.data.get("candidate", {})
        if all(candidate.get(stage) for stage in ("parsed", "calendar", "email")):
            self.data["completed"] = {"hash":candidate["hash"],"source_url":candidate["source_url"],"at":now()}
            self.data["last_successfully_completed_run"] = now(); self.data.pop("last_failure", None); self.save(); return True
        return False
