import os
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


ROOT_DIR = Path(__file__).resolve().parent
STATIC_DIR = ROOT_DIR / "web_static"


class StartRequest(BaseModel):
    once: bool = True
    count: Optional[int] = Field(default=None, ge=1)
    threads: Optional[int] = Field(default=None, ge=1)
    proxy: Optional[str] = None
    proxy_file: Optional[str] = None
    email_mode: Optional[str] = None
    sleep_min: Optional[int] = Field(default=None, ge=1)
    sleep_max: Optional[int] = Field(default=None, ge=1)


class ProcessRunner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen[str]] = None
        self._started_at: Optional[float] = None
        self._ended_at: Optional[float] = None
        self._last_cmd: List[str] = []
        self._logs: Deque[str] = deque(maxlen=3000)
        self._reader_thread: Optional[threading.Thread] = None
        self._history: Deque[Dict[str, Any]] = deque(maxlen=80)
        self._next_run_id = 1
        self._active_run_id: Optional[int] = None

    def _append_log(self, line: str) -> None:
        self._logs.append(line.rstrip("\n"))

    def _update_history(self, run_id: int, **updates: Any) -> None:
        for item in self._history:
            if item.get("run_id") == run_id:
                item.update(updates)
                return

    def _read_output(self, proc: subprocess.Popen[str], run_id: int) -> None:
        if proc.stdout is None:
            return
        for line in proc.stdout:
            self._append_log(line)
        proc.wait()
        with self._lock:
            self._ended_at = time.time()
            final_status = "stopped"
            if proc.returncode == 0:
                final_status = "success"
            elif proc.returncode not in (0, -15):
                final_status = "failed"
            for item in self._history:
                if item.get("run_id") == run_id and item.get("stopped_by_user"):
                    final_status = "stopped"
                    break
            self._update_history(
                run_id,
                ended_at=self._ended_at,
                returncode=proc.returncode,
                status=final_status,
            )
            if self._active_run_id == run_id:
                self._active_run_id = None
            self._append_log(f"[server] process exited with code {proc.returncode}")

    def _build_command(self, req: StartRequest) -> List[str]:
        cmd = [sys.executable, "-u", str(ROOT_DIR / "gpt.py")]
        if req.once and not req.count:
            cmd.append("--once")
        if req.count:
            cmd.extend(["--count", str(req.count)])
        if req.threads:
            cmd.extend(["--threads", str(req.threads)])
        if req.proxy:
            cmd.extend(["--proxy", req.proxy])
        if req.proxy_file:
            cmd.extend(["--proxy-file", req.proxy_file])
        if req.email_mode:
            cmd.extend(["--email-mode", req.email_mode])
        if req.sleep_min:
            cmd.extend(["--sleep-min", str(req.sleep_min)])
        if req.sleep_max:
            cmd.extend(["--sleep-max", str(req.sleep_max)])
        return cmd

    def start(self, req: StartRequest) -> dict:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                raise HTTPException(
                    status_code=409, detail="registration task is already running"
                )

            cmd = self._build_command(req)
            self._logs.clear()
            self._append_log("[server] starting process")
            self._append_log("[server] command: " + " ".join(cmd))
            self._started_at = time.time()
            self._ended_at = None
            self._last_cmd = cmd
            run_id = self._next_run_id
            self._next_run_id += 1
            req_payload = req.model_dump() if hasattr(req, "model_dump") else req.dict()
            self._history.append(
                {
                    "run_id": run_id,
                    "started_at": self._started_at,
                    "ended_at": None,
                    "status": "running",
                    "returncode": None,
                    "command": cmd,
                    "request": req_payload,
                    "stopped_by_user": False,
                }
            )
            self._active_run_id = run_id

            child_env = os.environ.copy()
            child_env["PYTHONUNBUFFERED"] = "1"
            child_env.setdefault("PYTHONIOENCODING", "utf-8")

            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(ROOT_DIR),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env=child_env,
                )
            except Exception as exc:
                self._ended_at = time.time()
                self._update_history(
                    run_id,
                    ended_at=self._ended_at,
                    status="failed_to_start",
                    returncode=-1,
                    error=str(exc),
                )
                self._active_run_id = None
                raise

            self._proc = proc
            self._reader_thread = threading.Thread(
                target=self._read_output,
                args=(proc, run_id),
                daemon=True,
            )
            self._reader_thread.start()

            return {
                "running": True,
                "pid": proc.pid,
                "command": cmd,
                "run_id": run_id,
            }

    def stop(self) -> dict:
        with self._lock:
            proc = self._proc
            if proc is None or proc.poll() is not None:
                return {"running": False, "stopped": False}
            run_id = self._active_run_id

            self._append_log("[server] stopping process")
            if run_id is not None:
                self._update_history(run_id, status="stopping", stopped_by_user=True)
            proc.terminate()

        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

        with self._lock:
            self._ended_at = time.time()

        return {
            "running": False,
            "stopped": True,
            "returncode": proc.returncode,
            "run_id": run_id,
        }

    def status(self) -> dict:
        with self._lock:
            running = self._proc is not None and self._proc.poll() is None
            return {
                "running": running,
                "run_id": self._active_run_id,
                "pid": self._proc.pid if running and self._proc else None,
                "returncode": None
                if running or self._proc is None
                else self._proc.returncode,
                "started_at": self._started_at,
                "ended_at": self._ended_at,
                "command": self._last_cmd,
                "log_lines": len(self._logs),
            }

    def logs(self, lines: int = 200) -> dict:
        line_count = max(1, min(lines, 2000))
        with self._lock:
            return {
                "lines": list(self._logs)[-line_count:],
                "running": self._proc is not None and self._proc.poll() is None,
            }

    def history(self, limit: int = 20) -> dict:
        max_items = max(1, min(limit, 80))
        with self._lock:
            items = list(self._history)[-max_items:]
            items.reverse()
            return {"items": items}


def _read_dotenv_defaults(path: Path) -> Dict[str, str]:
    env_data: Dict[str, str] = {}
    if not path.exists():
        return env_data
    try:
        with path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if not key:
                    continue
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                    value = value[1:-1]
                env_data[key] = value
    except Exception:
        return {}
    return env_data


def _env_value(key: str, dotenv_map: Dict[str, str]) -> Optional[str]:
    value = os.getenv(key)
    if value is not None and value != "":
        return value
    value = dotenv_map.get(key)
    if value is None or value == "":
        return None
    return value


def _to_int(value: Optional[str], default: int) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


def _build_presets() -> List[Dict[str, Any]]:
    dotenv_map = _read_dotenv_defaults(ROOT_DIR / ".env")
    proxy = _env_value("PROXY", dotenv_map)
    proxy_file = _env_value("PROXY_FILE", dotenv_map)
    threads = _to_int(_env_value("BATCH_THREADS", dotenv_map), 1)
    batch_count = _to_int(_env_value("BATCH_COUNT", dotenv_map), 5)

    return [
        {
            "name": "luckmail_cpa_once",
            "title": "LuckMail+CPA 单次",
            "description": "使用 luckmail 进行一次注册并自动上传 CPA",
            "payload": {
                "once": True,
                "count": None,
                "threads": max(1, threads),
                "proxy": proxy,
                "proxy_file": proxy_file,
                "email_mode": "luckmail",
                "sleep_min": 5,
                "sleep_max": 30,
            },
        },
        {
            "name": "luckmail_cpa_batch",
            "title": "LuckMail+CPA 批量",
            "description": "使用 luckmail 按批量模式注册并自动上传 CPA",
            "payload": {
                "once": False,
                "count": max(1, batch_count),
                "threads": max(1, threads),
                "proxy": proxy,
                "proxy_file": proxy_file,
                "email_mode": "luckmail",
                "sleep_min": 5,
                "sleep_max": 30,
            },
        },
    ]


class PresetStartRequest(BaseModel):
    name: str
    force_restart: bool = False


runner = ProcessRunner()

app = FastAPI(title="OpenAI Auto Register Web")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/status")
def api_status() -> dict:
    return runner.status()


@app.get("/api/logs")
def api_logs(lines: int = 200) -> dict:
    return runner.logs(lines=lines)


@app.post("/api/start")
def api_start(req: StartRequest) -> dict:
    return runner.start(req)


@app.post("/api/stop")
def api_stop() -> dict:
    return runner.stop()


@app.get("/api/config")
def api_config() -> dict:
    dotenv_map = _read_dotenv_defaults(ROOT_DIR / ".env")
    return {
        "root_dir": str(ROOT_DIR),
        "env_file": str(ROOT_DIR / ".env"),
        "defaults": {
            "email_mode": _env_value("EMAIL_MODE", dotenv_map),
            "proxy": _env_value("PROXY", dotenv_map),
            "proxy_file": _env_value("PROXY_FILE", dotenv_map),
            "batch_count": _env_value("BATCH_COUNT", dotenv_map),
            "batch_threads": _env_value("BATCH_THREADS", dotenv_map),
            "cpa_api_url": _env_value("CPA_API_URL", dotenv_map),
            "luckmail_base_url": _env_value("LUCKMAIL_BASE_URL", dotenv_map),
            "luckmail_project_code": _env_value("LUCKMAIL_PROJECT_CODE", dotenv_map),
        },
    }


@app.get("/api/history")
def api_history(limit: int = 20) -> dict:
    return runner.history(limit=limit)


@app.get("/api/presets")
def api_presets() -> dict:
    return {"items": _build_presets()}


@app.post("/api/start-preset")
def api_start_preset(req: PresetStartRequest) -> dict:
    items = _build_presets()
    selected = next((item for item in items if item.get("name") == req.name), None)
    if selected is None:
        raise HTTPException(status_code=404, detail="preset not found")
    current = runner.status()
    if current.get("running"):
        if not req.force_restart:
            raise HTTPException(
                status_code=409,
                detail=f"registration task is already running (run_id={current.get('run_id')}, pid={current.get('pid')})",
            )
        runner.stop()
        time.sleep(0.5)
    payload = selected.get("payload", {})
    start_req = StartRequest(**payload)
    result = runner.start(start_req)
    result["preset"] = req.name
    return result


if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR)), name="assets")


@app.get("/")
def index() -> FileResponse:
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="web_static/index.html not found")
    return FileResponse(index_path)
