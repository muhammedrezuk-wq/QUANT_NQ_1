from __future__ import annotations

import json
import os
from typing import Any

from app.jsonio import write_json_atomic


class JsonStorage:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)
        self.users_path = os.path.join(self.data_dir, "users.json")
        self.settings_path = os.path.join(self.data_dir, "settings.json")
        self._ensure_files()

    def _ensure_files(self) -> None:
        if not os.path.exists(self.users_path):
            self._write_json(self.users_path, {"owner_chat_id": 0, "allowed_users": []})
        if not os.path.exists(self.settings_path):
            self._write_json(self.settings_path, {})

    def _read_json(self, path: str) -> dict[str, Any]:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_json(self, path: str, data: dict[str, Any]) -> None:
        # كتابة ذرّية: `users.json` مبتور = بوت بلا مالك ولا مستخدمين.
        write_json_atomic(path, data, indent=2)

    def get_users(self) -> dict[str, Any]:
        return self._read_json(self.users_path)

    def set_owner_if_empty(self, owner_chat_id: int) -> None:
        users = self.get_users()
        if users.get("owner_chat_id", 0) in (0, "0") and owner_chat_id:
            users["owner_chat_id"] = owner_chat_id
            if owner_chat_id not in users.get("allowed_users", []):
                users.setdefault("allowed_users", []).append(owner_chat_id)
            self._write_json(self.users_path, users)

    def is_owner(self, chat_id: int) -> bool:
        users = self.get_users()
        return int(users.get("owner_chat_id", 0)) == int(chat_id)

    def is_allowed(self, chat_id: int) -> bool:
        users = self.get_users()
        return int(chat_id) in [int(x) for x in users.get("allowed_users", [])]

    def add_user(self, chat_id: int) -> bool:
        users = self.get_users()
        allowed = [int(x) for x in users.get("allowed_users", [])]
        if int(chat_id) in allowed:
            return False
        allowed.append(int(chat_id))
        users["allowed_users"] = allowed
        self._write_json(self.users_path, users)
        return True

    def remove_user(self, chat_id: int) -> bool:
        users = self.get_users()
        allowed = [int(x) for x in users.get("allowed_users", [])]
        if int(chat_id) not in allowed:
            return False
        if int(users.get("owner_chat_id", 0)) == int(chat_id):
            return False
        allowed.remove(int(chat_id))
        users["allowed_users"] = allowed
        self._write_json(self.users_path, users)
        return True

    def list_users(self) -> list[int]:
        users = self.get_users()
        return [int(x) for x in users.get("allowed_users", [])]
