from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from video_automation.events import (
    configure_event_store,
    current_event_id,
    publish_event,
    wait_for_events,
)


class PersistentEventStoreTests(unittest.TestCase):
    def tearDown(self) -> None:
        configure_event_store(None)

    def test_sqlite_event_outbox_is_readable_by_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "library.sqlite3"
            configure_event_store(database)
            previous_id = current_event_id()
            publish_event("job", {"id": "job-1", "status": "running"})

            events = wait_for_events(previous_id, timeout_seconds=0.2)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].type, "job")
            self.assertEqual(events[0].payload["id"], "job-1")
            connection = sqlite3.connect(database)
            try:
                count = connection.execute("SELECT COUNT(*) FROM server_events").fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
