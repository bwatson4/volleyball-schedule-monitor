import base64
import hashlib
import json

import pytest

from scripts.export_pi_archive import export
from scripts.import_pi_archive import unpack
from scripts.validate_pdf_archive import validate
from src.history import HistoryStore
from tests.pdf_samples import samples


def test_read_only_archive_export_and_verified_local_import(tmp_path):
    root = tmp_path / "pi"
    files = samples(root / "pdfs")
    files["malformed"].write_bytes(b"bad PDF")
    HistoryStore(root / "history.sqlite3")
    (root / "settings.json").write_text(json.dumps({"team_names": ["Team"], "gyms": ["Gym"],
        "schedule_match_text": "Wednesday", "email_recipients": ["private@example.com"]}))
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    snapshot = export(root)
    assert snapshot["pdf_count"] == 8
    assert "email_recipients" not in snapshot["settings"]
    assert before == {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    exported = tmp_path / "export.json"
    exported.write_text(json.dumps(snapshot))
    local = tmp_path / "local"
    assert unpack(exported, local) == 8
    for item in snapshot["pdfs"]:
        assert hashlib.sha256((local / item["filename"]).read_bytes()).hexdigest() == item["sha256"]
    assert len(json.loads((local / "archive-metadata.json").read_text())["pdfs"]) == 8


def test_export_refuses_live_wal(tmp_path):
    (tmp_path / "pdfs").mkdir()
    (tmp_path / "history.sqlite3-wal").write_bytes(b"live WAL")
    with pytest.raises(ValueError, match="Live SQLite WAL"):
        export(tmp_path)


@pytest.mark.parametrize("name,checksum", [("../escape.pdf", True), ("valid.pdf", False)])
def test_import_rejects_unsafe_or_corrupt_copy(tmp_path, name, checksum):
    content = b"fixture"
    path = tmp_path / "export.json"
    path.write_text(json.dumps({"pdf_count": 1, "pdfs": [{"filename": name,
        "sha256": hashlib.sha256(content).hexdigest() if checksum else "wrong",
        "bytes": len(content), "base64": base64.b64encode(content).decode()}]}))
    with pytest.raises(ValueError):
        unpack(path, tmp_path / "local")
    assert not (tmp_path / "escape.pdf").exists()


def test_empty_archive_does_not_claim_validation(tmp_path):
    with pytest.raises(ValueError, match="No PDF files"):
        validate([tmp_path], {"team_names": ["Team"], "gyms": ["Gym"], "schedule_match_text": "Wednesday"})


def test_replay_tool_processes_every_file_with_metadata(tmp_path):
    files = samples(tmp_path / "pdfs")
    files["malformed"].write_bytes(b"%PDF broken")
    snapshot = export(tmp_path)
    report = validate([tmp_path / "pdfs"], {"team_names": ["Configured Team"], "gyms": ["Example Gym"],
        "schedule_match_text": "Wednesday"}, snapshot)
    assert report["total_pdfs_tested"] == report["pi_export_count"] == 8
    assert report["pi_export_coverage_complete"]
    assert report["all_safety_and_recorded_comparisons_pass"]
    assert all(row["events_generated"] == 0 for row in report["rows"])
