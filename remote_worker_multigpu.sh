#!/usr/bin/env bash
# Run a transcription shard on a remote multi-GPU host, one container per GPU.
# Usage: ./remote_worker_multigpu.sh <host> <shard K/N> <ngpu> <image> [remote_workdir]
# Ships this shard's undone audio, launches <ngpu> containers (each pinned to
# one GPU, each taking a 1/ngpu sub-shard), waits, rsyncs transcripts back,
# and clears the .remote_<host>.active marker the transcribe watcher waits on.
# Resumable: skips audio whose transcript already exists.
set -u
HOST=$1; SHARD=$2; NGPU=$3; IMAGE=$4; WORK=${5:-/data/wilders-worker}
MARKER=/data/WILDERS/.remote_${HOST}.active
LOG=/data/WILDERS/milestones.log
cd "$(dirname "$0")"
log() { echo "$(date '+%F %T') [remote-mg:$HOST] $*" >> "$LOG"; }

touch "$MARKER"
trap 'rm -f "$MARKER"' EXIT
log "start shard $SHARD op $NGPU GPU's ($IMAGE)"

ssh -o BatchMode=yes "$HOST" "mkdir -p $WORK/audio $WORK/out $WORK/hf" || { log "ssh/mkdir faalt"; exit 1; }

python3 - "$SHARD" <<'EOF' > /tmp/mg_files.txt
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
N=$(grep -c opus /tmp/mg_files.txt || true)
log "$N audiobestanden naar $HOST"
[ "$N" -eq 0 ] && { log "niets te doen"; exit 0; }

rsync -a --files-from=/tmp/mg_files.txt /data/WILDERS/youtube/ "$HOST":"$WORK"/audio/ || { log "rsync heen faalt"; exit 1; }
scp -q remote_transcribe.py "$HOST":"$WORK"/ || exit 1

# one detached container per GPU, each a 1/NGPU sub-shard of the shipped set
REMOTE=$(cat <<EOF
set -e
cd $WORK
for i in \$(seq 0 $((NGPU-1))); do
  docker run -d --rm --gpus "device=\$i" \
    -e HF_HUB_OFFLINE=0 -e HF_HOME=/work/hf \
    -v $WORK:/work --name wilders_gpu\$i --entrypoint python3 \
    $IMAGE /work/remote_transcribe.py /work/audio /work/out cuda \$i/$NGPU \
    > /dev/null
done
echo "containers gestart: \$(docker ps --filter name=wilders_gpu -q | wc -l)"
# wait until all wilders_gpu containers exit
while [ \$(docker ps --filter name=wilders_gpu -q | wc -l) -gt 0 ]; do sleep 30; done
echo "alle containers klaar"
EOF
)
ssh -o BatchMode=yes "$HOST" "$REMOTE" 2>&1 | while read -r l; do log "$l"; done

rsync -a "$HOST":"$WORK"/out/ /data/WILDERS/transcripts/ \
  && log "resultaten terug: $(ssh $HOST "ls $WORK/out | grep -c metadata" 2>/dev/null) transcripten"
