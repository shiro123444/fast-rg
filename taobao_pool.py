"""淘宝邮箱池状态管理（支持并发消费与状态分类）"""

from __future__ import annotations

import fcntl
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

STATUS_NEW = "new"
STATUS_IN_USE = "in_use"
STATUS_USED = "used"
STATUS_FAILED = "failed"
STATUS_ABANDONED = "abandoned"

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _to_float_timestamp(value: str) -> Optional[float]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).timestamp()
    except Exception:
        return None


class TaobaoMailboxPool:
    def __init__(
        self,
        accounts_file: str,
        state_file: str = "",
        *,
        stale_in_use_seconds: int = 3600,
    ):
        self.accounts_file = Path(accounts_file).expanduser().resolve()
        if state_file:
            self.state_file = Path(state_file).expanduser().resolve()
        else:
            self.state_file = self.accounts_file.with_suffix(
                self.accounts_file.suffix + ".state.json"
            )
        self.lock_file = self.state_file.with_suffix(self.state_file.suffix + ".lock")
        self.stale_in_use_seconds = max(60, int(stale_in_use_seconds or 3600))

    @staticmethod
    def _key(email: str) -> str:
        return str(email or "").strip().lower()

    @staticmethod
    def _normalize_email(email: Any) -> str:
        return str(email or "").strip().lower()

    @staticmethod
    def _parse_line(raw_line: str) -> Optional[Dict[str, str]]:
        line = str(raw_line or "").strip()
        if not line or line.startswith("#"):
            return None

        # 兼容 hotmail007 原始格式: email:password:refresh_token:client_id
        parts = line.split(":")
        if len(parts) >= 4 and "@" in parts[0]:
            email = parts[0].strip().lower()
            client_id = parts[-1].strip()
            refresh_token = ":".join(parts[2:-1]).strip()
            password = parts[1].strip()
            if email and client_id and refresh_token:
                return {
                    "email": email,
                    "password": password,
                    "client_id": client_id,
                    "refresh_token": refresh_token,
                }

        for sep in ("----", "|", "\t", ","):
            if sep not in line:
                continue
            arr = [item.strip() for item in line.split(sep)]
            if len(arr) < 3 or "@" not in arr[0]:
                continue
            email = arr[0].strip().lower()
            password = ""
            client_id = ""
            refresh_token = ""

            # email----password----clientId----refreshToken
            if len(arr) >= 4 and _UUID_RE.fullmatch(arr[2]):
                password = arr[1]
                client_id = arr[2]
                refresh_token = sep.join(arr[3:]).strip()
            # email----clientId----refreshToken
            elif _UUID_RE.fullmatch(arr[1]):
                client_id = arr[1]
                refresh_token = sep.join(arr[2:]).strip()
            else:
                client_id = arr[1]
                refresh_token = sep.join(arr[2:]).strip()

            if email and client_id and refresh_token:
                return {
                    "email": email,
                    "password": password,
                    "client_id": client_id,
                    "refresh_token": refresh_token,
                }
        return None

    @classmethod
    def parse_bulk_text(cls, text: str) -> Dict[str, Any]:
        lines = str(text or "").splitlines()
        items: List[Dict[str, str]] = []
        invalid_lines: List[str] = []
        seen = set()

        for raw in lines:
            parsed = cls._parse_line(raw)
            if not parsed:
                if str(raw or "").strip() and not str(raw).strip().startswith("#"):
                    invalid_lines.append(str(raw).strip())
                continue
            key = cls._key(parsed["email"])
            if key in seen:
                continue
            seen.add(key)
            items.append(parsed)

        return {
            "items": items,
            "invalid_lines": invalid_lines,
            "total_lines": len(lines),
        }

    def _ensure_parent(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)

    def _load_state_unlocked(self) -> Dict[str, Any]:
        if not self.state_file.exists():
            return {"version": 1, "items": [], "updated_at": _now_iso()}
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        items = data.get("items")
        if not isinstance(items, list):
            items = []
        return {
            "version": 1,
            "items": [item for item in items if isinstance(item, dict)],
            "updated_at": str(data.get("updated_at") or _now_iso()),
        }

    def _save_state_unlocked(self, state: Dict[str, Any]) -> None:
        state["updated_at"] = _now_iso()
        payload = json.dumps(state, ensure_ascii=False, indent=2)
        tmp = self.state_file.with_suffix(self.state_file.suffix + ".tmp")
        tmp.write_text(payload + "\n", encoding="utf-8")
        os.replace(tmp, self.state_file)

    def _with_lock(self, handler):
        self._ensure_parent()
        with self.lock_file.open("a+", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                state = self._load_state_unlocked()
                changed, result = handler(state)
                if changed:
                    self._save_state_unlocked(state)
                return result
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    def _source_entries(self) -> List[Dict[str, str]]:
        if not self.accounts_file.exists():
            return []
        items: List[Dict[str, str]] = []
        seen = set()
        with self.accounts_file.open("r", encoding="utf-8") as f:
            for raw in f:
                parsed = self._parse_line(raw)
                if not parsed:
                    continue
                key = self._key(parsed["email"])
                if key in seen:
                    continue
                seen.add(key)
                items.append(parsed)
        return items

    def sync_from_source(self) -> Dict[str, Any]:
        source_items = self._source_entries()

        def _handler(state: Dict[str, Any]):
            items = state["items"]
            by_key = {
                self._key(str(item.get("email") or "")): item
                for item in items
                if str(item.get("email") or "").strip()
            }

            added = 0
            updated = 0
            now = _now_iso()
            for src in source_items:
                key = self._key(src["email"])
                row = by_key.get(key)
                if row is None:
                    items.append(
                        {
                            "email": src["email"],
                            "password": src.get("password") or "",
                            "client_id": src.get("client_id") or "",
                            "refresh_token": src.get("refresh_token") or "",
                            "status": STATUS_NEW,
                            "attempt_count": 0,
                            "success_count": 0,
                            "fail_count": 0,
                            "last_error": "",
                            "created_at": now,
                            "updated_at": now,
                        }
                    )
                    added += 1
                    continue

                changed_local = False
                for key_name in ("password", "client_id", "refresh_token"):
                    next_val = src.get(key_name) or ""
                    if str(row.get(key_name) or "") != next_val:
                        row[key_name] = next_val
                        changed_local = True
                if changed_local:
                    row["updated_at"] = now
                    updated += 1

            # 超时 in_use 自动回收为 failed，避免进程崩溃导致永久卡住。
            changed_stale = 0
            now_ts = datetime.now(timezone.utc).timestamp()
            for row in items:
                if str(row.get("status") or "") != STATUS_IN_USE:
                    continue
                in_use_at = _to_float_timestamp(str(row.get("in_use_at") or ""))
                if not in_use_at:
                    continue
                if now_ts - in_use_at < self.stale_in_use_seconds:
                    continue
                row["status"] = STATUS_FAILED
                row["last_error"] = "stale_in_use_timeout"
                row["updated_at"] = now
                row["in_use_at"] = ""
                row["fail_count"] = int(row.get("fail_count") or 0) + 1
                changed_stale += 1

            changed = added > 0 or updated > 0 or changed_stale > 0
            return changed, {
                "added": added,
                "updated": updated,
                "stale_recovered": changed_stale,
                "total": len(items),
            }

        return self._with_lock(_handler)

    def import_bulk_text(self, text: str) -> Dict[str, Any]:
        parsed = self.parse_bulk_text(text)
        incoming_items = parsed["items"]

        def _handler(state: Dict[str, Any]):
            items = state["items"]
            by_key = {
                self._key(str(item.get("email") or "")): item
                for item in items
                if str(item.get("email") or "").strip()
            }
            added = 0
            updated = 0
            duplicates = 0
            now = _now_iso()
            for incoming in incoming_items:
                key = self._key(incoming["email"])
                row = by_key.get(key)
                if row is None:
                    items.append(
                        {
                            "email": incoming["email"],
                            "password": incoming.get("password") or "",
                            "client_id": incoming.get("client_id") or "",
                            "refresh_token": incoming.get("refresh_token") or "",
                            "status": STATUS_NEW,
                            "attempt_count": 0,
                            "success_count": 0,
                            "fail_count": 0,
                            "last_error": "",
                            "created_at": now,
                            "updated_at": now,
                        }
                    )
                    added += 1
                    continue

                duplicates += 1
                changed_local = False
                for key_name in ("password", "client_id", "refresh_token"):
                    next_val = incoming.get(key_name) or ""
                    if str(row.get(key_name) or "") != next_val:
                        row[key_name] = next_val
                        changed_local = True
                if changed_local:
                    row["updated_at"] = now
                    updated += 1

            changed = added > 0 or updated > 0
            return changed, {
                "added": added,
                "updated": updated,
                "duplicates": duplicates,
                "invalid_lines": parsed["invalid_lines"],
                "total_lines": parsed["total_lines"],
                "total_pool": len(items),
            }

        return self._with_lock(_handler)

    def snapshot(self) -> Dict[str, Any]:
        self.sync_from_source()

        def _handler(state: Dict[str, Any]):
            items = state["items"]

            def _mask_token(token: str) -> str:
                text = str(token or "")
                if len(text) <= 12:
                    return "*" * len(text)
                return text[:6] + "..." + text[-4:]

            unused = []
            used = []
            failed = []
            abandoned = []
            in_use = []
            for row in items:
                output_row = {
                    "email": str(row.get("email") or ""),
                    "status": str(row.get("status") or STATUS_NEW),
                    "attempt_count": int(row.get("attempt_count") or 0),
                    "success_count": int(row.get("success_count") or 0),
                    "fail_count": int(row.get("fail_count") or 0),
                    "last_error": str(row.get("last_error") or ""),
                    "updated_at": str(row.get("updated_at") or ""),
                    "client_id": str(row.get("client_id") or ""),
                    "refresh_token_masked": _mask_token(str(row.get("refresh_token") or "")),
                }
                status = output_row["status"]
                if status == STATUS_USED:
                    used.append(output_row)
                elif status == STATUS_FAILED:
                    failed.append(output_row)
                elif status == STATUS_ABANDONED:
                    abandoned.append(output_row)
                elif status == STATUS_IN_USE:
                    in_use.append(output_row)
                else:
                    unused.append(output_row)

            return False, {
                "summary": {
                    "unused": len(unused),
                    "in_use": len(in_use),
                    "used": len(used),
                    "failed": len(failed),
                    "abandoned": len(abandoned),
                    "total": len(items),
                },
                "unused": unused,
                "in_use": in_use,
                "used": used,
                "failed": failed,
                "abandoned": abandoned,
                "state_file": str(self.state_file),
                "accounts_file": str(self.accounts_file),
            }

        return self._with_lock(_handler)

    def acquire_next_new(self) -> Optional[Dict[str, str]]:
        self.sync_from_source()

        def _handler(state: Dict[str, Any]):
            now = _now_iso()
            for row in state["items"]:
                if str(row.get("status") or "") != STATUS_NEW:
                    continue
                row["status"] = STATUS_IN_USE
                row["in_use_at"] = now
                row["updated_at"] = now
                row["attempt_count"] = int(row.get("attempt_count") or 0) + 1
                return True, {
                    "email": str(row.get("email") or ""),
                    "password": str(row.get("password") or ""),
                    "client_id": str(row.get("client_id") or ""),
                    "refresh_token": str(row.get("refresh_token") or ""),
                }
            return False, None

        return self._with_lock(_handler)

    def mark_success(self, email: str) -> bool:
        target = self._key(email)
        if not target:
            return False

        def _handler(state: Dict[str, Any]):
            now = _now_iso()
            for row in state["items"]:
                if self._key(str(row.get("email") or "")) != target:
                    continue
                row["status"] = STATUS_USED
                row["updated_at"] = now
                row["used_at"] = now
                row["in_use_at"] = ""
                row["last_error"] = ""
                row["success_count"] = int(row.get("success_count") or 0) + 1
                return True, True
            return False, False

        return bool(self._with_lock(_handler))

    def mark_failed(self, email: str, reason: str = "") -> bool:
        target = self._key(email)
        if not target:
            return False

        def _handler(state: Dict[str, Any]):
            now = _now_iso()
            for row in state["items"]:
                if self._key(str(row.get("email") or "")) != target:
                    continue
                row["status"] = STATUS_FAILED
                row["updated_at"] = now
                row["failed_at"] = now
                row["in_use_at"] = ""
                row["last_error"] = str(reason or "register_failed")
                row["fail_count"] = int(row.get("fail_count") or 0) + 1
                return True, True
            return False, False

        return bool(self._with_lock(_handler))

    def requeue(self, emails: List[str]) -> Dict[str, int]:
        targets = {self._key(item) for item in emails if self._key(item)}
        if not targets:
            return {"updated": 0, "missing": 0}

        def _handler(state: Dict[str, Any]):
            now = _now_iso()
            updated = 0
            matched = set()
            for row in state["items"]:
                key = self._key(str(row.get("email") or ""))
                if key not in targets:
                    continue
                matched.add(key)
                row["status"] = STATUS_NEW
                row["updated_at"] = now
                row["in_use_at"] = ""
                row["last_error"] = ""
                updated += 1
            missing = len(targets - matched)
            return updated > 0, {"updated": updated, "missing": missing}

        return self._with_lock(_handler)

    def abandon(self, emails: List[str]) -> Dict[str, int]:
        targets = {self._key(item) for item in emails if self._key(item)}
        if not targets:
            return {"updated": 0, "missing": 0}

        def _handler(state: Dict[str, Any]):
            now = _now_iso()
            updated = 0
            matched = set()
            for row in state["items"]:
                key = self._key(str(row.get("email") or ""))
                if key not in targets:
                    continue
                matched.add(key)
                row["status"] = STATUS_ABANDONED
                row["updated_at"] = now
                row["in_use_at"] = ""
                updated += 1
            missing = len(targets - matched)
            return updated > 0, {"updated": updated, "missing": missing}

        return self._with_lock(_handler)
