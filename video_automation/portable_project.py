from __future__ import annotations

import json
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .automation import AutomationRepository
from .library import LibraryRepository


PORTABLE_ARTIFACTS = {
    "job.json", "manifest.json", "cuts.json", "metadata.json", "publish_package.json",
    "transcript.json", "transcript.srt", "transcript.txt", "subtitles.ass", "subtitles_clipped.ass",
}


def create_portable_project_package(
    settings: Any,
    library: LibraryRepository,
    recipes: AutomationRepository,
    project_id: str,
    *,
    include_media: bool = False,
) -> dict[str, Any]:
    project = library.get_project(project_id)
    if project is None:
        raise ValueError("project not found")
    jobs = library.list_indexed_jobs(project_id=project_id)
    recipe_ids = {str(job.get("recipe_id") or "") for job in jobs if job.get("recipe_id")}
    recipe_payloads = [recipe for recipe_id in sorted(recipe_ids) if (recipe := recipes.get_recipe(recipe_id))]
    snapshots = []
    for job in jobs:
        snapshot_id = str(job.get("creator_kit_snapshot_id") or "")
        snapshot = library.get_creator_kit_snapshot(snapshot_id) if snapshot_id else None
        if snapshot:
            snapshots.append(snapshot)

    package_dir = Path(settings.jobs_dir).parent / "project_packages"
    package_dir.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r"[^A-Za-z0-9_-]+", "-", project_id).strip("-") or "project"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    package_path = package_dir / f"{safe_id}-{stamp}.zip"
    manifest = {
        "version": 1,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "project": project,
        "jobs": jobs,
        "recipes": recipe_payloads,
        "creator_kit_snapshots": snapshots,
        "media_included": bool(include_media),
    }
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        archive.writestr("project.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for job in jobs:
            job_name = str(job.get("job_name") or "")
            job_dir = Path(str(job.get("job_dir") or ""))
            if not job_name or not job_dir.is_dir():
                continue
            archive_job_name = _archive_component(job_name)
            candidates = [path for path in job_dir.iterdir() if path.is_file() and _include_artifact(path, include_media)]
            for path in sorted(candidates, key=lambda value: value.name):
                archive.write(path, f"jobs/{archive_job_name}/{path.name}")
    return {
        "project_id": project_id,
        "path": str(package_path),
        "filename": package_path.name,
        "size_bytes": package_path.stat().st_size,
        "job_count": len(jobs),
        "media_included": bool(include_media),
    }


def _include_artifact(path: Path, include_media: bool) -> bool:
    name = path.name.lower()
    if name in PORTABLE_ARTIFACTS:
        return True
    if name.startswith("cover_") and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
        return True
    return include_media and name in {"review.mp4", "final.mp4", "web_preview.mp4"}


def _archive_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\u4e00-\u9fff-]+", "-", value).strip("-") or "job"
