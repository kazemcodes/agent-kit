"""FixIndex YouTube crawler — collects repair video *metadata* via yt-dlp.

No videos are downloaded or rehosted: titles, channels, durations, view
counts, thumbnails and URLs are indexed into the device dossier
(profile.media, kind=youtube) with provenance, and rendered as playable
cards on the device page.

Usage:
    python crawl_youtube.py --dry-run
    python crawl_youtube.py --device samsung-galaxy-a14
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import FixIndexClient, Ledger, classify_topic, load_config  # noqa: E402


def yt_search(query: str, n: int) -> list[dict]:
    """Flat search metadata (fast, no video downloads)."""
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--flat-playlist", "--dump-single-json",
        "--playlist-items", f"1-{n}",
        "--no-warnings", "--quiet",
        f"ytsearch{n}:{query}",
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if out.returncode != 0:
            print(f"[warn] yt-dlp search failed for {query!r}: {out.stderr[:200]}", file=sys.stderr)
            return []
        data = json.loads(out.stdout or "{}")
        return data.get("entries") or []
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        print(f"[warn] yt-dlp error: {exc}", file=sys.stderr)
        return []


def yt_details(url: str) -> dict | None:
    """Full metadata for one video (used when flat search lacks fields)."""
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--dump-json", "--skip-download", "--no-warnings", "--quiet", url,
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        if out.returncode == 0 and out.stdout:
            return json.loads(out.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    return None


def is_persian(text: str) -> bool:
    return any("\u0600" <= ch <= "\u06ff" for ch in text)


_REPAIR_INTENT = [
    "repair", "fix", "solved", "solution", "problem", "issue", "not charging",
    "no power", "dead", "boot", "frp", "bypass", "flash", "unlock", "screen",
    "battery", "water", "charging", "motherboard", "jumper", "reball",
    "audio", "mic", "microphone", "speaker", "sound", "no sound", "no audio",
    "codec", "wcd", "pmic", "backlight", "baseband", "no service", "wifi",
    "diode", "schematic", "short", "ic", "cpu", "flex", "subboard", "sub board",
    "تعمیر", "مشکل", "حل شد", "خاموش", "شارژ", "بوت", "فلش", "باتری", "ال سی دی",
    "صدا", "میکروفن", "میکروفون", "اسپیکر", "کدک", "سی پی یو", "ریبال", "تصویر",
    "بک لایت", "تاچ", "وای فای", "آنتن", "بیس باند", "دوربین", "اتصالی", "آی سی",
]
_NOT_REPAIR = [
    " vs ", "versus", "freeze test", "frozen test", "speed test", "unboxing",
    "review", "comparison", "drop test", "durability", "camera test", "gaming test",
]


def is_relevant(title: str, device: dict) -> bool:
    """Device mention (never bare brand) + repair intent, minus entertainment/comparison content."""
    from common import matches_device, normalize_fa
    t = normalize_fa(title)
    name_hit = matches_device(title, device)
    intent_hit = any(normalize_fa(k) in t for k in _REPAIR_INTENT)
    excluded = any(k in t for k in _NOT_REPAIR)
    return name_hit and intent_hit and not excluded


def main() -> None:
    parser = argparse.ArgumentParser(description="FixIndex YouTube metadata crawler")
    parser.add_argument("--config")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--device", help="restrict to one device slug")
    args = parser.parse_args()

    cfg = load_config(args.config)
    yt = cfg.get("youtube", {})
    client = FixIndexClient(cfg["fixindex"]["base_url"], cfg["fixindex"]["api_key"], dry_run=args.dry_run)
    ledger = Ledger()

    devices = cfg["devices"]
    if args.device:
        devices = [d for d in devices if d.get("slug") == args.device]

    n = yt.get("results_per_query", 10)
    min_dur = yt.get("min_duration_seconds", 60)
    topics_en = yt.get("topics_en", ["repair"])
    topics_fa = yt.get("topics_fa", [])

    for device in devices:
        name = f"{device['brand']} {device['model_family']}"
        queries = [f"{name} {t}" for t in topics_en] + [f"{name} {t}" for t in topics_fa]
        videos: dict[str, dict] = {}
        provenance: list[dict] = []

        for query in queries:
            print(f"[search] {query}")
            for entry in yt_search(query, n):
                vid = entry.get("id")
                if not vid or vid in videos:
                    continue
                key = f"yt:{vid}:{device.get('slug')}"
                if ledger.has(key):
                    continue

                duration = entry.get("duration")
                views = entry.get("view_count")
                channel = entry.get("channel") or entry.get("uploader")
                # Flat search sometimes omits duration/views/channel — enrich top hits.
                if duration is None or channel is None:
                    details = yt_details(f"https://www.youtube.com/watch?v={vid}")
                    if details:
                        duration = details.get("duration")
                        views = details.get("view_count")
                        channel = details.get("channel") or details.get("uploader")

                if duration is not None and duration < min_dur:
                    continue

                title = entry.get("title") or ""
                if not is_relevant(title, device):
                    ledger.mark(key, "irrelevant")
                    continue
                url = f"https://www.youtube.com/watch?v={vid}"
                videos[vid] = {
                    "url": url,
                    "title": title[:200],
                    "kind": "youtube",
                    "video_id": vid,
                    "channel": (channel or "")[:120] or None,
                    "duration_seconds": int(duration) if duration else None,
                    "views": int(views) if views is not None else None,
                    "thumbnail_url": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                    "topic": classify_topic(title),
                    "language": "fa" if is_persian(title) else "en",
                    "shows": f"Search match: {query}"[:900],
                }
                provenance.append({"url": url, "title": title[:200]})
                ledger.mark(key, "youtube")

        if videos:
            result = client.enrich_device(device, {"media": list(videos.values())}, provenance)
            if result:
                print(f"[enriched] {device.get('slug')}: +{len(videos)} videos")
        else:
            print(f"[none] {device.get('slug')}: no new videos")

    print("Done. Video cards appear on the device page once reviewers approve the dossier.")


if __name__ == "__main__":
    main()
