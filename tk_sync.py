"""Sync Tweede Kamer verslagen via the OData v4 API (no key needed).

Incrementally fetches Verslag entities changed since the last run (tracked via
GewijzigdOp in <data>/tk/state.json), downloads their vlos XML to
<data>/tk/xml/<verslag_id>.xml, and records status/soort/vergadering so
tk_parse.py can pick the best verslag per vergadering.
"""
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from pipeline_config import load_config, ensure_dirs

def get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=120) as resp:
        dest.write_bytes(resp.read())


def main():
    cfg = load_config(sys.argv[1] if len(sys.argv) > 1 else None)
    ensure_dirs(cfg)
    paths = cfg["_paths"]
    base = cfg["tk"]["odata_base"]

    state = {"last_gewijzigd_op": "", "verslagen": {}}
    if paths["tk_state"].exists():
        state = json.loads(paths["tk_state"].read_text())

    since = state["last_gewijzigd_op"] or f"{cfg['tk'].get('since', '2023-01-01')}T00:00:00Z"
    filt = f"Verwijderd eq false and GewijzigdOp gt {since}"
    # No $top: in OData that caps the TOTAL result count, which suppresses
    # @odata.nextLink. The server paginates at its own page size (250).
    query = urllib.parse.urlencode(
        {
            "$filter": filt,
            "$expand": "Vergadering($select=Id,Titel,Datum,Soort)",
            "$orderby": "GewijzigdOp",
        }
    )
    url = f"{base}/Verslag?{query}"

    n_seen = n_downloaded = 0
    while url:
        page = get_json(url)
        for v in page.get("value", []):
            vid = v["Id"]
            verg = v.get("Vergadering") or {}
            info = {
                "gewijzigd_op": v.get("GewijzigdOp", ""),
                "status": v.get("Status", ""),
                "soort": v.get("Soort", ""),
                "vergadering_id": verg.get("Id", ""),
                "vergadering_datum": (verg.get("Datum") or "")[:10],
                "vergadering_titel": verg.get("Titel", ""),
            }
            prev = state["verslagen"].get(vid)
            xml_path = paths["tk_xml"] / f"{vid}.xml"
            if prev is None or prev["gewijzigd_op"] != info["gewijzigd_op"] or not xml_path.exists():
                try:
                    download(f"{base}/Verslag({vid})/resource", xml_path)
                    n_downloaded += 1
                except Exception as e:  # some verslagen have no resource yet
                    print(f"  skip resource {vid}: {e}", file=sys.stderr)
                    continue
            state["verslagen"][vid] = info
            if info["gewijzigd_op"] > state["last_gewijzigd_op"]:
                state["last_gewijzigd_op"] = info["gewijzigd_op"]
            n_seen += 1
        url = page.get("@odata.nextLink")
        paths["tk_state"].write_text(json.dumps(state, indent=1), encoding="utf-8")
        print(f"  ... {n_seen} verslagen seen, {n_downloaded} downloaded", flush=True)

    print(f"done: {n_seen} verslagen, {n_downloaded} XMLs downloaded -> {paths['tk_xml']}")


if __name__ == "__main__":
    main()
