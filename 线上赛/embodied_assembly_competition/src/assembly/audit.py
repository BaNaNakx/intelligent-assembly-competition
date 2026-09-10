'Timestamped JSONL and human-readable auditing required by the project book.'

from __future__ import annotations

import json
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any

from .models import AuditEvent


class AuditLogger:

    'Writes every audit event to machine-readable and display-friendly files.'
    def __init__(self, log_dir: Path, run_name: str) -> None:
        log_dir.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = log_dir / f"{run_name}.jsonl"
        self.text_path = log_dir / f"{run_name}.log"
        self._lock = Lock()

    def write(self, event: AuditEvent) -> None:
        payload = {
            "occurred_at": event.occurred_at.astimezone().isoformat(timespec="milliseconds"),
            "event_type": event.event_type,
            "state": event.state.value,
            "message": event.message,
            "data": event.data,
        }
        text_line = (
            f"{payload['occurred_at']} [{payload['state']}] "
            f"{event.event_type}: {event.message} "
            f"{json.dumps(event.data, ensure_ascii=False, default=_json_default)}\n"
        )
        with self._lock:
            with self.jsonl_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(
                    json.dumps(payload, ensure_ascii=False, default=_json_default) + "\n"
                )
            with self.text_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(text_line)


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"不能写入日志的类型：{type(value).__name__}")
