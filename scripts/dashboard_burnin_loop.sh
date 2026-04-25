#!/usr/bin/env bash
set -u

ROOT="${ROOT:-/Users/sam/dev/pollypm}"
CONFIG="${CONFIG:-$HOME/.pollypm/pollypm.toml}"
LOG_DIR="${LOG_DIR:-$ROOT/.pollypm/burnin}"
SLEEP_SECONDS="${SLEEP_SECONDS:-600}"

mkdir -p "$LOG_DIR"
cd "$ROOT" || exit 1

run_once() {
  local ts
  ts="$(date +%Y%m%d-%H%M%S)"
  local log="$LOG_DIR/dashboard-$ts.log"

  {
    echo "== dashboard burn-in $ts =="
    echo "commit: $(git rev-parse --short HEAD 2>/dev/null || true)"
    echo
    uv run pytest -q \
      tests/test_project_dashboard_ui.py \
      tests/test_cockpit_rail_routes.py \
      tests/test_plan_review_flow.py \
      tests/test_notification_tiering.py
    echo
    uv run python scripts/release_invariants.py --config "$CONFIG"
    echo
  } >>"$log" 2>&1

  for project in polly_remote booktalk; do
    local session="dashboard-burnin-$project"
    tmux kill-session -t "$session" >/dev/null 2>&1 || true
    tmux new-session -d -s "$session" -x 210 -y 70 \
      "cd '$ROOT' && uv run pm cockpit-pane project '$project' --config '$CONFIG'"
    sleep 2
    tmux capture-pane -t "$session:0.0" -p -S 0 -E 70 \
      >"$LOG_DIR/$project-$ts.txt" 2>/dev/null || true
  done

  if grep -qE "FAILED|FAIL |Error|Traceback" "$log"; then
    tail -80 "$log"
  else
    echo "dashboard burn-in $ts ok"
  fi
}

while true; do
  run_once
  sleep "$SLEEP_SECONDS"
done
