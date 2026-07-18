#!/usr/bin/env bash
# Idempotent recovery/resume for the wilders-search pipeline.
# Safe to run any time (boot, after power loss, manually): every sync script
# resumes from its own state (OData GewijzigdOp cursor, ob/dg state.json +
# file-exists checks, yt-dlp --download-archive), so double work is avoided.
# Starts only what is not already running. Logs: /data/WILDERS/resume.log
cd "$(dirname "$0")"
LOG=/data/WILDERS/resume.log
exec >> "$LOG" 2>&1
# make `systemctl --user` work from cron @reboot (no login session env)
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
echo "=== resume $(date '+%F %T')"

start_if_absent() {  # <pgrep-pattern> <command...>
  local pattern=$1; shift
  if pgrep -f "$pattern" > /dev/null; then
    echo "  already running: $*"
  else
    nohup "$@" >> /data/WILDERS/pipeline.log 2>&1 &
    echo "  started (pid $!): $*"
  fi
}

# leftover partial video downloads from a crash (only when dg_sync is not active)
pgrep -f 'python3 dg_sync\.py' > /dev/null || rm -f /data/WILDERS/debatgemist/*.part.mp4

# 1. text sources (each chains its parser; both incremental)
start_if_absent 'python3 (tk_sync|tk_parse)\.py' bash -c 'python3 tk_sync.py && python3 tk_parse.py'
start_if_absent 'python3 (ob_sync|ob_parse)\.py' bash -c 'python3 ob_sync.py && python3 ob_parse.py'

# 2. youtube audio (rate-limit friendly; archive.txt makes it incremental)
start_if_absent 'python3 yt_sync\.py' python3 yt_sync.py

# 3. debate videos: distributed shards (local + remote hosts) + puller;
# dg_distributed.sh is itself idempotent and remote workers survive our reboots
./dg_distributed.sh

# 4. search app — runs as a systemd --user service (auto-starts at boot via
# linger); this just makes sure it is up after a manual resume.
if [ -f /data/WILDERS/index/index.sqlite ]; then
  systemctl --user start wilders-search 2>/dev/null && echo "  app service ensured up" \
    || echo "  systemctl start wilders-search faalde"
else
  echo "  no index yet; app not started"
fi

# 5. milestone watchers (mail via notify.py)
for m in text youtube video; do
  if ! pgrep -f "milestone_watch\.sh $m" > /dev/null; then
    setsid nohup ./milestone_watch.sh "$m" < /dev/null > /dev/null 2>&1 &
    echo "  watcher '$m' started"
  else
    echo "  watcher '$m' already running"
  fi
done

python3 status.py > /dev/null && echo "  progress snapshot written"
echo "=== resume done $(date '+%F %T')"
