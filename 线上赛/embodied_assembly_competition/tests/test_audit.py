from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from assembly.audit import AuditLogger
from assembly.models import AuditEvent, TaskState


class AuditLoggerTests(unittest.TestCase):
    def test_writes_timestamped_jsonl_and_text_logs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            logger = AuditLogger(Path(temp_dir), "unit")
            timestamp = datetime(2026, 8, 1, 10, 30, 15, 123000, tzinfo=timezone.utc)
            logger.write(
                AuditEvent(
                    occurred_at=timestamp,
                    event_type="model_result",
                    state=TaskState.TASK1_RECOGNIZING,
                    message="完成场景解析",
                    data={"scene": ["齿轮", "水杯"]},
                )
            )

            payload = json.loads(logger.jsonl_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["event_type"], "model_result")
            parsed_timestamp = datetime.fromisoformat(payload["occurred_at"])
            self.assertIsNotNone(parsed_timestamp.tzinfo)
            self.assertIn("完成场景解析", logger.text_path.read_text(encoding="utf-8"))
