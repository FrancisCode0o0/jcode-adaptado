#!/usr/bin/env python3
"""Build the delivery ZIP of a jcode-adaptado version (used by Claude).

Origin: jcode 0.10.0-ma.3 (mejorador-audio plan step 1.0.b1).
Layout of the ZIP (what vm/windows/install_ma3.ps1 expects):
    jcode-adaptado/     every file git tracks in this repository
    mejorador-audio/    the shared secret check: tools/check_secrets.py,
                        tools/tests/test_check_secrets.py, .githooks/pre-commit,
                        .gitattributes (the same files as in jcode-adaptado)
    MANIFEST.sha256     "<sha256>  <path>" for every file above
Usage: python3 tools/make_package.py <output.zip>
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SHARED_WITH_AUDIO = ("tools/check_secrets.py", "tools/tests/test_check_secrets.py",
                     ".githooks/pre-commit", ".gitattributes")
# Fixed date inside the ZIP: the same content always gives the same file.
ZIP_DATE = (2026, 10, 20, 0, 0, 0)


def tracked_files() -> list[str]:
    listing = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, check=True,
                             capture_output=True, text=True).stdout
    return sorted(path for path in listing.split("\0") if path)


def add(archive: zipfile.ZipFile, name: str, data: bytes, executable: bool) -> None:
    info = zipfile.ZipInfo(name, date_time=ZIP_DATE)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (0o755 if executable else 0o644) << 16
    archive.writestr(info, data)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    entries: list[tuple[str, Path]] = [(f"jcode-adaptado/{p}", REPO / p) for p in tracked_files()]
    entries += [(f"mejorador-audio/{p}", REPO / p) for p in SHARED_WITH_AUDIO]
    manifest = []
    with zipfile.ZipFile(argv[0], "w") as archive:
        for name, source in entries:
            data = source.read_bytes()
            executable = source.suffix in (".sh", ".py") or source.name == "pre-commit"
            add(archive, name, data, executable)
            manifest.append(f"{hashlib.sha256(data).hexdigest()}  {name}")
        add(archive, "MANIFEST.sha256", ("\n".join(manifest) + "\n").encode(), False)
    print(f"{argv[0]}: {len(entries)} files + MANIFEST.sha256")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
