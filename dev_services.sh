#!/usr/bin/env bash

set -u
set -o pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$PROJECT_ROOT/logs/dev-services"
PID_DIR="$RUNTIME_DIR/pids"

SERVICES=(backend frontend)
FG_PIDS=()
FG_NAMES=()
CLEANED_UP=0

mkdir -p "$PID_DIR"

usage() {
  cat <<'EOF'
用法:
  ./dev_services.sh fg       前台启动两个服务，按 Ctrl+C 一键关闭
  ./dev_services.sh bg       后台启动两个服务
  ./dev_services.sh stop     停止后台服务
  ./dev_services.sh restart  重启后台服务
  ./dev_services.sh status   查看后台服务状态
EOF
}

service_log_file() {
  printf '%s/%s.log' "$RUNTIME_DIR" "$1"
}

service_pid_file() {
  printf '%s/%s.pid' "$PID_DIR" "$1"
}

service_command() {
  local service="$1"
  local cmd=""
  case "$service" in
    backend)
      printf -v cmd 'cd %q && exec %q api_server.py' "$PROJECT_ROOT" "python"
      ;;
    frontend)
      printf -v cmd 'cd %q && exec pnpm run dev -- --host 127.0.0.1 --port 8173' "$PROJECT_ROOT/frontend"
      ;;
    *)
      echo "未知服务: $service" >&2
      return 1
      ;;
  esac
  printf '%s' "$cmd"
}

is_pid_running() {
  local pid="$1"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

service_pid() {
  local pid_file
  pid_file="$(service_pid_file "$1")"
  [[ -f "$pid_file" ]] || return 1
  local pid
  pid="$(tr -d '[:space:]' <"$pid_file")"
  [[ -n "$pid" ]] || return 1
  printf '%s' "$pid"
}

service_running() {
  local pid
  pid="$(service_pid "$1")" || return 1
  is_pid_running "$pid"
}

clear_stale_pid() {
  local service="$1"
  local pid_file
  pid_file="$(service_pid_file "$service")"
  [[ -f "$pid_file" ]] || return 0
  local pid
  pid="$(tr -d '[:space:]' <"$pid_file")"
  if ! is_pid_running "$pid"; then
    rm -f "$pid_file"
  fi
}

stop_service() {
  local service="$1"
  clear_stale_pid "$service"
  local pid
  pid="$(service_pid "$service")" || return 0
  if ! is_pid_running "$pid"; then
    rm -f "$(service_pid_file "$service")"
    return 0
  fi
  kill -TERM "$pid" 2>/dev/null || true
  for _ in $(seq 1 20); do
    if ! is_pid_running "$pid"; then
      rm -f "$(service_pid_file "$service")"
      return 0
    fi
    sleep 0.3
  done
  kill -KILL "$pid" 2>/dev/null || true
  rm -f "$(service_pid_file "$service")"
}

start_service_background() {
  local service="$1"
  local cmd
  cmd="$(service_command "$service")"
  local log_file pid_file
  log_file="$(service_log_file "$service")"
  pid_file="$(service_pid_file "$service")"

  {
    printf '[%s] starting %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$service"
    printf '[%s] command: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$cmd"
  } >>"$log_file"

  bash -lc "$cmd" >>"$log_file" 2>&1 < /dev/null &
  local pid=$!
  printf '%s\n' "$pid" >"$pid_file"
  sleep 1
  if is_pid_running "$pid"; then
    printf '启动 %-10s 成功 pid=%s\n' "$service" "$pid"
    return 0
  fi
  echo "启动 $service 失败" >&2
  rm -f "$pid_file"
  return 1
}

start_background() {
  mkdir -p "$RUNTIME_DIR" "$PID_DIR"
  start_service_background backend || exit 1
  start_service_background frontend || exit 1
  echo "后台服务已启动"
}

show_status() {
  for service in "${SERVICES[@]}"; do
    clear_stale_pid "$service"
    if service_running "$service"; then
      printf '%-10s running pid=%s\n' "$service" "$(service_pid "$service")"
    else
      printf '%-10s stopped\n' "$service"
    fi
  done
}

cleanup_foreground() {
  if (( CLEANED_UP != 0 )); then
    return
  fi
  CLEANED_UP=1
  for pid in "${FG_PIDS[@]}"; do
    kill -TERM "$pid" 2>/dev/null || true
  done
}

start_service_foreground() {
  local service="$1"
  local cmd
  cmd="$(service_command "$service")"
  bash -lc "$cmd" > >(sed -u "s/^/[$service] /") 2>&1 &
  FG_PIDS+=("$!")
  FG_NAMES+=("$service")
}

start_foreground() {
  trap cleanup_foreground INT TERM EXIT
  start_service_foreground backend
  start_service_foreground frontend
  echo "前台模式运行中，按 Ctrl+C 停止"
  wait
}

main() {
  case "${1:-}" in
    fg) start_foreground ;;
    bg) start_background ;;
    stop) stop_service frontend; stop_service backend ;;
    restart) stop_service frontend; stop_service backend; start_background ;;
    status) show_status ;;
    *) usage ;;
  esac
}

main "$@"
