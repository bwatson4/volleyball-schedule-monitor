"""Verify and unpack a read-only SSH export into an explicitly local folder."""
import argparse
import base64
import hashlib
import json
from pathlib import Path


def unpack(export_path, destination):
    data = json.loads(Path(export_path).read_text(encoding="utf-8-sig"))
    entries = data["pdfs"]
    if data["pdf_count"] != len(entries):
        raise ValueError("Archive count mismatch")
    verified, names = [], set()
    for entry in entries:
        name = entry["filename"]
        if Path(name).name != name or not name.lower().endswith(".pdf") or "/" in name or "\\" in name or ":" in name:
            raise ValueError("Unsafe archive filename")
        if name in names:
            raise ValueError("Duplicate archive filename")
        names.add(name)
        content = base64.b64decode(entry["base64"], validate=True)
        if len(content) != entry["bytes"] or hashlib.sha256(content).hexdigest() != entry["sha256"]:
            raise ValueError(f"Archive integrity mismatch: {name}")
        verified.append((name, content))
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for name, content in verified:
        (destination / name).write_bytes(content)
    metadata = {key: value for key, value in data.items() if key != "pdfs"}
    metadata["pdfs"] = [{key: value for key, value in item.items() if key != "base64"} for item in entries]
    (destination / "archive-metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return len(verified)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export")
    parser.add_argument("destination")
    args = parser.parse_args()
    print(f"Verified and copied {unpack(args.export, args.destination)} PDFs locally")
