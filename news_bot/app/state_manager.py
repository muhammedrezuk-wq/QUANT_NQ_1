from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from app.jsonio import write_json_atomic


class StateManager:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)
        self.path = os.path.join(self.data_dir, "system_state.json")
        self._ensure_file()

    def _ensure_file(self) -> None:
        if not os.path.exists(self.path):
            self._write({
                "bot_running": False,
                "scheduler_running": False,
                "event_lock": False,
                "macro_bridge_available": False,
                "last_news_fetch": None,
                "last_news_sent": None,
                "last_morning_report": None,
                "last_error": None,
                "last_heartbeat": None,
            })

    def _read(self) -> dict[str, Any]:
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write(self, data: dict[str, Any]) -> None:
        # يُكتب عشرات المرّات بالساعة — فالكتابة الذرّية هنا ليست ترفًا.
        write_json_atomic(self.path, data, indent=2)

    def set(self, key: str, value: Any) -> None:
        data = self._read()
        data[key] = value
        self._write(data)

    def touch(self, key: str) -> None:
        self.set(key, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    def get_all(self) -> dict[str, Any]:
        return self._read()
