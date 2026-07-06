from __future__ import annotations

import shutil
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


class InsufficientDiskSpace(RuntimeError):
    def __init__(self, required_bytes: int, available_bytes: int):
        self.required_bytes = int(required_bytes)
        self.available_bytes = int(available_bytes)
        super().__init__(
            f"Insufficient disk space: need {self.required_bytes} bytes, "
            f"only {self.available_bytes} bytes available"
        )


def database_is_healthy(database_path: Path | str) -> bool:
    path = Path(database_path)
    if not path.exists():
        return True
    try:
        with closing(sqlite3.connect(path, timeout=5)) as connection:
            return connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    except (OSError, sqlite3.DatabaseError):
        return False


def backup_database(database_path: Path | str, *, keep: int = 5) -> Path:
    source_path = Path(database_path)
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    backup_dir = source_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    destination = backup_dir / f"library-{stamp}.sqlite3"
    with closing(sqlite3.connect(source_path, timeout=10)) as source:
        with closing(sqlite3.connect(destination)) as target:
            source.backup(target)
            target.commit()
    backups = sorted(backup_dir.glob("library-*.sqlite3"), key=lambda item: item.name, reverse=True)
    for stale in backups[max(1, int(keep)):]:
        stale.unlink(missing_ok=True)
    return destination


def ensure_database_ready(database_path: Path | str) -> dict[str, Any]:
    path = Path(database_path)
    if not path.exists():
        return {"status": "new", "database_path": str(path)}
    if database_is_healthy(path):
        return {"status": "healthy", "database_path": str(path)}

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    quarantined = path.with_name(f"{path.name}.corrupt-{stamp}")
    path.replace(quarantined)
    backups = sorted(
        (path.parent / "backups").glob("library-*.sqlite3"),
        key=lambda item: item.name,
        reverse=True,
    )
    for backup in backups:
        if not database_is_healthy(backup):
            continue
        shutil.copy2(backup, path)
        return {
            "status": "restored",
            "database_path": str(path),
            "backup_path": str(backup),
            "quarantined_path": str(quarantined),
        }
    return {
        "status": "reset",
        "database_path": str(path),
        "quarantined_path": str(quarantined),
    }


def ensure_disk_capacity(
    path: Path | str,
    *,
    required_bytes: int,
    free_bytes: Callable[[Path], int] | None = None,
) -> dict[str, int]:
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    available = int(free_bytes(target) if free_bytes else shutil.disk_usage(target).free)
    required = max(0, int(required_bytes))
    if available < required:
        raise InsufficientDiskSpace(required, available)
    return {"required_bytes": required, "available_bytes": available}


def ensure_job_capacity(settings: Any, source_path: Path | str) -> dict[str, int]:
    source = Path(source_path)
    source_size = source.stat().st_size if source.is_file() else 0
    reserve = max(0, int(getattr(settings, "min_free_disk_bytes", 1_073_741_824)))
    multiplier = max(1.0, float(getattr(settings, "job_disk_multiplier", 2.0)))
    required = reserve + int(source_size * multiplier)
    return ensure_disk_capacity(Path(settings.jobs_dir), required_bytes=required)
