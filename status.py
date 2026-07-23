"""Snapshot pipeline progress to /data/WILDERS/progress.json (+ stdout).

Run periodically (cron) and after milestones. resume.sh uses this to decide
nothing — every sync script is itself idempotent — but the snapshot tells a
human (or a fresh Claude session) exactly where the pipeline stands after a
crash or power loss.
"""
import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from pipeline_config import load_config


def count(globber) -> int:
    return sum(1 for _ in globber)


def running(pattern: str) -> bool:
    return subprocess.run(["pgrep", "-f", pattern], capture_output=True).returncode == 0


def main():
    cfg = load_config(sys.argv[1] if len(sys.argv) > 1 else None)
    p = cfg["_paths"]
    data = p["data"]
    dg = p["debatgemist"]

    # tk/ob xml + transcript counts reflect the whole SHARED pool (raw
    # material + parsed multi-speaker debates), not just what mentions this
    # person -- with only one tracked person today the two are identical;
    # once a second person's config shares this pool they will diverge, and
    # build_index.py is what actually filters to "relevant to this person".
    snap = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "tk": {
            "xml": count(p["tk_xml"].glob("*.xml")),
            "transcripts": count(p["shared_transcripts"].glob("tk_*.metadata.json")),
            "sync_running": running(r"python3 (tk_sync|tk_parse)\.py"),
        },
        "ob": {
            "xml": count(p["ob_xml"].glob("*.xml")),
            "transcripts": count(p["shared_transcripts"].glob("ob_*.metadata.json")),
            "sync_running": running(r"python3 (ob_sync|ob_parse)\.py"),
        },
        "youtube": {
            "audio": count(p["youtube"].glob("*.opus")),
            "transcripts": count(p["transcripts"].glob("yt_*.metadata.json")),
            "sync_running": running(r"python3 yt_sync\.py"),
            "transcribe_running": running(r"python3 transcribe_batch\.py"),
        },
        "video": {
            "files": count(dg.glob("*.mp4")) - count(dg.glob("*.part.mp4")),
            "bytes": sum(f.stat().st_size for f in dg.glob("*.mp4")) if dg.exists() else 0,
            "sync_running": running(r"python3 dg_sync\.py"),
        },
        "index": {},
        "app_running": running(r"uvicorn app:app"),
        "watchers": {
            m: running(rf"milestone_watch\.sh {m}") for m in ("text", "youtube", "video")
        },
        "disk_free_gb": round(shutil.disk_usage(data).free / 1e9, 1),
    }
    db_path = p["index"] / "index.sqlite"
    if db_path.exists():
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        snap["index"] = {
            "videos": db.execute("SELECT COUNT(*) FROM videos").fetchone()[0],
            "chunks": db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
            "built": datetime.fromtimestamp(db_path.stat().st_mtime).isoformat(timespec="seconds"),
        }
        db.close()

    out = data / "progress.json"
    history = data / "progress.log"
    out.write_text(json.dumps(snap, indent=1), encoding="utf-8")
    with history.open("a", encoding="utf-8") as f:
        f.write(json.dumps(snap) + "\n")
    print(json.dumps(snap, indent=1))


if __name__ == "__main__":
    main()
