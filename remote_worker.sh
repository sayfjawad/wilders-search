#!/usr/bin/env bash
# Run a transcription shard on a remote machine (over Tailscale SSH).
# Usage: ./remote_worker.sh <host> <shard K/N> <device> [extra whisper args]
# Flow: rsync audio slice + scripts to host -> run remote transcriber ->
# rsync transcripts back -> clear the .remote_<host>.active marker that the
# transcribe milestone watcher waits on. Safe to rerun (skips done files).
set -u
HOST=$1; SHARD=$2; DEVICE=${3:-cuda}; RUNNER=${4:-native}  # native | docker:<image>
MARKER=/data/WILDERS/.remote_${HOST}.active
LOG=/data/WILDERS/milestones.log
cd "$(dirname "$0")"
log() { echo "$(date '+%F %T') [remote:$HOST] $*" >> "$LOG"; }

touch "$MARKER"
trap 'rm -f "$MARKER"' EXIT
log "start shard $SHARD"

ssh -o BatchMode=yes "$HOST" "mkdir -p ~/wilders-worker/audio ~/wilders-worker/out" || { log "ssh faalt"; exit 1; }

# ship only this shard's undone audio (index-based, matches transcribe_batch)
python3 - "$SHARD" <<'EOF' > /tmp/shard_files.txt
import sys
from pathlib import Path
k, n = (int(x) for x in sys.argv[1].split("/"))
audio_dir = Path("/data/WILDERS/youtube")
done_dir = Path("/data/WILDERS/transcripts")
for idx, f in enumerate(sorted(audio_dir.glob("*.opus"))):
    if idx % n == k and not (done_dir / f"yt_{f.stem}.json").exists():
        print(f.name)
        info = f.with_suffix(".info.json")
        if info.exists():
            print(info.name)
EOF
N=$(grep -c opus /tmp/shard_files.txt || true)
log "$N audiobestanden naar $HOST"
[ "$N" -eq 0 ] && { log "niets te doen"; exit 0; }

rsync -a --files-from=/tmp/shard_files.txt /data/WILDERS/youtube/ "$HOST":wilders-worker/audio/ || { log "rsync heen faalt"; exit 1; }
scp -q remote_transcribe.py "$HOST":wilders-worker/ || exit 1

if [[ "$RUNNER" == docker:* ]]; then
  IMAGE=${RUNNER#docker:}
  # the scrib-r image runs HF offline; enable download + persist model cache
  RUN_CMD="docker run --rm --gpus all -e HF_HUB_OFFLINE=0 -e HF_HOME=/work/hf -v \$HOME/wilders-worker:/work --entrypoint python3 $IMAGE /work/remote_transcribe.py /work/audio /work/out $DEVICE"
else
  RUN_CMD="cd wilders-worker && python3 remote_transcribe.py audio out $DEVICE"
fi
ssh -o BatchMode=yes "$HOST" "$RUN_CMD > wilders-worker/worker.log 2>&1; tail -2 wilders-worker/worker.log" \
  && log "remote run klaar op $HOST" || log "remote run eindigde met fout op $HOST (zie worker.log daar)"

rsync -a "$HOST":wilders-worker/out/ /data/WILDERS/transcripts/ && log "resultaten terug van $HOST: $(ssh $HOST 'ls wilders-worker/out | grep -c metadata' 2>/dev/null) transcripten"
