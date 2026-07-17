"""Load a per-person pipeline config (config/<slug>.json)."""
import json
import os
from pathlib import Path

CONFIG_DIR = Path(__file__).parent / "config"


def load_config(slug: str | None = None) -> dict:
    slug = slug or os.environ.get("PERSON", "wilders")
    cfg = json.loads((CONFIG_DIR / f"{slug}.json").read_text(encoding="utf-8"))
    data_dir = Path(cfg["data_dir"])
    cfg["_paths"] = {
        "data": data_dir,
        "tk_xml": data_dir / "tk" / "xml",
        "tk_state": data_dir / "tk" / "state.json",
        "youtube": data_dir / "youtube",
        "transcripts": data_dir / "transcripts",
        "index": data_dir / "index",
    }
    return cfg


def ensure_dirs(cfg: dict) -> None:
    for key in ("tk_xml", "youtube", "transcripts", "index"):
        cfg["_paths"][key].mkdir(parents=True, exist_ok=True)
