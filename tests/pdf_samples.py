"""Small real PDF samples for hermetic extractor/pipeline tests (no new deps)."""
from pathlib import Path


def write_pdf(path, rows=(), image=False):
    content = []
    for x, y, text in rows:
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        content.append(f"BT /F1 10 Tf {x} {y} Td ({escaped}) Tj ET\n".encode("ascii"))
    if image:
        content.append(b"q 200 0 0 200 50 300 cm /Im1 Do Q\n")
    stream = b"".join(content)
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> /XObject << /Im1 6 0 R >> >> /Contents 5 0 R >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
               b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"endstream",
               b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Length 3 >>\nstream\n\xff\x00\x00\nendstream"]
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(result)
    result.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    Path(path).write_bytes(result)
    return Path(path)


def samples(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    table = [(50, 730, "Wednesday"), (200, 730, "October 14, 2026"),
             (50, 710, "Example Gym"), (50, 690, "A POOL"),
             (100, 670, "1"), (140, 670, "Other Team"),
             (100, 650, "2"), (140, 650, "Another Team")]
    return {
        "unrelated": write_pdf(directory / "unrelated.pdf", [(50, 700, "Wednesday project planning notes")]),
        "empty": write_pdf(directory / "empty.pdf"),
        "image_only": write_pdf(directory / "image-only.pdf", image=True),
        "team_absent": write_pdf(directory / "team-absent.pdf", table + [(330, 660, "7:00-8:30")]),
        "no_games": write_pdf(directory / "no-games.pdf", [(50, 700, "Wednesday: no games are scheduled this week")]),
        "waiting": write_pdf(directory / "waiting.pdf", [(50, 700, "Wednesday schedule coming soon")]),
        "incomplete": write_pdf(directory / "incomplete.pdf", table),
        "malformed": directory / "malformed.pdf",
    }
