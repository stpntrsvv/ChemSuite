"""Reusable IO infrastructure. Domain modules own column semantics and data types."""

import csv
from pathlib import Path


def read_text(path: str | Path) -> str:
    raw = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Unsupported text encoding: {path}")


def csv_rows(path: str | Path):
    # Streaming common CSV reader; large cycling files do not need a DataFrame.
    with Path(path).open("r", encoding="utf-8-sig", newline="") as stream:
        sample = stream.read(8192)
        stream.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        yield from csv.DictReader(stream, dialect=dialect)
