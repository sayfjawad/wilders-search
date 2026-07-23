"""Human-readable progress report + ETA for the video pipeline.

Computes the download rate from the progress.log history (written every 15
minutes by status.py via cron), estimates the total archive size from the
average bytes per covered debate day, and projects a completion date.

Writes /data/WILDERS/report.txt every run; with --mail it also sends the
report via notify.py. Cron: hourly report, daily morning mail.
"""
import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from pipeline_config import load_config

_PATHS = load_config()["_paths"]
DATA = _PATHS["data"]
DG = _PATHS["debatgemist"]  # shared video pool
RATE_WINDOW_H = 24


def history():
    out = []
    for line in (DATA / "progress.log").read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def fmt_gb(b) -> str:
    return f"{b / 1e9:.1f} GB"


def main():
    now = datetime.now()
    hist = history()
    latest = hist[-1]
    cur_bytes = latest["video"]["bytes"]
    cur_files = latest["video"]["files"]

    # rate over the last RATE_WINDOW_H hours (or as far back as history goes)
    cutoff = now - timedelta(hours=RATE_WINDOW_H)
    past = next((h for h in hist if datetime.fromisoformat(h["timestamp"]) >= cutoff), hist[0])
    dt_h = (datetime.fromisoformat(latest["timestamp"])
            - datetime.fromisoformat(past["timestamp"])).total_seconds() / 3600
    rate = (cur_bytes - past["video"]["bytes"]) / dt_h if dt_h > 0.5 else 0.0

    # coverage: distinct debate days with >=1 file vs the work manifest
    total_dates = len(json.loads((DG / "dates.json").read_text()))
    covered = len({f.name[:10] for f in DG.glob("*.mp4") if ".part." not in f.name})

    # prognosis from average bytes per covered day
    if covered and rate > 0:
        est_total = cur_bytes / covered * total_dates
        remaining = max(est_total - cur_bytes, 0)
        eta_h = remaining / rate
        eta_txt = (f"~{fmt_gb(remaining)} te gaan bij {rate / 1e9:.2f} GB/u "
                   f"-> klaar rond {(now + timedelta(hours=eta_h)):%a %d %b %H:%M} "
                   f"(~{eta_h / 24:.1f} dagen), geschat totaal {fmt_gb(est_total)}")
    else:
        eta_txt = "nog geen betrouwbare snelheid te bepalen"

    shards_local = int(subprocess.run(
        ["pgrep", "-c", "-f", r"dg_sync\.py --shard"],
        capture_output=True, text=True).stdout.strip() or 0)
    markers = sorted(p.name.split("_")[-1].split(".")[0]
                     for p in Path("/data/WILDERS").glob(".dg_remote_*.active"))
    puller = subprocess.run(["pgrep", "-f", r"dg_pull\.sh"],
                            capture_output=True).returncode == 0
    disk_free = shutil.disk_usage("/data").free

    lines = [
        f"wilders-search voortgang  {now:%F %H:%M}",
        "",
        f"Video-archief : {cur_files} sessies, {fmt_gb(cur_bytes)}; "
        f"{covered}/{total_dates} debatdagen gedekt ({100 * covered / total_dates:.0f}%)",
        f"Snelheid      : {rate / 1e9:.2f} GB/u (laatste {dt_h:.0f} u)",
        f"Prognose      : {eta_txt}",
        f"Workers       : {shards_local} lokale shards, remote shards actief: "
        f"{', '.join(markers) if markers else 'geen'}, puller {'draait' if puller else 'UIT'}",
        f"Schijf /data  : {fmt_gb(disk_free)} vrij",
        f"Index         : {latest['index']['videos']} bronnen / {latest['index']['chunks']} chunks "
        f"(gebouwd {latest['index']['built']})",
        "",
        "Hervatten na onderbreking: alles herstelt automatisch (cron @reboot -> "
        "resume.sh -> dg_distributed.sh); handmatig: ./resume.sh",
    ]
    report = "\n".join(lines)
    (DATA / "report.txt").write_text(report + "\n", encoding="utf-8")
    print(report)

    if "--mail" in sys.argv:
        from notify import send_mail
        send_mail(f"[wilders-search] dagrapport: {covered}/{total_dates} dagen, "
                  f"{fmt_gb(cur_bytes)}", report)


if __name__ == "__main__":
    main()
