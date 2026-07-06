from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _identifier() -> str:
    return f"recipe_{uuid.uuid4().hex}"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _decode(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


class AutomationRepository:
    """Persistent automation recipes stored beside the Phase 1 library metadata."""

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
                    CREATE TABLE IF NOT EXISTS automation_recipes (
                        id TEXT PRIMARY KEY,
                        client_id TEXT UNIQUE,
                        name TEXT NOT NULL,
                        stages_json TEXT NOT NULL DEFAULT '[]',
                        options_json TEXT NOT NULL DEFAULT '{}',
                        creator_kit_id TEXT,
                        target_platforms_json TEXT NOT NULL DEFAULT '[]',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_automation_recipes_updated
                    ON automation_recipes(updated_at DESC, name COLLATE NOCASE);
                    """
                )
            self._initialized = True

    def create_recipe(self, values: dict[str, Any], *, client_id: str | None = None) -> dict[str, Any]:
        normalized = self._normalize(values)
        recipe_id = str(values.get("id") or _identifier())
        created_at = _now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO automation_recipes(
                    id, client_id, name, stages_json, options_json, creator_kit_id,
                    target_platforms_json, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    recipe_id,
                    str(client_id).strip() if client_id else None,
                    normalized["name"],
                    _json(normalized["stages"]),
                    _json(normalized["options"]),
                    normalized["creator_kit_id"],
                    _json(normalized["target_platforms"]),
                    created_at,
                    created_at,
                ),
            )
        return self.get_recipe(recipe_id) or {}

    def get_recipe(self, recipe_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM automation_recipes WHERE id = ?", (recipe_id,)
            ).fetchone()
        return self._payload(row) if row else None

    def list_recipes(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM automation_recipes ORDER BY updated_at DESC, name COLLATE NOCASE"
            ).fetchall()
        return [self._payload(row) for row in rows]

    def update_recipe(self, recipe_id: str, values: dict[str, Any]) -> dict[str, Any] | None:
        current = self.get_recipe(recipe_id)
        if current is None:
            return None
        normalized = self._normalize({**current, **values})
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE automation_recipes
                SET name = ?, stages_json = ?, options_json = ?, creator_kit_id = ?,
                    target_platforms_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    normalized["name"],
                    _json(normalized["stages"]),
                    _json(normalized["options"]),
                    normalized["creator_kit_id"],
                    _json(normalized["target_platforms"]),
                    _now(),
                    recipe_id,
                ),
            )
        return self.get_recipe(recipe_id)

    def delete_recipe(self, recipe_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM automation_recipes WHERE id = ?", (recipe_id,))
        return cursor.rowcount > 0

    def import_client_recipe(
        self, client_id: str, values: dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        normalized_client_id = str(client_id or "").strip()
        if not normalized_client_id:
            raise ValueError("client_id is required")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM automation_recipes WHERE client_id = ?",
                (normalized_client_id,),
            ).fetchone()
        if row:
            return self._payload(row), False
        try:
            return self.create_recipe(values, client_id=normalized_client_id), True
        except sqlite3.IntegrityError:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM automation_recipes WHERE client_id = ?",
                    (normalized_client_id,),
                ).fetchone()
            if row:
                return self._payload(row), False
            raise

    @staticmethod
    def _normalize(values: dict[str, Any]) -> dict[str, Any]:
        name = str(values.get("name") or "").strip()
        if not name:
            raise ValueError("recipe name is required")
        stages = values.get("stages", [])
        options = values.get("options", {})
        target_platforms = values.get("target_platforms", [])
        if not isinstance(stages, list) or not all(isinstance(item, str) for item in stages):
            raise ValueError("stages must be a list of strings")
        if not isinstance(options, dict):
            raise ValueError("options must be an object")
        if not isinstance(target_platforms, list) or not all(
            isinstance(item, str) for item in target_platforms
        ):
            raise ValueError("target_platforms must be a list of strings")
        return {
            "name": name[:120],
            "stages": [item.strip() for item in stages if item.strip()],
            "options": options,
            "creator_kit_id": str(values.get("creator_kit_id") or "").strip() or None,
            "target_platforms": [item.strip() for item in target_platforms if item.strip()],
        }

    @staticmethod
    def _payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "client_id": row["client_id"],
            "name": row["name"],
            "stages": _decode(row["stages_json"], []),
            "options": _decode(row["options_json"], {}),
            "creator_kit_id": row["creator_kit_id"],
            "target_platforms": _decode(row["target_platforms_json"], []),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
