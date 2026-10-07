"""Download pinned public recordings to an ignored cache, never during pytest."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def matches(path, entry):
    if not path.is_file() or path.stat().st_size != entry["size"]:
        return False
    algorithm = "sha256" if "sha256" in entry else "md5"
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, algorithm).hexdigest() == entry[algorithm]


def fetch(entry, destination):
    target = destination / entry["filename"]
    if Path(entry["filename"]).name != entry["filename"]:
        raise ValueError("Manifest filename must not contain directories")
    if matches(target, entry):
        print(f"Verified cache: {target.name}", flush=True)
        return
    temporary = None
    try:
        request = Request(entry["url"], headers={"User-Agent": "ChemSuite-public-data-test"})
        with urlopen(request, timeout=60) as response, tempfile.NamedTemporaryFile(
            dir=destination, prefix=".download-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            size, reported = 0, 0
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > entry["size"]:
                    raise ValueError(f"Download exceeds expected size: {target.name}")
                stream.write(chunk)
                if size - reported >= 10 * 1024 * 1024:
                    print(f"{target.name}: {size}/{entry['size']} bytes", flush=True)
                    reported = size
        if not matches(temporary, entry):
            raise ValueError(f"Download checksum or size mismatch: {target.name}")
        os.replace(temporary, target)
        print(f"Downloaded and verified: {target.name}", flush=True)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=ROOT / ".runtime/public-biologic")
    parser.add_argument("--include-large", action="store_true", help="Also download SINTEF MPTs (~128 MB)")
    arguments = parser.parse_args()
    manifest = json.loads((ROOT / "tests/public_data/biologic.json").read_text())
    arguments.destination.mkdir(parents=True, exist_ok=True)
    for source in manifest["sources"].values():
        print(f"{source['attribution']} | {source['license']} | {source['page']}", flush=True)
    for entry in manifest["files"]:
        if not entry["large"] or arguments.include_large:
            fetch(entry, arguments.destination)
    (arguments.destination / "provenance.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
