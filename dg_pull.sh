#!/usr/bin/env bash
# Drains remote dg shard output into /data/WILDERS/debatgemist and merges all
# shard state files into state.json (only entries whose mp4 exists locally, so
# the app never gets a window pointing at a file that is still on a remote).
# Removes a shard's .active marker once its worker stopped and its output is
# fully drained; exits when no markers remain. Log: /data/WILDERS/dg_pull.log
cd "$(dirname "$0")"
DG=/data/WILDERS/debatgemist
N=8

merge_states() {
python3 - <<'EOF'
import json
from pathlib import Path
dg = Path('/data/WILDERS/debatgemist')
main = dg / 'state.json'
state = json.loads(main.read_text()) if main.exists() else {}
n0 = len(state)
for p in dg.glob('state.*of*.json'):
    for k, v in json.loads(p.read_text()).items():
        if (dg / k).exists():
            state.setdefault(k, v)
if len(state) != n0:
    tmp = dg / 'state.json.tmp'
    tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding='utf-8')
    tmp.rename(main)
    print(f"state.json: {n0} -> {len(state)} entries", flush=True)
EOF
}

echo "=== dg_pull start $(date '+%F %T')"
while ls /data/WILDERS/.dg_remote_*.active > /dev/null 2>&1; do
  for m in /data/WILDERS/.dg_remote_*.active; do
    [ -e "$m" ] || continue
    read -r host wd bw < "$m"
    shard=$(basename "$m" .active); shard=${shard##*_}
    BWOPT=""; [ "$bw" != 0 ] && BWOPT="--bwlimit=$bw"
    rsync -a --remove-source-files $BWOPT \
      --exclude='*.part.mp4' --include='*.mp4' --exclude='*' \
      "$host:$wd/out/" "$DG/" 2>/dev/null
    rsync -a $BWOPT "$host:$wd/out/state.*of*.json" "$DG/" 2>/dev/null
    # marker weg zodra de worker gestopt is én alles gedraind is
    ssh -n -o BatchMode=yes -o ConnectTimeout=10 "$host" \
        "pgrep -f 'dg_sync.py.*--shard $shard/$N'" > /dev/null 2>&1
    rc=$?
    if [ $rc -eq 1 ]; then   # bereikbaar, worker niet actief (255 = ssh-fout)
      left=$(ssh -o BatchMode=yes "$host" "ls $wd/out/ 2>/dev/null" \
             | grep -v '\.part\.' | grep -c '\.mp4$')
      if [ "$left" -eq 0 ]; then
        echo "$(date '+%F %T') shard $shard op $host klaar en gedraind"
        rm -f "$m"
      fi
    fi
  done
  merge_states
  sleep 120
done
merge_states
echo "=== dg_pull klaar $(date '+%F %T')"
