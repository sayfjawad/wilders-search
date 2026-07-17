"""Transcribe downloaded YouTube audio with whisperx and write transcripts in
the abo-ali format to <data>/transcripts/.

For every <data>/youtube/<base>.opus without a transcripts/yt_<base>.json:
  - runs the whisperx CLI (json output) with settings from the config
  - converts segments to {speaker_id, speaker, start, end, text}
  - writes yt_<base>.json + yt_<base>.metadata.json (url, title, upload_date
    from the .info.json; transcript_source: "asr")

Pass --diarize to enable speaker diarization (needs HF_TOKEN in the env).
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from pipeline_config import load_config, ensure_dirs


def run_whisperx(audio: Path, wx: dict, diarize: bool, tmp_dir: str) -> dict | None:
    cmd = [
        "whisperx", str(audio),
        "--model", wx.get("model", "large-v3"),
        "--language", wx.get("language", "nl"),
        "--device", wx.get("device", "cuda"),
        "--batch_size", str(wx.get("batch_size", 16)),
        "--output_dir", tmp_dir,
        "--output_format", "json",
    ]
    if diarize:
        cmd += ["--diarize"]
        if os.environ.get("HF_TOKEN"):
            cmd += ["--hf_token", os.environ["HF_TOKEN"]]
    rc = subprocess.run(cmd).returncode
    if rc != 0:
        print(f"  whisperx exited {rc} for {audio.name}", file=sys.stderr)
        return None
    out = Path(tmp_dir) / f"{audio.stem}.json"
    if not out.exists():
        return None
    return json.loads(out.read_text(encoding="utf-8"))


def convert(wx_result: dict, info: dict) -> tuple[dict, dict]:
    segments = []
    for seg in wx_result.get("segments", []):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        speaker = seg.get("speaker") or ""
        segments.append(
            {
                "speaker_id": speaker,
                "speaker": speaker,
                "start": round(float(seg.get("start", 0)), 2),
                "end": round(float(seg.get("end", 0)), 2),
                "text": text,
            }
        )
    title = info.get("title", "")
    duration = info.get("duration") or (segments[-1]["end"] if segments else 0)
    transcript = {"title": title, "duration_seconds": duration, "segments": segments}
    metadata = {
        "id": info.get("id", ""),
        "title": title,
        "url": info.get("webpage_url", ""),
        "upload_date": info.get("upload_date", ""),
        "duration_seconds": duration,
        "source": f"youtube:{info.get('channel', '')}",
        "transcript_source": "asr",
    }
    return transcript, metadata


def main():
    diarize = "--diarize" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    cfg = load_config(args[0] if args else None)
    ensure_dirs(cfg)
    paths = cfg["_paths"]
    wx = cfg.get("whisperx", {})

    todo = []
    for audio in sorted(paths["youtube"].glob("*.opus")):
        base = f"yt_{audio.stem}"
        if not (paths["transcripts"] / f"{base}.json").exists():
            todo.append((audio, base))
    print(f"{len(todo)} audio files to transcribe")

    for i, (audio, base) in enumerate(todo, 1):
        info_path = audio.with_suffix(".info.json")
        info = json.loads(info_path.read_text(encoding="utf-8")) if info_path.exists() else {}
        print(f"[{i}/{len(todo)}] {audio.name}", flush=True)
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = run_whisperx(audio, wx, diarize, tmp_dir)
        if result is None:
            continue
        transcript, metadata = convert(result, info)
        (paths["transcripts"] / f"{base}.json").write_text(
            json.dumps(transcript, ensure_ascii=False), encoding="utf-8"
        )
        (paths["transcripts"] / f"{base}.metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    main()
