import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path


def write_json(path: Path, value: dict) -> None:
    """Atomic strict JSON; partial writes are never published as results."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class JobCancelled(Exception):
    pass


class JobContext:
    def __init__(self, directory: str, cancel_event, connection) -> None:
        self.directory = Path(directory)
        self.cancel_event = cancel_event
        self.connection = connection

    def check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise JobCancelled("Расчёт отменён")

    def progress(self, fraction: float, message: str) -> None:
        self.check_cancelled()
        self.connection.send(
            {"type": "progress", "progress": max(0.0, min(1.0, fraction)), "message": message[:1000]}
        )

    def artifact(self, name: str) -> Path:
        root = self.directory.resolve()
        path = (root / name).resolve()
        if path == root or not path.is_relative_to(root):
            raise ValueError("Artifact path must stay inside this job's directory")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def publication(self, destination: str) -> Path:
        """Prepare an export; only the supervisor can publish it as successful."""
        from chem_suite.core.publications import prepare

        self.check_cancelled()
        return prepare(self.directory, destination)

    def snapshot(self, source: str | Path) -> tuple[Path, dict]:
        """Work on a private source copy; preserve the original and record its content hash."""
        source = Path(source).resolve(strict=True)
        before = source.stat()
        target = self.artifact(f"inputs/{uuid.uuid4().hex}/{source.name}")
        digest = hashlib.sha256()
        with source.open("rb") as incoming, target.open("wb") as outgoing:
            while chunk := incoming.read(1024 * 1024):
                self.check_cancelled()
                digest.update(chunk)
                outgoing.write(chunk)
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            target.unlink(missing_ok=True)
            raise ValueError(f"Source changed during import: {source}")
        return target, {
            "path": str(source),
            "sha256": digest.hexdigest(),
            "size_bytes": before.st_size,
            "snapshot": str(target.relative_to(self.directory)),
        }


def export_result(result_path: str, destination: str) -> Path:
    """Copy a completed job bundle into a new directory, including data and provenance."""
    source = Path(result_path).resolve(strict=True)
    if source.name != "result.json":
        raise ValueError("Expected a completed job's result.json")
    data = json.loads(source.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported result schema")
    target = Path(destination).resolve()
    if target.exists():
        raise FileExistsError(f"Export destination already exists: {target}")
    if target.is_relative_to(source.parent):
        raise ValueError("Export cannot be nested inside its source job")
    staging = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        shutil.copytree(source.parent, staging)
        staging.rename(target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return target
