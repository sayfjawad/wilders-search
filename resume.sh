#!/usr/bin/env bash
# Idempotent recovery/resume for the wilders-search pipeline.
# Safe to run any time (boot, after power loss, manually): every sync script
# resumes from its own state (OData GewijzigdOp cursor, ob/dg state.json +
# file-exists checks, yt-dlp --download-archive), so double work is avoided.
# Starts only what is not already running. Logs: /data/WILDERS/resume.log
cd "$(dirname "$0")"
LOG=/data/WILDERS/resume.log
exec >> "$LOG" 2>&1
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
pgrep -f 'dg_sync\.py' > /dev/null || rm -f /data/WILDERS/debatgemist/*.part.mp4

# 1. text sources (each chains its parser; both incremental)
start_if_absent 'tk_sync\.py|tk_parse\.py' bash -c 'python3 tk_sync.py && python3 tk_parse.py'
start_if_absent 'ob_sync\.py|ob_parse\.py' bash -c 'python3 ob_sync.py && python3 ob_parse.py'

# 2. youtube audio (rate-limit friendly; archive.txt makes it incremental)
start_if_absent 'yt_sync\.py' python3 yt_sync.py

# 3. debate videos (file-exists + state.json make it incremental)
start_if_absent 'dg_sync\.py' python3 dg_sync.py

# 4. search app
if ! pgrep -f 'uvicorn app:app' > /dev/null; then
  if [ -f /data/WILDERS/index/index.sqlite ]; then
    nohup env HF_HOME=/data/huggingface CUDA_VISIBLE_DEVICES=0 \
      python3 -m uvicorn app:app --host 0.0.0.0 --port 8902 >> /data/WILDERS/app.log 2>&1 &
    echo "  app started (pid $!)"
  else
    echo "  no index yet; app not started"
  fi
else
  echo "  app already running"
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
