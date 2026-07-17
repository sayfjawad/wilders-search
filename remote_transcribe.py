"""Self-contained transcription worker for remote machines (no repo deps).

Usage: python3 remote_transcribe.py <audio_dir> <out_dir> [device] [K/N]

The optional K/N shard splits the audio_dir files (index % N == K) so several
GPU workers can run over the same directory in parallel, each pinned to its
own GPU via CUDA_VISIBLE_DEVICES.

Transcribes every *.opus in audio_dir to out_dir/yt_<base>.json +
yt_<base>.metadata.json in the abo-ali format used by wilders-search.
Backend: faster-whisper if importable, else the whisperx CLI, else
transformers' whisper pipeline. Skips files whose output already exists,
so it is safe to rerun after a crash.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MODEL = "large-v3"
LANG = "nl"


def convert(segments, info, duration):
    segs = []
    for s in segments:
        text = (s.get("text") or "").strip()
        if text:
            segs.append({
                "speaker_id": "", "speaker": "",
                "start": round(float(s.get("start", 0)), 2),
                "end": round(float(s.get("end", 0)), 2),
                "text": text,
            })
    title = info.get("title", "")
    dur = info.get("duration") or duration or (segs[-1]["end"] if segs else 0)
    transcript = {"title": title, "duration_seconds": dur, "segments": segs}
    metadata = {
        "id": info.get("id", ""), "title": title,
        "url": info.get("webpage_url", ""),
        "upload_date": info.get("upload_date", ""),
        "duration_seconds": dur,
        "source": f"youtube:{info.get('channel', '')}",
        "transcript_source": "asr",
    }
    return transcript, metadata


def backend_faster_whisper(device):
    from faster_whisper import WhisperModel
    model = WhisperModel(MODEL, device=device, compute_type="float16" if device == "cuda" else "int8")

    def run(audio: Path):
        segments, info = model.transcribe(str(audio), language=LANG, beam_size=5, vad_filter=True)
        return [{"start": s.start, "end": s.end, "text": s.text} for s in segments], info.duration
    return run


def backend_whisperx_cli(device):
    def run(audio: Path):
        with tempfile.TemporaryDirectory() as tmp:
            rc = subprocess.run(
                ["whisperx", str(audio), "--model", MODEL, "--language", LANG,
                 "--device", device, "--batch_size", "16",
                 "--output_dir", tmp, "--output_format", "json"]).returncode
            out = Path(tmp) / f"{audio.stem}.json"
            if rc != 0 or not out.exists():
                raise RuntimeError(f"whisperx rc={rc}")
            data = json.loads(out.read_text(encoding="utf-8"))
        return data.get("segments", []), 0
    return run


def backend_transformers(device):
    import torch
    from transformers import pipeline
    pipe = pipeline(
        "automatic-speech-recognition", model=f"openai/whisper-{MODEL}",
        torch_dtype=torch.float16 if device == "cuda" else torch.float32,
        device=0 if device == "cuda" else -1, return_timestamps=True,
        chunk_length_s=30, batch_size=8,
    )

    def run(audio: Path):
        out = pipe(str(audio), generate_kwargs={"language": LANG})
        segs = [{"start": c["timestamp"][0] or 0, "end": c["timestamp"][1] or 0, "text": c["text"]}
                for c in out.get("chunks", [])]
        return segs, 0
    return run


def ct2_cuda_ok() -> bool:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def pick_backend(device):
    # faster-whisper and the whisperx CLI both run on ctranslate2; on cuda
    # they only work when the ct2 build actually has CUDA (not all aarch64
    # wheels do), otherwise fall through to plain torch/transformers.
    ct2_ok = device != "cuda" or ct2_cuda_ok()
    if ct2_ok:
        try:
            import faster_whisper  # noqa: F401
            print("backend: faster-whisper")
            return backend_faster_whisper(device)
        except Exception as e:
            print(f"faster-whisper unavailable ({e})")
        if shutil.which("whisperx"):
            print("backend: whisperx CLI")
            return backend_whisperx_cli(device)
    print("backend: transformers")
    return backend_transformers(device)


def main():
    audio_dir, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    device = sys.argv[3] if len(sys.argv) > 3 else "cuda"
    shard_k, shard_n = 0, 1
    if len(sys.argv) > 4 and "/" in sys.argv[4]:
        shard_k, shard_n = (int(x) for x in sys.argv[4].split("/"))
    out_dir.mkdir(parents=True, exist_ok=True)
    run = pick_backend(device)

    todo = [a for i, a in enumerate(sorted(audio_dir.glob("*.opus")))
            if i % shard_n == shard_k and not (out_dir / f"yt_{a.stem}.json").exists()]
    print(f"{len(todo)} files to transcribe on {device} (shard {shard_k}/{shard_n})")
    for i, audio in enumerate(todo, 1):
        print(f"[{i}/{len(todo)}] {audio.name}", flush=True)
        info_p = audio.with_suffix(".info.json")
        info = json.loads(info_p.read_text(encoding="utf-8")) if info_p.exists() else {}
        try:
            segments, duration = run(audio)
        except Exception as e:
            print(f"  FAILED {audio.name}: {e}", flush=True)
            continue
        transcript, metadata = convert(segments, info, duration)
        (out_dir / f"yt_{audio.stem}.json").write_text(
            json.dumps(transcript, ensure_ascii=False), encoding="utf-8")
        (out_dir / f"yt_{audio.stem}.metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print("done")


if __name__ == "__main__":
    main()
