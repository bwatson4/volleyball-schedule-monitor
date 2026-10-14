"""Every PDF in the read-only October 13 Pi export is a regression case."""
import hashlib
import json
from pathlib import Path

import pytest

from scripts.validate_pdf_archive import evaluate

ARCHIVE = Path(__file__).parent / "fixtures/pi-archive"
METADATA = json.loads((ARCHIVE / "metadata.json").read_text())


@pytest.mark.parametrize("entry", METADATA["pdfs"], ids=lambda entry: entry["filename"][:12])
def test_every_retained_pi_pdf(entry, tmp_path):
    path = ARCHIVE / entry["filename"]
    assert path.stat().st_size == entry["bytes"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"] == path.stem
    games = [row for row in METADATA["history"]["parsed_game"] if row["content_hash"] == path.stem]
    rosters = [row for row in METADATA["history"].get("parsed_pool_team", []) if row["content_hash"] == path.stem]
    result = evaluate(path, METADATA["settings"], tmp_path, games, rosters)
    assert result["pass"], result


def test_complete_inventory_and_exact_october12_revision():
    assert len(list(ARCHIVE.glob("*.pdf"))) == len(METADATA["pdfs"]) == METADATA["pdf_count"] == 21
    candidate = METADATA["state"]["candidate"]
    assert candidate["detected_at"] == "2026-10-13T01:20:12.692658+00:00"  # Oct 12 18:20 Vancouver
    assert candidate["unparsed"] and not candidate["parsed"]
    assert (ARCHIVE / (candidate["hash"] + ".pdf")).read_bytes() == (ARCHIVE.parent / "pdfs/publisher-135.pdf").read_bytes()
