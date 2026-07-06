from __future__ import annotations

import os
import json
import threading
from pathlib import Path
from typing import Any

from .automation import AutomationRepository
from .credentials import SystemCredentialStore
from .library import LibraryRepository
from .providers.bilibili import BilibiliHttpTransport, BilibiliProvider
from .preferences import PreferenceRepository
from .publish_center import PublishRepository, PublishService
from .quality_gate import evaluate_quality_gate
from .task_queue import QueueRepository


_REPOSITORIES: dict[Path, LibraryRepository] = {}
_INDEXED_DATABASES: set[Path] = set()
_REPOSITORY_LOCK = threading.Lock()
_AUTOMATION_REPOSITORIES: dict[Path, AutomationRepository] = {}
_QUEUE_REPOSITORIES: dict[Path, QueueRepository] = {}
_PUBLISH_REPOSITORIES: dict[Path, PublishRepository] = {}
_PUBLISH_SERVICES: dict[Path, PublishService] = {}
_PREFERENCE_REPOSITORIES: dict[Path, PreferenceRepository] = {}


def library_database_path(settings: Any) -> Path:
    jobs_dir = Path(settings.jobs_dir)
    return jobs_dir.parent / "library.sqlite3"


def repository_for(settings: Any) -> LibraryRepository:
    database_path = library_database_path(settings).resolve()
    with _REPOSITORY_LOCK:
        repository = _REPOSITORIES.get(database_path)
        if repository is None:
            repository = LibraryRepository(database_path)
            _REPOSITORIES[database_path] = repository
        if database_path not in _INDEXED_DATABASES:
            repository.index_existing_jobs(Path(settings.jobs_dir))
            _INDEXED_DATABASES.add(database_path)
    return repository


def automation_repository_for(settings: Any) -> AutomationRepository:
    database_path = library_database_path(settings).resolve()
    with _REPOSITORY_LOCK:
        repository = _AUTOMATION_REPOSITORIES.get(database_path)
        if repository is None:
            repository = AutomationRepository(database_path)
            _AUTOMATION_REPOSITORIES[database_path] = repository
    return repository


def queue_repository_for(settings: Any) -> QueueRepository:
    database_path = library_database_path(settings).resolve()
    with _REPOSITORY_LOCK:
        repository = _QUEUE_REPOSITORIES.get(database_path)
        if repository is None:
            repository = QueueRepository(database_path)
            _QUEUE_REPOSITORIES[database_path] = repository
    return repository


class _UnavailableBilibiliTransport:
    def validate(self, _token: str, _client_id: str) -> dict[str, Any]:
        return {"can_publish": False}


def _bilibili_transport_for(settings: Any):
    base_url = str(
        getattr(settings, "bilibili_api_base_url", "")
        or os.environ.get("BILIBILI_API_BASE_URL", "")
    ).strip()
    endpoints = getattr(settings, "bilibili_api_endpoints", None)
    if not isinstance(endpoints, dict):
        endpoints = {
            "validate": os.environ.get("BILIBILI_VALIDATE_PATH", ""),
            "create_upload": os.environ.get("BILIBILI_CREATE_UPLOAD_PATH", ""),
            "complete_upload": os.environ.get("BILIBILI_COMPLETE_UPLOAD_PATH", ""),
            "publish": os.environ.get("BILIBILI_PUBLISH_PATH", ""),
            "query": os.environ.get("BILIBILI_QUERY_PATH", ""),
        }
    try:
        return BilibiliHttpTransport(base_url, endpoints)
    except ValueError:
        return _UnavailableBilibiliTransport()


def publish_repository_for(settings: Any) -> PublishRepository:
    database_path = library_database_path(settings).resolve()
    with _REPOSITORY_LOCK:
        repository = _PUBLISH_REPOSITORIES.get(database_path)
        if repository is None:
            repository = PublishRepository(database_path)
            _PUBLISH_REPOSITORIES[database_path] = repository
    return repository


def credential_store_for(settings: Any):
    store = getattr(settings, "credential_store", None)
    if store is not None and all(hasattr(store, method) for method in ("get", "set", "delete")):
        return store
    return SystemCredentialStore()


def preference_repository_for(settings: Any) -> PreferenceRepository:
    database_path = library_database_path(settings).resolve()
    with _REPOSITORY_LOCK:
        repository = _PREFERENCE_REPOSITORIES.get(database_path)
        if repository is None:
            repository = PreferenceRepository(database_path)
            _PREFERENCE_REPOSITORIES[database_path] = repository
    return repository


def publish_service_for(settings: Any) -> PublishService:
    database_path = library_database_path(settings).resolve()
    repository = publish_repository_for(settings)
    with _REPOSITORY_LOCK:
        service = _PUBLISH_SERVICES.get(database_path)
        if service is None:
            provider = BilibiliProvider(credential_store_for(settings), _bilibili_transport_for(settings))
            service = PublishService(repository, {"bilibili": provider})
            _PUBLISH_SERVICES[database_path] = service
    return service


def structured_error(
    code: str,
    message: str,
    *,
    retryable: bool = False,
    action: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {
        "code": code,
        "message": message,
        "retryable": retryable,
    }
    if action:
        error["action"] = action
    if details is not None:
        error["details"] = details
    return {"error": error}


def attach_job_context(settings: Any, job: Any, payload: dict[str, Any]) -> dict[str, Any]:
    repository = repository_for(settings)
    job_name = Path(job.job_dir).name
    repository.index_job(
        job_name,
        job_dir=job.job_dir,
        source_path=job.source_path,
        status=str(job.status),
        created_at=str(job.created_at),
        updated_at=str(job.updated_at),
    )
    current = repository.get_indexed_job(job_name) or {}
    context_keys = {"project_id", "creator_kit_id", "recipe_id"}
    if not context_keys.intersection(payload):
        return current

    project_id = (
        str(payload.get("project_id") or "").strip() or None
        if "project_id" in payload
        else current.get("project_id")
    )
    creator_kit_id = str(payload.get("creator_kit_id") or "").strip() or None
    recipe_id = (
        str(payload.get("recipe_id") or "").strip() or None
        if "recipe_id" in payload
        else current.get("recipe_id")
    )

    project = repository.get_project(project_id) if project_id else None
    if project_id and project is None:
        raise ValueError("project not found")
    if not creator_kit_id and project and "project_id" in payload:
        creator_kit_id = project.get("default_kit_id") or None
    if creator_kit_id and repository.get_creator_kit(creator_kit_id) is None:
        raise ValueError("creator kit not found")

    snapshot_id = current.get("creator_kit_snapshot_id")
    if "creator_kit_id" in payload or ("project_id" in payload and creator_kit_id):
        snapshot_id = repository.snapshot_creator_kit(creator_kit_id)["id"] if creator_kit_id else None

    attached = repository.assign_job(
        job_name,
        project_id=project_id,
        creator_kit_snapshot_id=snapshot_id,
        recipe_id=recipe_id,
    )
    return attached or {}


def job_library_fields(settings: Any, job_name: str) -> dict[str, Any]:
    repository = repository_for(settings)
    indexed = repository.get_indexed_job(job_name) or {}
    return {
        "id": job_name,
        "project_id": indexed.get("project_id"),
        "recipe_id": indexed.get("recipe_id"),
        "creator_kit_snapshot_id": indexed.get("creator_kit_snapshot_id"),
        "revision": repository.latest_revision_number(job_name),
        "capabilities": ["review", "enhance", "export", "advanced"],
    }


def job_library_fields_map(settings: Any, job_names: list[str]) -> dict[str, dict[str, Any]]:
    contexts = repository_for(settings).get_job_contexts(job_names)
    return {
        job_name: _job_library_payload(job_name, contexts.get(job_name, {}))
        for job_name in job_names
    }


def _job_library_payload(job_name: str, indexed: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": job_name,
        "project_id": indexed.get("project_id"),
        "recipe_id": indexed.get("recipe_id"),
        "creator_kit_snapshot_id": indexed.get("creator_kit_snapshot_id"),
        "revision": int(indexed.get("revision") or 0),
        "capabilities": ["review", "enhance", "export", "advanced"],
    }


def dispatch_library_request(
    settings: Any,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]] | None:
    normalized = "/" + path.strip("/")
    if not normalized.startswith("/api/v1/"):
        return None
    repository = repository_for(settings)
    payload = payload or {}

    if normalized == "/api/v1/capabilities" and method == "GET":
        return 200, {
            "version": 1,
            "local_first": True,
            "features": {
                "projects": True,
                "creator_kits": True,
                "revisions": True,
                "recipes": True,
                "smart_queue": True,
                "quality_gate": True,
                "preference_learning": True,
                "publish_connectors": True,
            },
        }

    parts = normalized.strip("/").split("/")
    resource = parts[2] if len(parts) >= 3 else ""
    resource_id = parts[3] if len(parts) >= 4 else ""

    try:
        if resource == "projects":
            return _dispatch_projects(repository, method, resource_id, payload)
        if resource == "creator-kits":
            return _dispatch_creator_kits(repository, method, resource_id, payload)
        if resource == "recipes":
            return _dispatch_recipes(automation_repository_for(settings), method, resource_id, payload)
        if resource == "queue":
            action = parts[4] if len(parts) >= 5 else ""
            return _dispatch_queue(queue_repository_for(settings), method, resource_id, action, payload)
        if resource == "publish-targets":
            return _dispatch_publish_targets(settings, method, resource_id, parts, payload)
        if resource == "publish-attempts":
            action = parts[4] if len(parts) >= 5 else ""
            return _dispatch_publish_attempts(settings, method, resource_id, action, payload)
        if resource == "jobs" and len(parts) >= 5 and parts[4] == "revisions":
            revision_id = parts[5] if len(parts) >= 6 else ""
            return _dispatch_revisions(repository, method, resource_id, revision_id)
        if resource == "jobs" and len(parts) >= 5 and parts[4] == "quality":
            if method != "GET":
                return 405, structured_error("method_not_allowed", "Method not allowed")
            job_dir = Path(settings.jobs_dir) / resource_id
            if not (job_dir / "job.json").is_file():
                return 404, structured_error("not_found", "Job not found")
            return 200, evaluate_job_quality(settings, resource_id)
        if resource == "preferences":
            return _dispatch_preferences(preference_repository_for(settings), method, resource_id)
    except ValueError as exc:
        return 400, structured_error("validation_error", str(exc))

    return 404, structured_error("not_found", "API resource not found")


def evaluate_job_quality(settings: Any, job_name: str) -> dict[str, Any]:
    repository = repository_for(settings)
    indexed = repository.get_indexed_job(job_name) or {}
    snapshot_id = indexed.get("creator_kit_snapshot_id")
    snapshot = repository.get_creator_kit_snapshot(snapshot_id) if snapshot_id else None
    kit = snapshot.get("payload", {}) if snapshot else {}
    subtitle_style = kit.get("subtitle_style") if isinstance(kit.get("subtitle_style"), dict) else {}
    cover_style = kit.get("cover_style") if isinstance(kit.get("cover_style"), dict) else {}
    metadata_style = kit.get("metadata_style") if isinstance(kit.get("metadata_style"), dict) else {}
    policy = metadata_style.get("quality_gate") if isinstance(metadata_style.get("quality_gate"), dict) else {}
    policy = dict(policy)
    if kit.get("aspect"):
        policy.setdefault("aspect", kit["aspect"])
    policy.setdefault("subtitle_max_chars_per_line", subtitle_style.get("max_chars_per_line", 18))
    policy.setdefault("subtitle_max_lines", subtitle_style.get("max_lines", 2))
    policy.setdefault("cover_required", bool(cover_style.get("required", False)))
    return evaluate_quality_gate(Path(settings.jobs_dir) / job_name, policy)


def _dispatch_preferences(
    repository: PreferenceRepository,
    method: str,
    action: str,
) -> tuple[int, dict[str, Any]]:
    if action == "export":
        if method != "GET":
            return 405, structured_error("method_not_allowed", "Method not allowed")
        return 200, repository.export()
    if action:
        return 404, structured_error("not_found", "Preference resource not found")
    if method == "GET":
        return 200, repository.summary()
    if method == "DELETE":
        return 200, {"cleared": repository.clear()}
    return 405, structured_error("method_not_allowed", "Method not allowed")


def _dispatch_publish_targets(
    settings: Any,
    method: str,
    target_id: str,
    parts: list[str],
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    targets = [{
        "id": "bilibili",
        "name": "Bilibili",
        "authorization": "operating_system_credential_store",
        "requires_platform_approval": True,
        "manual_fallback": True,
        "sandbox_supported": True,
    }]
    action = parts[4] if len(parts) >= 5 else ""
    account_id = parts[5] if len(parts) >= 6 else ""
    if target_id == "bilibili" and action == "credentials":
        store = credential_store_for(settings)
        if method == "POST" and not account_id:
            account = str(payload.get("account_id") or "").strip()
            client_id = str(payload.get("client_id") or "").strip()
            access_token = str(payload.get("access_token") or "").strip()
            if not account or not client_id or not access_token:
                raise ValueError("account_id, client_id, and access_token are required")
            reference = f"bilibili:{account[:120]}"
            secret = {
                "client_id": client_id,
                "access_token": access_token,
            }
            refresh_token = str(payload.get("refresh_token") or "").strip()
            if refresh_token:
                secret["refresh_token"] = refresh_token
            store.set(reference, json.dumps(secret, ensure_ascii=False, separators=(",", ":")))
            return 200, {
                "provider": "bilibili",
                "account_id": account,
                "credential_ref": reference,
                "configured": True,
            }
        if method == "DELETE" and account_id:
            reference = f"bilibili:{account_id[:120]}"
            store.delete(reference)
            return 200, {"deleted": True, "credential_ref": reference}
        return 405, structured_error("method_not_allowed", "Method not allowed")
    if method != "GET":
        return 405, structured_error("method_not_allowed", "Method not allowed")
    if target_id:
        target = next((item for item in targets if item["id"] == target_id), None)
        if target is None:
            return 404, structured_error("not_found", "Publish target not found")
        return 200, target
    return 200, {"items": targets, "count": len(targets)}


def _dispatch_publish_attempts(
    settings: Any,
    method: str,
    attempt_id: str,
    action: str,
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    repository = publish_repository_for(settings)
    if not attempt_id:
        if method == "GET":
            items = repository.list_attempts(job_name=str(payload.get("job_id") or "").strip() or None)
            return 200, {"items": items, "count": len(items)}
        if method == "POST":
            job_id = str(payload.get("job_id") or "").strip()
            provider = str(payload.get("provider") or "bilibili").strip()
            job_dir = (Path(settings.jobs_dir) / job_id).resolve()
            try:
                job_dir.relative_to(Path(settings.jobs_dir).resolve())
            except ValueError as exc:
                raise ValueError("invalid job_id") from exc
            video_path = job_dir / "final.mp4"
            if not video_path.is_file():
                video_path = job_dir / "review.mp4"
            if not video_path.is_file():
                raise ValueError("final.mp4 or review.mp4 is required before publishing")
            package_path = job_dir / "publish_package.json"
            publish_payload = {
                key: value
                for key, value in payload.items()
                if key not in {"credential_ref", "access_token", "refresh_token", "client_secret"}
            }
            publish_payload["video_path"] = str(video_path)
            attempt = repository.create_attempt(
                job_id,
                provider,
                credential_ref=str(payload.get("credential_ref") or "").strip() or None,
                payload=publish_payload,
                total_bytes=video_path.stat().st_size,
                manual_package_path=str(package_path) if package_path.is_file() else None,
            )
            preference_repository_for(settings).record(
                "publish_selection", {"platform": provider}, job_name=job_id
            )
            return 201, attempt
        return 405, structured_error("method_not_allowed", "Method not allowed")
    attempt = repository.get(attempt_id)
    if attempt is None:
        return 404, structured_error("not_found", "Publish attempt not found")
    if method == "GET" and not action:
        return 200, attempt
    if method != "POST":
        return 405, structured_error("method_not_allowed", "Method not allowed")
    service = publish_service_for(settings)
    if action in {"start", "retry"}:
        return 200, service.run_attempt(attempt_id)
    if action == "sync":
        return 200, service.sync_attempt(attempt_id)
    return 404, structured_error("not_found", "Publish action not found")


def _dispatch_queue(
    repository: QueueRepository,
    method: str,
    queue_id: str,
    action: str,
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    if not queue_id:
        if method == "GET":
            items = repository.list_items()
            return 200, {**repository.control_state(), "items": items, "count": len(items)}
        return 405, structured_error("method_not_allowed", "Method not allowed")
    if queue_id == "pause" and method == "POST":
        return 200, repository.set_global_paused(True)
    if queue_id == "resume" and method == "POST":
        return 200, repository.set_global_paused(False)
    if queue_id == "reorder" and method == "POST":
        ids = payload.get("ids")
        if not isinstance(ids, list):
            raise ValueError("ids must be a list")
        items = repository.reorder([str(value) for value in ids])
        return 200, {"items": items, "count": len(items)}

    item = repository.get(queue_id)
    if item is None:
        return 404, structured_error("not_found", "Queue item not found")
    if method == "GET" and not action:
        return 200, item
    if method == "DELETE" and not action:
        return 200, repository.cancel(queue_id) or item
    if method != "POST":
        return 405, structured_error("method_not_allowed", "Method not allowed")
    if action == "pause":
        return 200, repository.pause(queue_id) or item
    if action == "resume":
        return 200, repository.resume(queue_id) or item
    if action == "cancel":
        return 200, repository.cancel(queue_id) or item
    if action == "retry-stage":
        retried = repository.retry_stage(queue_id, str(payload.get("stage") or ""))
        return 200, retried or item
    return 404, structured_error("not_found", "Queue action not found")


def _dispatch_recipes(
    repository: AutomationRepository,
    method: str,
    recipe_id: str,
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    if recipe_id == "import":
        if method != "POST":
            return 405, structured_error("method_not_allowed", "Method not allowed")
        items = payload.get("items")
        if not isinstance(items, list):
            raise ValueError("items must be a list")
        imported = []
        created = 0
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("each recipe must be an object")
            recipe, was_created = repository.import_client_recipe(item.get("client_id"), item)
            imported.append(recipe)
            created += 1 if was_created else 0
        return 200, {
            "items": imported,
            "created": created,
            "existing": len(imported) - created,
        }
    if not recipe_id:
        if method == "GET":
            items = repository.list_recipes()
            return 200, {"items": items, "count": len(items)}
        if method == "POST":
            return 201, repository.create_recipe(payload)
    else:
        if method == "GET":
            recipe = repository.get_recipe(recipe_id)
            if recipe is None:
                return 404, structured_error("not_found", "Recipe not found")
            return 200, recipe
        if method == "POST":
            recipe = repository.update_recipe(recipe_id, payload)
            if recipe is None:
                return 404, structured_error("not_found", "Recipe not found")
            return 200, recipe
        if method == "DELETE":
            if not repository.delete_recipe(recipe_id):
                return 404, structured_error("not_found", "Recipe not found")
            return 200, {"deleted": True, "id": recipe_id}
    return 405, structured_error("method_not_allowed", "Method not allowed")


def record_job_revision(
    settings: Any,
    job_name: str,
    kind: str,
    payload: dict[str, Any],
    *,
    summary: str = "",
) -> dict[str, Any]:
    return repository_for(settings).create_revision(
        job_name,
        kind,
        payload,
        summary=summary,
    )


def _dispatch_revisions(
    repository: LibraryRepository,
    method: str,
    job_name: str,
    revision_id: str,
) -> tuple[int, dict[str, Any]]:
    if method != "GET":
        return 405, structured_error("method_not_allowed", "Method not allowed")
    if not revision_id:
        items = repository.list_revisions(job_name)
        return 200, {"items": items, "count": len(items)}
    revision = repository.get_revision(revision_id)
    if revision is None or revision["job_name"] != job_name:
        return 404, structured_error("not_found", "Revision not found")
    return 200, revision


def _dispatch_projects(
    repository: LibraryRepository,
    method: str,
    project_id: str,
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    if not project_id:
        if method == "GET":
            items = repository.list_projects(include_archived=bool(payload.get("include_archived")))
            return 200, {"items": items, "count": len(items)}
        if method == "POST":
            return 201, repository.create_project(payload)
    else:
        if method == "GET":
            project = repository.get_project(project_id)
            if project is None:
                return 404, structured_error("not_found", "Project not found")
            jobs = repository.list_indexed_jobs(project_id=project_id)
            return 200, {**project, "jobs": jobs, "job_count": len(jobs)}
        if method == "POST":
            project = repository.update_project(project_id, payload)
            if project is None:
                return 404, structured_error("not_found", "Project not found")
            return 200, project
        if method == "DELETE":
            deleted = repository.delete_project(project_id)
            if not deleted:
                return 404, structured_error("not_found", "Project not found")
            return 200, {"deleted": True, "id": project_id}
    return 405, structured_error("method_not_allowed", "Method not allowed")


def _dispatch_creator_kits(
    repository: LibraryRepository,
    method: str,
    kit_id: str,
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    if not kit_id:
        if method == "GET":
            items = repository.list_creator_kits()
            return 200, {"items": items, "count": len(items)}
        if method == "POST":
            return 201, repository.create_creator_kit(payload)
    else:
        if method == "GET":
            kit = repository.get_creator_kit(kit_id)
            if kit is None:
                return 404, structured_error("not_found", "Creator kit not found")
            return 200, kit
        if method == "POST":
            kit = repository.update_creator_kit(kit_id, payload)
            if kit is None:
                return 404, structured_error("not_found", "Creator kit not found")
            return 200, kit
        if method == "DELETE":
            deleted = repository.delete_creator_kit(kit_id)
            if not deleted:
                return 404, structured_error("not_found", "Creator kit not found")
            return 200, {"deleted": True, "id": kit_id}
    return 405, structured_error("method_not_allowed", "Method not allowed")
