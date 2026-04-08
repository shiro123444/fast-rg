#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib import error, parse, request


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = PROJECT_ROOT / "config.json"
DEFAULT_LOG_DIR = PROJECT_ROOT / "logs"


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key or key in os.environ:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            os.environ[key] = value
    except Exception:
        return


def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise RuntimeError(f"配置文件不存在: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("配置文件格式错误，顶层必须是 JSON 对象")
    return data


def pick_conf(
    root: Dict[str, Any], section: str, key: str, *legacy_keys: str, default: Any = None
) -> Any:
    sec = root.get(section)
    if isinstance(sec, dict):
        value = sec.get(key)
        if value is None:
            for legacy in legacy_keys:
                value = sec.get(legacy)
                if value is not None:
                    break
        if value is not None:
            return value

    value = root.get(key)
    if value is None:
        for legacy in legacy_keys:
            value = root.get(legacy)
            if value is not None:
                break
    return default if value is None else value


def setup_logger(log_dir: Path) -> Tuple[logging.Logger, Path]:
    custom_log_file = str(os.environ.get("APP_LOG_FILE", "") or "").strip()
    if custom_log_file:
        log_path = Path(custom_log_file)
        if not log_path.is_absolute():
            log_path = (log_dir / log_path).resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        log_path = log_dir / f"pool_maintainer_{ts}.log"

    logger = logging.getLogger("pool_maintainer")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger, log_path


def mgmt_headers(token: str, content_type: Optional[str] = None) -> Dict[str, str]:
    out = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "codex_cli_rs/0.76.0 (Debian 13.0.0; x86_64) WindowsTerminal",
    }
    if content_type:
        out["Content-Type"] = content_type
    return out


def get_item_type(item: Dict[str, Any]) -> str:
    return str(item.get("type") or item.get("typo") or "")


def is_item_disabled(item: Dict[str, Any]) -> bool:
    disabled = item.get("disabled")
    if isinstance(disabled, bool):
        return disabled
    if isinstance(disabled, (int, float)):
        return bool(disabled)
    text = str(item.get("status") or item.get("state") or "").strip().lower()
    return text in {"disabled", "inactive"}


def _http_json(
    method: str,
    url: str,
    headers: Dict[str, str],
    timeout: int,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    data: Optional[bytes] = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(url, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=max(3, int(timeout or 10))) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw or "{}")
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise RuntimeError(
            f"{method} {url} 失败: HTTP {exc.code} {body[:200]}"
        ) from exc
    except Exception as exc:
        raise RuntimeError(f"{method} {url} 失败: {exc}") from exc


def fetch_auth_files(base_url: str, token: str, timeout: int) -> List[Dict[str, Any]]:
    endpoint = f"{base_url.rstrip('/')}/v0/management/auth-files"
    payload = _http_json("GET", endpoint, mgmt_headers(token), timeout)
    files = payload.get("files", []) if isinstance(payload, dict) else []
    return files if isinstance(files, list) else []


def get_candidates_count(
    base_url: str, token: str, target_type: str, timeout: int
) -> Tuple[int, int]:
    base = str(base_url or "").rstrip("/")
    if not base:
        raise RuntimeError("clean.base_url 未配置")
    if not token:
        raise RuntimeError("clean.token 未配置")

    files = fetch_auth_files(base, token, timeout)
    total = len(files)
    candidates = 0
    target_lower = str(target_type or "codex").strip().lower()
    for item in files:
        if not isinstance(item, dict):
            continue
        if get_item_type(item).strip().lower() != target_lower:
            continue
        if is_item_disabled(item):
            continue
        candidates += 1
    return total, candidates


def _usage_probe(
    base_url: str,
    token: str,
    auth_index: str,
    timeout: int,
    user_agent: str,
) -> Tuple[Optional[int], Optional[float], Optional[str]]:
    endpoint = f"{base_url.rstrip('/')}/v0/management/api-call"
    payload = {
        "authIndex": str(auth_index),
        "method": "GET",
        "url": "https://chatgpt.com/backend-api/wham/usage",
        "header": {
            "Authorization": "Bearer $TOKEN$",
            "Content-Type": "application/json",
            "User-Agent": user_agent,
        },
    }
    try:
        data = _http_json(
            "POST",
            endpoint,
            mgmt_headers(token, content_type="application/json"),
            timeout,
            payload,
        )
    except Exception as exc:
        return None, None, str(exc)

    status_code = data.get("status_code")
    try:
        status_code = int(status_code)
    except Exception:
        status_code = None

    used_percent = None
    body = data.get("body")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except Exception:
            body = None
    if isinstance(body, dict):
        for key in ["used_percent", "usedPercent", "usedPercentage"]:
            if key in body:
                try:
                    used_percent = float(body.get(key))
                except Exception:
                    used_percent = None
                break
    return status_code, used_percent, None


def _delete_account(base_url: str, token: str, name: str, timeout: int) -> bool:
    if not name:
        return False
    encoded_name = parse.quote(name, safe="")
    endpoint = f"{base_url.rstrip('/')}/v0/management/auth-files?name={encoded_name}"
    try:
        data = _http_json("DELETE", endpoint, mgmt_headers(token), timeout)
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    return str(data.get("status") or "").strip().lower() == "ok"


def _set_disabled(
    base_url: str, token: str, name: str, disabled: bool, timeout: int
) -> bool:
    if not name:
        return False
    payload = {"name": name, "disabled": bool(disabled)}
    headers = mgmt_headers(token, content_type="application/json")
    for endpoint in [
        f"{base_url.rstrip('/')}/v0/management/auth-files",
        f"{base_url.rstrip('/')}/v0/management/auth-files/status",
    ]:
        try:
            data = _http_json("PATCH", endpoint, headers, timeout, payload)
            if isinstance(data, dict):
                status = str(data.get("status") or "ok").strip().lower()
                if status in {"ok", "success", ""}:
                    return True
        except Exception:
            continue
    return False


def clean_pool(
    *,
    base_url: str,
    token: str,
    target_type: str,
    timeout: int,
    retries: int,
    sample_size: int,
    used_percent_threshold: int,
    user_agent: str,
    logger: logging.Logger,
) -> Dict[str, int]:
    files = fetch_auth_files(base_url, token, timeout)
    candidates: List[Dict[str, Any]] = []
    target_lower = str(target_type or "codex").strip().lower()
    for item in files:
        if not isinstance(item, dict):
            continue
        if get_item_type(item).strip().lower() != target_lower:
            continue
        candidates.append(item)

    probe_targets = list(candidates)
    if sample_size > 0 and sample_size < len(probe_targets):
        probe_targets = random.sample(probe_targets, sample_size)

    delete_names: List[str] = []
    disable_names: List[str] = []
    enable_names: List[str] = []
    invalid_401 = 0
    over_threshold = 0

    for item in probe_targets:
        auth_index = item.get("auth_index")
        name = str(item.get("name") or item.get("id") or "").strip()
        disabled = is_item_disabled(item)
        if not auth_index or not name:
            continue
        status_code = None
        used_percent = None
        err = None
        for attempt in range(max(1, retries + 1)):
            status_code, used_percent, err = _usage_probe(
                base_url, token, str(auth_index), timeout, user_agent
            )
            if err is None or attempt >= retries:
                break
            time.sleep(0.3)

        if err:
            logger.warning("探测失败 name=%s err=%s", name, err)
            continue

        if status_code == 401:
            invalid_401 += 1
            delete_names.append(name)
            continue

        if used_percent is not None and used_percent >= float(used_percent_threshold):
            over_threshold += 1
            if not disabled:
                disable_names.append(name)
            continue

        if status_code == 200 and disabled:
            enable_names.append(name)

    deleted_ok = 0
    for name in sorted(set(delete_names)):
        if _delete_account(base_url, token, name, timeout):
            deleted_ok += 1

    disabled_ok = 0
    for name in sorted(set(disable_names)):
        if _set_disabled(base_url, token, name, True, timeout):
            disabled_ok += 1

    enabled_ok = 0
    for name in sorted(set(enable_names)):
        if _set_disabled(base_url, token, name, False, timeout):
            enabled_ok += 1

    logger.info(
        "清理完成: 总账号=%s, 探测=%s, 401失效=%s, 超阈值=%s, 删除=%s, 禁用=%s, 恢复=%s",
        len(candidates),
        len(probe_targets),
        invalid_401,
        over_threshold,
        deleted_ok,
        disabled_ok,
        enabled_ok,
    )

    return {
        "total": len(candidates),
        "probed": len(probe_targets),
        "invalid_401": invalid_401,
        "over_threshold": over_threshold,
        "deleted_ok": deleted_ok,
        "disabled_ok": disabled_ok,
        "enabled_ok": enabled_ok,
    }


def _provider_mode(provider: str) -> str:
    normalized = str(provider or "").strip().lower()
    mapping = {
        "luckmail": "luckmail",
        "gmail": "gmail",
        "hotmail007": "hotmail007",
        "outlook_api": "outlook_api",
        "outlookapi": "outlook_api",
        "msapi": "outlook_api",
        "file": "file",
        "cf": "cf",
        "cfmail": "cf",
    }
    return mapping.get(normalized, "luckmail")


def _env_string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        parts = [str(item).strip() for item in value]
        parts = [item for item in parts if item]
        return ",".join(parts)
    return str(value).strip()


def _build_child_env(
    conf: Dict[str, Any], provider: str, pool: Dict[str, Any]
) -> Dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("PYTHONIOENCODING", "utf-8")

    pool_base = str(pool.get("base_url") or "").strip()
    pool_token = str(pool.get("token") or "").strip()
    if pool_base:
        env["CPA_API_URL"] = pool_base
    if pool_token:
        env["CPA_API_KEY"] = pool_token

    mode = _provider_mode(provider)
    env["EMAIL_MODE"] = mode

    if mode == "luckmail":
        luckmail = (
            conf.get("luckmail", {}) if isinstance(conf.get("luckmail"), dict) else {}
        )
        for src, dst in [
            ("sdk_path", "LUCKMAIL_SDK_PATH"),
            ("api_base", "LUCKMAIL_BASE_URL"),
            ("api_key", "LUCKMAIL_API_KEY"),
            ("project_code", "LUCKMAIL_PROJECT_CODE"),
            ("email_type", "LUCKMAIL_EMAIL_TYPE"),
            ("domain", "LUCKMAIL_DOMAIN"),
            ("auto_switch_email_type", "LUCKMAIL_AUTO_SWITCH_EMAIL_TYPE"),
            ("allow_domain_auto_fallback", "LUCKMAIL_ALLOW_DOMAIN_AUTO_FALLBACK"),
            ("stock_hard_block", "LUCKMAIL_STOCK_HARD_BLOCK"),
        ]:
            value = _env_string(luckmail.get(src))
            if value:
                env[dst] = value

    if mode == "gmail":
        gmail = conf.get("gmail", {}) if isinstance(conf.get("gmail"), dict) else {}
        base = str(gmail.get("base") or "").strip()
        if base:
            env["GMAIL_BASE"] = base

    if mode == "hotmail007":
        hotmail = (
            conf.get("hotmail007", {})
            if isinstance(conf.get("hotmail007"), dict)
            else {}
        )
        for src, dst in [
            ("api_url", "HOTMAIL007_API_URL"),
            ("api_key", "HOTMAIL007_API_KEY"),
            ("mail_type", "HOTMAIL007_MAIL_TYPE"),
            ("mail_mode", "HOTMAIL007_MAIL_MODE"),
        ]:
            value = str(hotmail.get(src) or "").strip()
            if value:
                env[dst] = value

    if mode == "outlook_api":
        outlook_api = (
            conf.get("outlook_api", {})
            if isinstance(conf.get("outlook_api"), dict)
            else {}
        )
        for src, dst in [
            ("api_url", "OUTLOOK_API_URL"),
            ("accounts_file", "OUTLOOK_API_ACCOUNTS_FILE"),
            ("client_id", "OUTLOOK_API_CLIENT_ID"),
            ("refresh_token", "OUTLOOK_API_REFRESH_TOKEN"),
            ("num", "OUTLOOK_API_NUM"),
            ("box_type", "OUTLOOK_API_BOX_TYPE"),
            ("timeout_seconds", "OUTLOOK_API_TIMEOUT"),
            ("poll_interval_seconds", "OUTLOOK_API_POLL_INTERVAL"),
            ("otp_timeout_seconds", "OUTLOOK_API_POLL_TIMEOUT"),
        ]:
            value = _env_string(outlook_api.get(src))
            if value:
                env[dst] = value

    if mode == "file":
        file_conf = (
            conf.get("file_mail", {}) if isinstance(conf.get("file_mail"), dict) else {}
        )
        accounts_file = str(file_conf.get("accounts_file") or "accounts.txt").strip()
        if accounts_file:
            env["ACCOUNTS_FILE"] = accounts_file

    if mode == "cf":
        cf = conf.get("cfmail", {}) if isinstance(conf.get("cfmail"), dict) else {}
        for src, dst in [
            ("domain", "MAIL_DOMAIN"),
            ("worker_base", "MAIL_WORKER_BASE"),
            ("admin_password", "MAIL_ADMIN_PASSWORD"),
        ]:
            value = str(cf.get(src) or "").strip()
            if value:
                env[dst] = value

    return env


def _build_registration_cmd(conf: Dict[str, Any], target_count: int) -> List[str]:
    cmd = [
        sys.executable,
        "-u",
        str(PROJECT_ROOT / "gpt.py"),
        "--count",
        str(max(1, int(target_count))),
        "--threads",
        str(max(1, int(pick_conf(conf, "run", "workers", default=1) or 1))),
        "--email-mode",
        _provider_mode(
            str(pick_conf(conf, "mail", "provider", default="luckmail") or "luckmail")
        ),
        "--sleep-min",
        "1",
        "--sleep-max",
        "2",
    ]
    proxy = str(pick_conf(conf, "run", "proxy", default="") or "").strip()
    if proxy:
        cmd.extend(["--proxy", proxy])
    proxy_file = str(pick_conf(conf, "run", "proxy_file", default="") or "").strip()
    if proxy_file:
        cmd.extend(["--proxy-file", proxy_file])
    return cmd


def run_registration_batch(
    conf: Dict[str, Any],
    pool: Dict[str, Any],
    target_count: int,
    logger: logging.Logger,
) -> Tuple[int, int, int]:
    provider = str(
        pick_conf(conf, "mail", "provider", default="luckmail") or "luckmail"
    )
    env = _build_child_env(conf, provider, pool)
    pool_name = str(pool.get("name") or "pool")
    retry_rounds = int(
        pick_conf(
            conf,
            "maintainer",
            "register_retry_rounds",
            "register_retry_attempts",
            default=3,
        )
        or 3
    )
    retry_rounds = max(1, retry_rounds)
    retry_backoff = float(
        pick_conf(conf, "maintainer", "register_retry_backoff_seconds", default=3)
        or 3
    )
    retry_backoff = max(0.0, retry_backoff)

    target_total = max(1, int(target_count))
    remaining = target_total
    total_success = 0
    total_failed = 0
    total_skipped = 0

    success_re = re.compile(r"共成功:\s*(\d+)")

    for attempt in range(1, retry_rounds + 1):
        if remaining <= 0:
            break

        cmd = _build_registration_cmd(conf, remaining)
        logger.info(
            "[池:%s] 补号尝试 %s/%s, 本次目标 token=%s",
            pool_name,
            attempt,
            retry_rounds,
            remaining,
        )
        logger.info("注册命令: %s", " ".join(cmd))

        proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )

        run_success = 0
        run_failed = 0
        run_skipped = 0
        summary_success: Optional[int] = None

        if proc.stdout is not None:
            for line in proc.stdout:
                text = line.rstrip("\n")
                logger.info(text)
                if "注册成功" in text:
                    run_success += 1
                if "本次注册失败" in text:
                    run_failed += 1
                if "邮箱队列已用完" in text or "达到补号目标" in text:
                    run_skipped += 1
                match = success_re.search(text)
                if match:
                    summary_success = int(match.group(1))

        returncode = proc.wait()
        if summary_success is not None:
            run_success = max(run_success, summary_success)
        run_target = max(1, int(remaining))
        if run_success + run_failed < run_target:
            run_failed = max(run_failed, run_target - run_success)
        if returncode != 0:
            logger.warning("注册进程退出码异常: %s", returncode)

        total_success += run_success
        total_failed += run_failed
        total_skipped += run_skipped
        remaining = max(0, target_total - total_success)

        if remaining <= 0:
            break

        logger.warning(
            "[池:%s] 本次补号未达标，累计 token=%s/%s，fail=%s，skip=%s",
            pool_name,
            total_success,
            target_total,
            total_failed,
            total_skipped,
        )
        if attempt < retry_rounds and retry_backoff > 0:
            logger.info(
                "[池:%s] %.1fs 后继续重试补号",
                pool_name,
                retry_backoff,
            )
            time.sleep(retry_backoff)

    if remaining > 0:
        logger.warning(
            "[池:%s] 达到最大补号尝试轮次(%s)，本轮仍缺 token=%s",
            pool_name,
            retry_rounds,
            remaining,
        )

    return total_success, total_failed, total_skipped


def _enabled_pools(conf: Dict[str, Any]) -> List[Dict[str, Any]]:
    pools = conf.get("cpa_pools")
    out: List[Dict[str, Any]] = []
    if isinstance(pools, list):
        for idx, raw in enumerate(pools):
            if not isinstance(raw, dict):
                continue
            if not bool(raw.get("enabled", True)):
                continue
            base_url = str(raw.get("base_url") or "").strip()
            token = str(raw.get("token") or "").strip()
            if not base_url:
                continue
            out.append(
                {
                    "name": str(raw.get("name") or f"pool-{idx + 1}").strip()
                    or f"pool-{idx + 1}",
                    "base_url": base_url,
                    "token": token,
                    "target_type": str(raw.get("target_type") or "codex").strip()
                    or "codex",
                    "min_candidates": int(
                        raw.get("min_candidates")
                        or pick_conf(conf, "maintainer", "min_candidates", default=1)
                        or 1
                    ),
                }
            )
    if out:
        return out

    base_url = (
        str(pick_conf(conf, "clean", "base_url", default="") or "").strip()
        or str(os.environ.get("CPA_API_URL", "") or "").strip()
    )
    token = (
        str(pick_conf(conf, "clean", "token", default="") or "").strip()
        or str(os.environ.get("CPA_API_KEY", "") or "").strip()
    )
    if not base_url:
        return []
    return [
        {
            "name": "default",
            "base_url": base_url,
            "token": token,
            "target_type": str(
                pick_conf(conf, "clean", "target_type", default="codex") or "codex"
            ),
            "min_candidates": int(
                pick_conf(conf, "maintainer", "min_candidates", default=1) or 1
            ),
        }
    ]


def execute_pool_round(
    conf: Dict[str, Any], pool: Dict[str, Any], logger: logging.Logger
) -> Dict[str, Any]:
    pool_name = str(pool.get("name") or "pool")
    base_url = str(pool.get("base_url") or "").strip()
    token = str(pool.get("token") or "").strip()
    target_type = str(pool.get("target_type") or "codex")
    timeout = int(pick_conf(conf, "clean", "timeout", default=10) or 10)
    retries = int(pick_conf(conf, "clean", "retries", default=1) or 1)
    sample_size = int(pick_conf(conf, "clean", "sample_size", default=0) or 0)
    used_percent_threshold = int(
        pick_conf(conf, "clean", "used_percent_threshold", default=95) or 95
    )
    user_agent = str(
        pick_conf(
            conf,
            "clean",
            "user_agent",
            default="codex_cli_rs/0.76.0 (Debian 13.0.0; x86_64) WindowsTerminal",
        )
        or "codex_cli_rs/0.76.0 (Debian 13.0.0; x86_64) WindowsTerminal"
    )
    threshold = int(pool.get("min_candidates") or 1)

    logger.info("[池:%s] 开始清理探测...", pool_name)
    clean_stats = clean_pool(
        base_url=base_url,
        token=token,
        target_type=target_type,
        timeout=timeout,
        retries=retries,
        sample_size=sample_size,
        used_percent_threshold=used_percent_threshold,
        user_agent=user_agent,
        logger=logger,
    )

    total, candidates = get_candidates_count(base_url, token, target_type, timeout)
    logger.info(
        "[池:%s] 清理后统计: total=%s, candidates=%s, 阈值=%s",
        pool_name,
        total,
        candidates,
        threshold,
    )

    if candidates >= threshold:
        logger.info("[池:%s] 账号池已达标，无需补号", pool_name)
        logger.info("[池:%s] 补号进度: token 0/0 | ✅0 ❌0 ⏭️0", pool_name)
        logger.info("[池:%s] 补号完成: token=0/0, fail=0, skip=0", pool_name)
        return {
            "pool": pool_name,
            "needed": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0,
            "before": candidates,
            "after": candidates,
            "clean": clean_stats,
        }

    needed = max(0, threshold - candidates)
    logger.info("[池:%s] 开始补号: 目标 token=%s", pool_name, needed)
    success, failed, skipped = run_registration_batch(conf, pool, needed, logger)
    logger.info(
        "[池:%s] 补号进度: token %s/%s | ✅%s ❌%s ⏭️%s",
        pool_name,
        success,
        needed,
        success,
        failed,
        skipped,
    )
    logger.info(
        "[池:%s] 补号完成: token=%s/%s, fail=%s, skip=%s",
        pool_name,
        success,
        needed,
        failed,
        skipped,
    )

    _, after_candidates = get_candidates_count(base_url, token, target_type, timeout)
    logger.info(
        "[池:%s] 补号后统计: candidates=%s, 阈值=%s",
        pool_name,
        after_candidates,
        threshold,
    )
    return {
        "pool": pool_name,
        "needed": needed,
        "success": success,
        "failed": failed,
        "skipped": skipped,
        "before": candidates,
        "after": after_candidates,
        "clean": clean_stats,
    }


def execute_maintainer_round(
    conf: Dict[str, Any], logger: logging.Logger
) -> Dict[str, Any]:
    pools = _enabled_pools(conf)
    if not pools:
        raise RuntimeError("未找到可用的 CPA 号池配置")

    logger.info("本轮维护号池数量: %s", len(pools))
    total_needed = 0
    total_success = 0
    total_failed = 0
    total_skipped = 0
    for pool in pools:
        result = execute_pool_round(conf, pool, logger)
        total_needed += int(result.get("needed") or 0)
        total_success += int(result.get("success") or 0)
        total_failed += int(result.get("failed") or 0)
        total_skipped += int(result.get("skipped") or 0)

    logger.info(
        "全池汇总: token=%s/%s, fail=%s, skip=%s",
        total_success,
        total_needed,
        total_failed,
        total_skipped,
    )
    return {
        "needed": total_needed,
        "success": total_success,
        "failed": total_failed,
        "skipped": total_skipped,
    }


def run_single(conf: Dict[str, Any], logger: logging.Logger) -> int:
    logger.info("=== 账号池自动维护开始 ===")
    try:
        execute_maintainer_round(conf, logger)
    except Exception as exc:
        logger.exception("维护任务失败: %s", exc)
        logger.error("=== 账号池自动维护结束（失败）===")
        return 1
    logger.info("=== 账号池自动维护结束（成功）===")
    return 0


def run_loop(conf: Dict[str, Any], logger: logging.Logger) -> int:
    logger.info("=== 账号池循环维护开始 ===")
    base_interval = float(
        pick_conf(conf, "maintainer", "loop_interval_seconds", default=60) or 60
    )
    jitter_min = float(
        pick_conf(conf, "run", "loop_jitter_min_seconds", default=0) or 0
    )
    jitter_max = float(
        pick_conf(conf, "run", "loop_jitter_max_seconds", default=0) or 0
    )
    round_num = 0
    try:
        while True:
            round_num += 1
            logger.info(">>> 循环轮次 #%s 开始", round_num)
            execute_maintainer_round(conf, logger)
            jitter_low = min(jitter_min, jitter_max)
            jitter_high = max(jitter_min, jitter_max)
            interval = max(5.0, base_interval + random.uniform(jitter_low, jitter_high))
            logger.info("循环模式休眠 %.1fs 后再次检查号池", interval)
            time.sleep(interval)
    except KeyboardInterrupt:
        logger.warning("收到中断信号，循环维护停止")
        logger.info("=== 账号池自动维护结束（成功）===")
        return 0
    except Exception as exc:
        logger.exception("循环维护异常: %s", exc)
        logger.error("=== 账号池自动维护结束（失败）===")
        return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="账号池自动维护（使用当前 gpt.py 注册逻辑）"
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="配置文件路径")
    parser.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR), help="日志目录")
    parser.add_argument("--loop", action="store_true", help="循环补号模式")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    _load_dotenv(PROJECT_ROOT / ".env")
    conf = load_json(Path(args.config))
    logger, log_path = setup_logger(Path(args.log_dir))
    logger.info("配置文件: %s", args.config)
    logger.info("日志文件: %s", log_path)
    if args.loop:
        return run_loop(conf, logger)
    return run_single(conf, logger)


if __name__ == "__main__":
    raise SystemExit(main())
