#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import math
import os
import re
import secrets
import signal
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from pathlib import Path
from typing import Any, Dict, List, Optional

from auto_pool_maintainer import get_candidates_count
from taobao_pool import TaobaoMailboxPool

PROJECT_ROOT = Path(__file__).resolve().parent
APP_DATA_DIR = Path(os.environ.get("APP_DATA_DIR", str(PROJECT_ROOT)))
CONFIG_PATH = Path(os.environ.get("APP_CONFIG_PATH", str(APP_DATA_DIR / "config.json")))
LOGS_DIR = Path(os.environ.get("APP_LOG_DIR", str(APP_DATA_DIR / "logs")))
TEMPLATE_CONFIG_PATH = Path(
    os.environ.get(
        "APP_TEMPLATE_CONFIG_PATH", str(PROJECT_ROOT / "config.example.json")
    )
)
API_HOST = os.environ.get("APP_HOST", "127.0.0.1")
API_PORT = int(os.environ.get("APP_PORT", "8318"))
ADMIN_TOKEN_ENV = os.environ.get("APP_ADMIN_TOKEN", "").strip()
ADMIN_TOKEN_FILE = Path(
    os.environ.get("APP_ADMIN_TOKEN_FILE", str(APP_DATA_DIR / "admin_token.txt"))
)
RUN_STATE_FILE = Path(
    os.environ.get(
        "APP_RUN_STATE_FILE", str(APP_DATA_DIR / ".maintainer_run_state.json")
    )
)
MASKED_VALUE = "__MASKED__"
RUN_PROCESS: Optional[subprocess.Popen[str]] = None
RUN_MODE: str = ""
RUN_LOG_PATH: str = ""
RUN_PROCESS_LOCK = threading.Lock()
ADMIN_TOKEN_LOCK = threading.Lock()
ADMIN_TOKEN_CACHE: Optional[str] = None
FRONTEND_DIST_DIR = PROJECT_ROOT / "frontend" / "dist"


def ensure_runtime_paths() -> None:
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    ADMIN_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    RUN_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)


def load_run_state() -> Dict[str, Any]:
    ensure_runtime_paths()
    if not RUN_STATE_FILE.exists():
        return {}
    try:
        data = json.loads(RUN_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_run_state(pid: int, mode: str, log_path: str = "") -> None:
    ensure_runtime_paths()
    payload = {
        "pid": int(pid),
        "mode": str(mode or ""),
        "log_path": str(log_path or ""),
        "updated_at": datetime.now().isoformat(),
    }
    RUN_STATE_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def clear_run_state() -> None:
    try:
        RUN_STATE_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


def read_running_state() -> tuple[Optional[int], str, str]:
    state = load_run_state()
    raw_pid = state.get("pid")
    mode = str(state.get("mode") or "")
    log_path = str(state.get("log_path") or "")
    try:
        pid = int(raw_pid)
    except Exception:
        return None, mode, log_path
    if not is_pid_running(pid):
        clear_run_state()
        return None, mode, log_path
    return pid, mode, log_path


def terminate_pid(pid: int, timeout_seconds: float = 8.0) -> bool:
    if not is_pid_running(pid):
        return True
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except Exception:
        return False

    deadline = time.time() + max(0.5, timeout_seconds)
    while time.time() < deadline:
        if not is_pid_running(pid):
            return True
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except Exception:
        return False
    return not is_pid_running(pid)


def ensure_config_exists() -> None:
    ensure_runtime_paths()
    if CONFIG_PATH.exists():
        return
    if TEMPLATE_CONFIG_PATH.exists():
        shutil.copyfile(TEMPLATE_CONFIG_PATH, CONFIG_PATH)
        return
    raise RuntimeError(
        f"配置文件不存在，且模板不存在: {CONFIG_PATH} | {TEMPLATE_CONFIG_PATH}"
    )


def get_admin_token() -> str:
    global ADMIN_TOKEN_CACHE

    with ADMIN_TOKEN_LOCK:
        if ADMIN_TOKEN_CACHE:
            return ADMIN_TOKEN_CACHE

        if ADMIN_TOKEN_ENV:
            ADMIN_TOKEN_CACHE = ADMIN_TOKEN_ENV
            return ADMIN_TOKEN_CACHE

        ensure_runtime_paths()
        if ADMIN_TOKEN_FILE.exists():
            token = ADMIN_TOKEN_FILE.read_text(encoding="utf-8").strip()
            if token:
                ADMIN_TOKEN_CACHE = token
                return ADMIN_TOKEN_CACHE

        token = secrets.token_urlsafe(32)
        ADMIN_TOKEN_FILE.write_text(f"{token}\n", encoding="utf-8")
        try:
            os.chmod(ADMIN_TOKEN_FILE, 0o600)
        except OSError:
            pass
        ADMIN_TOKEN_CACHE = token
        return ADMIN_TOKEN_CACHE


def load_config() -> Dict[str, Any]:
    ensure_config_exists()
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise RuntimeError("config.json 顶层必须是 JSON 对象")
    return data


def save_config(payload: Dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise RuntimeError("配置数据必须是 JSON 对象")
    ensure_runtime_paths()
    merged = merge_config_with_sensitive_fields(load_config(), payload)
    with CONFIG_PATH.open("w", encoding="utf-8") as handle:
        json.dump(merged, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def mask_sensitive_config(config: Dict[str, Any]) -> Dict[str, Any]:
    masked = json.loads(json.dumps(config))
    sensitive_fields = [
        ("clean", "token"),
        ("luckmail", "api_key"),
        ("hotmail007", "api_key"),
        ("outlook_api", "refresh_token"),
        ("cfmail", "admin_password"),
    ]
    for section, key in sensitive_fields:
        sec = masked.get(section)
        if isinstance(sec, dict) and sec.get(key):
            sec[key] = MASKED_VALUE

    pools = masked.get("cpa_pools")
    if isinstance(pools, list):
        for item in pools:
            if not isinstance(item, dict):
                continue
            token = item.get("token")
            if isinstance(token, str) and token.strip():
                item["token"] = MASKED_VALUE
    return masked


def merge_config_with_sensitive_fields(
    current: Dict[str, Any], incoming: Dict[str, Any]
) -> Dict[str, Any]:
    merged = json.loads(json.dumps(incoming))
    sensitive_fields = [
        ("clean", "token"),
        ("luckmail", "api_key"),
        ("hotmail007", "api_key"),
        ("outlook_api", "refresh_token"),
        ("cfmail", "admin_password"),
    ]
    for section, key in sensitive_fields:
        current_section = (
            current.get(section) if isinstance(current.get(section), dict) else {}
        )
        merged_section = (
            merged.get(section) if isinstance(merged.get(section), dict) else {}
        )
        if merged_section.get(key) == MASKED_VALUE and key in current_section:
            merged_section[key] = current_section.get(key)
        merged[section] = merged_section

    current_pools = (
        current.get("cpa_pools") if isinstance(current.get("cpa_pools"), list) else []
    )
    merged_pools = (
        merged.get("cpa_pools") if isinstance(merged.get("cpa_pools"), list) else []
    )
    if isinstance(current_pools, list) and isinstance(merged_pools, list):
        index_map: Dict[str, Dict[str, Any]] = {}
        for item in current_pools:
            if not isinstance(item, dict):
                continue
            key = f"{str(item.get('name') or '').strip()}|{str(item.get('base_url') or '').strip()}"
            if key != "|":
                index_map[key] = item

        for item in merged_pools:
            if not isinstance(item, dict):
                continue
            if item.get("token") != MASKED_VALUE:
                continue
            key = f"{str(item.get('name') or '').strip()}|{str(item.get('base_url') or '').strip()}"
            old = index_map.get(key)
            if isinstance(old, dict):
                old_token = old.get("token")
                if isinstance(old_token, str):
                    item["token"] = old_token
                else:
                    item["token"] = ""
            else:
                item["token"] = ""
        merged["cpa_pools"] = merged_pools

    return merged


def _resolve_taobao_accounts_file(config: Optional[Dict[str, Any]] = None) -> Path:
    cfg = config or load_config()
    outlook_conf = cfg.get("outlook_api") if isinstance(cfg.get("outlook_api"), dict) else {}
    raw_path = str((outlook_conf or {}).get("accounts_file") or "outlook_accounts.txt").strip()
    if not raw_path:
        raw_path = "outlook_accounts.txt"
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    return path


def _taobao_pool(config: Optional[Dict[str, Any]] = None) -> TaobaoMailboxPool:
    return TaobaoMailboxPool(str(_resolve_taobao_accounts_file(config=config)))


def _coerce_email_list(payload: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    seen = set()
    raw_list = payload.get("emails")
    if isinstance(raw_list, list):
        for item in raw_list:
            email = str(item or "").strip().lower()
            if not email or email in seen:
                continue
            seen.add(email)
            out.append(email)

    raw_text = str(payload.get("emails_text") or "").strip()
    if raw_text:
        for line in raw_text.splitlines():
            email = str(line or "").strip().lower()
            if not email or email in seen:
                continue
            seen.add(email)
            out.append(email)
    return out


def is_sensitive_field_masked(value: Any) -> bool:
    return isinstance(value, str) and value == MASKED_VALUE


def get_latest_log_path() -> Optional[Path]:
    ensure_runtime_paths()
    if not LOGS_DIR.exists():
        return None
    candidates = sorted(
        LOGS_DIR.glob("pool_maintainer_*.log"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def tail_lines(path: Path, max_lines: int = 120) -> List[str]:
    buffer: deque[str] = deque(maxlen=max_lines)
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if line.strip():
                buffer.append(line)
    return list(buffer)


def tone_from_log(level: str, message: str) -> str:
    normalized_level = level.upper()
    normalized_message = message.lower()
    if normalized_level in {"ERROR", "CRITICAL"}:
        return "danger"
    if normalized_level == "WARNING":
        return "warning"
    if "成功" in message or "完成" in message or "已达标" in message:
        return "success"
    if "失败" in message or "异常" in message or "错误" in message:
        return "danger"
    if "等待" in message or "进度" in message:
        return "info"
    if "warning" in normalized_message:
        return "warning"
    return "muted"


def parse_log_line(index: int, raw_line: str) -> Dict[str, Any]:
    match = re.match(
        r"^(?P<date>\d{4}-\d{2}-\d{2}) (?P<clock>\d{2}:\d{2}:\d{2}) \| (?P<level>[A-Z]+) \| (?P<message>.*)$",
        raw_line,
    )
    if not match:
        return {
            "id": f"log-{index}",
            "prefix": "[系统]",
            "timestamp": "[--:--:--]",
            "message": raw_line,
            "tone": "muted",
        }

    level = match.group("level")
    message = match.group("message")
    return {
        "id": f"log-{index}",
        "prefix": f"[{level}]",
        "timestamp": f"[{match.group('clock')}]",
        "message": message,
        "tone": tone_from_log(level, message),
    }


def build_single_account_timing(
    raw_lines: List[str], window_size: int = 20
) -> Dict[str, Any]:
    pattern = re.compile(
        r"注册\+OAuth 成功: .*?\| 注册 (?P<reg>\d+(?:\.\d+)?)s \+ OAuth (?P<oauth>\d+(?:\.\d+)?)s = (?P<total>\d+(?:\.\d+)?)s"
    )
    samples: List[Dict[str, float]] = []
    for line in raw_lines:
        matched = pattern.search(line)
        if not matched:
            continue
        samples.append(
            {
                "reg": float(matched.group("reg")),
                "oauth": float(matched.group("oauth")),
                "total": float(matched.group("total")),
            }
        )

    result: Dict[str, Any] = {
        "latest_reg_seconds": None,
        "latest_oauth_seconds": None,
        "latest_total_seconds": None,
        "recent_avg_reg_seconds": None,
        "recent_avg_oauth_seconds": None,
        "recent_avg_total_seconds": None,
        "recent_slow_count": 0,
        "sample_size": 0,
        "window_size": max(1, int(window_size)),
    }
    if not samples:
        return result

    latest = samples[-1]
    recent = samples[-result["window_size"] :]
    result["latest_reg_seconds"] = round(latest["reg"], 1)
    result["latest_oauth_seconds"] = round(latest["oauth"], 1)
    result["latest_total_seconds"] = round(latest["total"], 1)
    result["recent_avg_reg_seconds"] = round(
        sum(item["reg"] for item in recent) / len(recent), 1
    )
    result["recent_avg_oauth_seconds"] = round(
        sum(item["oauth"] for item in recent) / len(recent), 1
    )
    result["recent_avg_total_seconds"] = round(
        sum(item["total"] for item in recent) / len(recent), 1
    )
    result["recent_slow_count"] = sum(1 for item in recent if item["total"] >= 100.0)
    result["sample_size"] = len(recent)
    return result


def parse_loop_next_check_in_seconds(raw_lines: List[str]) -> Optional[int]:
    pattern = re.compile(
        r"^(?P<date>\d{4}-\d{2}-\d{2}) (?P<clock>\d{2}:\d{2}:\d{2}) \| [A-Z]+ \| 循环模式休眠 (?P<seconds>\d+(?:\.\d+)?)s 后再次检查号池$"
    )
    now_ts = time.time()
    for line in reversed(raw_lines):
        matched = pattern.match(line.strip())
        if not matched:
            continue
        try:
            sleep_seconds = float(matched.group("seconds"))
            logged_at = datetime.strptime(
                f"{matched.group('date')} {matched.group('clock')}",
                "%Y-%m-%d %H:%M:%S",
            )
            next_check_ts = logged_at.timestamp() + sleep_seconds
            remaining = int(math.ceil(next_check_ts - now_ts))
            return max(0, remaining)
        except Exception:
            continue
    return None


def get_enabled_pools_from_config(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    pools = config.get("cpa_pools")
    out: List[Dict[str, Any]] = []
    if isinstance(pools, list):
        for idx, raw in enumerate(pools):
            if not isinstance(raw, dict):
                continue
            if not bool(raw.get("enabled", True)):
                continue
            base_url = str(raw.get("base_url") or "").strip()
            token = str(raw.get("token") or "").strip()
            target_type = str(raw.get("target_type") or "codex").strip() or "codex"
            min_candidates = int(raw.get("min_candidates") or 0)
            if not base_url or not token:
                continue
            out.append(
                {
                    "name": str(raw.get("name") or f"pool-{idx + 1}").strip()
                    or f"pool-{idx + 1}",
                    "base_url": base_url,
                    "token": token,
                    "target_type": target_type,
                    "min_candidates": min_candidates,
                }
            )

    if out:
        return out

    clean = config.get("clean") if isinstance(config.get("clean"), dict) else {}
    base_url = str((clean or {}).get("base_url") or "").strip()
    token = str((clean or {}).get("token") or "").strip()
    if not base_url or not token:
        return []
    return [
        {
            "name": "default",
            "base_url": base_url,
            "token": token,
            "target_type": str((clean or {}).get("target_type") or "codex"),
            "min_candidates": int(
                ((config.get("maintainer") or {}).get("min_candidates") or 0)
            ),
        }
    ]


def test_pool_connection(
    base_url: str, token: str, target_type: str, timeout: int
) -> Dict[str, Any]:
    base = str(base_url or "").strip()
    tk = str(token or "").strip()
    if not base:
        return {"ok": False, "message": "base_url 不能为空"}
    if not tk:
        return {"ok": False, "message": "token 不能为空"}
    try:
        total, candidates = get_candidates_count(
            base, tk, target_type or "codex", timeout
        )
        return {
            "ok": True,
            "message": "连接成功",
            "total": total,
            "candidates": candidates,
            "target_type": target_type or "codex",
        }
    except Exception as exc:
        return {"ok": False, "message": str(exc)}


def build_runtime_status() -> Dict[str, Any]:
    tracked_log_path = ""
    with RUN_PROCESS_LOCK:
        process = RUN_PROCESS
        running = process is not None and process.poll() is None
        run_mode = RUN_MODE if running else ""
        if running:
            tracked_log_path = RUN_LOG_PATH
        if not running:
            state_pid, state_mode, state_log_path = read_running_state()
            if state_pid is not None:
                running = True
                run_mode = state_mode
                tracked_log_path = state_log_path

    status: Dict[str, Any] = {
        "running": running,
        "run_mode": run_mode,
        "loop_running": running and run_mode == "loop",
        "loop_next_check_in_seconds": None,
        "phase": "idle",
        "message": "等待任务启动",
        "available_candidates": None,
        "available_candidates_error": "",
        "completed": 0,
        "total": 0,
        "percent": 0,
        "stats": [
            {"label": "成功", "value": 0, "icon": "☑", "tone": "success"},
            {"label": "失败", "value": 0, "icon": "✕", "tone": "danger"},
            {"label": "剩余", "value": 0, "icon": "⏳", "tone": "pending"},
        ],
        "single_account_timing": {
            "latest_reg_seconds": None,
            "latest_oauth_seconds": None,
            "latest_total_seconds": None,
            "recent_avg_reg_seconds": None,
            "recent_avg_oauth_seconds": None,
            "recent_avg_total_seconds": None,
            "recent_slow_count": 0,
            "sample_size": 0,
            "window_size": 20,
        },
        "logs": [],
        "last_log_path": "",
    }

    latest_log: Optional[Path] = None
    if tracked_log_path:
        tracked_path = Path(tracked_log_path)
        if tracked_path.exists():
            latest_log = tracked_path

    if latest_log is None:
        latest_log = get_latest_log_path()
    if latest_log is None:
        status["logs"] = [
            {
                "id": "log-empty",
                "prefix": "[系统]",
                "timestamp": "[--:--:--]",
                "message": "暂无运行日志",
                "tone": "muted",
            }
        ]
        return status

    try:
        config = load_config()
        timeout = int(((config.get("clean") or {}).get("timeout")) or 10)
        pools = get_enabled_pools_from_config(config)
        if pools:
            total_candidates = 0
            pool_errors: List[str] = []
            for pool in pools:
                try:
                    _, cnt = get_candidates_count(
                        base_url=str(pool.get("base_url") or ""),
                        token=str(pool.get("token") or ""),
                        target_type=str(pool.get("target_type") or "codex"),
                        timeout=timeout,
                    )
                    total_candidates += int(cnt)
                except Exception as pool_err:
                    pool_name = str(pool.get("name") or "pool")
                    pool_errors.append(f"{pool_name}: {pool_err}")
            status["available_candidates"] = total_candidates
            if pool_errors:
                status["available_candidates_error"] = " | ".join(pool_errors)
    except Exception as e:
        status["available_candidates_error"] = str(e)

    status["last_log_path"] = str(latest_log)
    raw_lines = tail_lines(latest_log)
    status["logs"] = [
        parse_log_line(index, line) for index, line in enumerate(raw_lines, start=1)
    ]
    status["single_account_timing"] = build_single_account_timing(
        raw_lines, window_size=20
    )
    if status.get("loop_running"):
        status["loop_next_check_in_seconds"] = parse_loop_next_check_in_seconds(
            raw_lines
        )

    round_start_pattern = re.compile(r">>> 循环轮次 #\d+ 开始")
    scan_lines = raw_lines
    last_round_start_index: Optional[int] = None
    for index, line in enumerate(raw_lines):
        if round_start_pattern.search(line):
            last_round_start_index = index
    if last_round_start_index is not None:
        scan_lines = raw_lines[last_round_start_index:]

    start_pattern = re.compile(
        r"(?:\[池:(?P<pool>[^\]]+)\]\s*)?开始补号: 目标 token=(?P<total>\d+)"
    )
    progress_pattern = re.compile(
        r"(?:\[池:(?P<pool>[^\]]+)\]\s*)?补号进度: token (?P<success>\d+)/(?P<total>\d+) \| ✅(?P<ok>\d+) ❌(?P<fail>\d+) ⏭️(?P<skip>\d+)"
    )
    done_pattern = re.compile(
        r"(?:\[池:(?P<pool>[^\]]+)\]\s*)?补号完成: token=(?P<success>\d+)/(?P<total>\d+), fail=(?P<fail>\d+), skip=(?P<skip>\d+)"
    )

    by_pool: Dict[str, Dict[str, int]] = {}

    def _ensure_pool(key: str) -> Dict[str, int]:
        if key not in by_pool:
            by_pool[key] = {"success": 0, "total": 0, "fail": 0, "skip": 0}
        return by_pool[key]

    for line in scan_lines:
        matched_start = start_pattern.search(line)
        if matched_start:
            pool = str(matched_start.group("pool") or "default").strip() or "default"
            state = _ensure_pool(pool)
            state["total"] = int(matched_start.group("total"))
            continue

        matched_progress = progress_pattern.search(line)
        if matched_progress:
            pool = str(matched_progress.group("pool") or "default").strip() or "default"
            state = _ensure_pool(pool)
            state["success"] = int(matched_progress.group("success"))
            state["total"] = int(matched_progress.group("total"))
            state["fail"] = int(matched_progress.group("fail"))
            state["skip"] = int(matched_progress.group("skip"))
            continue

        matched_done = done_pattern.search(line)
        if matched_done:
            pool = str(matched_done.group("pool") or "default").strip() or "default"
            state = _ensure_pool(pool)
            state["success"] = int(matched_done.group("success"))
            state["total"] = int(matched_done.group("total"))
            state["fail"] = int(matched_done.group("fail"))
            state["skip"] = int(matched_done.group("skip"))

    success = sum(item.get("success", 0) for item in by_pool.values())
    failed = sum(item.get("fail", 0) for item in by_pool.values())
    skipped = sum(item.get("skip", 0) for item in by_pool.values())
    total = sum(item.get("total", 0) for item in by_pool.values())

    completed = success
    remaining = max(total - success, 0) if total else 0
    percent = int((success / total) * 100) if total else 0

    status["completed"] = completed
    status["total"] = total
    status["percent"] = percent
    status["stats"] = [
        {"label": "成功", "value": success, "icon": "☑", "tone": "success"},
        {"label": "失败", "value": failed, "icon": "✕", "tone": "danger"},
        {"label": "剩余", "value": remaining, "icon": "⏳", "tone": "pending"},
    ]

    if raw_lines:
        last_message = raw_lines[-1]
        has_batch_start = "开始补号" in "\n".join(scan_lines)

        if status["running"]:
            if status.get("loop_running"):
                status["phase"] = "looping"
                status["message"] = "循环补号运行中"
            else:
                status["phase"] = "maintaining"
                status["message"] = (
                    "补号任务运行中" if has_batch_start else "维护任务运行中"
                )
        elif "=== 账号池自动维护结束（成功）===" in last_message:
            status["phase"] = "completed"
            status["message"] = "最近一次维护已完成"
        elif "=== 账号池自动维护结束（失败）===" in last_message:
            status["phase"] = "failed"
            status["message"] = "最近一次维护失败"
        elif has_batch_start:
            status["message"] = "最近一次维护已停止，日志未写入结束标记"
        else:
            status["message"] = "已加载最近一次运行日志"

    return status


def start_maintainer_process(*, loop_mode: bool = False) -> Dict[str, Any]:
    global RUN_PROCESS, RUN_MODE, RUN_LOG_PATH

    with RUN_PROCESS_LOCK:
        if RUN_PROCESS is not None and RUN_PROCESS.poll() is None:
            return {"ok": True, "started": False, "message": "维护任务已在运行中"}
        state_pid, state_mode, state_log_path = read_running_state()
        if state_pid is not None:
            RUN_MODE = state_mode
            RUN_LOG_PATH = state_log_path
            return {
                "ok": True,
                "started": False,
                "pid": state_pid,
                "mode": state_mode,
                "message": "维护任务已在运行中",
            }

        process_env = os.environ.copy()
        process_env["APP_DATA_DIR"] = str(APP_DATA_DIR)
        process_env["APP_CONFIG_PATH"] = str(CONFIG_PATH)
        process_env["APP_LOG_DIR"] = str(LOGS_DIR)
        planned_log_path = (
            LOGS_DIR
            / f"pool_maintainer_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.log"
        )
        process_env["APP_LOG_FILE"] = str(planned_log_path)

        command = [sys.executable, str(PROJECT_ROOT / "auto_pool_maintainer.py")]
        command.extend(["--config", str(CONFIG_PATH), "--log-dir", str(LOGS_DIR)])
        if loop_mode:
            command.append("--loop")
        RUN_PROCESS = subprocess.Popen(
            command,
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            env=process_env,
        )
        time.sleep(0.3)
        if RUN_PROCESS.poll() is not None:
            exit_code = RUN_PROCESS.returncode
            RUN_PROCESS = None
            RUN_MODE = ""
            RUN_LOG_PATH = ""
            clear_run_state()
            return {
                "ok": False,
                "started": False,
                "message": f"维护任务启动失败（进程已退出，code={exit_code}）",
            }

        RUN_MODE = "loop" if loop_mode else "single"
        RUN_LOG_PATH = str(planned_log_path)
        save_run_state(RUN_PROCESS.pid, RUN_MODE, RUN_LOG_PATH)
        return {
            "ok": True,
            "started": True,
            "pid": RUN_PROCESS.pid,
            "mode": RUN_MODE,
            "message": "已启动循环补号任务" if loop_mode else "已启动维护任务",
        }


def stop_maintainer_process() -> Dict[str, Any]:
    global RUN_PROCESS, RUN_MODE, RUN_LOG_PATH

    with RUN_PROCESS_LOCK:
        if RUN_PROCESS is not None and RUN_PROCESS.poll() is None:
            target_pid = RUN_PROCESS.pid
            try:
                RUN_PROCESS.terminate()
                try:
                    RUN_PROCESS.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    RUN_PROCESS.kill()
                    RUN_PROCESS.wait(timeout=5)
            except Exception as e:
                return {
                    "ok": False,
                    "stopped": False,
                    "message": f"停止维护任务失败: {e}",
                }
            RUN_PROCESS = None
            RUN_MODE = ""
            RUN_LOG_PATH = ""
            clear_run_state()
            return {
                "ok": True,
                "stopped": True,
                "pid": target_pid,
                "message": "已停止维护任务",
            }

        state_pid, state_mode, state_log_path = read_running_state()
        if state_pid is None:
            RUN_PROCESS = None
            RUN_MODE = ""
            RUN_LOG_PATH = ""
            clear_run_state()
            return {"ok": True, "stopped": False, "message": "当前没有运行中的维护任务"}
        target_pid = state_pid
        RUN_MODE = state_mode
        RUN_LOG_PATH = state_log_path

        try:
            if not terminate_pid(target_pid, timeout_seconds=8.0):
                return {
                    "ok": False,
                    "stopped": False,
                    "message": f"停止维护任务失败: pid={target_pid}",
                }
        except Exception as e:
            return {"ok": False, "stopped": False, "message": f"停止维护任务失败: {e}"}

        RUN_PROCESS = None
        RUN_MODE = ""
        RUN_LOG_PATH = ""
        clear_run_state()

        return {
            "ok": True,
            "stopped": True,
            "pid": target_pid,
            "message": "已停止维护任务",
        }


class ApiHandler(BaseHTTPRequestHandler):
    server_version = "AutoPoolMaintainerAPI/0.1"

    def _send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        origin = self.headers.get("Origin", "")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Admin-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(data)

    def _read_json_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length > 0 else b"{}"
        data = json.loads(raw.decode("utf-8") or "{}")
        if not isinstance(data, dict):
            raise RuntimeError("请求体必须是 JSON 对象")
        return data

    def _send_unauthorized(self, message: str = "Unauthorized") -> None:
        self._send_json({"error": message}, status=HTTPStatus.UNAUTHORIZED)

    def _is_authorized(self) -> bool:
        expected = get_admin_token()
        incoming = self.headers.get("X-Admin-Token", "").strip()
        return incoming == expected

    def _require_auth(self) -> bool:
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            return True
        if self._is_authorized():
            return True
        self._send_unauthorized("Invalid or missing X-Admin-Token")
        return False

    def _send_text(self, text: str, status: int = HTTPStatus.OK) -> None:
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_static_file(self, path: Path, content_type: str) -> None:
        if not path.exists() or not path.is_file():
            self._send_json({"error": "Not Found"}, status=HTTPStatus.NOT_FOUND)
            return
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self) -> None:
        if not self._require_auth():
            return
        self._send_json({"ok": True})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            index_path = FRONTEND_DIST_DIR / "index.html"
            if index_path.exists():
                self._send_static_file(index_path, "text/html; charset=utf-8")
            else:
                self._send_text(
                    "frontend 未构建，请先执行: cd frontend && pnpm install && pnpm build"
                )
            return

        if path.startswith("/assets/"):
            relative = path[len("/assets/") :].strip("/")
            asset_path = FRONTEND_DIST_DIR / "assets" / relative
            if relative.endswith(".js"):
                ctype = "application/javascript; charset=utf-8"
            elif relative.endswith(".css"):
                ctype = "text/css; charset=utf-8"
            elif relative.endswith(".svg"):
                ctype = "image/svg+xml"
            else:
                ctype = "application/octet-stream"
            self._send_static_file(asset_path, ctype)
            return

        if not path.startswith("/api"):
            index_path = FRONTEND_DIST_DIR / "index.html"
            if index_path.exists():
                self._send_static_file(index_path, "text/html; charset=utf-8")
            else:
                self._send_text(
                    "frontend 未构建，请先执行: cd frontend && pnpm install && pnpm build"
                )
            return

        if not self._require_auth():
            return

        if path == "/api/config":
            self._send_json(mask_sensitive_config(load_config()))
            return
        if path == "/api/runtime/status":
            self._send_json(build_runtime_status())
            return
        if path == "/api/runtime/pools":
            config = load_config()
            self._send_json({"items": get_enabled_pools_from_config(config)})
            return
        if path == "/api/mail/taobao-pool":
            config = load_config()
            pool = _taobao_pool(config=config)
            snapshot = pool.snapshot()
            self._send_json(snapshot)
            return
        if path == "/api/health":
            self._send_json({"ok": True, "time": datetime.now().isoformat()})
            return
        self._send_json({"error": "Not Found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if not self._require_auth():
            return
        if path == "/api/config":
            payload = self._read_json_body()
            save_config(payload)
            self._send_json(mask_sensitive_config(load_config()))
            return
        if path == "/api/runtime/start":
            self._send_json(start_maintainer_process())
            return
        if path == "/api/runtime/start-loop":
            self._send_json(start_maintainer_process(loop_mode=True))
            return
        if path == "/api/runtime/stop":
            self._send_json(stop_maintainer_process())
            return
        if path == "/api/runtime/test-pool":
            payload = self._read_json_body()
            self._send_json(
                test_pool_connection(
                    base_url=str(payload.get("base_url") or ""),
                    token=str(payload.get("token") or ""),
                    target_type=str(payload.get("target_type") or "codex"),
                    timeout=int(
                        payload.get("timeout")
                        or int(
                            ((load_config().get("clean") or {}).get("timeout") or 10)
                        )
                    ),
                )
            )
            return
        if path == "/api/mail/taobao-pool/import":
            payload = self._read_json_body()
            bulk_text = str(payload.get("text") or "")
            config = load_config()
            pool = _taobao_pool(config=config)
            result = pool.import_bulk_text(bulk_text)
            snapshot = pool.snapshot()
            self._send_json({"ok": True, "result": result, "snapshot": snapshot})
            return
        if path == "/api/mail/taobao-pool/requeue":
            payload = self._read_json_body()
            config = load_config()
            pool = _taobao_pool(config=config)
            emails = _coerce_email_list(payload)
            if not emails:
                snapshot = pool.snapshot()
                emails = [
                    str(item.get("email") or "").strip().lower()
                    for item in (snapshot.get("failed") or [])
                    if str(item.get("email") or "").strip()
                ]
            result = pool.requeue(emails)
            snapshot = pool.snapshot()
            self._send_json(
                {
                    "ok": True,
                    "emails": emails,
                    "result": result,
                    "snapshot": snapshot,
                }
            )
            return
        if path == "/api/mail/taobao-pool/abandon":
            payload = self._read_json_body()
            config = load_config()
            pool = _taobao_pool(config=config)
            emails = _coerce_email_list(payload)
            if not emails:
                self._send_json(
                    {"ok": False, "message": "emails 不能为空"},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            result = pool.abandon(emails)
            snapshot = pool.snapshot()
            self._send_json(
                {
                    "ok": True,
                    "emails": emails,
                    "result": result,
                    "snapshot": snapshot,
                }
            )
            return
        self._send_json({"error": "Not Found"}, status=HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:
        return


def run_server(host: str = API_HOST, port: int = API_PORT) -> None:
    ensure_runtime_paths()
    admin_token = get_admin_token()
    if ADMIN_TOKEN_ENV:
        print("Using APP_ADMIN_TOKEN from environment.")
    else:
        print(f"Generated admin token saved to: {ADMIN_TOKEN_FILE}")
        print(f"Generated admin token: {admin_token}")
    server = ThreadingHTTPServer((host, port), ApiHandler)
    print(f"API server listening on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run_server()
