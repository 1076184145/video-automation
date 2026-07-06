from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator


SCHEMA_VERSION = 2


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _json(value: Any, fallback: Any) -> str:
    return json.dumps(fallback if value is None else value, ensure_ascii=False, separators=(",", ":"))


def _decode(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (TypeError, ValueError):
        return fallback


def _identifier(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class LibraryRepository:
    """SQLite metadata index; video artifacts remain in job directories."""

    def __init__(self, database_path: Path | str):
        self.database_path = Path(database_path)
        self._schema_lock = threading.Lock()
        self._initialized = False
        self._ensure_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        if self._initialized:
            return
        with self._schema_lock:
            if self._initialized:
                return
            with self._connect() as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS library_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS creator_kits (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        platform TEXT NOT NULL DEFAULT '',
                        aspect TEXT NOT NULL DEFAULT '',
                        subtitle_style_json TEXT NOT NULL DEFAULT '{}',
                        cover_style_json TEXT NOT NULL DEFAULT '{}',
                        metadata_style_json TEXT NOT NULL DEFAULT '{}',
                        hotwords_json TEXT NOT NULL DEFAULT '[]',
                        replacements_json TEXT NOT NULL DEFAULT '{}',
                        outro_json TEXT NOT NULL DEFAULT '{}',
                        default_recipe_id TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS projects (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        description TEXT NOT NULL DEFAULT '',
                        tags_json TEXT NOT NULL DEFAULT '[]',
                        default_kit_id TEXT REFERENCES creator_kits(id) ON DELETE SET NULL,
                        archived INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS creator_kit_snapshots (
                        id TEXT PRIMARY KEY,
                        creator_kit_id TEXT REFERENCES creator_kits(id) ON DELETE SET NULL,
                        payload_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS job_index (
                        job_name TEXT PRIMARY KEY,
                        job_dir TEXT NOT NULL UNIQUE,
                        source_path TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT 'pending',
                        project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
                        creator_kit_snapshot_id TEXT REFERENCES creator_kit_snapshots(id) ON DELETE SET NULL,
                        recipe_id TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS revisions (
                        id TEXT PRIMARY KEY,
                        job_name TEXT NOT NULL,
                        revision INTEGER NOT NULL,
                        kind TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        summary TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        UNIQUE(job_name, revision)
                    );

                    CREATE INDEX IF NOT EXISTS idx_job_index_project ON job_index(project_id, updated_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_job_index_status ON job_index(status, updated_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_revisions_job ON revisions(job_name, revision DESC);
                    """
                )
                connection.execute(
                    "INSERT INTO library_meta(key, value) VALUES('schema_version', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (str(SCHEMA_VERSION),),
                )
            self._initialized = True

    def create_project(self, values: dict[str, Any]) -> dict[str, Any]:
        name = str(values.get("name") or "").strip()
        if not name:
            raise ValueError("project name is required")
        project_id = str(values.get("id") or _identifier("project"))
        created_at = _now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO projects(id, name, description, tags_json, default_kit_id, archived, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    name[:120],
                    str(values.get("description") or "")[:2000],
                    _json(values.get("tags"), []),
                    values.get("default_kit_id") or None,
                    1 if values.get("archived") else 0,
                    created_at,
                    created_at,
                ),
            )
        return self.get_project(project_id) or {}

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return self._project_payload(row) if row else None

    def list_projects(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM projects"
        params: tuple[Any, ...] = ()
        if not include_archived:
            query += " WHERE archived = 0"
        query += " ORDER BY updated_at DESC, name COLLATE NOCASE"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._project_payload(row) for row in rows]

    def update_project(self, project_id: str, values: dict[str, Any]) -> dict[str, Any] | None:
        current = self.get_project(project_id)
        if current is None:
            return None
        name = str(values.get("name", current["name"]) or "").strip()
        if not name:
            raise ValueError("project name is required")
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE projects
                SET name = ?, description = ?, tags_json = ?, default_kit_id = ?, archived = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    name[:120],
                    str(values.get("description", current["description"]))[:2000],
                    _json(values.get("tags", current["tags"]), []),
                    values.get("default_kit_id", current["default_kit_id"]) or None,
                    1 if values.get("archived", current["archived"]) else 0,
                    _now(),
                    project_id,
                ),
            )
        return self.get_project(project_id)

    def delete_project(self, project_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        return cursor.rowcount > 0

    def create_creator_kit(self, values: dict[str, Any]) -> dict[str, Any]:
        name = str(values.get("name") or "").strip()
        if not name:
            raise ValueError("creator kit name is required")
        kit_id = str(values.get("id") or _identifier("kit"))
        created_at = _now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO creator_kits(
                    id, name, platform, aspect, subtitle_style_json, cover_style_json,
                    metadata_style_json, hotwords_json, replacements_json, outro_json,
                    default_recipe_id, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    kit_id,
                    name[:120],
                    str(values.get("platform") or "")[:40],
                    str(values.get("aspect") or "")[:20],
                    _json(values.get("subtitle_style"), {}),
                    _json(values.get("cover_style"), {}),
                    _json(values.get("metadata_style"), {}),
                    _json(values.get("hotwords"), []),
                    _json(values.get("replacements"), {}),
                    _json(values.get("outro"), {}),
                    values.get("default_recipe_id") or None,
                    created_at,
                    created_at,
                ),
            )
        return self.get_creator_kit(kit_id) or {}

    def get_creator_kit(self, kit_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM creator_kits WHERE id = ?", (kit_id,)).fetchone()
        return self._kit_payload(row) if row else None

    def list_creator_kits(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM creator_kits ORDER BY updated_at DESC, name COLLATE NOCASE"
            ).fetchall()
        return [self._kit_payload(row) for row in rows]

    def update_creator_kit(self, kit_id: str, values: dict[str, Any]) -> dict[str, Any] | None:
        current = self.get_creator_kit(kit_id)
        if current is None:
            return None
        name = str(values.get("name", current["name"]) or "").strip()
        if not name:
            raise ValueError("creator kit name is required")
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE creator_kits SET
                    name = ?, platform = ?, aspect = ?, subtitle_style_json = ?, cover_style_json = ?,
                    metadata_style_json = ?, hotwords_json = ?, replacements_json = ?, outro_json = ?,
                    default_recipe_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    name[:120],
                    str(values.get("platform", current["platform"]))[:40],
                    str(values.get("aspect", current["aspect"]))[:20],
                    _json(values.get("subtitle_style", current["subtitle_style"]), {}),
                    _json(values.get("cover_style", current["cover_style"]), {}),
                    _json(values.get("metadata_style", current["metadata_style"]), {}),
                    _json(values.get("hotwords", current["hotwords"]), []),
                    _json(values.get("replacements", current["replacements"]), {}),
                    _json(values.get("outro", current["outro"]), {}),
                    values.get("default_recipe_id", current["default_recipe_id"]) or None,
                    _now(),
                    kit_id,
                ),
            )
        return self.get_creator_kit(kit_id)

    def delete_creator_kit(self, kit_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM creator_kits WHERE id = ?", (kit_id,))
        return cursor.rowcount > 0

    def snapshot_creator_kit(self, kit_id: str) -> dict[str, Any]:
        kit = self.get_creator_kit(kit_id)
        if kit is None:
            raise ValueError("creator kit not found")
        snapshot_id = _identifier("kit_snapshot")
        created_at = _now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO creator_kit_snapshots(id, creator_kit_id, payload_json, created_at) VALUES(?, ?, ?, ?)",
                (snapshot_id, kit_id, _json(kit, {}), created_at),
            )
        return self.get_creator_kit_snapshot(snapshot_id) or {}

    def get_creator_kit_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM creator_kit_snapshots WHERE id = ?", (snapshot_id,)
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "creator_kit_id": row["creator_kit_id"],
            "payload": _decode(row["payload_json"], {}),
            "created_at": row["created_at"],
        }

    def index_existing_jobs(self, jobs_dir: Path) -> int:
        inserted = 0
        for state_path in sorted(Path(jobs_dir).glob("*/job.json")):
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            job_name = state_path.parent.name
            if self.index_job(
                job_name,
                job_dir=state_path.parent,
                source_path=str(state.get("source_path") or ""),
                status=str(state.get("status") or "pending"),
                created_at=str(state.get("created_at") or _now()),
                updated_at=str(state.get("updated_at") or _now()),
            ):
                inserted += 1
        return inserted

    def index_job(
        self,
        job_name: str,
        *,
        job_dir: Path | str,
        source_path: Path | str = "",
        status: str = "pending",
        created_at: str | None = None,
        updated_at: str | None = None,
    ) -> bool:
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM job_index WHERE job_name = ?", (job_name,)
            ).fetchone()
            connection.execute(
                """
                INSERT INTO job_index(job_name, job_dir, source_path, status, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_name) DO UPDATE SET
                    job_dir = excluded.job_dir,
                    source_path = excluded.source_path,
                    status = excluded.status,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at
                """,
                (
                    job_name,
                    str(Path(job_dir).resolve()),
                    str(source_path),
                    status,
                    created_at or _now(),
                    updated_at or _now(),
                ),
            )
        return exists is None

    def assign_job(
        self,
        job_name: str,
        *,
        project_id: str | None = None,
        creator_kit_snapshot_id: str | None = None,
        recipe_id: str | None = None,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE job_index
                SET project_id = ?, creator_kit_snapshot_id = ?, recipe_id = ?
                WHERE job_name = ?
                """,
                (project_id, creator_kit_snapshot_id, recipe_id, job_name),
            )
        return self.get_indexed_job(job_name)

    def get_indexed_job(self, job_name: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM job_index WHERE job_name = ?", (job_name,)).fetchone()
        return dict(row) if row else None

    def list_indexed_jobs(self, *, project_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM job_index"
        params: Iterable[Any] = ()
        if project_id:
            query += " WHERE project_id = ?"
            params = (project_id,)
        query += " ORDER BY updated_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def create_revision(
        self,
        job_name: str,
        kind: str,
        payload: dict[str, Any],
        *,
        summary: str = "",
    ) -> dict[str, Any]:
        normalized_job = str(job_name or "").strip()
        normalized_kind = str(kind or "").strip()
        if not normalized_job:
            raise ValueError("job name is required")
        if normalized_kind not in {"cuts", "transcript"}:
            raise ValueError("revision kind must be cuts or transcript")
        revision_id = _identifier("revision")
        created_at = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT COALESCE(MAX(revision), 0) + 1 AS next_revision FROM revisions WHERE job_name = ?",
                (normalized_job,),
            ).fetchone()
            revision = int(row["next_revision"])
            connection.execute(
                """
                INSERT INTO revisions(id, job_name, revision, kind, payload_json, summary, created_at)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    revision_id,
                    normalized_job,
                    revision,
                    normalized_kind,
                    _json(payload, {}),
                    str(summary or "")[:240],
                    created_at,
                ),
            )
        return self.get_revision(revision_id) or {}

    def get_revision(self, revision_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM revisions WHERE id = ?", (revision_id,)
            ).fetchone()
        return self._revision_payload(row, include_payload=True) if row else None

    def list_revisions(self, job_name: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM revisions WHERE job_name = ? ORDER BY revision DESC",
                (job_name,),
            ).fetchall()
        return [self._revision_payload(row, include_payload=False) for row in rows]

    def latest_revision_number(self, job_name: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(revision), 0) AS revision FROM revisions WHERE job_name = ?",
                (job_name,),
            ).fetchone()
        return int(row["revision"] if row else 0)

    def get_job_contexts(self, job_names: Iterable[str]) -> dict[str, dict[str, Any]]:
        requested = {str(name) for name in job_names}
        if not requested:
            return {}
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT job_index.*,
                       COALESCE((
                           SELECT MAX(revisions.revision)
                           FROM revisions
                           WHERE revisions.job_name = job_index.job_name
                       ), 0) AS revision
                FROM job_index
                """
            ).fetchall()
        return {row["job_name"]: dict(row) for row in rows if row["job_name"] in requested}

    @staticmethod
    def _project_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "tags": _decode(row["tags_json"], []),
            "default_kit_id": row["default_kit_id"],
            "archived": bool(row["archived"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _kit_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "platform": row["platform"],
            "aspect": row["aspect"],
            "subtitle_style": _decode(row["subtitle_style_json"], {}),
            "cover_style": _decode(row["cover_style_json"], {}),
            "metadata_style": _decode(row["metadata_style_json"], {}),
            "hotwords": _decode(row["hotwords_json"], []),
            "replacements": _decode(row["replacements_json"], {}),
            "outro": _decode(row["outro_json"], {}),
            "default_recipe_id": row["default_recipe_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _revision_payload(row: sqlite3.Row, *, include_payload: bool) -> dict[str, Any]:
        payload = {
            "id": row["id"],
            "job_name": row["job_name"],
            "revision": int(row["revision"]),
            "kind": row["kind"],
            "summary": row["summary"],
            "created_at": row["created_at"],
        }
        if include_payload:
            payload["payload"] = _decode(row["payload_json"], {})
        return payload
