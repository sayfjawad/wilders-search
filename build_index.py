"""Build the search index from <data>/transcripts/.

Same design as abo-ali-search: reads every <base>.json + <base>.metadata.json,
merges consecutive same-speaker segments into ~700-char retrieval chunks,
embeds them with BGE-M3 on GPU, and writes:
  <data>/index/index.sqlite   - videos + chunks tables
  <data>/index/embeddings.npy - fp16 (n_chunks, 1024), row i == chunk id i

Extra columns vs abo-ali: videos.source ("tk_verslag" / "youtube:<channel>")
and videos.transcript_source ("official" / "asr").
"""
import json
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

from embedder import Embedder, DIM
from pipeline_config import load_config, ensure_dirs

MERGE_TARGET_CHARS = 700


def iter_videos(transcripts_dir: Path):
    for meta_path in sorted(transcripts_dir.glob("*.metadata.json")):
        base = meta_path.name[: -len(".metadata.json")]
        transcript_path = transcripts_dir / f"{base}.json"
        if not transcript_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"skip {base}: {e}", file=sys.stderr)
            continue
        yield base, meta, transcript


def merge_segments(segments):
    """Greedy merge of consecutive same-speaker segments into chunks."""
    chunks = []
    cur = None
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        if (
            cur is not None
            and seg.get("speaker_id") == cur["speaker_id"]
            and len(cur["text"]) + len(text) <= MERGE_TARGET_CHARS
        ):
            cur["text"] += " " + text
            cur["end"] = seg["end"]
        else:
            if cur:
                chunks.append(cur)
            cur = {
                "speaker_id": seg.get("speaker_id") or "",
                "speaker": seg.get("speaker") or "",
                "start": seg["start"],
                "end": seg["end"],
                "text": text,
                "wallclock": seg.get("wallclock") or "",
            }
    if cur:
        chunks.append(cur)
    return chunks


def find_media_file(youtube_dir: Path, base: str) -> str:
    if base.startswith("yt_"):
        for ext in (".opus", ".m4a", ".webm"):
            if (youtube_dir / f"{base[3:]}{ext}").exists():
                return f"{base[3:]}{ext}"
    return ""


def main():
    cfg = load_config(sys.argv[1] if len(sys.argv) > 1 else None)
    ensure_dirs(cfg)
    paths = cfg["_paths"]

    db_path = paths["index"] / "index.sqlite"
    if db_path.exists():
        db_path.unlink()
    db = sqlite3.connect(db_path)
    db.executescript("""
        CREATE TABLE videos (
            video TEXT PRIMARY KEY, yt_id TEXT, title TEXT, url TEXT,
            upload_date TEXT, duration REAL, media_file TEXT,
            source TEXT, transcript_source TEXT
        );
        CREATE TABLE chunks (
            id INTEGER PRIMARY KEY, video TEXT, speaker TEXT,
            start REAL, end REAL, text TEXT, wallclock TEXT
        );
    """)

    print("pass 1: parsing transcripts...", flush=True)
    all_texts = []
    chunk_id = 0
    n_videos = 0
    for base, meta, transcript in iter_videos(paths["transcripts"]):
        chunks = merge_segments(transcript.get("segments") or [])
        if not chunks:
            continue
        db.execute(
            "INSERT INTO videos VALUES (?,?,?,?,?,?,?,?,?)",
            (
                base,
                meta.get("id") or "",
                meta.get("title") or transcript.get("title") or base,
                meta.get("url") or "",
                meta.get("upload_date") or "",
                meta.get("duration_seconds") or transcript.get("duration_seconds") or 0,
                find_media_file(paths["youtube"], base),
                meta.get("source") or "",
                meta.get("transcript_source") or "",
            ),
        )
        for c in chunks:
            db.execute(
                "INSERT INTO chunks VALUES (?,?,?,?,?,?,?)",
                (chunk_id, base, c["speaker"], c["start"], c["end"], c["text"], c["wallclock"]),
            )
            all_texts.append(c["text"])
            chunk_id += 1
        n_videos += 1
        if n_videos % 250 == 0:
            print(f"  {n_videos} videos, {chunk_id} chunks", flush=True)
    db.commit()
    print(f"parsed {n_videos} videos -> {chunk_id} chunks", flush=True)

    print("pass 2: embedding on GPU...", flush=True)
    embedder = Embedder(device="cuda:0")
    tok = embedder.tokenizer

    lengths = [len(t) for t in all_texts]
    order = np.argsort(lengths)
    emb = np.zeros((len(all_texts), DIM), dtype=np.float16)

    TOKEN_BUDGET = 16384
    batch_idx: list[int] = []
    batch_max_tok = 0
    done = 0
    t0 = time.time()

    def flush():
        nonlocal batch_idx, batch_max_tok, done
        if not batch_idx:
            return
        texts = [all_texts[i] for i in batch_idx]
        vecs = embedder.encode(texts).numpy().astype(np.float16)
        emb[batch_idx] = vecs
        done += len(batch_idx)
        if done % 5000 < len(batch_idx):
            rate = done / (time.time() - t0)
            eta = (len(all_texts) - done) / max(rate, 1)
            print(f"  {done}/{len(all_texts)}  {rate:.0f} chunks/s  eta {eta/60:.1f} min", flush=True)
        batch_idx = []
        batch_max_tok = 0

    for i in order:
        ntok = min(len(tok.encode(all_texts[i], add_special_tokens=True)), embedder.max_length)
        new_max = max(batch_max_tok, ntok)
        if batch_idx and new_max * (len(batch_idx) + 1) > TOKEN_BUDGET:
            flush()
            new_max = ntok
        batch_idx.append(int(i))
        batch_max_tok = new_max
    flush()

    np.save(paths["index"] / "embeddings.npy", emb)
    db.close()
    print(f"done in {(time.time()-t0)/60:.1f} min -> {paths['index']}", flush=True)


if __name__ == "__main__":
    main()
