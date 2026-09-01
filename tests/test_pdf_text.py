from pathlib import Path

import main


def test_pdf_text_uses_direct_pdfminer_extractor(monkeypatch, tmp_path):
    pdf_path = tmp_path / "schedule.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fixture")
    calls = []

    def extract(path):
        calls.append(path)
        return "Wednesday\nExample Team"

    monkeypatch.setattr(main, "_extract_pdf_text", extract)

    assert main._pdf_text(pdf_path) == "Wednesday\nExample Team"
    assert calls == [str(pdf_path)]


def test_pdf_text_preserves_pdfminer_extracted_text(monkeypatch):
    extracted = "Wednesday\fExample Team\nGym 1\n"
    monkeypatch.setattr(main, "_extract_pdf_text", lambda path: extracted)

    assert main._pdf_text(Path("fixture.pdf")) == "Wednesday\nExample Team\nGym 1\n"
