"""Download the Portuguese OCR language data used by the extraction stage.

This is an explicit, developer-approved setup step (hard rule 5): the app itself
never downloads anything. Files are pinned to a tessdata_fast commit and verified
by SHA-256 before being installed.

Usage: uv run python scripts/fetch_tessdata.py [DEST_DIR]   (default: .tessdata/)
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

COMMIT = "87416418657359cb625c412a48b6e1d6d41c29bd"
BASE_URL = f"https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/{COMMIT}/"
FILES = {"por.traineddata": "c4932b937207a9514b7514d518b931a99938c02a28a5a5a553f8599ed58b7deb"}
DEFAULT_DEST = Path(__file__).resolve().parent.parent / ".tessdata"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main(argv: list[str]) -> int:
    dest = Path(argv[0]) if argv else DEFAULT_DEST
    dest.mkdir(parents=True, exist_ok=True)
    for name, digest in FILES.items():
        target = dest / name
        if target.is_file() and sha256(target.read_bytes()) == digest:
            sys.stderr.write(f"{name}: already present, checksum ok\n")
            continue
        with urllib.request.urlopen(BASE_URL + name, timeout=120) as response:
            data = response.read()
        if sha256(data) != digest:
            sys.stderr.write(f"{name}: checksum mismatch, not installed\n")
            return 1
        tmp = target.with_name(name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(target)
        sys.stderr.write(f"{name}: installed, checksum ok\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
