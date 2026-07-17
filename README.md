# wilders-search

Archive + AI-search pipeline for all public video/speech of a politician,
configured per person (`config/<slug>.json`). First target: Geert Wilders.
Same architecture and transcript/index format as `abo-ali-search`.

## Sources

1. **Tweede Kamer verslagen** (official, corrected transcripts) via the open
   OData API (`gegevensmagazijn.tweedekamer.nl`, no key). vlos 2.0 XML is
   parsed into per-vergadering transcripts; only vergaderingen where the
   person speaks are kept. Segments carry a `wallclock` timestamp
   (markeertijdbegin) to link into Debat Gemist video.
2. **YouTube channels** (e.g. PVVpers, 803 videos) — audio-only opus via
   yt-dlp, transcribed with whisperx (ASR).

## Pipeline

```bash
python3 tk_sync.py            # incremental OData sync -> /data/WILDERS/tk/xml/
python3 tk_parse.py           # vlos XML -> /data/WILDERS/transcripts/tk_*.json
python3 yt_sync.py            # yt-dlp audio+info-json -> /data/WILDERS/youtube/
python3 transcribe_batch.py   # whisperx -> /data/WILDERS/transcripts/yt_*.json
python3 build_index.py        # BGE-M3 embeddings -> /data/WILDERS/index/
```

All scripts take an optional person slug argument (default `wilders`, or set
`PERSON=<slug>`).

## Transcript format (abo-ali compatible)

- `<base>.json`: `{"title", "duration_seconds", "segments": [{speaker_id,
  speaker, start, end, text}]}` — TK segments additionally have `wallclock`.
- `<base>.metadata.json`: `{"id", "title", "url", "upload_date",
  "duration_seconds", "source": "tk_verslag" | "youtube:<channel>",
  "transcript_source": "official" | "asr"}`.

Verslag preference per vergadering: status Gecorrigeerd > Casco >
Ongecorrigeerd, then latest GewijzigdOp. Re-running `tk_parse.py` after a
sync upgrades transcripts as corrected verslagen appear.

## Deployment

Live at **https://wilders.scrib-r.com**.

- Runs as a systemd **user** service on the HP Z8 (`wilders-search.service`,
  linger enabled) so it auto-starts at boot and restarts on crash:
  `systemctl --user {status,restart} wilders-search`.
- The edge VPS `vmi2702091` (Tailscale `100.64.0.5`) runs nginx, which proxies
  `https://wilders.scrib-r.com` → `http://100.64.0.2:8902` over Tailscale
  (config `/etc/nginx/sites-available/wilders.scrib-r.com`, TLS via certbot,
  media-streaming proxy settings). Mirrors the `aboali.scrib-r.com` vhost.
- `resume.sh` (cron `@reboot`) + the milestone watchers use
  `systemctl --user` to keep the service up; power-loss recovery unchanged.

## TODO

- Search app (port `abo-ali-search/app.py` + static UI; playback links:
  YouTube `&t=<s>`, TK via Debat Gemist wallclock).
- Diarization for YouTube ASR (`transcribe_batch.py --diarize`, needs
  `HF_TOKEN`).
- Optional extra sources: interviews on broadcaster channels (WNL, ON), X/
  Twitter video.
