#!/usr/bin/env bash
# Distributed Debat Direct download: 8 shards over 4 hosts + a puller that
# drains remote output back to /data/WILDERS/debatgemist.
# Idempotent: starts only what is not already running — safe from resume.sh,
# cron @reboot or by hand. Remote workers survive Z8 reboots on their own.
# Markers /data/WILDERS/.dg_remote_<shard>.active tell dg_pull.sh and
# milestone_watch.sh which remote shards are (still) in flight.
cd "$(dirname "$0")"
DG=/data/WILDERS/debatgemist
LOG=/data/WILDERS/pipeline.log
# shard layout + remote hosts live in hosts.env (untracked; see hosts.env.example)
N=1
LOCAL_SHARDS="0"
REMOTES=""
[ -f hosts.env ] && . ./hosts.env

echo "--- dg_distributed $(date '+%F %T')"

# `kill <pid>` on a dg_sync.py shard only signals that process, not its
# ffmpeg child (started via plain subprocess.run, no process-group setup) --
# the child survives as an orphan (reparented to init) and can keep writing
# to the same target a freshly (re)started shard also picks up, corrupting
# both. Kill any such orphan and its now-stale .part.mp4 before (re)starting.
reap_orphan_ffmpeg() {  # <dir with .part.mp4 outputs>
  local dir=$1
  for pid in $(pgrep -f 'ffmpeg.*\.part\.mp4' 2>/dev/null); do
    [ "$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')" = "1" ] || continue
    kill -9 "$pid" 2>/dev/null && echo "  wees-ffmpeg gekilld: pid $pid"
  done
  sleep 1
  for f in "$dir"/*.part.mp4; do
    [ -e "$f" ] || continue
    pgrep -f "ffmpeg.*$(basename "$f")" > /dev/null || { rm -f "$f"; echo "  stale part verwijderd: $(basename "$f")"; }
  done
}
reap_orphan_ffmpeg "$DG"

# fresh work manifest for the remote workers
python3 dg_sync.py --export-dates "$DG/dates.json"
ls "$DG"/*.mp4 2>/dev/null | grep -v '\.part\.mp4' | xargs -rn1 basename > "$DG/have.txt"

for s in $LOCAL_SHARDS; do
  if pgrep -f "dg_sync.py --shard $s/$N" > /dev/null; then
    echo "  lokale shard $s/$N draait al"
  else
    nohup python3 dg_sync.py --shard "$s/$N" >> "$LOG" 2>&1 &
    echo "  lokale shard $s/$N gestart (pid $!)"
  fi
done

REMOTE_REAP='
for pid in $(pgrep -f "ffmpeg.*\.part\.mp4" 2>/dev/null); do
  [ "$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d " ")" = "1" ] || continue
  kill -9 "$pid" 2>/dev/null && echo "  wees-ffmpeg gekilld op $(hostname): pid $pid"
done
sleep 1
for f in "$1"/*.part.mp4; do
  [ -e "$f" ] || continue
  pgrep -f "ffmpeg.*$(basename "$f")" > /dev/null || { rm -f "$f"; echo "  stale part verwijderd op $(hostname): $(basename "$f")"; }
done'

SSH="ssh -n -o BatchMode=yes -o ConnectTimeout=10"
for spec in $REMOTES; do
  IFS=: read -r host shard wd bw <<< "$spec"
  timeout 30 $SSH "$host" "bash -s -- '$wd/out'" <<< "$REMOTE_REAP" 2>&1 | sed 's/^/  /'
  timeout 30 $SSH "$host" "pgrep -f 'dg_sync.py.*--shard $shard/$N'" > /dev/null 2>&1
  rc=$?
  if [ $rc -eq 0 ]; then
    echo "  remote shard $shard/$N draait al op $host"
  elif [ $rc -eq 1 ]; then
    # launch in a subshell so the remote bash exits immediately and ssh
    # cannot hang on the detached worker's inherited descriptors
    timeout 60 $SSH "$host" "mkdir -p $wd/out && rm -f $wd/out/*.part.mp4" \
      && timeout 120 scp -q dg_sync.py "$DG/dates.json" "$DG/have.txt" "$host:$wd/" \
      && timeout 30 $SSH "$host" "cd $wd && (setsid nohup python3 dg_sync.py \
           --dates-json dates.json --have have.txt --dest out --shard $shard/$N \
           >> worker.log 2>&1 < /dev/null &); exit 0" \
      && echo "  remote shard $shard/$N gestart op $host" \
      || { echo "  remote shard $shard/$N starten op $host FAALDE"; continue; }
  else
    echo "  $host onbereikbaar (rc=$rc); shard $shard/$N overgeslagen"
    continue
  fi
  echo "$host $wd ${bw:-0}" > "/data/WILDERS/.dg_remote_${shard}.active"
done

if pgrep -f 'dg_pull\.sh' > /dev/null; then
  echo "  puller draait al"
else
  setsid nohup ./dg_pull.sh >> /data/WILDERS/dg_pull.log 2>&1 < /dev/null &
  echo "  puller gestart (pid $!)"
fi
echo "--- dg_distributed klaar"
