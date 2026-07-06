from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from video_automation.recovery import backup_database, ensure_database_ready


class RecoveryTests(unittest.TestCase):
    def test_corrupt_database_is_quarantined_and_latest_backup_is_restored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "library.sqlite3"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("CREATE TABLE sample(value TEXT)")
                connection.execute("INSERT INTO sample VALUES('safe')")
                connection.commit()
            backup = backup_database(database, keep=3)
            self.assertTrue(backup.is_file())

            database.write_bytes(b"not-a-sqlite-database")
            result = ensure_database_ready(database)

            self.assertEqual(result["status"], "restored")
            self.assertTrue(Path(result["quarantined_path"]).is_file())
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(connection.execute("SELECT value FROM sample").fetchone()[0], "safe")

    def test_rolling_backups_keep_only_requested_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "library.sqlite3"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("CREATE TABLE sample(value INTEGER)")
                connection.commit()
            for _ in range(5):
                backup_database(database, keep=2)
            self.assertLessEqual(len(list((Path(tmp) / "backups").glob("library-*.sqlite3"))), 2)


if __name__ == "__main__":
    unittest.main()
