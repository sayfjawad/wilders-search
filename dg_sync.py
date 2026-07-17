"""Download Debat Direct (voorheen Debat Gemist) video for every plenary
debate session in which the person speaks, in the lowest available quality.

For each TK transcript (tk_*.json, produced by tk_parse.py) the script:
1. collects the wallclock timestamps of the person's segments,
2. asks the Debat Direct agenda API which plenaire-zaal debates ran that day,
3. selects the debates whose [startedAt, endedAt] window contains at least
   one of those timestamps,
4. fetches the debate's vodUrl and appends ?start=&end= (the windowing the
   web player uses) to get an HLS master for exactly that session,
5. downloads lowest-bandwidth video + audio with ffmpeg (stream copy) to
   <data>/debatgemist/<date>_<slug>.mp4.

Video position 0 corresponds to the debate's startedAt wallclock, so
playback offset for a transcript segment = wallclock - startedAt.
Mapping is stored in <data>/debatgemist/state.json.

The archive reaches back to ~2010; days before that simply yield no video.
"""
import json
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from pipeline_config import load_config

API = "https://api.debatdirect.tweedekamer.nl/api"
PLENAIR_LOCATION = "plenaire-zaal"


def get_json(url: str):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def parse_dd_time(s: str) -> datetime | None:
    """'2026-04-22T10:01:13+0200' -> naive local datetime."""
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z").replace(tzinfo=None)
    except ValueError:
        return None


def person_wallclocks(transcript: dict, achternaam: str) -> list[datetime]:
    out = []
    for seg in transcript.get("segments", []):
        if achternaam.lower() in (seg.get("speaker") or "").lower() and seg.get("wallclock"):
            try:
                out.append(datetime.fromisoformat(seg["wallclock"]))
            except ValueError:
                pass
    return out


def windowed_master(vod_url: str, starts_at: str, ends_at: str) -> str:
    sep = "&" if "?" in vod_url else "?"
    return f"{vod_url}{sep}start={urllib.parse.quote(starts_at, safe='')}&end={urllib.parse.quote(ends_at, safe='')}"


def pick_streams(master_url: str) -> tuple[str, str] | None:
    """Return (lowest-bandwidth video variant URL, audio rendition URL)."""
    with urllib.request.urlopen(master_url, timeout=60) as resp:
        lines = resp.read().decode().splitlines()
    audio = video = None
    best_bw = None
    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-MEDIA") and "TYPE=AUDIO" in line and audio is None:
            m = re.search(r'URI="([^"]+)"', line)
            audio = m.group(1) if m else None
        elif line.startswith("#EXT-X-STREAM-INF"):
            m = re.search(r"BANDWIDTH=(\d+)", line)
            bw = int(m.group(1)) if m else 0
            if (best_bw is None or bw < best_bw) and i + 1 < len(lines):
                best_bw, video = bw, lines[i + 1]
    if not video:
        return None
    return (urllib.parse.urljoin(master_url, video),
            urllib.parse.urljoin(master_url, audio) if audio else "")


def variant_duration(url: str) -> float:
    with urllib.request.urlopen(url, timeout=60) as resp:
        pl = resp.read().decode()
    return sum(float(x) for x in re.findall(r"#EXTINF:([\d.]+)", pl))


def download_debate(master_url: str, dest: Path) -> bool:
    streams = pick_streams(master_url)
    if not streams:
        return False
    video_url, audio_url = streams
    # a stub playlist ("nomeeting") means no footage for this window
    if variant_duration(video_url) < 60:
        return False
    tmp = dest.with_suffix(".part.mp4")
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", video_url]
    maps = ["-map", "0:v"]
    if audio_url:
        cmd += ["-i", audio_url]
        maps += ["-map", "1:a"]
    cmd += maps + ["-c", "copy", str(tmp)]
    rc = subprocess.run(cmd).returncode
    if rc != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        return False
    tmp.rename(dest)
    return True


def main():
    cfg = load_config(sys.argv[1] if len(sys.argv) > 1 else None)
    paths = cfg["_paths"]
    achternaam = cfg["tk"]["match"]["achternaam"]
    dg_dir = paths["data"] / "debatgemist"
    dg_dir.mkdir(parents=True, exist_ok=True)
    state_path = dg_dir / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}

    # collect person wallclocks per date from TK transcripts
    dates: dict[str, list[datetime]] = {}
    for meta_path in sorted(paths["transcripts"].glob("tk_*.metadata.json")):
        base = meta_path.name[: -len(".metadata.json")]
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        d = meta.get("upload_date", "")
        if len(d) != 8:
            continue
        date_iso = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        transcript = json.loads((paths["transcripts"] / f"{base}.json").read_text(encoding="utf-8"))
        wcs = person_wallclocks(transcript, achternaam)
        if wcs:
            dates.setdefault(date_iso, []).extend(wcs)

    print(f"{len(dates)} dates with {achternaam} speech to check", flush=True)
    n_new = n_skip = n_novideo = 0
    for date_iso in sorted(dates):
        try:
            agenda = get_json(f"{API}/agenda/{date_iso}")
        except Exception as e:
            print(f"  agenda {date_iso}: {e}", file=sys.stderr)
            continue
        for deb in agenda.get("debates", []):
            if deb.get("locationId") != PLENAIR_LOCATION:
                continue
            starts, ends = deb.get("startedAt"), deb.get("endedAt")
            t0, t1 = parse_dd_time(starts), parse_dd_time(ends)
            if not t0 or not t1:
                continue
            if not any(t0 <= wc <= t1 for wc in dates[date_iso]):
                continue
            slug = deb.get("slug") or deb["id"][:8]
            dest = dg_dir / f"{date_iso}_{slug}.mp4"
            key = dest.name
            if dest.exists():
                n_skip += 1
                continue
            try:
                detail = get_json(f"{API}/debates/{deb['id']}")
                vod = ((detail.get("video") or {}).get("vodUrl")) or ""
                if not vod:
                    n_novideo += 1
                    continue
                master = windowed_master(vod, starts, ends)
                ok = download_debate(master, dest)
            except Exception as e:
                print(f"  {key}: {e}", file=sys.stderr)
                continue
            if ok:
                state[key] = {
                    "date": date_iso,
                    "debate_id": deb["id"],
                    "name": deb.get("name", ""),
                    "video_start": starts,
                    "video_end": ends,
                }
                state_path.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")
                mb = dest.stat().st_size / 1e6
                n_new += 1
                print(f"  + {key} ({mb:.0f} MB) {deb.get('name','')[:50]}", flush=True)
            else:
                n_novideo += 1
                print(f"  no footage: {date_iso} {deb.get('name','')[:50]}", flush=True)
            time.sleep(1)
    print(f"done: {n_new} downloaded, {n_skip} already present, {n_novideo} without footage")


if __name__ == "__main__":
    main()
