#!/usr/bin/env bash
# chess-tutor-ai 개발 서버 관리.  사용법: scripts/server.sh help
#
# Runs on the macOS default bash 3.2 and on Linux. Each service is started detached in its own
# process group (so `stop` also takes down uvicorn's reload child, Stockfish, and vite's node
# process); pid files and logs live under .run/ (gitignored).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_DIR="$ROOT/apps/api"
WEB_DIR="$ROOT/apps/web"
RUN_DIR="$ROOT/.run"
LOG_DIR="$RUN_DIR/logs"
SELF="scripts/server.sh"

# A root .env is exported so it reaches uvicorn and vite whatever their cwd is.
# (The API additionally reads apps/api/.env through pydantic-settings.)
if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env"
  set +a
fi

API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8000}"
API_RELOAD="${API_RELOAD:-1}"          # 0 이면 --reload 없이 띄운다
WEB_HOST="${WEB_HOST:-}"               # 비우면 vite 기본값(localhost). LAN 공개는 0.0.0.0
WEB_PORT="${WEB_PORT:-5173}"
DESIGN_PORT="${DESIGN_PORT:-8765}"
START_TIMEOUT="${START_TIMEOUT:-90}"   # 헬스 체크 대기(초)
STOP_TIMEOUT="${STOP_TIMEOUT:-15}"     # SIGTERM 후 SIGKILL 까지(초)

DEFAULT_SERVICES="api web"             # start/stop/restart 에 이름을 안 주면 이것
ALL_SERVICES="api web design"

# ---------------------------------------------------------------- output helpers

if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
  C_RED=$(printf '\033[31m'); C_GRN=$(printf '\033[32m'); C_YLW=$(printf '\033[33m')
  C_DIM=$(printf '\033[2m');  C_BLD=$(printf '\033[1m');  C_RST=$(printf '\033[0m')
else
  C_RED=""; C_GRN=""; C_YLW=""; C_DIM=""; C_BLD=""; C_RST=""
fi

info() { printf '%s\n' "$*"; }
ok()   { printf '%s✔%s %s\n' "$C_GRN" "$C_RST" "$*"; }
warn() { printf '%s!%s %s\n' "$C_YLW" "$C_RST" "$*" >&2; }
err()  { printf '%s✘%s %s\n' "$C_RED" "$C_RST" "$*" >&2; }
die()  { err "$@"; exit 1; }
hdr()  { printf '\n%s%s%s\n' "$C_BLD" "$*" "$C_RST"; }
run()  { printf '%s$ %s%s\n' "$C_DIM" "$*" "$C_RST"; "$@"; }

need() { command -v "$1" >/dev/null 2>&1 || die "'$1' 이(가) 필요합니다. ${2:-}"; }
upper() { printf '%s' "$1" | tr '[:lower:]' '[:upper:]'; }

# ---------------------------------------------------------------- service table

svc_check() {
  case " $ALL_SERVICES " in
    *" $1 "*) ;;
    *) die "알 수 없는 서비스: '$1' (api | web | design)" ;;
  esac
}

svc_port() {
  case "$1" in
    api) echo "$API_PORT" ;;
    web) echo "$WEB_PORT" ;;
    design) echo "$DESIGN_PORT" ;;
  esac
}

svc_url() {
  case "$1" in
    api) echo "http://$API_HOST:$API_PORT" ;;
    web) echo "http://${WEB_HOST:-localhost}:$WEB_PORT" ;;
    design) echo "http://localhost:$DESIGN_PORT" ;;
  esac
}

# What the health probe hits. uvicorn and http.server bind 127.0.0.1; vite binds `localhost`,
# which on recent Node is [::1] only, so its probe goes through the name (curl tries v6 then v4).
svc_probe_url() {
  case "$1" in
    api) echo "http://127.0.0.1:$API_PORT/health" ;;
    web) echo "http://localhost:$WEB_PORT/" ;;
    design) echo "http://127.0.0.1:$DESIGN_PORT/" ;;
  esac
}

# Regex the leader's command line must match. Guards a stale pid file against pid reuse.
svc_match() {
  case "$1" in
    api) echo "uvicorn" ;;
    web) echo "pnpm|vite" ;;
    design) echo "http\\.server" ;;
  esac
}

pidfile() { echo "$RUN_DIR/$1.pid"; }
logfile() { echo "$LOG_DIR/$1.log"; }

# Space-separated pids listening on a TCP port (empty when none or lsof is missing).
port_pids() {
  command -v lsof >/dev/null 2>&1 || return 0
  { lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null || true; } | sort -u | tr '\n' ' ' | sed 's/ *$//'
}

ps_lines() { { ps -o pid=,command= -p "$(printf '%s' "$1" | tr ' ' ',')" 2>/dev/null || true; } | sed 's/^/    /'; }

# Prints the pid from the service's pid file if that process is alive and looks right.
managed_pid() {
  local f pid
  f=$(pidfile "$1")
  [[ -f "$f" ]] || return 1
  pid=$(cat "$f" 2>/dev/null) || return 1
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  ps -o command= -p "$pid" 2>/dev/null | grep -Eq "$(svc_match "$1")" || return 1
  echo "$pid"
}

probe() { curl -fsS -m 2 -o /dev/null "$(svc_probe_url "$1")" 2>/dev/null; }

# ---------------------------------------------------------------- env helpers

# Effective value of a backend setting: exported env (root .env is already loaded) → apps/api/.env.
setting() {
  local v="${!1:-}"
  if [[ -z "$v" && -f "$API_DIR/.env" ]]; then
    v=$({ grep -E "^[[:space:]]*$1=" "$API_DIR/.env" || true; } | tail -n 1 | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/")
  fi
  printf '%s' "$v"
}

db_url() {
  local v
  v=$(setting DATABASE_URL)
  printf '%s' "${v:-sqlite+aiosqlite:///./chess_tutor.db}"
}

# Path of the SQLite file (relative URLs resolve against apps/api, uvicorn's cwd).
sqlite_path() {
  local url p
  url=$(db_url)
  p="${url#*:///}"
  case "$p" in
    /*) printf '%s' "$p" ;;
    *) printf '%s/%s' "$API_DIR" "${p#./}" ;;
  esac
}

find_stockfish() {
  local p
  p=$(setting STOCKFISH_PATH)
  if [[ -n "$p" && -x "$p" ]]; then printf '%s' "$p"; return 0; fi
  command -v stockfish 2>/dev/null
}

# ---------------------------------------------------------------- launchers (exec'd in a subshell)

launch_api() {
  cd "$API_DIR"
  set -- run uvicorn chess_tutor.api:app --host "$API_HOST" --port "$API_PORT"
  if [[ "$API_RELOAD" == "1" ]]; then set -- "$@" --reload; fi
  exec nohup uv "$@"
}

launch_web() {
  cd "$WEB_DIR"
  set -- dev --port "$WEB_PORT" --strictPort
  if [[ -n "$WEB_HOST" ]]; then set -- "$@" --host "$WEB_HOST"; fi
  API_PORT="$API_PORT" exec nohup pnpm "$@"
}

launch_design() {
  cd "$ROOT"
  exec nohup python3 -m http.server "$DESIGN_PORT" --bind 127.0.0.1 --directory design
}

# ---------------------------------------------------------------- start / stop

wait_ready() {   # 0 ready, 1 process died, 2 timeout
  local svc=$1 pid=$2 i=0 tries=$((START_TIMEOUT * 2))
  while (( i < tries )); do
    kill -0 "$pid" 2>/dev/null || return 1
    if probe "$svc"; then return 0; fi
    sleep 0.5
    i=$((i + 1))
  done
  return 2
}

wait_group_gone() {
  local pid=$1 i=0 tries=$((STOP_TIMEOUT * 2))
  while (( i < tries )); do
    kill -0 -- "-$pid" 2>/dev/null || return 0
    sleep 0.5
    i=$((i + 1))
  done
  return 1
}

start_one() {
  local svc=$1 port pid other log rc
  svc_check "$svc"
  port=$(svc_port "$svc")

  if pid=$(managed_pid "$svc"); then
    ok "$svc: 이미 실행 중 (pid $pid) → $(svc_url "$svc")"
    return 0
  fi
  other=$(port_pids "$port")
  if [[ -n "$other" ]]; then
    err "$svc: 포트 $port 를 다른 프로세스가 쓰고 있습니다 (pid $other)"
    ps_lines "$other" >&2
    info "    이 스크립트가 띄운 프로세스가 아닙니다. '$SELF stop --force $svc' 로 정리하거나 $(upper "$svc")_PORT 를 바꾸세요."
    return 1
  fi
  case "$svc" in
    api) need uv "설치: https://docs.astral.sh/uv/" ;;
    web) need pnpm "설치: corepack enable 또는 npm i -g pnpm" ;;
    design) need python3 ;;
  esac

  mkdir -p "$LOG_DIR"
  log=$(logfile "$svc")
  if [[ -f "$log" ]]; then mv -f "$log" "$log.1"; fi

  # set -m gives the job its own process group; the leader's pid is the group id.
  set -m
  ( "launch_$svc" ) </dev/null >"$log" 2>&1 &
  pid=$!
  disown "$pid" 2>/dev/null || true
  set +m
  echo "$pid" >"$(pidfile "$svc")"
  info "$svc: 시작 중… (pid $pid, 로그 $log)"

  rc=0
  wait_ready "$svc" "$pid" || rc=$?
  case "$rc" in
    0)
      ok "$svc: 준비됨 → $(svc_url "$svc")"
      if [[ "$svc" == api ]]; then info "    API 문서: $(svc_url api)/docs"; fi
      return 0
      ;;
    1)
      err "$svc: 프로세스가 바로 종료됐습니다. 로그 마지막 부분:"
      rm -f "$(pidfile "$svc")"
      ;;
    *)
      err "$svc: ${START_TIMEOUT}초 안에 응답하지 않습니다 (START_TIMEOUT 으로 조정). 프로세스는 살려 둡니다. 로그 마지막 부분:"
      ;;
  esac
  tail -n 30 "$log" | sed 's/^/    /' >&2
  return 1
}

stop_one() {
  local svc=$1 force=${2:-0} pid port other f i
  svc_check "$svc"
  port=$(svc_port "$svc")
  f=$(pidfile "$svc")

  if pid=$(managed_pid "$svc"); then
    info "$svc: 종료 중… (pid $pid)"
    kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    if ! wait_group_gone "$pid"; then
      warn "$svc: ${STOP_TIMEOUT}초 안에 끝나지 않아 강제 종료합니다 (SIGKILL)"
      kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
      sleep 0.5
    fi
    ok "$svc: 종료됨"
  elif [[ -f "$f" ]]; then
    info "$svc: 실행 중이 아님 (오래된 pid 파일 정리)"
  else
    info "$svc: 실행 중이 아님"
  fi
  rm -f "$f"

  other=$(port_pids "$port")
  [[ -n "$other" ]] || return 0
  if [[ "$force" != 1 ]]; then
    warn "$svc: 포트 $port 는 아직 사용 중입니다 (pid $other). 이 스크립트가 띄운 프로세스가 아닙니다."
    ps_lines "$other" >&2
    info "    '$SELF stop --force $svc' 로 종료할 수 있습니다."
    return 0
  fi
  warn "$svc: 포트 $port 를 점유한 외부 프로세스를 종료합니다 (pid $other)"
  ps_lines "$other" >&2
  # shellcheck disable=SC2086
  kill -TERM $other 2>/dev/null || true
  i=0
  while (( i < STOP_TIMEOUT * 2 )); do
    [[ -n "$(port_pids "$port")" ]] || break
    sleep 0.5
    i=$((i + 1))
  done
  other=$(port_pids "$port")
  if [[ -n "$other" ]]; then
    warn "$svc: 응답이 없어 SIGKILL 을 보냅니다 (pid $other)"
    # shellcheck disable=SC2086
    kill -KILL $other 2>/dev/null || true
    sleep 0.5
  fi
  if [[ -z "$(port_pids "$port")" ]]; then ok "$svc: 포트 $port 비움"; else err "$svc: 포트 $port 를 비우지 못했습니다"; return 1; fi
}

# ---------------------------------------------------------------- commands

resolve_services() {   # "$@" → list; empty or "all" → default set
  local out="" s
  if [[ $# -eq 0 ]]; then echo "$DEFAULT_SERVICES"; return; fi
  for s in "$@"; do
    if [[ "$s" == all ]]; then out="$out $DEFAULT_SERVICES"; else svc_check "$s"; out="$out $s"; fi
  done
  echo "$out"
}

cmd_start() {
  local rc=0 s services
  services=$(resolve_services "$@")   # a bad name dies here (set -e), not inside a subshell
  for s in $services; do start_one "$s" || rc=1; done
  return $rc
}

cmd_stop() {
  local force=0 names="" s rc=0
  for s in "$@"; do
    case "$s" in
      -f|--force) force=1 ;;
      *) names="$names $s" ;;
    esac
  done
  if [[ -z "$names" ]]; then
    names="web design api"      # front-end first, then the API that holds the engines
  elif [[ "$names" == *" all"* ]]; then
    names="web design api"
  fi
  # shellcheck disable=SC2086
  for s in $names; do stop_one "$s" "$force" || rc=1; done
  return $rc
}

cmd_restart() {
  local s services
  services=$(resolve_services "$@")
  for s in $services; do stop_one "$s"; done
  cmd_start $services
}

row() {   # service port pid colour state url
  printf '%-8s %-6s %-12s %s%-10s%s %s\n' "$1" "$2" "$3" "$4" "$5" "$C_RST" "$6"
}

cmd_status() {
  local s pid other colour state pids
  row SERVICE PORT PID "" STATE URL
  for s in $ALL_SERVICES; do
    pid=""; other=""; colour=""; state="stopped"; pids="-"
    if pid=$(managed_pid "$s"); then
      pids=$pid
      if probe "$s"; then colour=$C_GRN; state="running"; else colour=$C_YLW; state="starting"; fi
    else
      other=$(port_pids "$(svc_port "$s")")
      if [[ -n "$other" ]]; then
        pids=$(printf '%s' "$other" | tr ' ' ',')
        colour=$C_YLW
        if probe "$s"; then state="external"; else state="ext-busy"; fi
      fi
    fi
    row "$s" "$(svc_port "$s")" "$pids" "$colour" "$state" "$(svc_url "$s")"
  done
  db_status_line
  printf '\n%sDATABASE_URL%s %s\n' "$C_DIM" "$C_RST" "$(db_url)"
  printf '%sSTOCKFISH   %s %s\n' "$C_DIM" "$C_RST" "$(find_stockfish || echo '(없음 — brew install stockfish)')"
  printf '%sLLM         %s %s\n' "$C_DIM" "$C_RST" "$([[ -n "$(setting ANTHROPIC_API_KEY)" ]] && echo "ANTHROPIC_API_KEY 설정됨 ($(setting ANTHROPIC_MODEL))" || echo '키 없음 → 템플릿 설명만')"
}

db_status_line() {
  local colour="" state url
  url=$(db_url)
  case "$url" in
    postgres*)
      if ! command -v docker >/dev/null 2>&1; then
        state="no-docker"
      elif [[ -n "$(cd "$ROOT" && docker compose ps --status running -q db 2>/dev/null)" ]]; then
        colour=$C_GRN; state="up"
      else
        state="down"
      fi
      row db 5432 docker "$colour" "$state" "postgres (docker compose)"
      ;;
    *)
      if [[ -f "$(sqlite_path)" ]]; then
        colour=$C_GRN; state="$(du -h "$(sqlite_path)" | cut -f1 | tr -d ' ')"
      else
        state="no-file"
      fi
      row db - sqlite "$colour" "$state" "$(sqlite_path)"
      ;;
  esac
}

cmd_health() {
  local rc=0 body
  if body=$(curl -fsS -m 3 "$(svc_probe_url api)" 2>/dev/null); then
    ok "api  $(svc_url api)/health → $body"
  else
    err "api  $(svc_url api)/health 응답 없음"; rc=1
  fi
  if probe web; then ok "web  $(svc_url web)"; else warn "web  $(svc_url web) 응답 없음"; fi
  return $rc
}

cmd_logs() {
  local follow=0 n=80 names="" s f files=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -f|--follow) follow=1 ;;
      -n) shift; n=${1:-80} ;;
      -n*) n=${1#-n} ;;
      *) svc_check "$1"; names="$names $1" ;;
    esac
    shift
  done
  [[ -n "$names" ]] || names="$DEFAULT_SERVICES"
  for s in $names; do
    f=$(logfile "$s")
    if [[ -f "$f" ]]; then files="$files $f"; else warn "$s: 로그 없음 ($f)"; fi
  done
  [[ -n "$files" ]] || return 1
  # shellcheck disable=SC2086
  if [[ "$follow" == 1 ]]; then exec tail -n "$n" -F $files; else tail -n "$n" $files; fi
}

cmd_dev() {
  local tail_pid services
  services=$(resolve_services "$@")
  if ! cmd_start "$@"; then
    err "일부 서비스가 뜨지 않았습니다. 이미 뜬 것은 그대로 둡니다 ('$SELF stop' 으로 정리)."
    return 1
  fi
  info ""
  info "로그를 따라갑니다. Ctrl-C 를 누르면 두 서버를 모두 내립니다."
  info ""
  # Ctrl-C (INT), kill (TERM) and a closed terminal (HUP) all take the servers down.
  # shellcheck disable=SC2064
  trap "echo; cmd_stop $services; exit 0" INT TERM HUP
  # shellcheck disable=SC2046
  tail -n 0 -F $(for s in $services; do logfile "$s"; done) &
  tail_pid=$!
  wait "$tail_pid" || true
}

cmd_test() {
  local which=${1:-all}
  if [[ $# -gt 0 ]]; then shift; fi
  case "$which" in
    api) (cd "$API_DIR" && run uv run pytest -q "$@") ;;
    web) web_test "$@" ;;
    all) (cd "$API_DIR" && run uv run pytest -q "$@"); web_test ;;
    *) die "test [api|web|all] [pytest/vitest 인자...]" ;;
  esac
}

web_test() {
  if [[ -x "$WEB_DIR/node_modules/.bin/vitest" ]]; then
    (cd "$WEB_DIR" && run pnpm exec vitest run "$@")
  else
    warn "web: vitest 가 설치되어 있지 않아 apps/web/tests 는 건너뜁니다 (pnpm add -D vitest 후 실행 가능)."
  fi
}

cmd_lint() {
  local which=${1:-all}
  case "$which" in
    api|all)
      (cd "$API_DIR" && run uv run ruff format --check src tests && run uv run ruff check src tests && run uv run mypy src)
      ;;
  esac
  case "$which" in
    web|all)
      (cd "$WEB_DIR" && run pnpm lint && run pnpm exec tsc --noEmit)
      ;;
  esac
  case "$which" in api|web|all) ;; *) die "lint [api|web|all]" ;; esac
}

cmd_fmt() {
  (cd "$API_DIR" && run uv run ruff format src tests && run uv run ruff check --fix src tests)
  (cd "$WEB_DIR" && run pnpm exec eslint . --fix)
}

cmd_ci() {   # mirrors .github/workflows/ci.yml
  hdr "api: lint + type check"
  cmd_lint api
  hdr "api: pytest"
  (cd "$API_DIR" && run uv run pytest -q --timeout=180)
  hdr "web: lint + build"
  (cd "$WEB_DIR" && run pnpm lint && run pnpm build)
  hdr "완료"
  ok "CI 와 같은 검사를 모두 통과했습니다."
}

cmd_setup() {
  local maia=0 a
  for a in "$@"; do case "$a" in --maia) maia=1 ;; *) die "setup [--maia]" ;; esac; done
  need uv "설치: https://docs.astral.sh/uv/"
  need pnpm "설치: corepack enable 또는 npm i -g pnpm"
  hdr "엔진"
  if p=$(find_stockfish); then ok "stockfish: $p"; else warn "stockfish 가 없습니다. brew install stockfish (엔진 테스트는 건너뜁니다)"; fi
  hdr "apps/api"
  if [[ "$maia" == 1 ]]; then
    (cd "$API_DIR" && run uv sync --all-groups --extra maia)
  else
    (cd "$API_DIR" && run uv sync --all-groups)
  fi
  hdr "apps/web"
  (cd "$WEB_DIR" && run pnpm install)
  hdr "환경"
  if [[ -f "$ROOT/.env" || -f "$API_DIR/.env" ]]; then
    ok ".env 있음"
  else
    info ".env 없음: 기본값(SQLite, PATH 의 stockfish, LLM 없음)으로 동작합니다. 필요하면 .env.example 을 참고해 .env 를 만드세요."
  fi
  ok "준비 끝. '$SELF dev' 로 시작하세요."
}

mark() { printf '  %s%s%s %s\n' "$2" "$1" "$C_RST" "$3"; }   # symbol colour text

tool_line() {   # name, version-command...
  local name=$1 path ver
  shift
  if path=$(command -v "$name" 2>/dev/null); then
    ver=$("$@" 2>/dev/null | head -n 1 || true)
    printf '  %s✔%s %-10s %s  %s%s%s\n' "$C_GRN" "$C_RST" "$name" "$path" "$C_DIM" "$ver" "$C_RST"
  else
    printf '  %s✘%s %-10s (없음)\n' "$C_RED" "$C_RST" "$name"
  fi
}

cmd_doctor() {
  local sf maia_dir
  hdr "도구"
  tool_line uv uv --version
  tool_line pnpm pnpm --version
  tool_line node node --version
  tool_line python3 python3 --version
  tool_line docker docker --version
  tool_line curl sh -c 'curl --version | cut -d" " -f1-2'
  tool_line lsof true
  if sf=$(find_stockfish); then
    printf '  %s✔%s %-10s %s  %s%s%s\n' "$C_GRN" "$C_RST" stockfish "$sf" "$C_DIM" "$(printf 'uci\nquit\n' | "$sf" 2>/dev/null | grep -m1 'id name' || echo 'UCI 응답 없음')" "$C_RST"
  else
    printf '  %s✘%s %-10s (없음 → brew install stockfish)\n' "$C_RED" "$C_RST" stockfish
  fi

  hdr "의존성"
  if [[ -d "$API_DIR/.venv" ]]; then mark ✔ "$C_GRN" "apps/api/.venv"; else mark ✘ "$C_RED" "apps/api/.venv 없음 → $SELF setup"; fi
  if [[ -d "$WEB_DIR/node_modules" ]]; then mark ✔ "$C_GRN" "apps/web/node_modules"; else mark ✘ "$C_RED" "apps/web/node_modules 없음 → $SELF setup"; fi
  if ls "$API_DIR"/.venv/lib/python3*/site-packages/maia2 >/dev/null 2>&1; then
    maia_dir="${MAIA_MODEL_DIR:-$HOME/.cache/chess-tutor/maia2}"
    if [[ -d "$maia_dir" && -n "$(ls -A "$maia_dir" 2>/dev/null)" ]]; then
      mark ✔ "$C_GRN" "maia2 설치됨, 가중치 $maia_dir"
    else
      mark · "" "maia2 설치됨, 가중치는 첫 사용 때 $maia_dir 에 내려받습니다"
    fi
  else
    mark · "" "maia2 미설치 (Stockfish 폴백으로 동작). 쓰려면 $SELF setup --maia"
  fi

  hdr "환경 변수"
  if [[ -f "$ROOT/.env" ]]; then mark ✔ "$C_GRN" ".env (루트, 서비스에 export)"; fi
  if [[ -f "$API_DIR/.env" ]]; then mark ✔ "$C_GRN" "apps/api/.env (API 만 읽음)"; fi
  if [[ ! -f "$ROOT/.env" && ! -f "$API_DIR/.env" ]]; then mark · "" ".env 없음 (기본값으로 동작, 참고: .env.example)"; fi
  sf=$(setting STOCKFISH_PATH)
  info "  DATABASE_URL      $(db_url)"
  info "  STOCKFISH_PATH    ${sf:-(미설정 → PATH 검색)}"
  info "  ANTHROPIC_API_KEY $([[ -n "$(setting ANTHROPIC_API_KEY)" ]] && echo '설정됨' || echo '없음 → 템플릿 설명만')"
  info "  LICHESS_TOKEN     $([[ -n "$(setting LICHESS_TOKEN)" ]] && echo '설정됨' || echo '없음 → 마스터 오버레이 꺼짐')"
  info "  API_PORT=$API_PORT WEB_PORT=$WEB_PORT API_RELOAD=$API_RELOAD"

  hdr "서비스"
  cmd_status
}

confirm() {
  local ans
  if [[ "${ASSUME_YES:-0}" == 1 ]]; then return 0; fi
  if [[ ! -t 0 ]]; then die "대화형 터미널이 아닙니다. -y 를 붙여 실행하세요."; fi
  printf '%s (y/N) ' "$1"
  read -r ans
  [[ "$ans" == y || "$ans" == Y ]]
}

cmd_db() {
  local sub=${1:-} a url p
  if [[ $# -gt 0 ]]; then shift; fi
  for a in "$@"; do case "$a" in -y|--yes) ASSUME_YES=1 ;; esac; done
  url=$(db_url)
  case "$sub" in
    url) echo "$url" ;;
    up)
      need docker
      (cd "$ROOT" && run docker compose up -d db)
      case "$url" in
        postgres*) ;;
        *) info "지금 DATABASE_URL 은 SQLite 입니다. Postgres 를 쓰려면 .env 에
    DATABASE_URL=postgresql+asyncpg://chess:chess@localhost:5432/chess_tutor
  를 넣고 'cd apps/api && uv sync --all-groups --extra postgres' 를 실행하세요." ;;
      esac
      ;;
    down) need docker; (cd "$ROOT" && run docker compose stop db) ;;
    shell)
      case "$url" in
        postgres*) need docker; (cd "$ROOT" && exec docker compose exec db psql -U chess -d chess_tutor) ;;
        *) need sqlite3; p=$(sqlite_path); [[ -f "$p" ]] || die "SQLite 파일이 없습니다: $p"; exec sqlite3 "$p" ;;
      esac
      ;;
    reset)
      if managed_pid api >/dev/null || [[ -n "$(port_pids "$API_PORT")" ]]; then
        die "API 가 실행 중입니다. 먼저 '$SELF stop api' 를 실행하세요."
      fi
      case "$url" in
        postgres*)
          need docker
          confirm "Postgres 볼륨(pgdata)을 지우고 DB 를 비웁니다. 계속할까요?" || die "취소"
          (cd "$ROOT" && run docker compose down -v)
          ok "지웠습니다. '$SELF db up' 으로 다시 만드세요 (테이블은 API 가 시작할 때 생성)."
          ;;
        *)
          p=$(sqlite_path)
          [[ -f "$p" ]] || { info "SQLite 파일이 없습니다: $p"; return 0; }
          confirm "$p ($(du -h "$p" | cut -f1 | tr -d ' ')) 을 지웁니다. 분석·리뷰 캐시가 모두 사라집니다. 계속할까요?" || die "취소"
          rm -f "$p" "$p-wal" "$p-shm" "$p-journal"
          ok "지웠습니다. 테이블은 API 가 다음에 시작할 때 다시 만듭니다."
          ;;
      esac
      ;;
    *) die "db url | up | down | shell | reset [-y]" ;;
  esac
}

cmd_open() {
  local url
  url=$(svc_url web)
  if command -v open >/dev/null 2>&1; then open "$url"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$url"
  else echo "$url"; fi
}

usage() {
  cat <<USAGE
chess-tutor-ai 개발 서버 관리

사용법: $SELF <명령> [인자]

서버
  dev [svc...]         api + web 을 띄우고 로그를 따라간다. Ctrl-C 로 모두 종료
  start [svc...]       백그라운드로 띄운다 (기본: api web). 헬스 체크까지 기다림
  stop [svc...] [-f]   종료 (기본: 전부). -f/--force 는 이 스크립트가 띄우지 않은 포트 점유 프로세스도 종료
  restart [svc...]     stop 후 start
  status               서비스·포트·PID·상태 (별칭: ps)
  health               /health 응답 확인
  logs [svc...] [-f] [-n N]   로그 보기 (.run/logs/). -f 는 follow
  open                 브라우저로 웹 열기

svc = api (uvicorn :$API_PORT) | web (vite :$WEB_PORT) | design (design/ 미리보기 :$DESIGN_PORT)

개발
  setup [--maia]       uv sync + pnpm install + 도구 점검
  doctor               도구·의존성·환경 변수·포트 진단
  test [api|web] [...] pytest / vitest (추가 인자는 그대로 전달)
  lint [api|web]       ruff · mypy · eslint · tsc
  fmt                  ruff format/fix + eslint --fix
  ci                   .github/workflows/ci.yml 과 같은 검사를 로컬에서 실행

DB
  db url               적용 중인 DATABASE_URL
  db up | down         Postgres (docker compose)
  db shell             sqlite3 또는 psql
  db reset [-y]        SQLite 파일 삭제 / Postgres 볼륨 삭제 (API 가 꺼져 있어야 함)

환경 변수 (루트 .env 도 읽음)
  API_HOST=$API_HOST API_PORT=$API_PORT API_RELOAD=$API_RELOAD
  WEB_HOST=${WEB_HOST:-(localhost)} WEB_PORT=$WEB_PORT DESIGN_PORT=$DESIGN_PORT
  START_TIMEOUT=$START_TIMEOUT STOP_TIMEOUT=$STOP_TIMEOUT
USAGE
}

# ---------------------------------------------------------------- main

main() {
  local cmd=${1:-help}
  if [[ $# -gt 0 ]]; then shift; fi
  case "$cmd" in
    dev|up)          cmd_dev "$@" ;;
    start)           cmd_start "$@" ;;
    stop|down)       cmd_stop "$@" ;;
    restart)         cmd_restart "$@" ;;
    status|ps)       cmd_status ;;
    health)          cmd_health ;;
    logs|log)        cmd_logs "$@" ;;
    open)            cmd_open ;;
    setup|install)   cmd_setup "$@" ;;
    doctor)          cmd_doctor ;;
    test)            cmd_test "$@" ;;
    lint)            cmd_lint "$@" ;;
    fmt|format)      cmd_fmt ;;
    ci|check)        cmd_ci ;;
    db)              cmd_db "$@" ;;
    help|-h|--help)  usage ;;
    *) err "알 수 없는 명령: $cmd"; usage >&2; exit 2 ;;
  esac
}

main "$@"
