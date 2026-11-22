"""One-off cleanup: drop non-repair videos ingested before the relevance gate."""
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "youtube"))
from common import load_config  # noqa: E402
from crawl_youtube import is_relevant  # noqa: E402

cfg = load_config()
base = cfg["fixindex"]["base_url"].rstrip("/")
key = cfg["fixindex"]["api_key"]
s = requests.Session()
s.headers.update({"Authorization": f"Bearer {key}", "Accept": "application/json"})

devices_by_slug = {d["slug"]: d for d in cfg["devices"]}
for slug in ["samsung-galaxy-a10", "samsung-galaxy-a10s"]:
    device = devices_by_slug[slug]
    r = s.get(f"{base}/api/v1/devices/{slug}", timeout=30)
    data = r.json()["data"]
    media = (data.get("profile") or {}).get("media") or []
    kept = [m for m in media if is_relevant(m.get("title", ""), device)]
    dropped = len(media) - len(kept)
    payload = {
        "device": {
            "brand": device["brand"],
            "model_family": device["model_family"],
            "platform": device["platform"],
            "slug": slug,
            "profile": {"media": kept},
        },
        "provenance": [{"url": m["url"], "title": m["title"][:200]} for m in kept],
    }
    resp = s.post(f"{base}/api/v1/agent/devices", json=payload, timeout=60)
    print(f"{slug}: kept {len(kept)}, dropped {dropped} -> HTTP {resp.status_code}")
