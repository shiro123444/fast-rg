import json
import os
import re
import sys
import time
import uuid
import math
import random
import string
import secrets
import hashlib
import base64
import threading
import argparse
from itertools import product
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, parse_qs, urlencode, quote
from dataclasses import dataclass
from typing import Any, Dict, Optional, List
import urllib.parse
import ssl
import urllib.request
import urllib.error

from curl_cffi import requests
from curl_cffi import CurlMime
from taobao_pool import TaobaoMailboxPool

# ==========================================
# Cloudflare Temp Email API
# ==========================================


def _load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for raw in handle:
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
        pass


_load_dotenv()


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        return int(str(raw).strip() or str(default))
    except Exception:
        return int(default)

MAIL_DOMAIN = os.getenv("MAIL_DOMAIN", "")
MAIL_WORKER_BASE = os.getenv("MAIL_WORKER_BASE", "").rstrip("/")
MAIL_ADMIN_PASSWORD = os.getenv("MAIL_ADMIN_PASSWORD", "")
TOKEN_OUTPUT_DIR = os.getenv("TOKEN_OUTPUT_DIR", "").strip()
CLI_PROXY_AUTHS_DIR = os.getenv("CLI_PROXY_AUTHS_DIR", "").strip()

CPA_API_URL = os.getenv("CPA_API_URL", "").strip().rstrip("/")
CPA_API_KEY = os.getenv("CPA_API_KEY", "").strip()

PROXY_FILE = os.getenv("PROXY_FILE", "").strip()
SINGLE_PROXY = os.getenv("PROXY", "").strip()
BATCH_COUNT = os.getenv("BATCH_COUNT", "").strip()
BATCH_THREADS = os.getenv("BATCH_THREADS", "").strip()

EMAIL_MODE = os.getenv("EMAIL_MODE", "cf").strip().lower()
HOTMAIL007_API_URL = os.getenv(
    "HOTMAIL007_API_URL", "https://gapi.hotmail007.com"
).rstrip("/")
HOTMAIL007_API_KEY = os.getenv("HOTMAIL007_API_KEY", "").strip()
HOTMAIL007_MAIL_TYPE = os.getenv(
    "HOTMAIL007_MAIL_TYPE", "outlook Trusted Graph"
).strip()
HOTMAIL007_MAIL_MODE = os.getenv("HOTMAIL007_MAIL_MODE", "graph").strip().lower()
OUTLOOK_API_URL = os.getenv("OUTLOOK_API_URL", "").strip().rstrip("/")
OUTLOOK_API_CLIENT_ID = os.getenv("OUTLOOK_API_CLIENT_ID", "").strip()
OUTLOOK_API_REFRESH_TOKEN = os.getenv("OUTLOOK_API_REFRESH_TOKEN", "").strip()
OUTLOOK_API_ACCOUNTS_FILE = os.getenv(
    "OUTLOOK_API_ACCOUNTS_FILE", "outlook_accounts.txt"
).strip()
OUTLOOK_API_NUM = max(1, min(2, _env_int("OUTLOOK_API_NUM", 1)))
OUTLOOK_API_BOX_TYPE = 2 if _env_int("OUTLOOK_API_BOX_TYPE", 1) == 2 else 1
OUTLOOK_API_TIMEOUT = max(5, _env_int("OUTLOOK_API_TIMEOUT", 15))
OUTLOOK_API_POLL_INTERVAL = max(1, _env_int("OUTLOOK_API_POLL_INTERVAL", 3))
OUTLOOK_API_POLL_TIMEOUT = max(20, _env_int("OUTLOOK_API_POLL_TIMEOUT", 120))

ACCOUNTS_FILE = os.getenv("ACCOUNTS_FILE", "accounts.txt").strip()

LUCKMAIL_SDK_PATH = os.getenv(
    "LUCKMAIL_SDK_PATH", "/home/shiro/文档/codex/tools/auto_reg/core"
).strip()
LUCKMAIL_BASE_URL = os.getenv("LUCKMAIL_BASE_URL", "").strip().rstrip("/")
LUCKMAIL_API_KEY = os.getenv("LUCKMAIL_API_KEY", "").strip()
LUCKMAIL_PROJECT_CODE = os.getenv("LUCKMAIL_PROJECT_CODE", "openai").strip() or "openai"
LUCKMAIL_EMAIL_TYPE = os.getenv("LUCKMAIL_EMAIL_TYPE", "").strip()
LUCKMAIL_DOMAIN = os.getenv("LUCKMAIL_DOMAIN", "").strip()
LUCKMAIL_AUTO_SWITCH_EMAIL_TYPE = os.getenv(
    "LUCKMAIL_AUTO_SWITCH_EMAIL_TYPE", "1"
).strip().lower() not in {"0", "false", "no"}
LUCKMAIL_ALLOW_DOMAIN_AUTO_FALLBACK = os.getenv(
    "LUCKMAIL_ALLOW_DOMAIN_AUTO_FALLBACK", "0"
).strip().lower() in {"1", "true", "yes"}
LUCKMAIL_STOCK_HARD_BLOCK = os.getenv(
    "LUCKMAIL_STOCK_HARD_BLOCK", "0"
).strip().lower() in {"1", "true", "yes"}
LUCKMAIL_OTP_TIMEOUT = int(os.getenv("LUCKMAIL_OTP_TIMEOUT", "60") or "60")
LUCKMAIL_PRECHECK_ENABLED = os.getenv(
    "LUCKMAIL_PRECHECK_ENABLED", "1"
).strip().lower() not in {"0", "false", "no"}
LUCKMAIL_PRECHECK_RETRIES = int(os.getenv("LUCKMAIL_PRECHECK_RETRIES", "2") or "2")
LUCKMAIL_PURCHASE_MAX_ATTEMPTS = int(
    os.getenv("LUCKMAIL_PURCHASE_MAX_ATTEMPTS", "6") or "6"
)


def _load_proxies(filepath: str) -> List[str]:
    proxies_list = []
    if not filepath or not os.path.exists(filepath):
        return proxies_list
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                proxies_list.append(line)
    except Exception as e:
        print(f"[Error] 加载代理文件失败 ({filepath}): {e}")
    return proxies_list


class ProxyRotator:
    """线程安全的代理轮换器 (round-robin)"""

    def __init__(self, proxy_list: List[str]):
        self._proxies = list(proxy_list) if proxy_list else []
        self._index = 0
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._proxies)

    def next(self) -> Optional[str]:
        if not self._proxies:
            return None
        with self._lock:
            proxy = self._proxies[self._index % len(self._proxies)]
            self._index += 1
            return proxy


class EmailQueue:
    """线程安全的邮箱队列，从文件逐行读取并消费"""

    def __init__(self, filepath: str):
        self._filepath = filepath
        self._emails: List[str] = []
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        if not os.path.exists(self._filepath):
            return
        with open(self._filepath, "r", encoding="utf-8") as f:
            for line in f:
                addr = line.strip()
                if not addr or addr.startswith("#"):
                    continue
                if "----" in addr:
                    addr = addr.split("----")[0].strip()
                if addr and "@" in addr:
                    self._emails.append(addr)

    def pop(self) -> Optional[str]:
        with self._lock:
            if not self._emails:
                return None
            email = self._emails.pop(0)
            self._save_unlocked()
            return email

    def _save_unlocked(self):
        try:
            with open(self._filepath, "w", encoding="utf-8") as f:
                for email in self._emails:
                    f.write(email + "\n")
        except Exception:
            pass

    def __len__(self) -> int:
        with self._lock:
            return len(self._emails)


class OutlookApiCredentialQueue:
    """线程安全的 Outlook 凭据队列（基于状态池，不破坏源文件）"""

    def __init__(
        self,
        filepath: str,
        fallback_client_id: str = "",
        fallback_refresh_token: str = "",
    ):
        self._fallback_client_id = str(fallback_client_id or "").strip()
        self._fallback_refresh_token = str(fallback_refresh_token or "").strip()
        self._store = TaobaoMailboxPool(filepath)
        self._store.sync_from_source()

    def pop(self) -> Optional[Dict[str, str]]:
        item = self._store.acquire_next_new()
        if not item:
            return None

        # 兼容：若池内条目仅包含邮箱，允许用全局兜底。
        if not str(item.get("client_id") or "").strip() and self._fallback_client_id:
            item["client_id"] = self._fallback_client_id
        if not str(item.get("refresh_token") or "").strip() and self._fallback_refresh_token:
            item["refresh_token"] = self._fallback_refresh_token

        if not str(item.get("client_id") or "").strip() or not str(
            item.get("refresh_token") or ""
        ).strip():
            email = str(item.get("email") or "").strip()
            self._store.mark_failed(email, "missing_client_or_refresh_token")
            return None
        return item

    def mark_success(self, email: str) -> None:
        self._store.mark_success(email)

    def mark_failed(self, email: str, reason: str = "") -> None:
        self._store.mark_failed(email, reason)

    def requeue(self, emails: List[str]) -> Dict[str, int]:
        return self._store.requeue(emails)

    def __len__(self) -> int:
        snapshot = self._store.snapshot()
        summary = snapshot.get("summary") if isinstance(snapshot, dict) else {}
        return int((summary or {}).get("unused") or 0)


_email_queue: Optional[EmailQueue] = None
_outlook_api_queue: Optional[OutlookApiCredentialQueue] = None

_thread_mail_ctx = threading.local()


def _set_current_outlook_email(email: str) -> None:
    setattr(_thread_mail_ctx, "outlook_email", str(email or "").strip().lower())


def _get_current_outlook_email() -> str:
    return str(getattr(_thread_mail_ctx, "outlook_email", "") or "").strip().lower()


def _clear_current_outlook_email() -> None:
    if hasattr(_thread_mail_ctx, "outlook_email"):
        delattr(_thread_mail_ctx, "outlook_email")

_outlook_api_credentials: Dict[str, dict] = {}

_luckmail_client = None
_luckmail_token_by_email: Dict[str, str] = {}


def _split_csv_values(raw: str) -> List[str]:
    values: List[str] = []
    seen = set()
    for chunk in str(raw or "").split(","):
        value = chunk.strip()
        if not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        values.append(value)
    return values


def _optional_csv_values(raw: str) -> List[Optional[str]]:
    values = _split_csv_values(raw)
    if not values:
        return [None]
    return values


def _format_luckmail_selector(email_type: Optional[str], domain: Optional[str]) -> str:
    et = email_type or "auto"
    dm = domain or "auto"
    return f"type={et}, domain={dm}"


def _get_luckmail_project_stock_map(client) -> Dict[str, int]:
    stock_map: Dict[str, int] = {}
    try:
        projects = client.user.get_projects(page=1, page_size=500)
        project_list = getattr(projects, "list", []) or []
        target = None
        for item in project_list:
            code = str(getattr(item, "code", "") or "").strip().lower()
            if code == LUCKMAIL_PROJECT_CODE.lower():
                target = item
                break
        if not target:
            return stock_map
        for price in getattr(target, "prices", []) or []:
            email_type = str(getattr(price, "email_type", "") or "").strip()
            if not email_type:
                continue
            raw_stock = getattr(price, "stock", 0)
            try:
                stock = int(raw_stock)
            except Exception:
                stock = 0
            stock_map[email_type] = stock
    except Exception as e:
        print(f"[Warn] LuckMail 获取项目库存失败: {e}")
    return stock_map


def _build_luckmail_purchase_plan(stock_map: Dict[str, int]) -> List[tuple]:
    type_candidates: List[Optional[str]] = _optional_csv_values(LUCKMAIL_EMAIL_TYPE)
    domain_candidates: List[Optional[str]] = _optional_csv_values(LUCKMAIL_DOMAIN)
    has_explicit_domain = any(v for v in domain_candidates if v)

    if stock_map:
        stock_summary = ", ".join(f"{k}:{v}" for k, v in sorted(stock_map.items()))
        print(
            f"[*] LuckMail 项目库存 ({LUCKMAIL_PROJECT_CODE}): {stock_summary}"
        )

    if stock_map and LUCKMAIL_AUTO_SWITCH_EMAIL_TYPE:
        configured_set = {v for v in type_candidates if v}
        stocked_types = [k for k, v in sorted(stock_map.items()) if int(v) > 0]
        if stocked_types:
            if configured_set:
                # 保留用户优先级，同时补上有库存类型作为降级兜底
                for t in stocked_types:
                    if t not in configured_set:
                        type_candidates.append(t)
            else:
                type_candidates = stocked_types

    if stock_map:
        supported_types = {k for k in stock_map.keys() if k}
        if supported_types:
            filtered_types = [
                t for t in type_candidates if (t is None or t in supported_types)
            ]
            if filtered_types:
                type_candidates = filtered_types

    if LUCKMAIL_AUTO_SWITCH_EMAIL_TYPE:
        # 兜底让 LuckMail 自行分配类型/后缀，避免用户手工限定过窄。
        if None not in type_candidates:
            type_candidates.append(None)

        if LUCKMAIL_ALLOW_DOMAIN_AUTO_FALLBACK or not has_explicit_domain:
            if None not in domain_candidates:
                domain_candidates.append(None)
        elif has_explicit_domain:
            print(
                "[*] LuckMail 已启用严格后缀模式：仅尝试配置中的 domain，不使用 auto 后缀。"
            )

    if stock_map:
        selected_types = [v for v in type_candidates if v]
        if selected_types and all(int(stock_map.get(t, 0)) <= 0 for t in selected_types):
            print(
                "[Warn] 当前配置的 LuckMail 邮箱类型库存为 0，建议在面板切换邮箱类型/后缀后再试。"
            )

    plan = list(product(type_candidates, domain_candidates))
    if not plan:
        plan = [(None, None)]

    if len(plan) > 1:
        readable = " | ".join(_format_luckmail_selector(t, d) for t, d in plan)
        print(f"[*] LuckMail 候选下单组合: {readable}")

    return plan


def _get_luckmail_client():
    global _luckmail_client
    if _luckmail_client is not None:
        return _luckmail_client
    if not LUCKMAIL_BASE_URL or not LUCKMAIL_API_KEY:
        raise RuntimeError("LuckMail 未配置: LUCKMAIL_BASE_URL / LUCKMAIL_API_KEY")

    if LUCKMAIL_SDK_PATH and LUCKMAIL_SDK_PATH not in sys.path:
        sys.path.insert(0, LUCKMAIL_SDK_PATH)

    try:
        from luckmail import LuckMailClient
    except Exception as e:
        raise RuntimeError(
            f"无法导入 luckmail SDK，请检查 LUCKMAIL_SDK_PATH: {LUCKMAIL_SDK_PATH}, 错误: {e}"
        )

    _luckmail_client = LuckMailClient(
        base_url=LUCKMAIL_BASE_URL,
        api_key=LUCKMAIL_API_KEY,
    )
    return _luckmail_client


def _ssl_verify() -> bool:
    return True


def _skip_net_check() -> bool:
    return False


GMAIL_BASE = os.getenv("GMAIL_BASE", "").strip()


def get_email_and_token(proxies: Any = None) -> tuple:
    """根据 EMAIL_MODE 获取邮箱: file=从accounts.txt读取, cf=自有域名随机生成, hotmail007=API拉取微软邮箱, outlook_api=凭据文件+GetLastEmails接码, gmail=Gmail别名, luckmail=LuckMail购买邮箱"""
    if EMAIL_MODE == "luckmail":
        try:
            client = _get_luckmail_client()
            stock_map = _get_luckmail_project_stock_map(client)
            purchase_plan = _build_luckmail_purchase_plan(stock_map)

            all_zero_stock = bool(stock_map) and all(
                int(v) <= 0 for v in stock_map.values()
            )
            if all_zero_stock:
                if LUCKMAIL_STOCK_HARD_BLOCK:
                    print(
                        "[Error] LuckMail 当前项目各邮箱类型库存均为 0，按配置停止下单。"
                    )
                    print(
                        "[Tip] 请在 LuckMail 控制台点击“查看后缀”，调整邮箱类型与后缀后再试。"
                    )
                    return "", ""
                print(
                    "[Warn] LuckMail API 返回库存全 0，但该字段可能与网页口径不一致，将继续按候选组合实单探测。"
                )

            configured_attempts = max(1, int(LUCKMAIL_PURCHASE_MAX_ATTEMPTS or 1))
            max_attempts = max(configured_attempts, len(purchase_plan))
            unsupported_types: set[str] = set()
            if max_attempts > configured_attempts:
                print(
                    f"[*] LuckMail 尝试次数提升为 {max_attempts}，以覆盖全部候选组合"
                )
            for purchase_attempt in range(1, max_attempts + 1):
                email_type, domain = purchase_plan[(purchase_attempt - 1) % len(purchase_plan)]
                selector = _format_luckmail_selector(email_type, domain)

                if email_type and email_type in unsupported_types:
                    if purchase_attempt < max_attempts:
                        continue
                    print(
                        "[Error] LuckMail 候选组合均不可用（无库存或不支持邮箱类型）"
                    )
                    return "", ""

                try:
                    result = client.user.purchase_emails(
                        project_code=LUCKMAIL_PROJECT_CODE,
                        quantity=1,
                        email_type=email_type,
                        domain=domain,
                    )
                except Exception as purchase_err:
                    err_code = getattr(purchase_err, "code", None)
                    err_text = str(purchase_err or "")
                    if err_code is None:
                        m = re.search(r"API Error \[(\d+)\]", err_text)
                        if m:
                            try:
                                err_code = int(m.group(1))
                            except Exception:
                                err_code = None

                    unsupported_type = (
                        "不支持邮箱类型" in err_text
                        or "unsupported email type" in err_text.lower()
                    )
                    if unsupported_type and email_type:
                        unsupported_types.add(email_type)
                        print(
                            f"[Warn] LuckMail 项目不支持邮箱类型 {email_type}，已从后续候选中移除"
                        )
                        if purchase_attempt < max_attempts:
                            continue
                        print("[Error] LuckMail 候选类型均不受当前项目支持")
                        return "", ""

                    if err_code == 429 or "过于频繁" in err_text or "429" in err_text:
                        backoff = min(12.0, 1.5 + purchase_attempt * 0.8)
                        print(
                            f"[Warn] LuckMail 请求频率受限(429)，退避 {backoff:.1f}s 后重试..."
                        )
                        time.sleep(backoff)
                        if purchase_attempt < max_attempts:
                            continue
                        return "", ""

                    if err_code == 2003:
                        print(
                            f"[Warn] LuckMail 无库存 ({selector})，尝试下一个组合..."
                        )
                        if purchase_attempt < max_attempts:
                            time.sleep(0.8)
                            continue
                        print("[Error] LuckMail 候选组合已全部尝试，均返回无库存(2003)")
                        print(
                            "[Tip] 这通常是接口库存口径差异或当前账号可购库存为空，请切换后缀/类型或改用 JWT 用户接口重试。"
                        )
                        return "", ""
                    raise

                purchases = (result or {}).get("purchases") or []
                if not purchases:
                    print(f"[Error] LuckMail 购买邮箱返回为空 ({selector}): {result}")
                    return "", ""

                item = purchases[0] or {}
                email = str(item.get("email_address") or "").strip()
                token = str(item.get("token") or "").strip()
                purchase_id = item.get("id")
                if not email or not token:
                    print(f"[Error] LuckMail 返回缺少 email/token: {item}")
                    return "", ""

                print(
                    f"[*] LuckMail 购买邮箱成功: {email} ({selector}, attempt {purchase_attempt}/{max_attempts})"
                )

                if not LUCKMAIL_PRECHECK_ENABLED:
                    _luckmail_token_by_email[email] = token
                    return email, token

                alive_ok = False
                last_msg = ""
                retries = max(0, int(LUCKMAIL_PRECHECK_RETRIES or 0))
                for chk in range(retries + 1):
                    try:
                        alive = client.user.check_token_alive(token)
                        is_alive = bool(getattr(alive, "alive", False))
                        mail_count = getattr(alive, "mail_count", None)
                        status = str(getattr(alive, "status", "") or "")
                        message = str(getattr(alive, "message", "") or "")
                        if is_alive:
                            print(
                                f"[*] LuckMail 可用性检测通过: alive={is_alive}, mail_count={mail_count}, status={status}"
                            )
                            alive_ok = True
                            break
                        last_msg = f"alive={is_alive}, mail_count={mail_count}, status={status}, message={message}"
                    except Exception as e:
                        last_msg = str(e)
                    if chk < retries:
                        time.sleep(1)

                if alive_ok:
                    _luckmail_token_by_email[email] = token
                    return email, token

                print(f"[Warn] LuckMail 可用性检测未通过，重买邮箱: {last_msg}")
                try:
                    if purchase_id:
                        client.user.set_purchase_disabled(int(purchase_id), 1)
                        print(
                            f"[*] 已将疑似死号标记禁用: purchase_id={purchase_id}, email={email}"
                        )
                except Exception as e:
                    print(f"[Warn] 标记禁用失败: {e}")

            print("[Error] LuckMail 连续多次购买均未通过可用性检测")
            return "", ""
        except Exception as e:
            print(f"[Error] LuckMail 获取邮箱失败: {e}")
            if "API Error [2003]" in str(e):
                print("[Tip] 官方提示为无库存时，请切换邮箱类型和邮箱后缀（域名）后重试。")
            return "", ""

    if EMAIL_MODE == "gmail":
        if not GMAIL_BASE:
            print("[Error] GMAIL_BASE 未配置")
            return "", ""
        suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
        email = f"{GMAIL_BASE}+{suffix}@gmail.com"
        print(f"[*] 生成 Gmail 别名邮箱: {email}")
        return email, email
    if EMAIL_MODE == "file":
        if _email_queue is None:
            print("[Error] 邮箱队列未初始化")
            return "", ""
        email = _email_queue.pop()
        if not email:
            print("[Error] accounts.txt 中没有可用的邮箱了")
            return "", ""
        print(f"[*] 从文件读取邮箱: {email} (剩余: {len(_email_queue)})")
        return email, email
    if EMAIL_MODE == "hotmail007":
        if not HOTMAIL007_API_KEY:
            print("[Error] HOTMAIL007_API_KEY 未配置")
            return "", ""
        mails, err = hotmail007_get_mail(quantity=1, proxies=proxies)
        if err or not mails:
            print(f"[Error] Hotmail007 拉取邮箱失败: {err}")
            return "", ""
        mail_info = mails[0]
        email = mail_info["email"]
        _hotmail007_credentials[email] = {
            "client_id": mail_info["client_id"],
            "refresh_token": mail_info["refresh_token"],
            "ms_password": mail_info["password"],
        }
        print(f"[*] Hotmail007 预获取已有邮件ID...")
        known_ids = _outlook_get_known_ids(
            email, mail_info["client_id"], mail_info["refresh_token"], proxies
        )
        _hotmail007_credentials[email]["known_ids"] = known_ids
        return email, email
    if EMAIL_MODE == "outlook_api":
        _clear_current_outlook_email()
        if _outlook_api_queue is None:
            print("[Error] Outlook API 凭据队列未初始化")
            return "", ""

        item = _outlook_api_queue.pop()
        if not item:
            print("[Error] Outlook API 凭据队列已耗尽")
            return "", ""

        email = str(item.get("email") or "").strip()
        client_id = str(item.get("client_id") or "").strip()
        refresh_token = str(item.get("refresh_token") or "").strip()
        if not email or not client_id or not refresh_token:
            print(f"[Error] Outlook API 凭据不完整: {item}")
            return "", ""

        _outlook_api_credentials[email] = {
            "client_id": client_id,
            "refresh_token": refresh_token,
        }
        _set_current_outlook_email(email)
        if OUTLOOK_API_URL:
            print(f"[*] Outlook API 预获取已有邮件ID...")
            known_ids = _outlook_api_get_known_ids(
                email, client_id, refresh_token, proxies
            )
        else:
            print(f"[*] Outlook 直连模式预获取已有邮件ID...")
            known_ids = _outlook_get_known_ids(email, client_id, refresh_token, proxies)
        _outlook_api_credentials[email]["known_ids"] = known_ids
        return email, email
    prefix = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    email = f"{prefix}@{MAIL_DOMAIN}"
    return email, email


def _manual_input_code(
    token: str, email: str, proxies: Any = None, seen_ids: set = None
) -> str:
    """手动输入验证码"""
    print(f"\n[*] 请查看你的 Gmail 收件箱 ({email}) 中的 OpenAI 验证码")
    print(f"[*] 邮件来自: noreply@tm.openai.com 或 otp@tm1.openai.com")
    print(f"[*] 邮件主题: Your ChatGPT code is XXXXXX")
    code = input("[*] 请输入 6 位验证码 (输入 q 取消): ").strip()
    if code.lower() == "q":
        return ""
    if re.match(r"^\d{6}$", code):
        print(f"[*] 收到验证码: {code}")
        return code
    print(f"[Error] 验证码格式错误，请输入 6 位数字")
    return ""


def _extract_otp_code(content: str) -> str:
    if not content:
        return ""
    patterns = [
        r"Your ChatGPT code is\s*(\d{6})",
        r"ChatGPT code is\s*(\d{6})",
        r"verification code to continue:\s*(\d{6})",
        r"Subject:.*?(\d{6})",
    ]
    for pattern in patterns:
        match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1)
    fallback = re.search(r"(?<!\d)(\d{6})(?!\d)", content)
    return fallback.group(1) if fallback else ""


def get_oai_code(
    token: str, email: str, proxies: Any = None, seen_ids: set = None
) -> str:
    """根据 EMAIL_MODE 获取 OpenAI 验证码: cf=Cloudflare Worker, hotmail007=Outlook Graph/IMAP, outlook_api=GetLastEmails 接码, gmail=手动输入, luckmail=Token邮件轮询"""
    if EMAIL_MODE == "luckmail":
        try:
            client = _get_luckmail_client()
            lm_token = token or _luckmail_token_by_email.get(email, "")
            if not lm_token:
                print(f"[Error] LuckMail 未找到 {email} 对应 token")
                return ""
            print(
                f"[*] LuckMail 开始轮询验证码 (邮箱: {email}, token: {lm_token[:10]}..., 收件直连不走注册代理)"
            )
            before_ids = set(seen_ids or set())
            start = time.time()
            timeout_sec = max(20, int(LUCKMAIL_OTP_TIMEOUT or 60))
            poll_count = 0
            while time.time() - start < timeout_sec:
                poll_count += 1
                try:
                    mail_list = client.user.get_token_mails(lm_token)
                    mails = getattr(mail_list, "mails", []) or []
                    if mails and (poll_count <= 3 or poll_count % 10 == 0):
                        latest = mails[0]
                        latest_subj = str(getattr(latest, "subject", "") or "")[:80]
                        latest_from = str(getattr(latest, "from_addr", "") or "")[:80]
                        print(
                            f"[*] LuckMail 轮询#{poll_count}: mails={len(mails)}, latest_from={latest_from}, latest_subject={latest_subj}"
                        )
                    elif (not mails) and poll_count % 10 == 0:
                        print(f"[*] LuckMail 轮询#{poll_count}: 暂无邮件")

                    for mail in mails:
                        msg_id = str(getattr(mail, "message_id", "") or "")
                        if msg_id and msg_id in before_ids:
                            continue
                        if msg_id:
                            before_ids.add(msg_id)
                        content = " ".join(
                            [
                                str(getattr(mail, "subject", "") or ""),
                                str(getattr(mail, "body", "") or ""),
                                str(getattr(mail, "html_body", "") or ""),
                            ]
                        )
                        code = _extract_otp_code(content)
                        if code:
                            print(f"[*] LuckMail 抓到验证码(get_token_mails): {code}")
                            return code
                except Exception as e:
                    if poll_count <= 3 or poll_count % 10 == 0:
                        print(f"[Warn] LuckMail get_token_mails 异常: {e}")

                # 兜底: 直接调 token/code 接口，避免邮件列表延迟导致错过 OTP
                try:
                    code_result = client.user.get_token_code(lm_token)
                    has_new_mail = bool(getattr(code_result, "has_new_mail", False))
                    direct_code = str(
                        getattr(code_result, "verification_code", "") or ""
                    )
                    if direct_code:
                        m = re.search(r"(?<!\d)(\d{6})(?!\d)", direct_code)
                        final_code = m.group(1) if m else direct_code
                        print(f"[*] LuckMail 抓到验证码(get_token_code): {final_code}")
                        return final_code
                    if has_new_mail and (poll_count <= 3 or poll_count % 10 == 0):
                        print(
                            f"[*] LuckMail get_token_code 提示有新邮件，但暂未提取到验证码"
                        )
                except Exception as e:
                    if poll_count <= 3 or poll_count % 10 == 0:
                        print(f"[Warn] LuckMail get_token_code 异常: {e}")

                time.sleep(3)

            # 超时后做一次诊断，帮助定位是没来信还是 token 本身失效
            try:
                alive = client.user.check_token_alive(lm_token)
                print(
                    f"[Diag] LuckMail alive={getattr(alive, 'alive', None)}, mail_count={getattr(alive, 'mail_count', None)}, status={getattr(alive, 'status', '')}, message={getattr(alive, 'message', '')}"
                )
            except Exception as e:
                print(f"[Diag] LuckMail check_token_alive 异常: {e}")

            print("[Error] LuckMail 等待验证码超时")
            return ""
        except Exception as e:
            print(f"[Error] LuckMail 获取验证码失败: {e}")
            return ""

    if EMAIL_MODE == "gmail":
        return _manual_input_code(token, email, proxies, seen_ids)
    if EMAIL_MODE == "hotmail007":
        creds = _hotmail007_credentials.get(email, {})
        if not creds:
            print(f"[Error] 未找到 {email} 的 Hotmail007 凭据")
            return ""
        known_ids = creds.get("known_ids", set())
        return _outlook_fetch_otp(
            email,
            creds["client_id"],
            creds["refresh_token"],
            known_ids=known_ids,
            proxies=proxies,
            timeout=120,
        )
    if EMAIL_MODE == "outlook_api":
        creds = _outlook_api_credentials.get(email, {})
        if not creds:
            print(f"[Error] 未找到 {email} 的 Outlook API 凭据")
            return ""
        client_id = str(creds.get("client_id") or "").strip()
        refresh_token = str(creds.get("refresh_token") or "").strip()
        known_ids = set(creds.get("known_ids") or set())
        if OUTLOOK_API_URL:
            return _outlook_api_fetch_otp(
                email,
                client_id,
                refresh_token,
                known_ids=known_ids,
                proxies=proxies,
                timeout=OUTLOOK_API_POLL_TIMEOUT,
            )
        return _outlook_fetch_otp(
            email,
            client_id,
            refresh_token,
            known_ids=known_ids,
            proxies=proxies,
            timeout=OUTLOOK_API_POLL_TIMEOUT,
        )
    headers = {
        "x-admin-auth": MAIL_ADMIN_PASSWORD,
        "Content-Type": "application/json",
    }
    if seen_ids is None:
        seen_ids = set()
    print(f"[*] 正在等待邮箱 {email} 的验证码...", end="", flush=True)

    for _ in range(40):
        print(".", end="", flush=True)
        try:
            res = requests.get(
                f"{MAIL_WORKER_BASE}/admin/mails",
                params={"limit": 5, "offset": 0, "address": email},
                headers=headers,
                proxies=proxies,
                impersonate="safari",
                verify=_ssl_verify(),
                timeout=15,
            )
            if res.status_code == 200:
                j = res.json()
                results = j.get("results") or []
                for mail in results:
                    mail_id = mail.get("id")
                    if mail_id in seen_ids:
                        continue
                    seen_ids.add(mail_id)
                    raw = mail.get("raw") or ""
                    content = raw
                    subj_match = re.search(r"^Subject:\s*(.+)$", raw, re.MULTILINE)
                    if subj_match:
                        content = subj_match.group(1) + "\n" + raw
                    code = _extract_otp_code(content)
                    if code:
                        print(" 抓到啦! 验证码:", code)
                        return code
        except Exception:
            pass

        time.sleep(3)

    print(" 超时，未收到验证码")
    return ""


def delete_temp_email(email: str, proxies: Any = None) -> None:
    """注册成功后清理邮箱: hotmail007/outlook_api 模式仅清理本地凭据, cf模式删除Worker邮件"""
    if EMAIL_MODE == "luckmail":
        _luckmail_token_by_email.pop(email, None)
        print(f"[*] LuckMail 邮箱 {email} 本地 token 已清理")
        return
    if EMAIL_MODE == "hotmail007":
        _hotmail007_credentials.pop(email, None)
        print(f"[*] Hotmail007 邮箱 {email} 本地凭据已清理")
        return
    if EMAIL_MODE == "outlook_api":
        _outlook_api_credentials.pop(email, None)
        print(f"[*] Outlook API 邮箱 {email} 本地凭据已清理")
        return
    headers = {
        "x-admin-auth": MAIL_ADMIN_PASSWORD,
        "Content-Type": "application/json",
    }
    try:
        res = requests.get(
            f"{MAIL_WORKER_BASE}/admin/mails",
            params={"limit": 50, "offset": 0, "address": email},
            headers=headers,
            proxies=proxies,
            impersonate="safari",
            verify=_ssl_verify(),
            timeout=15,
        )
        if res.status_code == 200:
            for mail in res.json().get("results") or []:
                mail_id = mail.get("id")
                if mail_id:
                    requests.delete(
                        f"{MAIL_WORKER_BASE}/admin/mails/{mail_id}",
                        headers=headers,
                        proxies=proxies,
                        impersonate="safari",
                        verify=_ssl_verify(),
                        timeout=10,
                    )
        print(f"[*] 临时邮箱 {email} 的邮件已清理")
    except Exception as e:
        print(f"[*] 清理临时邮箱时出错: {e}")


# ==========================================
# Hotmail007 API & Outlook OTP
# ==========================================

_hotmail007_credentials: Dict[str, dict] = {}


def _outlook_api_get_last_emails(
    email: str,
    client_id: str,
    refresh_token: str,
    proxies: Any = None,
    *,
    num: Optional[int] = None,
    box_type: Optional[int] = None,
) -> tuple:
    if not OUTLOOK_API_URL:
        return [], "OUTLOOK_API_URL 未配置"

    req_num = OUTLOOK_API_NUM if num is None else int(num)
    req_num = max(1, min(2, req_num))
    req_box_type = OUTLOOK_API_BOX_TYPE if box_type is None else int(box_type)
    req_box_type = 2 if req_box_type == 2 else 1

    params = {
        "email": str(email or "").strip(),
        "clientId": str(client_id or "").strip(),
        "refreshToken": str(refresh_token or "").strip(),
        "num": req_num,
        "boxType": req_box_type,
    }
    if not params["email"] or not params["clientId"] or not params["refreshToken"]:
        return [], "缺少 email/clientId/refreshToken"

    try:
        resp = requests.get(
            f"{OUTLOOK_API_URL}/api/GetLastEmails",
            params=params,
            proxies=proxies,
            verify=_ssl_verify(),
            timeout=OUTLOOK_API_TIMEOUT,
            impersonate="safari",
        )
    except Exception as e:
        return [], f"请求异常: {str(e)[:200]}"

    try:
        payload = resp.json()
    except Exception:
        return [], f"接口返回非 JSON: HTTP {resp.status_code}"

    if resp.status_code >= 400:
        return [], f"HTTP {resp.status_code}: {str(payload)[:200]}"

    code = int(payload.get("code", 0) or 0) if isinstance(payload, dict) else 0
    if code != 200:
        message = ""
        if isinstance(payload, dict):
            message = str(payload.get("message") or "")
        return [], f"接口错误 code={code} message={message}"

    data = payload.get("data", []) if isinstance(payload, dict) else []
    if not isinstance(data, list):
        return [], "响应 data 不是列表"

    mails = [item for item in data if isinstance(item, dict)]
    return mails, ""


def _outlook_api_mail_digest(mail: Dict[str, Any]) -> str:
    body = str(mail.get("Body") or "")
    # Body 可能很大，摘要时截断可降低内存与日志压力。
    raw = "|".join(
        [
            str(mail.get("Date") or ""),
            str(mail.get("From") or ""),
            str(mail.get("To") or ""),
            str(mail.get("Subject") or ""),
            body[:2000],
        ]
    )
    return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()


def _outlook_api_get_known_ids(
    email: str,
    client_id: str,
    refresh_token: str,
    proxies: Any = None,
) -> set:
    mails, err = _outlook_api_get_last_emails(
        email,
        client_id,
        refresh_token,
        proxies=proxies,
        num=OUTLOOK_API_NUM,
        box_type=OUTLOOK_API_BOX_TYPE,
    )
    if err:
        print(f"[Warn] Outlook API 预取历史邮件失败: {err}")
        return set()
    return {_outlook_api_mail_digest(mail) for mail in mails}


def _outlook_api_fetch_otp(
    email: str,
    client_id: str,
    refresh_token: str,
    known_ids: set,
    proxies: Any = None,
    timeout: int = 120,
) -> str:
    seen_ids = set(known_ids or set())
    timeout_sec = max(20, int(timeout or OUTLOOK_API_POLL_TIMEOUT))
    poll_interval = max(1, int(OUTLOOK_API_POLL_INTERVAL or 3))
    start = time.time()
    poll_count = 0

    while time.time() - start < timeout_sec:
        poll_count += 1
        mails, err = _outlook_api_get_last_emails(
            email,
            client_id,
            refresh_token,
            proxies=proxies,
            num=OUTLOOK_API_NUM,
            box_type=OUTLOOK_API_BOX_TYPE,
        )

        if err:
            if poll_count <= 3 or poll_count % 10 == 0:
                print(f"[Warn] Outlook API 拉信异常: {err}")
            time.sleep(poll_interval)
            continue

        if mails and (poll_count <= 3 or poll_count % 10 == 0):
            latest = mails[0]
            latest_from = str(latest.get("From") or "")[:80]
            latest_subject = str(latest.get("Subject") or "")[:80]
            print(
                f"[*] Outlook API 轮询#{poll_count}: mails={len(mails)}, latest_from={latest_from}, latest_subject={latest_subject}"
            )
        elif (not mails) and poll_count % 10 == 0:
            print(f"[*] Outlook API 轮询#{poll_count}: 暂无邮件")

        for mail in mails:
            mid = _outlook_api_mail_digest(mail)
            if mid in seen_ids:
                continue
            seen_ids.add(mid)

            content = " ".join(
                [
                    str(mail.get("Subject") or ""),
                    str(mail.get("From") or ""),
                    str(mail.get("To") or ""),
                    str(mail.get("Body") or ""),
                ]
            )
            code = _extract_otp_code(content)
            if code:
                print(f"[*] Outlook API 抓到验证码: {code}")
                return code

        time.sleep(poll_interval)

    print("[Error] Outlook API 等待验证码超时")
    return ""


def _hotmail007_api_get(path: str, proxies: Any = None, **params) -> dict:
    url = f"{HOTMAIL007_API_URL}/{path.lstrip('/')}"
    if params:
        qs = "&".join(
            f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items() if v
        )
        url = f"{url}?{qs}"
    try:
        r = requests.get(
            url, proxies=proxies, verify=_ssl_verify(), timeout=15, impersonate="safari"
        )
        return r.json()
    except Exception as e:
        return {"success": False, "message": str(e)[:200]}


def hotmail007_get_balance(proxies: Any = None) -> tuple:
    data = _hotmail007_api_get(
        "api/user/balance", proxies=proxies, clientKey=HOTMAIL007_API_KEY
    )
    if data.get("success") and data.get("code") == 0:
        return data.get("data"), None
    return None, data.get("message", "查询余额失败")


def hotmail007_get_stock(proxies: Any = None) -> tuple:
    params = {"clientKey": HOTMAIL007_API_KEY}
    if HOTMAIL007_MAIL_TYPE:
        params["mailType"] = HOTMAIL007_MAIL_TYPE
    data = _hotmail007_api_get("api/mail/getStock", proxies=proxies, **params)
    if data.get("success") and data.get("code") == 0:
        raw = data.get("data")
        if isinstance(raw, (int, float)):
            return int(raw), None
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    t = (item.get("type") or "").strip().lower()
                    if t == HOTMAIL007_MAIL_TYPE.strip().lower():
                        return int(item.get("stock", 0)), None
            total = sum(
                int(item.get("stock", 0)) for item in raw if isinstance(item, dict)
            )
            return total, None
        return 0, None
    return None, data.get("message", "查询库存失败")


def hotmail007_get_mail(quantity: int = 1, proxies: Any = None) -> tuple:
    data = _hotmail007_api_get(
        "api/mail/getMail",
        proxies=proxies,
        clientKey=HOTMAIL007_API_KEY,
        mailType=HOTMAIL007_MAIL_TYPE,
        quantity=quantity,
    )
    if not data.get("success") or data.get("code") != 0:
        return [], data.get("message", "拉取邮箱失败")
    raw_list = data.get("data") or []
    out = []
    for raw in raw_list:
        if not isinstance(raw, str):
            continue
        parts = raw.split(":")
        if len(parts) < 4:
            continue
        email_addr = parts[0].strip()
        pwd = parts[1].strip()
        cid = parts[-1].strip()
        rtk = ":".join(parts[2:-1]).strip()
        if email_addr:
            out.append(
                {
                    "email": email_addr,
                    "password": pwd,
                    "refresh_token": rtk,
                    "client_id": cid,
                }
            )
    if not out:
        return [], "API 返回数据解析为空"
    return out, ""


def _outlook_get_graph_token(
    client_id: str, refresh_token: str, proxies: Any = None
) -> str:
    url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    data = {
        "client_id": client_id,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": "https://graph.microsoft.com/.default",
    }
    r = requests.post(
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        proxies=proxies,
        verify=_ssl_verify(),
        timeout=30,
        impersonate="safari",
    )
    j = r.json()
    if not j.get("access_token"):
        err = j.get("error_description", j.get("error", str(j)))
        if "service abuse" in (err or "").lower():
            raise Exception(f"账号被封禁: {err}")
        raise Exception(f"Graph token 失败: {err[:150]}")
    return j["access_token"]


def _outlook_get_imap_token(
    client_id: str, refresh_token: str, proxies: Any = None, email_addr: str = ""
) -> tuple:
    import imaplib as _imaplib

    methods = [
        {
            "url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
            "data": {
                "client_id": client_id,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": "https://outlook.office365.com/IMAP.AccessAsUser.All offline_access",
            },
            "imap_server": "outlook.office365.com",
        },
        {
            "url": "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
            "data": {
                "client_id": client_id,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": "https://outlook.office365.com/IMAP.AccessAsUser.All offline_access",
            },
            "imap_server": "outlook.office365.com",
        },
        {
            "url": "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
            "data": {
                "client_id": client_id,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": "https://outlook.office.com/IMAP.AccessAsUser.All offline_access",
            },
            "imap_server": "outlook.live.com",
        },
        {
            "url": "https://login.live.com/oauth20_token.srf",
            "data": {
                "client_id": client_id,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            "imap_server": "outlook.office365.com",
        },
    ]
    last_err = ""
    for idx, m in enumerate(methods):
        try:
            r = requests.post(
                m["url"],
                data=m["data"],
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                proxies=proxies,
                verify=_ssl_verify(),
                timeout=30,
                impersonate="safari",
            )
            j = r.json()
            if not j.get("access_token"):
                last_err = j.get("error_description", j.get("error", str(j)))
                if "service abuse" in (last_err or "").lower():
                    raise Exception(f"账号被封禁: {last_err}")
                continue
            token = j["access_token"]
            server = m["imap_server"]
            if email_addr:
                try:
                    imap_test = _imaplib.IMAP4_SSL(server, 993)
                    auth_str = f"user={email_addr}auth=Bearer {token}"
                    imap_test.authenticate(
                        "XOAUTH2", lambda x: auth_str.encode("utf-8")
                    )
                    imap_test.select("INBOX")
                    imap_test.logout()
                    print(f"[IMAP] 方法{idx + 1}验证通过: {server}")
                    return token, server
                except Exception as ve:
                    last_err = f"方法{idx + 1} SELECT失败({server}): {ve}"
                    print(f"[IMAP] {last_err}")
                    continue
            else:
                return token, server
        except Exception as e:
            if "封禁" in str(e):
                raise
            last_err = str(e)
    raise Exception(f"IMAP 所有方法均失败: {last_err[:200]}")


def _outlook_graph_get_openai_messages(
    access_token: str, proxies: Any = None, top: int = 10
) -> list:
    all_items = []
    headers_dict = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    params = {
        "$select": "id,subject,body,from,receivedDateTime",
        "$orderby": "receivedDateTime desc",
        "$top": str(top * 5),
    }
    for folder in ["inbox", "junkemail"]:
        url = f"https://graph.microsoft.com/v1.0/me/mailFolders/{folder}/messages"
        try:
            r = requests.get(
                url,
                params=params,
                headers=headers_dict,
                proxies=proxies,
                verify=_ssl_verify(),
                timeout=30,
                impersonate="safari",
            )
            if r.status_code == 200:
                all_items.extend(r.json().get("value", []))
        except Exception:
            pass
    if not all_items:
        url_all = "https://graph.microsoft.com/v1.0/me/messages"
        try:
            r = requests.get(
                url_all,
                params=params,
                headers=headers_dict,
                proxies=proxies,
                verify=_ssl_verify(),
                timeout=30,
                impersonate="safari",
            )
            if r.status_code == 200:
                all_items = r.json().get("value", [])
        except Exception:
            pass
    return [
        m
        for m in all_items
        if "openai.com"
        in (m.get("from") or {}).get("emailAddress", {}).get("address", "").lower()
    ]


def _outlook_graph_extract_otp(message: dict) -> str:
    subject = message.get("subject", "")
    body_content = (message.get("body") or {}).get("content", "")
    text = subject + "\n" + body_content
    for pat in [
        r">\s*(\d{6})\s*<",
        r"code[:\s]+(\d{6})",
        r"(\d{6})\s*\n",
        r"(?<!\d)(\d{6})(?!\d)",
    ]:
        m = re.search(pat, text, re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(1)
    return ""


def _outlook_get_known_ids(
    email_addr: str, client_id: str, refresh_token: str, proxies: Any = None
) -> set:
    try:
        token = _outlook_get_graph_token(client_id, refresh_token, proxies)
        messages = _outlook_graph_get_openai_messages(token, proxies)
        known = {m["id"] for m in messages}
        print(f"[Graph] 已有 {len(known)} 封 OpenAI 邮件")
        return known
    except Exception as e:
        print(f"[Graph] 获取已有邮件失败: {e}")
        return set()


def _outlook_fetch_otp_graph(
    email_addr: str,
    client_id: str,
    refresh_token: str,
    known_ids: set,
    proxies: Any = None,
    timeout: int = 120,
) -> str:
    try:
        access_token = _outlook_get_graph_token(client_id, refresh_token, proxies)
    except Exception as e:
        print(f"[Graph] access token 失败: {e}")
        return ""
    _graph_debug_done = False
    print(
        f"[Graph] 轮询收件箱(最多{timeout}s, 已知{len(known_ids)}封)...",
        end="",
        flush=True,
    )
    start = time.time()
    while time.time() - start < timeout:
        print(".", end="", flush=True)
        try:
            messages = _outlook_graph_get_openai_messages(access_token, proxies)
            if not _graph_debug_done:
                _graph_debug_done = True
                headers_dict = {
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                }
                for dbg_folder in ["inbox", "junkemail"]:
                    try:
                        dbg_r = requests.get(
                            f"https://graph.microsoft.com/v1.0/me/mailFolders/{dbg_folder}/messages",
                            params={
                                "$top": "3",
                                "$select": "id,subject,from,receivedDateTime",
                            },
                            headers=headers_dict,
                            proxies=proxies,
                            verify=_ssl_verify(),
                            timeout=15,
                            impersonate="safari",
                        )
                        if dbg_r.status_code == 200:
                            dbg_msgs = dbg_r.json().get("value", [])
                            print(
                                f"\n[Graph调试] {dbg_folder}: {len(dbg_msgs)}封邮件",
                                end="",
                                flush=True,
                            )
                            for dm in dbg_msgs[:3]:
                                fr = (
                                    (dm.get("from") or {})
                                    .get("emailAddress", {})
                                    .get("address", "?")
                                )
                                subj = (dm.get("subject") or "")[:40]
                                print(
                                    f"\n  - from={fr} subj={subj}", end="", flush=True
                                )
                        else:
                            print(
                                f"\n[Graph调试] {dbg_folder}: HTTP {dbg_r.status_code}",
                                end="",
                                flush=True,
                            )
                    except Exception as de:
                        print(
                            f"\n[Graph调试] {dbg_folder}异常: {de}", end="", flush=True
                        )
            all_ids = {m["id"] for m in messages}
            new_ids = all_ids - known_ids
            for msg in [m for m in messages if m["id"] in new_ids]:
                code = _outlook_graph_extract_otp(msg)
                if code:
                    print(f" 抓到啦! 验证码: {code}")
                    return code
        except Exception as e:
            print(f"\n[Graph] 轮询出错: {e}", end="", flush=True)
        time.sleep(3)
    print(" 超时，未收到验证码")
    return ""


def _outlook_fetch_otp_imap(
    email_addr: str,
    client_id: str,
    refresh_token: str,
    known_ids: set,
    proxies: Any = None,
    timeout: int = 120,
) -> str:
    import imaplib
    import email as email_lib

    try:
        access_token, imap_server = _outlook_get_imap_token(
            client_id, refresh_token, proxies, email_addr=email_addr
        )
    except Exception as e:
        print(f"[IMAP] access token 失败: {e}")
        return ""
    print(
        f"[IMAP] 轮询收件箱(最多{timeout}s, 已知{len(known_ids)}封)...",
        end="",
        flush=True,
    )
    start = time.time()
    while time.time() - start < timeout:
        print(".", end="", flush=True)
        try:
            imap = imaplib.IMAP4_SSL(imap_server, 993)
            auth_str = f"user={email_addr}\x01auth=Bearer {access_token}\x01\x01"
            imap.authenticate("XOAUTH2", lambda x: auth_str.encode("utf-8"))
            try:
                imap.select("INBOX")
                status, msg_ids = imap.search(None, '(FROM "noreply@tm.openai.com")')
                if status != "OK" or not msg_ids[0]:
                    status, msg_ids = imap.search(None, '(FROM "openai.com")')
                if status == "OK" and msg_ids[0]:
                    all_ids = set(msg_ids[0].split())
                    new_ids = all_ids - known_ids
                    for mid in sorted(new_ids, key=lambda x: int(x), reverse=True):
                        st, msg_data = imap.fetch(mid, "(RFC822)")
                        if st != "OK":
                            continue
                        msg = email_lib.message_from_bytes(msg_data[0][1])
                        body = ""
                        if msg.is_multipart():
                            for part in msg.walk():
                                if part.get_content_type() in (
                                    "text/plain",
                                    "text/html",
                                ):
                                    try:
                                        body += (
                                            part.get_payload(decode=True) or b""
                                        ).decode(
                                            part.get_content_charset() or "utf-8",
                                            errors="ignore",
                                        )
                                    except Exception:
                                        pass
                        else:
                            try:
                                body = (msg.get_payload(decode=True) or b"").decode(
                                    msg.get_content_charset() or "utf-8",
                                    errors="ignore",
                                )
                            except Exception:
                                pass
                        code = _extract_otp_code(body)
                        if code:
                            print(f" 抓到啦! 验证码: {code}")
                            return code
            finally:
                try:
                    imap.logout()
                except Exception:
                    pass
        except Exception as e:
            err_str = str(e)
            print(f"\n[IMAP] 轮询出错: {e}", end="", flush=True)
            if (
                "not connected" in err_str.lower()
                or "authenticated but not connected" in err_str.lower()
            ):
                try:
                    access_token, imap_server = _outlook_get_imap_token(
                        client_id, refresh_token, proxies, email_addr=email_addr
                    )
                    time.sleep(1)
                    continue
                except Exception:
                    pass
        time.sleep(3)
    print(" 超时，未收到验证码")
    return ""


def _outlook_fetch_otp(
    email_addr: str,
    client_id: str,
    refresh_token: str,
    known_ids: set = None,
    proxies: Any = None,
    timeout: int = 120,
) -> str:
    if known_ids is None:
        known_ids = set()
    return _outlook_fetch_otp_graph(
        email_addr, client_id, refresh_token, known_ids, proxies, timeout
    )


# ==========================================
# OAuth 授权与辅助函数
# ==========================================

AUTH_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"

DEFAULT_REDIRECT_URI = f"http://localhost:1455/auth/callback"
DEFAULT_SCOPE = "openid email profile offline_access"


def _b64url_no_pad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _sha256_b64url_no_pad(s: str) -> str:
    return _b64url_no_pad(hashlib.sha256(s.encode("ascii")).digest())


def _random_state(nbytes: int = 16) -> str:
    return secrets.token_urlsafe(nbytes)


def _pkce_verifier() -> str:
    return secrets.token_urlsafe(64)


def _parse_callback_url(callback_url: str) -> Dict[str, Any]:
    candidate = callback_url.strip()
    if not candidate:
        return {"code": "", "state": "", "error": "", "error_description": ""}

    if "://" not in candidate:
        if candidate.startswith("?"):
            candidate = f"http://localhost{candidate}"
        elif any(ch in candidate for ch in "/?#") or ":" in candidate:
            candidate = f"http://{candidate}"
        elif "=" in candidate:
            candidate = f"http://localhost/?{candidate}"

    parsed = urllib.parse.urlparse(candidate)
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    fragment = urllib.parse.parse_qs(parsed.fragment, keep_blank_values=True)

    for key, values in fragment.items():
        if key not in query or not query[key] or not (query[key][0] or "").strip():
            query[key] = values

    def get1(k: str) -> str:
        v = query.get(k, [""])
        return (v[0] or "").strip()

    code = get1("code")
    state = get1("state")
    error = get1("error")
    error_description = get1("error_description")

    if code and not state and "#" in code:
        code, state = code.split("#", 1)

    if not error and error_description:
        error, error_description = error_description, ""

    return {
        "code": code,
        "state": state,
        "error": error,
        "error_description": error_description,
    }


def _jwt_claims_no_verify(id_token: str) -> Dict[str, Any]:
    if not id_token or id_token.count(".") < 2:
        return {}
    payload_b64 = id_token.split(".")[1]
    pad = "=" * ((4 - (len(payload_b64) % 4)) % 4)
    try:
        payload = base64.urlsafe_b64decode((payload_b64 + pad).encode("ascii"))
        return json.loads(payload.decode("utf-8"))
    except Exception:
        return {}


def _decode_jwt_segment(seg: str) -> Dict[str, Any]:
    raw = (seg or "").strip()
    if not raw:
        return {}
    pad = "=" * ((4 - (len(raw) % 4)) % 4)
    try:
        decoded = base64.urlsafe_b64decode((raw + pad).encode("ascii"))
        return json.loads(decoded.decode("utf-8"))
    except Exception:
        return {}


def _to_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _post_form(
    url: str, data: Dict[str, str], timeout: int = 30, proxies: Any = None
) -> Dict[str, Any]:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    try:
        context = None
        if not _ssl_verify():
            context = ssl._create_unverified_context()
        opener = None
        if proxies and isinstance(proxies, dict):
            proxy_map: Dict[str, str] = {}
            http_proxy = str(proxies.get("http") or "").strip()
            https_proxy = str(proxies.get("https") or "").strip()
            if http_proxy:
                proxy_map["http"] = http_proxy
            if https_proxy:
                proxy_map["https"] = https_proxy
            if proxy_map:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxy_map))
        if opener is not None:
            resp_ctx = opener.open(req, timeout=timeout)
        else:
            resp_ctx = urllib.request.urlopen(req, timeout=timeout, context=context)
        with resp_ctx as resp:
            raw = resp.read()
            if resp.status != 200:
                raise RuntimeError(
                    f"token exchange failed: {resp.status}: {raw.decode('utf-8', 'replace')}"
                )
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        raise RuntimeError(
            f"token exchange failed: {exc.code}: {raw.decode('utf-8', 'replace')}"
        ) from exc


def _post_with_retry(
    session: requests.Session,
    url: str,
    *,
    headers: Dict[str, Any],
    data: Any = None,
    json_body: Any = None,
    proxies: Any = None,
    timeout: int = 30,
    retries: int = 2,
) -> Any:
    last_error: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            if json_body is not None:
                return session.post(
                    url,
                    headers=headers,
                    json=json_body,
                    proxies=proxies,
                    verify=_ssl_verify(),
                    timeout=timeout,
                )
            return session.post(
                url,
                headers=headers,
                data=data,
                proxies=proxies,
                verify=_ssl_verify(),
                timeout=timeout,
            )
        except Exception as e:
            last_error = e
            if attempt >= retries:
                break
            time.sleep(2 * (attempt + 1))
    if last_error:
        raise last_error
    raise RuntimeError("Request failed without exception")


@dataclass(frozen=True)
class OAuthStart:
    auth_url: str
    state: str
    code_verifier: str
    redirect_uri: str


def generate_oauth_url(
    *, redirect_uri: str = DEFAULT_REDIRECT_URI, scope: str = DEFAULT_SCOPE
) -> OAuthStart:
    state = _random_state()
    code_verifier = _pkce_verifier()
    code_challenge = _sha256_b64url_no_pad(code_verifier)

    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "prompt": "login",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
    }
    auth_url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"
    return OAuthStart(
        auth_url=auth_url,
        state=state,
        code_verifier=code_verifier,
        redirect_uri=redirect_uri,
    )


def submit_callback_url(
    *,
    callback_url: str,
    expected_state: str,
    code_verifier: str,
    redirect_uri: str = DEFAULT_REDIRECT_URI,
    proxies: Any = None,
) -> str:
    cb = _parse_callback_url(callback_url)
    if cb["error"]:
        desc = cb["error_description"]
        raise RuntimeError(f"oauth error: {cb['error']}: {desc}".strip())

    if not cb["code"]:
        raise ValueError("callback url missing ?code=")
    if not cb["state"]:
        raise ValueError("callback url missing ?state=")
    if cb["state"] != expected_state:
        raise ValueError("state mismatch")

    token_resp = _post_form(
        TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": cb["code"],
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        },
        proxies=proxies,
    )

    access_token = (token_resp.get("access_token") or "").strip()
    refresh_token = (token_resp.get("refresh_token") or "").strip()
    id_token = (token_resp.get("id_token") or "").strip()
    expires_in = _to_int(token_resp.get("expires_in"))

    claims = _jwt_claims_no_verify(id_token)
    email = str(claims.get("email") or "").strip()
    auth_claims = claims.get("https://api.openai.com/auth") or {}
    account_id = str(auth_claims.get("chatgpt_account_id") or "").strip()

    now = int(time.time())
    expired_rfc3339 = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + max(expires_in, 0))
    )
    now_rfc3339 = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))

    config = {
        "id_token": id_token,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "account_id": account_id,
        "last_refresh": now_rfc3339,
        "email": email,
        "type": "codex",
        "expired": expired_rfc3339,
    }

    return json.dumps(config, ensure_ascii=False, separators=(",", ":"))


# ==========================================
# 核心注册逻辑
# ==========================================


_FIRST_NAMES = [
    "James",
    "John",
    "Robert",
    "Michael",
    "David",
    "William",
    "Richard",
    "Joseph",
    "Thomas",
    "Christopher",
    "Daniel",
    "Matthew",
    "Anthony",
    "Mary",
    "Patricia",
    "Jennifer",
    "Linda",
    "Elizabeth",
    "Barbara",
    "Sarah",
    "Jessica",
    "Karen",
    "Emily",
    "Olivia",
    "Emma",
    "Sophia",
]
_LAST_NAMES = [
    "Smith",
    "Johnson",
    "Williams",
    "Brown",
    "Jones",
    "Garcia",
    "Miller",
    "Davis",
    "Rodriguez",
    "Martinez",
    "Wilson",
    "Anderson",
    "Taylor",
    "Thomas",
    "Moore",
    "Jackson",
    "Martin",
    "Lee",
    "Harris",
    "Clark",
]


def _random_user_info() -> dict:
    name = f"{random.choice(_FIRST_NAMES)} {random.choice(_LAST_NAMES)}"
    year = random.randint(datetime.now().year - 45, datetime.now().year - 18)
    month = random.randint(1, 12)
    day = random.randint(1, 28)
    return {"name": name, "birthdate": f"{year}-{month:02d}-{day:02d}"}


def _generate_password(length: int = 16) -> str:
    """生成符合 OpenAI 要求的随机强密码（大小写+数字+特殊字符）"""
    upper = random.choices(string.ascii_uppercase, k=2)
    lower = random.choices(string.ascii_lowercase, k=2)
    digits = random.choices(string.digits, k=2)
    specials = random.choices("!@#$%&*", k=2)
    rest_len = length - 8
    pool = string.ascii_letters + string.digits + "!@#$%&*"
    rest = random.choices(pool, k=rest_len)
    chars = upper + lower + digits + specials + rest
    random.shuffle(chars)
    return "".join(chars)


def run(proxy: Optional[str]) -> tuple:
    proxies: Any = None
    if proxy:
        proxies = {"http": proxy, "https": proxy}

    s = requests.Session(proxies=proxies, impersonate="safari")

    if not _skip_net_check():
        try:
            trace = s.get(
                "https://cloudflare.com/cdn-cgi/trace",
                proxies=proxies,
                verify=_ssl_verify(),
                timeout=10,
            )
            trace = trace.text
            loc_re = re.search(r"^loc=(.+)$", trace, re.MULTILINE)
            loc = loc_re.group(1) if loc_re else None
            print(f"[*] 当前 IP 所在地: {loc}")
            if loc == "CN" or loc == "HK":
                raise RuntimeError("检查代理哦w - 所在地不支持")
        except Exception as e:
            print(f"[Error] 网络连接检查失败: {e}")
            return None, None

    _clear_current_outlook_email()
    email, dev_token = get_email_and_token(proxies)
    if not email or not dev_token:
        return None, None
    print(f"[*] 成功获取临时邮箱与授权: {email}")
    masked = dev_token[:8] + "..." if dev_token else ""
    print(f"[*] 临时邮箱 JWT: {masked}")

    oauth = generate_oauth_url()
    url = oauth.auth_url

    try:
        resp = s.get(url, proxies=proxies, verify=True, timeout=15)
        did = s.cookies.get("oai-did")
        print(f"[*] Device ID: {did}")

        # 确保 oai-device-id 同时作为 Cookie 设置
        if did:
            s.cookies.set("oai-device-id", did, domain=".openai.com", path="/")

        signup_body = f'{{"username":{{"value":"{email}","kind":"email"}},"screen_hint":"signup"}}'
        sen_req_body = f'{{"p":"","id":"{did}","flow":"authorize_continue"}}'

        sen_resp = requests.post(
            "https://sentinel.openai.com/backend-api/sentinel/req",
            headers={
                "origin": "https://sentinel.openai.com",
                "referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html?sv=20260219f9f6",
                "content-type": "text/plain;charset=UTF-8",
            },
            data=sen_req_body,
            proxies=proxies,
            impersonate="safari",
            verify=_ssl_verify(),
            timeout=15,
        )

        if sen_resp.status_code != 200:
            print(f"[Error] Sentinel 异常拦截，状态码: {sen_resp.status_code}")
            return None, None

        sen_token = sen_resp.json()["token"]
        sentinel = f'{{"p": "", "t": "", "c": "{sen_token}", "id": "{did}", "flow": "authorize_continue"}}'

        signup_resp = s.post(
            "https://auth.openai.com/api/accounts/authorize/continue",
            headers={
                "referer": "https://auth.openai.com/create-account",
                "accept": "application/json",
                "content-type": "application/json",
                "openai-sentinel-token": sentinel,
            },
            data=signup_body,
            proxies=proxies,
            verify=_ssl_verify(),
        )
        signup_status = signup_resp.status_code
        print(f"[*] 提交注册表单状态: {signup_status}")

        if signup_status == 403:
            print("[Error] 提交注册表单返回 403，中断本次运行，将在10秒后重试...")
            return "retry_403", None
        if signup_status != 200:
            print("[Error] 提交注册表单失败，跳过本次流程")
            print(signup_resp.text)
            return None, None

        password = _generate_password()
        register_body = json.dumps({"password": password, "username": email})
        print(f"[*] 生成随机密码: {password[:4]}****")

        pwd_resp = s.post(
            "https://auth.openai.com/api/accounts/user/register",
            headers={
                "referer": "https://auth.openai.com/create-account/password",
                "accept": "application/json",
                "content-type": "application/json",
            },
            json={"password": password, "username": email},
            proxies=proxies,
            verify=_ssl_verify(),
        )
        print(f"[*] 提交注册(密码)状态: {pwd_resp.status_code}")
        if pwd_resp.status_code != 200:
            print(pwd_resp.text)
            return None, None

        try:
            register_json = pwd_resp.json()
            register_continue = register_json.get("continue_url", "")
            register_page = (register_json.get("page") or {}).get("type", "")
            print(f"[*] 注册响应 continue_url: {register_continue}")
            print(f"[*] 注册响应 page.type: {register_page}")
        except Exception:
            register_continue = ""
            register_page = ""
            print(f"[*] 注册响应(raw): {pwd_resp.text[:300]}")

        need_otp = (
            "email-verification" in register_continue or "verify" in register_continue
        )
        if not need_otp and register_page:
            need_otp = "verification" in register_page or "otp" in register_page

        if need_otp:
            print("[*] 需要邮箱验证，开始等待验证码...")

            if register_continue:
                otp_send_url = register_continue
                if not otp_send_url.startswith("http"):
                    otp_send_url = f"https://auth.openai.com{otp_send_url}"
                print(f"[*] 触发发送 OTP: {otp_send_url}")
                otp_send_resp = _post_with_retry(
                    s,
                    otp_send_url,
                    headers={
                        "referer": "https://auth.openai.com/create-account/password",
                        "accept": "application/json",
                        "content-type": "application/json",
                        "openai-sentinel-token": sentinel,
                    },
                    json_body={},
                    proxies=proxies,
                    timeout=30,
                    retries=2,
                )
                print(f"[*] OTP 发送状态: {otp_send_resp.status_code}")
                if otp_send_resp.status_code != 200:
                    print(otp_send_resp.text)

            processed_mails = set()
            code = ""
            need_rebuy = False
            for otp_attempt in range(5):
                if otp_attempt > 0:
                    print(f"\n[*] OTP 重试 {otp_attempt}/5，重新发送验证码...")
                    try:
                        _post_with_retry(
                            s,
                            "https://auth.openai.com/api/accounts/email-otp/resend",
                            headers={
                                "openai-sentinel-token": sentinel,
                                "oai-device-id": did,
                                "content-type": "application/json",
                            },
                            json_body={},
                            proxies=proxies,
                            timeout=15,
                            retries=1,
                        )
                        time.sleep(2)
                    except Exception as e:
                        print(f"[*] 重发 OTP 异常: {e}")
                code = get_oai_code(
                    token=dev_token,
                    email=email,
                    proxies=proxies,
                    seen_ids=processed_mails,
                )
                if code:
                    break
                if EMAIL_MODE == "luckmail":
                    print(
                        f"[Warn] LuckMail 本轮等待超时({LUCKMAIL_OTP_TIMEOUT}s)，触发重买邮箱 (attempt {otp_attempt + 1}/5)"
                    )
                    need_rebuy = True
                    break
            if need_rebuy:
                return "retry_rebuy", None
            if not code:
                print("[Error] 多次重试后仍未收到验证码，跳过")
                return None, None

            print("[*] 开始校验验证码...")
            code_resp = _post_with_retry(
                s,
                "https://auth.openai.com/api/accounts/email-otp/validate",
                headers={
                    "referer": "https://auth.openai.com/email-verification",
                    "accept": "application/json",
                    "content-type": "application/json",
                    "openai-sentinel-token": sentinel,
                },
                json_body={"code": code},
                proxies=proxies,
                timeout=30,
                retries=2,
            )
            print(f"[*] 验证码校验状态: {code_resp.status_code}")
            if code_resp.status_code != 200:
                print(code_resp.text)
        else:
            print("[*] 密码注册无需邮箱验证，跳过 OTP 步骤")

        user_info = _random_user_info()
        print(f"[*] 开始创建账户 (昵称: {user_info['name']})...")
        create_account_resp = _post_with_retry(
            s,
            "https://auth.openai.com/api/accounts/create_account",
            headers={
                "referer": "https://auth.openai.com/about-you",
                "accept": "application/json",
                "content-type": "application/json",
                "oai-device-id": did,
            },
            json_body=user_info,
            proxies=proxies,
            timeout=30,
            retries=2,
        )
        create_account_status = create_account_resp.status_code
        print(f"[*] 账户创建状态: {create_account_status}")

        if create_account_status != 200:
            print(create_account_resp.text)
            return None, None

        print("[*] 账户创建完毕，执行静默重登录...")
        s.cookies.clear()

        oauth = generate_oauth_url()
        s.get(oauth.auth_url, proxies=proxies, verify=True, timeout=15)
        new_did = s.cookies.get("oai-did") or did

        sen_req_body2 = f'{{"p":"","id":"{new_did}","flow":"authorize_continue"}}'
        sen_resp2 = requests.post(
            "https://sentinel.openai.com/backend-api/sentinel/req",
            headers={
                "origin": "https://sentinel.openai.com",
                "referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html?sv=20260219f9f6",
                "content-type": "text/plain;charset=UTF-8",
            },
            data=sen_req_body2,
            proxies=proxies,
            impersonate="safari",
            verify=_ssl_verify(),
            timeout=15,
        )
        sen_token2 = (
            sen_resp2.json().get("token", "") if sen_resp2.status_code == 200 else ""
        )
        sentinel2 = f'{{"p": "", "t": "", "c": "{sen_token2}", "id": "{new_did}", "flow": "authorize_continue"}}'

        _post_with_retry(
            s,
            "https://auth.openai.com/api/accounts/authorize/continue",
            headers={
                "openai-sentinel-token": sentinel2,
                "oai-device-id": new_did,
                "content-type": "application/json",
            },
            json_body={
                "username": {"value": email, "kind": "email"},
                "screen_hint": "login",
            },
            proxies=proxies,
        )

        pwd_login_resp = _post_with_retry(
            s,
            "https://auth.openai.com/api/accounts/password/verify",
            headers={
                "openai-sentinel-token": sentinel2,
                "oai-device-id": new_did,
                "content-type": "application/json",
            },
            json_body={"password": password},
            proxies=proxies,
        )
        print(f"[*] 密码登录状态: {pwd_login_resp.status_code}")

        if pwd_login_resp.status_code == 200:
            try:
                pwd_json = pwd_login_resp.json()
                pwd_continue = str(pwd_json.get("continue_url") or "").strip()
                pwd_page = (pwd_json.get("page") or {}).get("type", "")
                if pwd_continue:
                    print(f"[*] 登录后 continue_url: {pwd_continue}")
                    s.get(
                        pwd_continue, proxies=proxies, verify=_ssl_verify(), timeout=15
                    )
                    time.sleep(1)

                if "otp" in pwd_page or "verify" in pwd_continue:
                    print("[*] 登录触发二次邮箱验证，等待验证码...")
                    code2 = ""
                    need_rebuy2 = False
                    for otp2_attempt in range(5):
                        if otp2_attempt > 0:
                            print(f"\n[*] 二次 OTP 重试 {otp2_attempt}/5，重新发送...")
                            try:
                                _post_with_retry(
                                    s,
                                    "https://auth.openai.com/api/accounts/email-otp/resend",
                                    headers={
                                        "openai-sentinel-token": sentinel2,
                                        "oai-device-id": new_did,
                                        "content-type": "application/json",
                                    },
                                    json_body={},
                                    proxies=proxies,
                                    timeout=15,
                                    retries=1,
                                )
                                time.sleep(2)
                            except Exception as e:
                                print(f"[*] 重发异常: {e}")
                        code2 = get_oai_code(
                            token=dev_token,
                            email=email,
                            proxies=proxies,
                            seen_ids=processed_mails,
                        )
                        if code2:
                            break
                        if EMAIL_MODE == "luckmail":
                            print(
                                f"[Warn] LuckMail 二次OTP等待超时({LUCKMAIL_OTP_TIMEOUT}s)，触发重买邮箱 (attempt {otp2_attempt + 1}/5)"
                            )
                            need_rebuy2 = True
                            break
                    if need_rebuy2:
                        return "retry_rebuy", None
                    if not code2:
                        print("[Error] 二次验证码获取失败")
                        return None, None

                    code2_resp = _post_with_retry(
                        s,
                        "https://auth.openai.com/api/accounts/email-otp/validate",
                        headers={
                            "referer": "https://auth.openai.com/email-verification",
                            "oai-device-id": new_did,
                            "openai-sentinel-token": sentinel2,
                            "content-type": "application/json",
                        },
                        json_body={"code": code2},
                        proxies=proxies,
                    )
                    print(f"[*] 二次验证码校验状态: {code2_resp.status_code}")
                    if code2_resp.status_code != 200:
                        print(code2_resp.text)
                        return None, None

                    try:
                        code2_json = code2_resp.json()
                        otp_continue = str(code2_json.get("continue_url") or "").strip()
                        if otp_continue:
                            print(f"[*] 跟随 OTP continue_url: {otp_continue}")
                            s.get(
                                otp_continue,
                                proxies=proxies,
                                verify=_ssl_verify(),
                                timeout=15,
                            )
                            time.sleep(1)

                        # 如果进入 add-phone，尝试显式跳转到 codex consent 触发 workspace cookie 注入
                        if "/add-phone" in otp_continue:
                            print("[*] 检测到 add-phone，尝试跳转至 codex consent...")
                            s.get(
                                "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                                headers={"oai-device-id": new_did},
                                proxies=proxies,
                                verify=_ssl_verify(),
                                timeout=15,
                            )
                            time.sleep(1)
                    except Exception:
                        pass
            except Exception:
                pass

        # 关键步骤：访问 Consent 页面，初始化 session 上下文
        print("[*] 访问 Consent 页面初始化 session...")
        consent_resp = s.get(
            "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
            headers={
                "oai-device-id": new_did,
            },
            proxies=proxies,
            verify=_ssl_verify(),
            timeout=15,
        )
        print(f"[*] Consent 页面状态: {consent_resp.status_code}")
        time.sleep(1)

        # 确保 oai-device-id 同时作为 Cookie 设置
        s.cookies.set("oai-device-id", new_did, domain=".openai.com", path="/")

        auth_cookie = s.cookies.get("oai-client-auth-session")
        if not auth_cookie:
            auth_cookie = s.cookies.get(
                "oai-client-auth-session", domain="auth.openai.com"
            )
        if not auth_cookie:
            auth_cookie = s.cookies.get("oai-client-auth-session", domain=".openai.com")
        if not auth_cookie:
            print("[Error] 重登录后未能获取授权 Cookie")
            print(f"[*] 调试 - 当前所有 cookies: {dict(s.cookies)}")
            return None, None

        auth_json = {}
        raw_val = auth_cookie.strip()
        try:
            decoded_val = urllib.parse.unquote(raw_val)
            if decoded_val != raw_val:
                raw_val = decoded_val
        except Exception:
            pass
        for part in raw_val.split("."):
            decoded = _decode_jwt_segment(part)
            if isinstance(decoded, dict) and "workspaces" in decoded:
                auth_json = decoded
                break

        workspaces = auth_json.get("workspaces") or []
        if not workspaces:
            print("[Error] 重登录后 Cookie 里仍没有 workspace 信息")
            try:
                keys = list(auth_json.keys()) if isinstance(auth_json, dict) else []
                print(f"[*] 调试 - 已解码 auth cookie keys: {keys}")
            except Exception:
                pass
            try:
                print(f"[*] 调试 - 当前 cookie 名称: {[c.name for c in s.cookies]}")
            except Exception:
                pass
            return None, None
        workspace_id = str((workspaces[0] or {}).get("id") or "").strip()
        if not workspace_id:
            print("[Error] 无法解析 workspace_id")
            return None, None

        select_body = f'{{"workspace_id":"{workspace_id}"}}'
        print("[*] 开始选择 workspace...")
        # 先访问 auth.openai.com 主页，通过 Cloudflare 验证
        print("[*] 先访问主页通过 Cloudflare 验证...")
        cf_check = s.get(
            "https://auth.openai.com/",
            proxies=proxies,
            verify=_ssl_verify(),
            timeout=15,
        )
        time.sleep(2)
        select_resp = _post_with_retry(
            s,
            "https://auth.openai.com/api/accounts/workspace/select",
            headers={
                "referer": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                "oai-device-id": new_did,
                "content-type": "application/json",
            },
            data=select_body,
            proxies=proxies,
            timeout=30,
            retries=2,
        )

        if select_resp.status_code != 200:
            print(f"[Error] 选择 workspace 失败，状态码: {select_resp.status_code}")
            print(select_resp.text)
            return None, None

        continue_url = str((select_resp.json() or {}).get("continue_url") or "").strip()
        if not continue_url:
            print("[Error] workspace/select 响应里缺少 continue_url")
            return None, None

        try:
            select_data = select_resp.json()
            orgs = (select_data.get("data") or {}).get("orgs") or []
            if orgs:
                org_id = str((orgs[0] or {}).get("id") or "").strip()
                if org_id:
                    org_body = {"org_id": org_id}
                    projects = (orgs[0] or {}).get("projects") or []
                    if projects:
                        org_body["project_id"] = str(
                            (projects[0] or {}).get("id") or ""
                        ).strip()
                    print(f"[*] 选择组织: {org_id}")
                    org_resp = _post_with_retry(
                        s,
                        "https://auth.openai.com/api/accounts/organization/select",
                        headers={
                            "content-type": "application/json",
                            "oai-device-id": new_did,
                            "openai-sentinel-token": sentinel2,
                        },
                        json_body=org_body,
                        proxies=proxies,
                    )
                    if org_resp.status_code in [301, 302, 303, 307, 308]:
                        continue_url = org_resp.headers.get("Location", continue_url)
                    elif org_resp.status_code == 200:
                        try:
                            continue_url = org_resp.json().get(
                                "continue_url", continue_url
                            )
                        except Exception:
                            pass
        except Exception as e:
            print(f"[*] 组织选择异常(非致命): {e}")

        current_url = continue_url
        for _ in range(15):
            final_resp = s.get(
                current_url,
                allow_redirects=False,
                proxies=proxies,
                verify=_ssl_verify(),
                timeout=15,
            )

            if final_resp.status_code in [301, 302, 303, 307, 308]:
                next_url = urllib.parse.urljoin(
                    current_url, final_resp.headers.get("Location") or ""
                )
            elif final_resp.status_code == 200:
                if "consent_challenge=" in current_url:
                    c_resp = s.post(
                        current_url,
                        data={"action": "accept"},
                        allow_redirects=False,
                        proxies=proxies,
                        verify=_ssl_verify(),
                        timeout=15,
                    )
                    next_url = (
                        urllib.parse.urljoin(
                            current_url, c_resp.headers.get("Location") or ""
                        )
                        if c_resp.status_code in [301, 302, 303, 307, 308]
                        else ""
                    )
                else:
                    meta_match = re.search(
                        r'content=["\']?\d+;\s*url=([^"\'>\s]+)',
                        final_resp.text,
                        re.IGNORECASE,
                    )
                    next_url = (
                        urllib.parse.urljoin(current_url, meta_match.group(1))
                        if meta_match
                        else ""
                    )
                if not next_url:
                    break
            else:
                break

            if "code=" in next_url and "state=" in next_url:
                token_json = submit_callback_url(
                    callback_url=next_url,
                    code_verifier=oauth.code_verifier,
                    redirect_uri=oauth.redirect_uri,
                    expected_state=oauth.state,
                    proxies=proxies,
                )
                return token_json, password
            current_url = next_url
            time.sleep(0.5)

        print("[Error] 未能在重定向链中捕获到最终 Callback URL")
        return None, None

    except Exception as e:
        print(f"[Error] 运行时发生错误: {e}")
        return None, None


# ==========================================
# Token 检测与刷新
# ==========================================

AUTO_REGISTER_THRESHOLD = 10

_INVALID_ERRORS = {
    "account_deactivated",
    "invalid_api_key",
    "user_deactivated",
    "account_banned",
    "invalid_grant",
}


def _refresh_token(refresh_tok: str, proxies: Any = None) -> Dict[str, Any]:
    """用 refresh_token 换取新的 access_token"""
    try:
        resp = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": CLIENT_ID,
                "refresh_token": refresh_tok,
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            impersonate="safari",
            verify=_ssl_verify(),
            proxies=proxies,
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            now = int(time.time())
            expires_in = max(int(data.get("expires_in", 3600)), 0)
            return {
                "ok": True,
                "access_token": data.get("access_token", ""),
                "refresh_token": data.get("refresh_token", refresh_tok),
                "id_token": data.get("id_token", ""),
                "last_refresh": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                "expired": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + expires_in)
                ),
            }
        return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _test_token(
    access_token: str, account_id: str = "", proxies: Any = None
) -> Dict[str, Any]:
    """调用 ChatGPT API 测试 token 是否有效，返回 {valid, reason}"""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    if account_id:
        headers["Chatgpt-Account-Id"] = account_id
    try:
        resp = requests.get(
            "https://chatgpt.com/backend-api/me",
            headers=headers,
            proxies=proxies,
            impersonate="safari",
            verify=_ssl_verify(),
            timeout=20,
        )
        if resp.status_code == 200:
            try:
                me = resp.json()
                if me.get("id"):
                    return {"valid": True, "reason": "正常"}
            except Exception:
                pass
            return {"valid": True, "reason": "正常"}

        try:
            err_data = resp.json()
            err_detail = err_data.get("detail", "")
            if isinstance(err_detail, dict):
                err_msg = err_detail.get("message", str(err_detail))
            else:
                err_msg = str(err_detail)
        except Exception:
            err_msg = resp.text[:200]

        if any(kw in err_msg.lower() for kw in ("deactivat", "banned", "suspended")):
            return {"valid": False, "reason": f"账号停用/无效 ({err_msg})"}
        if resp.status_code == 401:
            return {"valid": False, "reason": f"认证失败 (401)"}
        if resp.status_code == 403:
            return {"valid": False, "reason": f"禁止访问 (403: {err_msg})"}
        return {"valid": False, "reason": f"HTTP {resp.status_code}: {err_msg}"}
    except Exception as e:
        return {"valid": False, "reason": f"请求异常: {e}"}


def check_codex_tokens(proxies: Any = None) -> Dict[str, int]:
    """扫描 auths 目录下所有 codex token，检测状态并处理"""
    if not os.path.isdir(CLI_PROXY_AUTHS_DIR):
        print(f"[Error] 目录不存在: {CLI_PROXY_AUTHS_DIR}")
        return {"total": 0, "valid": 0, "refreshed": 0, "deleted": 0}

    files = sorted(
        f
        for f in os.listdir(CLI_PROXY_AUTHS_DIR)
        if f.startswith("codex-") and f.endswith(".json")
    )
    if not files:
        print("[*] 没有找到 codex token 文件")
        return {"total": 0, "valid": 0, "refreshed": 0, "deleted": 0}

    print(f"[*] 共发现 {len(files)} 个 codex token，开始检测...\n")
    valid_count = 0
    refreshed_count = 0
    deleted_count = 0

    for i, fname in enumerate(files, 1):
        fpath = os.path.join(CLI_PROXY_AUTHS_DIR, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                token_data = json.loads(f.read())
        except Exception as e:
            print(f"  [{i}/{len(files)}] {fname} - 读取失败: {e}")
            continue

        email = token_data.get("email", fname)
        access_token = token_data.get("access_token", "")
        refresh_tok = token_data.get("refresh_token", "")
        account_id = token_data.get("account_id", "")

        is_expired = False
        claims = _jwt_claims_no_verify(access_token)
        exp_ts = claims.get("exp", 0)
        if exp_ts and int(time.time()) >= exp_ts:
            is_expired = True

        if is_expired:
            print(
                f"  [{i}/{len(files)}] {email} - access_token 已过期，尝试刷新...",
                end="",
            )
            result = _refresh_token(refresh_tok, proxies=proxies)
            if result.get("ok"):
                token_data["access_token"] = result["access_token"]
                token_data["refresh_token"] = result["refresh_token"]
                token_data["id_token"] = result.get(
                    "id_token", token_data.get("id_token", "")
                )
                token_data["last_refresh"] = result["last_refresh"]
                token_data["expired"] = result["expired"]
                access_token = result["access_token"]
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(
                        json.dumps(
                            token_data, ensure_ascii=False, separators=(",", ":")
                        )
                    )
                print(" 刷新成功!")
                refreshed_count += 1
            else:
                err = result.get("error", "")
                if any(
                    kw in err.lower() for kw in ("deactivat", "invalid_grant", "banned")
                ):
                    os.remove(fpath)
                    print(f" 刷新失败(账号无效)，已删除")
                    deleted_count += 1
                    continue
                else:
                    print(f" 刷新失败: {err}")
                    continue

        test = _test_token(access_token, account_id=account_id, proxies=proxies)
        if test["valid"]:
            print(f"  [{i}/{len(files)}] {email} - 状态正常 ✓")
            valid_count += 1
        else:
            reason = test["reason"]
            if "停用" in reason or "无效" in reason or "deactivat" in reason.lower():
                os.remove(fpath)
                print(f"  [{i}/{len(files)}] {email} - {reason}，已删除")
                deleted_count += 1
            elif "认证失败" in reason or "401" in reason:
                print(f"  [{i}/{len(files)}] {email} - {reason}，尝试刷新...", end="")
                result = _refresh_token(refresh_tok, proxies=proxies)
                if result.get("ok"):
                    token_data["access_token"] = result["access_token"]
                    token_data["refresh_token"] = result["refresh_token"]
                    token_data["id_token"] = result.get(
                        "id_token", token_data.get("id_token", "")
                    )
                    token_data["last_refresh"] = result["last_refresh"]
                    token_data["expired"] = result["expired"]
                    with open(fpath, "w", encoding="utf-8") as f:
                        f.write(
                            json.dumps(
                                token_data, ensure_ascii=False, separators=(",", ":")
                            )
                        )
                    print(" 刷新成功!")
                    refreshed_count += 1
                    valid_count += 1
                else:
                    os.remove(fpath)
                    print(f" 刷新失败，已删除")
                    deleted_count += 1
            else:
                print(f"  [{i}/{len(files)}] {email} - {reason}")

    print(
        f"\n[*] 检测完毕: 有效 {valid_count} / 刷新 {refreshed_count} / 删除 {deleted_count} / 共 {len(files)}"
    )
    return {
        "total": len(files),
        "valid": valid_count,
        "refreshed": refreshed_count,
        "deleted": deleted_count,
    }


_file_write_lock = threading.Lock()
_success_counter_lock = threading.Lock()
_success_counter = 0


def _upload_token_to_cpa(token_json: str) -> bool:
    if not CPA_API_URL or not CPA_API_KEY:
        return False
    try:
        token_data = json.loads(token_json)
        email = str(token_data.get("email") or "").strip()
        filename = f"{email}.json" if email else "token.json"
        upload_url = f"{CPA_API_URL}/v0/management/auth-files"
        mime = CurlMime()
        try:
            mime.addpart(
                name="file",
                data=token_json.encode("utf-8"),
                filename=filename,
                content_type="application/json",
            )
            resp = requests.post(
                upload_url,
                headers={"Authorization": f"Bearer {CPA_API_KEY}"},
                multipart=mime,
                proxies=None,
                verify=False,
                timeout=30,
                impersonate="chrome110",
            )
        finally:
            mime.close()
        if resp.status_code in (200, 201):
            print(f"[*] CPA 上传成功: {filename}")
            return True
        print(f"[Warn] CPA 上传失败: HTTP {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"[Warn] CPA 上传异常: {e}")
    return False


def _save_result(token_json: str, password: str, proxy_str: Optional[str]) -> None:
    """线程安全地保存注册结果"""
    try:
        t_data = json.loads(token_json)
        fname_email = t_data.get("email", "unknown").replace("@", "_")
        account_email = t_data.get("email", "")
    except Exception:
        fname_email = "unknown"
        account_email = ""

    file_name = f"token_{fname_email}_{int(time.time())}.json"
    if TOKEN_OUTPUT_DIR:
        os.makedirs(TOKEN_OUTPUT_DIR, exist_ok=True)
        file_name = os.path.join(TOKEN_OUTPUT_DIR, file_name)

    with _file_write_lock:
        with open(file_name, "w", encoding="utf-8") as f:
            f.write(token_json)

    print(f"[*] 成功! Token 已保存至: {file_name}")

    _upload_token_to_cpa(token_json)

    if os.path.isdir(CLI_PROXY_AUTHS_DIR) and account_email:
        dest = os.path.join(CLI_PROXY_AUTHS_DIR, f"codex-{account_email}.json")
        with _file_write_lock:
            with open(dest, "w", encoding="utf-8") as df:
                df.write(token_json)
        print(f"[*] Token 已拷贝至: {dest}")
        if os.path.exists(file_name):
            os.remove(file_name)
            print(f"[*] 本地 token 文件已删除: {file_name}")

    if account_email and password:
        accounts_file = (
            os.path.join(TOKEN_OUTPUT_DIR, "accounts.txt")
            if TOKEN_OUTPUT_DIR
            else "accounts.txt"
        )
        with _file_write_lock:
            with open(accounts_file, "a", encoding="utf-8") as af:
                af.write(f"{account_email}----{password}\n")
        print(f"[*] 账号密码已追加至: {accounts_file}")

    if account_email:
        proxies_cleanup = {"http": proxy_str, "https": proxy_str} if proxy_str else None
        delete_temp_email(account_email, proxies=proxies_cleanup)


def _worker(
    worker_id: int,
    rotator: ProxyRotator,
    single_proxy: Optional[str],
    sleep_min: int,
    sleep_max: int,
    count_target: Optional[int],
    remaining: Optional[list],
    stop_event: threading.Event,
) -> int:
    """单个注册工作线程，返回本线程成功注册数"""
    global _success_counter
    local_success = 0
    local_round = 0

    def _handle_outlook_round_result(status: str, reason: str = "") -> None:
        if EMAIL_MODE != "outlook_api" or _outlook_api_queue is None:
            return
        current_email = _get_current_outlook_email()
        if not current_email:
            return
        try:
            if status == "success":
                _outlook_api_queue.mark_success(current_email)
            elif status == "requeue":
                _outlook_api_queue.requeue([current_email])
            elif status == "failed":
                _outlook_api_queue.mark_failed(current_email, reason or "register_failed")
        except Exception as mark_err:
            print(f"[Warn] 更新淘宝邮箱池状态失败({current_email}): {mark_err}")
        finally:
            _outlook_api_credentials.pop(current_email, None)
            _clear_current_outlook_email()

    while not stop_event.is_set():
        if EMAIL_MODE == "file" and _email_queue is not None and len(_email_queue) == 0:
            print(f"[T{worker_id}] 邮箱队列已用完，停止线程")
            break
        if (
            EMAIL_MODE == "outlook_api"
            and _outlook_api_queue is not None
            and len(_outlook_api_queue) == 0
        ):
            print(f"[T{worker_id}] Outlook API 凭据队列已用完，停止线程")
            break

        if remaining is not None:
            with _success_counter_lock:
                if remaining[0] <= 0:
                    break
                remaining[0] -= 1

        local_round += 1
        proxy_str = rotator.next() if len(rotator) > 0 else single_proxy
        tag = f"[T{worker_id}#{local_round}]"

        print(
            f"\n{tag} [{datetime.now().strftime('%H:%M:%S')}] 开始注册 (代理: {proxy_str or '直连'})"
        )

        try:
            token_json, password = run(proxy_str)

            if token_json == "retry_403":
                print(f"{tag} 检测到 403，等待10秒后重试...")
                _handle_outlook_round_result("requeue", "retry_403")
                if remaining is not None:
                    with _success_counter_lock:
                        remaining[0] += 1
                time.sleep(10)
                continue

            if token_json == "retry_rebuy":
                print(f"{tag} LuckMail 超时触发重买，立即重试...")
                _handle_outlook_round_result("requeue", "retry_rebuy")
                if remaining is not None:
                    with _success_counter_lock:
                        remaining[0] += 1
                time.sleep(1)
                continue

            if token_json:
                _save_result(token_json, password, proxy_str)
                _handle_outlook_round_result("success")
                local_success += 1
                with _success_counter_lock:
                    _success_counter += 1
                print(f"{tag} 注册成功! (本线程累计: {local_success})")
            else:
                print(f"{tag} 本次注册失败")
                _handle_outlook_round_result("failed", "register_failed")
                if (
                    EMAIL_MODE == "file"
                    and _email_queue is not None
                    and len(_email_queue) == 0
                ):
                    print(f"{tag} 邮箱队列已用完，停止线程")
                    break
                if (
                    EMAIL_MODE == "outlook_api"
                    and _outlook_api_queue is not None
                    and len(_outlook_api_queue) == 0
                ):
                    print(f"{tag} Outlook API 凭据队列已用完，停止线程")
                    break

        except Exception as e:
            print(f"{tag} [Error] 未捕获异常: {e}")
            _handle_outlook_round_result("failed", f"worker_exception:{str(e)[:120]}")

        if count_target == 1 and remaining is None:
            break

        if remaining is not None:
            with _success_counter_lock:
                if remaining[0] <= 0:
                    break

        if not stop_event.is_set():
            wait_time = random.randint(sleep_min, sleep_max)
            print(f"{tag} 休息 {wait_time} 秒...")
            for _ in range(wait_time):
                if stop_event.is_set():
                    break
                time.sleep(1)

    return local_success


def main() -> None:
    global \
        EMAIL_MODE, \
        HOTMAIL007_API_KEY, \
        HOTMAIL007_MAIL_TYPE, \
        HOTMAIL007_MAIL_MODE, \
        OUTLOOK_API_URL, \
        OUTLOOK_API_CLIENT_ID, \
        OUTLOOK_API_REFRESH_TOKEN, \
        OUTLOOK_API_ACCOUNTS_FILE, \
        OUTLOOK_API_NUM, \
        OUTLOOK_API_BOX_TYPE, \
        OUTLOOK_API_POLL_INTERVAL, \
        OUTLOOK_API_POLL_TIMEOUT, \
        _email_queue, \
        _outlook_api_queue

    parser = argparse.ArgumentParser(description="OpenAI 自动注册脚本")
    parser.add_argument(
        "--proxy", default=None, help="单个代理地址，如 http://127.0.0.1:7890"
    )
    parser.add_argument(
        "--proxy-file",
        default=None,
        help="代理列表文件路径 (每行一个代理)，批量注册时自动轮换",
    )
    parser.add_argument("--once", action="store_true", help="只运行一次")
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="批量注册数量，如 --count 10 注册10个账号",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=1,
        help="并发线程数 (默认1)，配合 --count 或循环模式使用",
    )
    parser.add_argument(
        "--check", action="store_true", help="检测 auths 目录下 codex token 状态"
    )
    parser.add_argument("--sleep-min", type=int, default=5, help="循环模式最短等待秒数")
    parser.add_argument(
        "--sleep-max", type=int, default=30, help="循环模式最长等待秒数"
    )
    parser.add_argument(
        "--email-mode",
        default=None,
        choices=["cf", "hotmail007", "outlook_api", "file", "gmail", "luckmail"],
        help="邮箱模式: file=从accounts.txt读取, cf=Cloudflare自有域名, hotmail007=API拉取微软邮箱, outlook_api=凭据文件+GetLastEmails接码, gmail=Gmail别名+手动输入验证码, luckmail=LuckMail购买邮箱自动接码 (默认读.env EMAIL_MODE)",
    )
    parser.add_argument(
        "--accounts-file",
        default=None,
        help="邮箱列表文件路径 (每行一个邮箱)，配合 --email-mode file 使用 (默认 accounts.txt)",
    )
    parser.add_argument(
        "--hotmail007-key", default=None, help="Hotmail007 API Key (覆盖.env)"
    )
    parser.add_argument(
        "--hotmail007-type",
        default=None,
        help="Hotmail007 邮箱类型，如 'outlook Trusted Graph' (覆盖.env)",
    )
    parser.add_argument(
        "--hotmail007-mail-mode",
        default=None,
        choices=["graph", "imap"],
        help="Hotmail007 收信模式: graph=Microsoft Graph API, imap=IMAP协议 (默认graph)",
    )
    parser.add_argument(
        "--outlook-api-url",
        default=None,
        help="Outlook API 基础地址，例如 https://example.com",
    )
    parser.add_argument(
        "--outlook-api-accounts-file",
        default=None,
        help="Outlook API 凭据文件路径，支持 email----clientId----refreshToken",
    )
    parser.add_argument(
        "--outlook-api-client-id",
        default=None,
        help="Outlook API 全局 clientId（当凭据文件每行仅有邮箱时可用）",
    )
    parser.add_argument(
        "--outlook-api-refresh-token",
        default=None,
        help="Outlook API 全局 refreshToken（当凭据文件每行仅有邮箱时可用）",
    )
    parser.add_argument(
        "--outlook-api-num",
        type=int,
        default=None,
        help="Outlook API 每次拉取邮件数量 (默认1, 最大2)",
    )
    parser.add_argument(
        "--outlook-api-box-type",
        type=int,
        default=None,
        choices=[1, 2],
        help="Outlook API 邮箱类型: 1=收件箱 2=垃圾箱",
    )
    parser.add_argument(
        "--outlook-api-poll-interval",
        type=int,
        default=None,
        help="Outlook API 轮询间隔秒数",
    )
    parser.add_argument(
        "--outlook-api-poll-timeout",
        type=int,
        default=None,
        help="Outlook API 验证码等待超时秒数",
    )
    args = parser.parse_args()

    if args.email_mode:
        EMAIL_MODE = args.email_mode.strip().lower()
    if args.accounts_file:
        ACCOUNTS_FILE = args.accounts_file.strip()
        if EMAIL_MODE == "outlook_api":
            OUTLOOK_API_ACCOUNTS_FILE = ACCOUNTS_FILE
    if EMAIL_MODE == "file":
        _email_queue = EmailQueue(ACCOUNTS_FILE)
        if len(_email_queue) == 0:
            print(
                f"[Error] 邮箱文件 {ACCOUNTS_FILE} 为空或不存在，请先填入邮箱地址（一行一个）"
            )
            return
        print(f"[*] 从 {ACCOUNTS_FILE} 加载了 {len(_email_queue)} 个邮箱")
    if args.hotmail007_key:
        HOTMAIL007_API_KEY = args.hotmail007_key.strip()
    if args.hotmail007_type:
        HOTMAIL007_MAIL_TYPE = args.hotmail007_type.strip()
    if args.hotmail007_mail_mode:
        HOTMAIL007_MAIL_MODE = args.hotmail007_mail_mode.strip().lower()
    if args.outlook_api_url:
        OUTLOOK_API_URL = args.outlook_api_url.strip().rstrip("/")
    if args.outlook_api_accounts_file:
        OUTLOOK_API_ACCOUNTS_FILE = args.outlook_api_accounts_file.strip()
    if args.outlook_api_client_id:
        OUTLOOK_API_CLIENT_ID = args.outlook_api_client_id.strip()
    if args.outlook_api_refresh_token:
        OUTLOOK_API_REFRESH_TOKEN = args.outlook_api_refresh_token.strip()
    if args.outlook_api_num is not None:
        OUTLOOK_API_NUM = max(1, min(2, int(args.outlook_api_num)))
    if args.outlook_api_box_type is not None:
        OUTLOOK_API_BOX_TYPE = 2 if int(args.outlook_api_box_type) == 2 else 1
    if args.outlook_api_poll_interval is not None:
        OUTLOOK_API_POLL_INTERVAL = max(1, int(args.outlook_api_poll_interval))
    if args.outlook_api_poll_timeout is not None:
        OUTLOOK_API_POLL_TIMEOUT = max(20, int(args.outlook_api_poll_timeout))

    if EMAIL_MODE == "outlook_api":
        accounts_path = OUTLOOK_API_ACCOUNTS_FILE.strip()
        if not accounts_path:
            print("[Error] OUTLOOK_API_ACCOUNTS_FILE 未配置")
            return
        _outlook_api_queue = OutlookApiCredentialQueue(
            accounts_path,
            fallback_client_id=OUTLOOK_API_CLIENT_ID,
            fallback_refresh_token=OUTLOOK_API_REFRESH_TOKEN,
        )
        if len(_outlook_api_queue) == 0:
            print(
                f"[Error] Outlook API 凭据文件 {accounts_path} 为空或格式不正确，请填写 email----clientId----refreshToken"
            )
            return
        print(
            f"[*] 从 {accounts_path} 加载了 {len(_outlook_api_queue)} 组 Outlook API 凭据"
        )

    proxy_file_path = args.proxy_file or PROXY_FILE
    proxy_list = _load_proxies(proxy_file_path)
    rotator = ProxyRotator(proxy_list)

    effective_single_proxy = args.proxy or SINGLE_PROXY or None

    thread_count = args.threads
    if BATCH_THREADS and thread_count == 1:
        try:
            thread_count = int(BATCH_THREADS)
        except ValueError:
            pass
    thread_count = max(1, thread_count)

    batch_count = args.count
    if batch_count is None and BATCH_COUNT:
        try:
            batch_count = int(BATCH_COUNT)
        except ValueError:
            pass

    if args.check:
        check_proxy = effective_single_proxy
        if not check_proxy and len(rotator) > 0:
            check_proxy = rotator.next()
        proxies_dict = (
            {"http": check_proxy, "https": check_proxy} if check_proxy else None
        )
        stats = check_codex_tokens(proxies=proxies_dict)
        valid_count = stats.get("valid", 0)
        if valid_count >= AUTO_REGISTER_THRESHOLD:
            print(
                f"[*] 当前可用 token {valid_count} 个，已达到阈值 {AUTO_REGISTER_THRESHOLD}，不执行自动注册"
            )
            return
        need_count = AUTO_REGISTER_THRESHOLD - valid_count
        print(
            f"[*] 当前可用 token {valid_count} 个，低于阈值 {AUTO_REGISTER_THRESHOLD}，开始自动注册，目标补足 {need_count} 个"
        )
        batch_count = need_count

    sleep_min = max(1, args.sleep_min)
    sleep_max = max(sleep_min, args.sleep_max)

    print("[Info] Yasal's Seamless OpenAI Auto-Registrar Started for ZJH")
    print()
    print("=" * 60)
    print("  20260330更新hotamil邮箱支持")
    print("  QQ群382446")
    print("=" * 60)
    if EMAIL_MODE == "file":
        mode_label = f"文件读取 ({ACCOUNTS_FILE}, 剩余 {len(_email_queue)} 个)"
    elif EMAIL_MODE == "cf":
        mode_label = "Cloudflare Worker (自有域名)"
    elif EMAIL_MODE == "gmail":
        mode_label = f"Gmail 别名 + 手动输入验证码 ({GMAIL_BASE}@gmail.com)"
    elif EMAIL_MODE == "outlook_api":
        queue_size = len(_outlook_api_queue) if _outlook_api_queue is not None else 0
        if OUTLOOK_API_URL:
            mode_label = (
                "Outlook API 接码 "
                f"({OUTLOOK_API_URL}, num={OUTLOOK_API_NUM}, boxType={OUTLOOK_API_BOX_TYPE}, 剩余 {queue_size} 组)"
            )
        else:
            mode_label = f"Outlook 直连接码(Graph/IMAP, 剩余 {queue_size} 组)"
    elif EMAIL_MODE == "luckmail":
        mode_label = (
            "LuckMail 购买邮箱自动接码 "
            f"({LUCKMAIL_BASE_URL}, project={LUCKMAIL_PROJECT_CODE}, "
            f"type={LUCKMAIL_EMAIL_TYPE or 'auto'}, domain={LUCKMAIL_DOMAIN or 'auto'})"
        )
    else:
        mode_label = "Hotmail007 API (微软邮箱)"
    print(f"  邮箱模式: {mode_label}")
    if len(rotator) > 0:
        print(f"  代理模式: 文件轮换 ({len(rotator)} 个代理)")
    elif effective_single_proxy:
        print(f"  代理模式: 单代理 ({effective_single_proxy})")
    else:
        print(f"  代理模式: 直连 (未配置代理)")
    if batch_count:
        print(f"  批量数量: {batch_count}")
    print(f"  并发线程: {thread_count}")
    if EMAIL_MODE == "hotmail007":
        print(f"  API 地址: {HOTMAIL007_API_URL}")
        print(f"  邮箱类型: {HOTMAIL007_MAIL_TYPE}")
        print(f"  收信模式: {HOTMAIL007_MAIL_MODE.upper()}")
        check_proxy_str = effective_single_proxy
        if not check_proxy_str and len(rotator) > 0:
            check_proxy_str = rotator.next()
        proxies_check = (
            {"http": check_proxy_str, "https": check_proxy_str}
            if check_proxy_str
            else None
        )
        bal, bal_err = hotmail007_get_balance(proxies=proxies_check)
        if bal is not None:
            print(f"  账户余额: {bal}")
        else:
            print(f"  账户余额: 查询失败 ({bal_err})")
        stk, stk_err = hotmail007_get_stock(proxies=proxies_check)
        if stk is not None:
            print(f"  当前库存: {stk}")
        else:
            print(f"  当前库存: 查询失败 ({stk_err})")
    if EMAIL_MODE == "outlook_api":
        if OUTLOOK_API_URL:
            print(f"  API 地址: {OUTLOOK_API_URL}")
            print(f"  拉取数量(num): {OUTLOOK_API_NUM}")
            print(f"  邮箱类型(boxType): {OUTLOOK_API_BOX_TYPE}")
        else:
            print(f"  接码模式: 直连 Microsoft {HOTMAIL007_MAIL_MODE.upper()}")
        print(f"  轮询间隔: {OUTLOOK_API_POLL_INTERVAL}s")
        print(f"  超时设置: {OUTLOOK_API_POLL_TIMEOUT}s")
    print("=" * 60)
    print()

    if EMAIL_MODE == "file" and _email_queue is not None and not batch_count:
        batch_count = len(_email_queue)
        print(f"[*] file 模式自动设置批量数量: {batch_count}")
    if EMAIL_MODE == "outlook_api" and _outlook_api_queue is not None and not batch_count:
        batch_count = len(_outlook_api_queue)
        print(f"[*] outlook_api 模式自动设置批量数量: {batch_count}")

    if args.once and not batch_count:
        batch_count = 1

    if batch_count and batch_count > 0:
        remaining = [batch_count]
        stop_event = threading.Event()
        actual_threads = min(thread_count, batch_count)

        if actual_threads <= 1:
            _worker(
                worker_id=1,
                rotator=rotator,
                single_proxy=effective_single_proxy,
                sleep_min=sleep_min,
                sleep_max=sleep_max,
                count_target=batch_count,
                remaining=remaining,
                stop_event=stop_event,
            )
        else:
            print(f"[*] 启动 {actual_threads} 个并发线程...")
            threads = []
            for tid in range(1, actual_threads + 1):
                t = threading.Thread(
                    target=_worker,
                    args=(
                        tid,
                        rotator,
                        effective_single_proxy,
                        sleep_min,
                        sleep_max,
                        batch_count,
                        remaining,
                        stop_event,
                    ),
                    daemon=True,
                )
                threads.append(t)
                t.start()
                time.sleep(1)

            try:
                for t in threads:
                    t.join()
            except KeyboardInterrupt:
                print("\n[*] 收到中断信号，正在停止所有线程...")
                stop_event.set()
                for t in threads:
                    t.join(timeout=5)

        print(f"\n[*] 批量注册完毕! 共成功: {_success_counter} / 目标: {batch_count}")

    else:
        stop_event = threading.Event()

        if thread_count <= 1:
            try:
                _worker(
                    worker_id=1,
                    rotator=rotator,
                    single_proxy=effective_single_proxy,
                    sleep_min=sleep_min,
                    sleep_max=sleep_max,
                    count_target=None,
                    remaining=None,
                    stop_event=stop_event,
                )
            except KeyboardInterrupt:
                print("\n[*] 收到中断信号，停止运行")
        else:
            print(f"[*] 启动 {thread_count} 个并发线程 (循环模式)...")
            threads = []
            for tid in range(1, thread_count + 1):
                t = threading.Thread(
                    target=_worker,
                    args=(
                        tid,
                        rotator,
                        effective_single_proxy,
                        sleep_min,
                        sleep_max,
                        None,
                        None,
                        stop_event,
                    ),
                    daemon=True,
                )
                threads.append(t)
                t.start()
                time.sleep(1)

            try:
                while any(t.is_alive() for t in threads):
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\n[*] 收到中断信号，正在停止所有线程...")
                stop_event.set()
                for t in threads:
                    t.join(timeout=5)


if __name__ == "__main__":
    main()
