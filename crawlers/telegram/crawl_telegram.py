"""FixIndex Telegram crawler — MTProto *client* (user session), not a bot.

Most electronics repair groups ban bots, so this crawls with your own
Telegram account via Telethon. It only reads groups your account is already
a member of, respects FloodWait backoffs, scrubs customer PII locally, and
submits everything through the authenticated FixIndex ingestion API where it
lands in the human review queue (never auto-published).

First run: interactive phone + code login creates a reusable .session file.
Usage:
    python crawl_telegram.py --dry-run          # preview payloads, submit nothing
    python crawl_telegram.py --limit 200        # crawl and submit
    python crawl_telegram.py --device samsung-galaxy-a14
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import (  # noqa: E402
    FixIndexClient, Ledger, PIIScrubber, classify_topic, load_config,
    matches_device, normalize_fa,
)

from telethon import TelegramClient  # noqa: E402
from telethon.errors import FloodWaitError  # noqa: E402
from telethon.tl.types import User  # noqa: E402

# A message looks like a full repair case when it has both fault language
# and resolution language and enough substance to review.
_FAULT_WORDS = [
    "dead", "no power", "not charging", "boot loop", "short", "0.0",
    "no sound", "no audio", "mic not working", "no mic", "no service",
    "no display", "no backlight", "baseband", "wifi", "camera",
    "خاموش", "شارژ نمی", "بوت لوپ", "اتصالی", "صدا", "میکروفن",
    "میکروفون", "اسپیکر", "تصویر", "آنتن", "آی سی",
]
_FIX_WORDS = [
    "replaced", "reball", "reflow", "jumper", "fixed", "solved", "replace",
    "changed", "swapped", "cleaned", "تعویض", "ریبال", "جامپر", "حل شد",
    "درست شد", "هیت", "ریفرش", "شستشو",
]


def message_url(entity, msg_id: int) -> str:
    username = getattr(entity, "username", None)
    if username:
        return f"https://t.me/{username}/{msg_id}"
    chat_id = getattr(entity, "id", 0)
    return f"https://t.me/c/{chat_id}/{msg_id}"


def sender_label(sender) -> str:
    if isinstance(sender, User):
        return (sender.first_name or "technician")[:60]
    title = getattr(sender, "title", None)
    return (title or "community member")[:60]


async def crawl(args) -> None:
    cfg = load_config(args.config)
    tg = cfg["telegram"]
    client_fx = FixIndexClient(cfg["fixindex"]["base_url"], cfg["fixindex"]["api_key"], dry_run=args.dry_run)
    ledger = Ledger()
    scrubber = PIIScrubber()

    devices = cfg["devices"]
    if args.device:
        devices = [d for d in devices if d.get("slug") == args.device]
        if not devices:
            sys.exit(f"No device with slug {args.device} in config.")

    session_path = str(Path(__file__).resolve().parent / tg.get("session_name", "fixindex_crawler"))
    tg_client = TelegramClient(session_path, tg["api_id"], tg["api_hash"])
    await tg_client.start(phone=tg["phone"])
    print("Logged in to Telegram (client session).")

    limit = args.limit or tg.get("max_messages_per_group", 500)
    min_len = tg.get("min_message_length", 60)
    insights: dict[str, list] = {d["slug"]: [] for d in devices if d.get("slug")}
    provenance: dict[str, list] = {d["slug"]: [] for d in devices if d.get("slug")}
    cases_submitted = 0

    for group in tg["groups"]:
        try:
            entity = await tg_client.get_entity(group)
        except Exception as exc:  # noqa: BLE001
            print(f"[skip] cannot resolve group {group}: {exc}", file=sys.stderr)
            continue
        group_title = getattr(entity, "title", None) or getattr(entity, "username", str(group))
        print(f"\n=== Crawling {group_title} (limit {limit}) ===")

        count = 0
        try:
            async for msg in tg_client.iter_messages(entity, limit=limit):
                text = (msg.message or "").strip()
                if len(text) < min_len:
                    continue
                for device in devices:
                    if not matches_device(text, device):
                        continue
                    key = f"tg:{getattr(entity, 'id', group)}:{msg.id}:{device.get('slug')}"
                    if ledger.has(key):
                        continue

                    clean = scrubber.scrub(text)
                    if scrubber.is_unsafe(clean):
                        ledger.mark(key, "unsafe")
                        continue

                    url = message_url(entity, msg.id)
                    topic = classify_topic(clean)
                    low = normalize_fa(clean)
                    lang = "fa" if any("\u0600" <= ch <= "\u06ff" for ch in clean) else "en"
                    date = msg.date.strftime("%Y-%m-%d") if msg.date else None
                    sender = sender_label(await msg.get_sender())

                    slug = device.get("slug")
                    if slug:
                        insights[slug].append({
                            "source": f"Telegram · {group_title}"[:120],
                            "url": url,
                            "excerpt": clean[:1500],
                            "topic": topic,
                            "author": sender,
                            "date": date,
                        })
                        provenance[slug].append({
                            "url": url,
                            "title": f"Telegram report: {topic} ({date})"[:200],
                        })

                    # Full-case extraction: fault + fix language together.
                    if (tg.get("submit_full_cases", True)
                            and len(clean) > 200
                            and any(w in low for w in _FAULT_WORDS)
                            and any(w in low for w in _FIX_WORDS)):
                        title = f"{device['model_family']} {topic.replace('-', ' ')} — community report"
                        result = client_fx.submit_case(
                            device, title, clean, url,
                            f"Telegram {group_title} msg {msg.id}",
                            ("tg-case", str(getattr(entity, 'id', group)), str(msg.id)),
                            language=lang,
                        )
                        if result:
                            cases_submitted += 1
                            print(f"  [case] {url}")

                    ledger.mark(key, "insight")
                    count += 1
                    await asyncio.sleep(0.3)  # gentle pacing
        except FloodWaitError as exc:
            print(f"[floodwait] sleeping {exc.seconds}s as required by Telegram")
            time.sleep(exc.seconds + 1)
        print(f"  matched {count} messages for configured devices")

    # Batch-submit enrichment per device (one upsert per device per run).
    for device in devices:
        slug = device.get("slug")
        if not slug or not insights[slug]:
            continue
        result = client_fx.enrich_device(
            device,
            {"community": insights[slug][-100:]},
            provenance[slug][-100:],
        )
        if result:
            print(f"[enriched] {slug}: +{len(insights[slug])} community insights")

    await tg_client.disconnect()
    print(f"\nDone. Cases submitted: {cases_submitted}. "
          f"Insights: {sum(len(v) for v in insights.values())}. "
          "Everything awaits human review in /review.")


def main() -> None:
    parser = argparse.ArgumentParser(description="FixIndex Telegram client crawler")
    parser.add_argument("--config", help="path to config.json")
    parser.add_argument("--dry-run", action="store_true", help="print payloads without submitting")
    parser.add_argument("--limit", type=int, help="max messages per group")
    parser.add_argument("--device", help="restrict to one device slug")
    args = parser.parse_args()
    asyncio.run(crawl(args))


if __name__ == "__main__":
    main()
