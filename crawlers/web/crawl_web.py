"""FixIndex web crawler — repair forums, blogs, and service bulletins.

Respects robots.txt, rates itself, extracts readable article text, and
submits repair-relevant pages as draft cases with full provenance. Nothing
is auto-published; the review queue decides.

Usage:
    python crawl_web.py --dry-run
    python crawl_web.py --device samsung-galaxy-a14
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import FixIndexClient, Ledger, PIIScrubber, classify_topic, load_config  # noqa: E402

UA = "FixIndexCrawler/1.0 (+knowledge-base indexing; contact: admin@fixindex.local)"
_REPAIR_HINTS = [
    "repair", "fix", "replaced", "reball", "reflow", "jumper", "short",
    "diode", "testpoint", "schematic", "no power", "boot loop", "charging",
    "audio", "mic", "microphone", "speaker", "sound", "codec", "pmic",
    "backlight", "baseband", "no service", "cpu", "ic", "flex", "sub-board",
    "تعمیر", "تعویض", "ریبال", "جامپر", "اتصالی", "خاموشی", "شارژ",
    "صدا", "میکروفن", "میکروفون", "اسپیکر", "کدک", "سی پی یو", "آی سی",
]


def robots_allows(url: str, cache: dict[str, RobotFileParser | bool]) -> bool:
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin not in cache:
        rp = RobotFileParser()
        try:
            resp = requests.get(f"{origin}/robots.txt", headers={"User-Agent": UA}, timeout=10)
            if resp.status_code in (404, 410):
                cache[origin] = True
                return True
            if resp.status_code != 200:
                cache[origin] = False
                return False
            rp.parse(resp.text.splitlines())
            cache[origin] = rp
        except Exception:  # noqa: BLE001
            cache[origin] = False
            return False
    entry = cache[origin]
    if isinstance(entry, bool):
        return entry
    return bool(entry.can_fetch(UA, url))


def extract_text(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
        tag.decompose()
    title = (soup.find("h1") or soup.find("title"))
    title_text = title.get_text(strip=True) if title else ""
    body = soup.find("article") or soup.find("main") or soup.body or soup
    blocks = [el.get_text(" ", strip=True) for el in body.find_all(["h1", "h2", "h3", "p", "li", "pre", "code", "tr"])]
    seen: set[str] = set()
    lines: list[str] = []
    for block in blocks:
        if len(block) > 15 and block not in seen:
            seen.add(block)
            lines.append(block)
    text = "\n".join(lines)
    return title_text[:200], text[:6000]


def main() -> None:
    parser = argparse.ArgumentParser(description="FixIndex web crawler")
    parser.add_argument("--config")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--device", help="restrict to one device slug")
    args = parser.parse_args()

    cfg = load_config(args.config)
    web = cfg.get("web", {})
    client = FixIndexClient(cfg["fixindex"]["base_url"], cfg["fixindex"]["api_key"], dry_run=args.dry_run)
    ledger = Ledger()
    scrubber = PIIScrubber()
    delay = web.get("request_delay_seconds", 2.0)
    robots_cache: dict = {}

    devices_by_slug = {d.get("slug"): d for d in cfg["devices"] if d.get("slug")}
    submitted = 0

    for seed in web.get("seeds", []):
        slug = seed.get("device_slug")
        if args.device and slug != args.device:
            continue
        device = devices_by_slug.get(slug)
        if not device:
            print(f"[skip] unknown device slug {slug}", file=sys.stderr)
            continue

        for url in seed.get("urls", []):
            key = f"web:{url}:{slug}"
            if ledger.has(key):
                continue
            if not robots_allows(url, robots_cache):
                print(f"[robots] disallowed or unreachable: {url}")
                ledger.mark(key, "robots-skip")
                continue

            try:
                resp = requests.get(url, headers={"User-Agent": UA}, timeout=30)
                resp.raise_for_status()
            except requests.RequestException as exc:
                print(f"[warn] fetch failed {url}: {exc}", file=sys.stderr)
                continue
            finally:
                time.sleep(delay)

            title, text = extract_text(resp.text)
            if len(text) < 200:
                ledger.mark(key, "too-short")
                continue
            low = text.lower()
            if not any(h in low for h in _REPAIR_HINTS):
                ledger.mark(key, "not-repair")
                continue

            clean = scrubber.scrub(text)
            if scrubber.is_unsafe(clean):
                ledger.mark(key, "unsafe")
                continue

            topic = classify_topic(clean)
            lang = "fa" if any("\u0600" <= ch <= "\u06ff" for ch in clean) else "en"
            result = client.submit_case(
                device,
                title or f"{device['model_family']} {topic} — web report",
                clean,
                url,
                title or urlparse(url).netloc,
                ("web-case", url),
                language=lang,
            )
            if result:
                submitted += 1
                print(f"[case] {url}")
            ledger.mark(key, "submitted")

    print(f"Done. {submitted} draft cases submitted to the review queue.")


if __name__ == "__main__":
    main()
