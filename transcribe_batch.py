"""Transcribe downloaded YouTube audio with whisperx and write transcripts in
the abo-ali format to <data>/transcripts/.

For every <data>/youtube/<base>.opus without a transcripts/yt_<base>.json:
  - runs whisperx (CLI for plain ASR; Python API for --diarize, see below)
  - converts segments to {speaker_id, speaker, start, end, text}
  - writes yt_<base>.json + yt_<base>.metadata.json (url, title, upload_date
    from the .info.json; transcript_source: "asr")

Pass --diarize to enable speaker diarization. This uses the pyannote Python
API directly (whisperx.load_audio + whisperx model + pyannote.audio.Pipeline
+ assign_word_speakers), the exact pattern scrib-r's proven
batch/transcribe_abo_ali.py already runs at scale -- NOT whisperx's built-in
`--diarize` CLI flag, whose internal audio-loading path pulls in torchcodec,
which crashed on this machine with library/CUDA-ABI mismatches (undefined
symbols, missing libnvrtc.so.13) and silently produced empty transcripts for
~16 files. Run this mode with scrib-r's own venv, which has the compatible
versions already proven to work:
    /data/git/scrib-r/venv/bin/python3 transcribe_batch.py --diarize ...

Pass --shard K/N to process only files with index % N == K (parallel workers
on multiple GPUs/machines); a final run without --shard sweeps leftovers.
Pass --force to redo files that already have a transcript (e.g. adding
diarization to a batch that was first transcribed without it).
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from pipeline_config import load_config, ensure_dirs

DIARIZATION_MODEL = os.environ.get("DIARIZATION_MODEL", "pyannote/speaker-diarization-3.1")


def run_whisperx_cli(audio: Path, wx: dict, tmp_dir: str) -> dict | None:
    cmd = [
        "whisperx", str(audio),
        "--model", wx.get("model", "large-v3"),
        "--language", wx.get("language", "nl"),
        "--device", wx.get("device", "cuda"),
        "--batch_size", str(wx.get("batch_size", 16)),
        "--output_dir", tmp_dir,
        "--output_format", "json",
    ]
    rc = subprocess.run(cmd).returncode
    if rc != 0:
        print(f"  whisperx exited {rc} for {audio.name}", file=sys.stderr)
        return None
    out = Path(tmp_dir) / f"{audio.stem}.json"
    if not out.exists():
        return None
    return json.loads(out.read_text(encoding="utf-8"))


class DiarizedTranscriber:
    """Loads the whisperx model + pyannote diarization pipeline once and
    reuses them for every file -- same shape as scrib-r's DiarizedTranscriber
    in batch/transcribe_abo_ali.py, adapted to this repo's segment format."""

    def __init__(self, wx: dict):
        import torch
        import whisperx
        from pyannote.audio import Pipeline

        self._torch = torch
        self._whisperx = whisperx
        self.batch_size = wx.get("batch_size", 16)
        self.language = wx.get("language", "nl")
        self.torch_device = torch.device("cuda:0")  # CUDA_VISIBLE_DEVICES pins the physical GPU

        self.whisper_model = whisperx.load_model(
            wx.get("model", "large-v3"), device="cuda", device_index=0, compute_type="float16",
        )
        hf_token = os.environ.get("HF_TOKEN") or None
        try:
            self.diarization_pipeline = Pipeline.from_pretrained(DIARIZATION_MODEL, token=hf_token)
        except TypeError:
            self.diarization_pipeline = Pipeline.from_pretrained(DIARIZATION_MODEL, use_auth_token=hf_token)
        self.diarization_pipeline.to(self.torch_device)

    def transcribe(self, audio_path: Path) -> list[dict]:
        audio = self._whisperx.load_audio(str(audio_path))
        result = self.whisper_model.transcribe(audio, batch_size=self.batch_size, language=self.language)

        waveform = self._torch.from_numpy(audio).unsqueeze(0)  # [1, N], already 16kHz mono
        diarization = self.diarization_pipeline({"waveform": waveform, "sample_rate": 16000})
        annotation = getattr(diarization, "speaker_diarization", diarization)

        import pandas as pd
        diarize_df = pd.DataFrame([
            {"start": turn.start, "end": turn.end, "speaker": speaker}
            for turn, _, speaker in annotation.itertracks(yield_label=True)
        ])
        result = self._whisperx.assign_word_speakers(diarize_df, result)
        return result["segments"]


def convert(segments: list[dict], info: dict, person: str = "") -> tuple[dict, dict]:
    raw = []
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        raw.append((seg.get("speaker") or "", seg, text))

    # Diarization clusters voices (SPEAKER_00, ...) but doesn't know who they
    # are. For single-uploader channel content the channel's own person talks
    # the most by a wide margin, so label the majority-airtime voice with
    # their name and leave every other voice as an anonymous SPEAKER_NN --
    # honest about not knowing who they are, but no longer invisible to the
    # "only statements by <person>" filter, which used to exclude ALL
    # non-diarized ASR content (every segment had speaker == "").
    airtime: dict[str, float] = {}
    for spk, seg, _ in raw:
        if spk:
            airtime[spk] = airtime.get(spk, 0.0) + (float(seg.get("end", 0)) - float(seg.get("start", 0)))
    majority = max(airtime, key=airtime.get) if airtime else None

    segments_out = []
    for spk, seg, text in raw:
        label = person if (person and spk == majority) else spk
        segments_out.append(
            {
                "speaker_id": spk,
                "speaker": label,
                "start": round(float(seg.get("start", 0)), 2),
                "end": round(float(seg.get("end", 0)), 2),
                "text": text,
            }
        )
    title = info.get("title", "")
    duration = info.get("duration") or (segments_out[-1]["end"] if segments_out else 0)
    transcript = {"title": title, "duration_seconds": duration, "segments": segments_out}
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
    force = "--force" in sys.argv
    shard_k, shard_n = 0, 1
    argv = sys.argv[1:]
    if "--shard" in argv:
        i = argv.index("--shard")
        shard_k, shard_n = (int(x) for x in argv[i + 1].split("/"))
        del argv[i:i + 2]
    args = [a for a in argv if not a.startswith("--")]
    cfg = load_config(args[0] if args else None)
    ensure_dirs(cfg)
    paths = cfg["_paths"]
    wx = cfg.get("whisperx", {})
    person = cfg.get("person", "")

    todo = []
    for idx, audio in enumerate(sorted(paths["youtube"].glob("*.opus"))):
        if idx % shard_n != shard_k:
            continue
        base = f"yt_{audio.stem}"
        if force or not (paths["transcripts"] / f"{base}.json").exists():
            todo.append((audio, base))
    print(f"{len(todo)} audio files to transcribe (shard {shard_k}/{shard_n})")

    transcriber = DiarizedTranscriber(wx) if diarize else None

    for i, (audio, base) in enumerate(todo, 1):
        info_path = audio.with_suffix(".info.json")
        info = json.loads(info_path.read_text(encoding="utf-8")) if info_path.exists() else {}
        print(f"[{i}/{len(todo)}] {audio.name}", flush=True)
        try:
            if transcriber is not None:
                segments = transcriber.transcribe(audio)
            else:
                with tempfile.TemporaryDirectory() as tmp_dir:
                    result = run_whisperx_cli(audio, wx, tmp_dir)
                segments = result["segments"] if result else None
        except Exception as e:
            print(f"  failed: {audio.name}: {e}", file=sys.stderr)
            continue
        if not segments:
            continue
        transcript, metadata = convert(segments, info, person)
        (paths["transcripts"] / f"{base}.json").write_text(
            json.dumps(transcript, ensure_ascii=False), encoding="utf-8"
        )
        (paths["transcripts"] / f"{base}.metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    main()
