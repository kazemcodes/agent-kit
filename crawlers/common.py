"""Shared utilities for FixIndex crawler and diagnostic harness agents.

Every agent funnels through the same authenticated ingestion API:
  - diagnose / read   -> GET  /api/v1/diagnose, POST /api/v1/mcp
  - device enrichment -> POST /api/v1/agent/devices   (upsert dossier sections)
  - full repair cases -> POST /api/v1/agent/submissions (draft, review queue)

Nothing is ever auto-published: the server quarantines agent content in the
review queue and rejects PII / prompt-injection / prohibited content (A04/S08).
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

import requests

CRAWLER_DIR = Path(__file__).resolve().parent
LEDGER_PATH = CRAWLER_DIR / ".crawl_ledger.db"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def load_config(path: str | None = None) -> dict:
    cfg_path = Path(path) if path else CRAWLER_DIR / "config.json"
    if not cfg_path.exists():
        sys.exit(
            f"Config not found: {cfg_path}\n"
            f"Copy config.example.json to config.json and fill in your credentials."
        )
    with open(cfg_path, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Content safety (mirrors App\Application\Services\ContentSafetyService)
# ---------------------------------------------------------------------------

_INJECTION = [
    "ignore previous instructions", "ignore all previous instructions",
    "ignore prior instructions", "disregard the above",
    "disregard previous instructions", "you are now",
    "reveal your system prompt", "print your system prompt", "jailbreak",
]
_PROHIBITED = [
    "imei rewrite", "imei rewriting", "imei changer", "imei change tool",
    "baseband spoof", "blacklist bypass", "bypass carrier blacklist",
    "ransomware", "spyware", "trojan",
    "zero-day exploit", "zeroday exploit", "rce exploit",
]


class PIIScrubber:
    """Redacts customer identifiers before anything leaves this machine."""

    def scrub(self, text: str) -> str:
        text = re.sub(r"\b\d{15}\b", "[imei-redacted]", text)
        text = re.sub(r"\bimei\s*[:#]?\s*\d{14,16}\b", "IMEI [redacted]", text, flags=re.I)
        text = re.sub(
            r"\bserial\s*(?:number|no\.?)?\s*[:#]\s*[A-Z0-9]{8,}\b",
            "serial [redacted]",
            text,
            flags=re.I,
        )
        text = re.sub(r"\+\d{1,3}[\s-]?\d{6,12}\b", "[phone-redacted]", text)
        return text

    def is_unsafe(self, text: str) -> bool:
        low = text.lower()
        return any(p in low for p in _INJECTION + _PROHIBITED)


# ---------------------------------------------------------------------------
# Topic classification (EN + FA keywords)
# ---------------------------------------------------------------------------

_TOPICS = {
    "no-power": [
        "no power", "dead", "not turning on", "0.0", "dead boot",
        "خاموش", "روشن نمیشه", "روشن نمی", "بدون برق",
    ],
    "charging": [
        "charg", "usb", "tristar", "hydra", "vbus", "fast charge",
        "شارژ", "سوکت شارژ", "سوکت",
    ],
    "boot-loop": [
        "boot loop", "bootloop", "restart", "logo", "nv data",
        "بوت لوپ", "ریستارت", "لوگو", "روی آرم",
    ],
    "display-backlight": [
        "backlight", "no light", "black screen", "display ic", "vsn", "vsp",
        "بک لایت", "نور صفحه", "تصویر نداره", "صفحه سیاه",
    ],
    "touch": ["touch", "digitizer", "ghost touch", "تاچ", "لمس"],
    "screen": ["screen", "display", "lcd", "oled", "ال سی دی", "صفحه نمایش"],
    "water-damage": ["water", "liquid", "corrosion", "آب خوردگی", "آبخوردگی", "سولفاته"],
    "frp-unlock": ["frp", "unlock", "bypass", "mdm", "اف آر پی", "بای پس", "قفل"],
    "network": [
        "baseband", "imei", "signal", "no service", "qcn", "modem",
        "بیس باند", "آنتن", "انتن", "سرویس",
    ],
    "wifi-bt": ["wifi", "wi-fi", "bluetooth", "wlan", "وای فای", "وایفای", "بلوتوث"],
    "audio": [
        "audio", "mic", "microphone", "speaker", "earpiece", "sound", "no sound",
        "no audio", "codec", "wcd", "wsa", "صدا", "میکروفون", "میکروفن",
        "اسپیکر", "بلندگو", "کپسول", "کدک", "بدون صدا",
    ],
    "camera": ["camera", "flash", "دوربین"],
    "sensors": ["sensor", "proximity", "fingerprint", "gyro", "سنسور", "اثر انگشت"],
    "overheating": ["overheat", "hot", "thermal", "داغ کردن", "حرارت"],
    "cpu-reball": ["reball", "cpu", "soc", "sm8250", "snapdragon", "ریبال", "سی پی یو"],
    "software-flash": ["flash", "firmware", "rom", "odin", "edl", "9008", "فلش", "رام"],
}


def classify_topic(text: str) -> str:
    low = normalize_fa(text)
    for topic, keywords in _TOPICS.items():
        if any(normalize_fa(k) in low for k in keywords):
            return topic
    return "general"


def normalize_fa(text: str) -> str:
    return (
        text.replace("ي", "ی")
        .replace("ى", "ی")
        .replace("ك", "ک")
        .replace("ة", "ه")
        .replace("أ", "ا")
        .replace("إ", "ا")
        .replace("آ", "ا")
        .replace("\u200c", " ")
        .lower()
    )


def matches_device(text: str, device: dict) -> bool:
    """Match device model/aliases/keywords without matching bare brand alone."""
    hay = normalize_fa(text)
    brand = (device.get("brand") or "").strip()
    model = (device.get("model_family") or "").strip()
    needles: list[str] = []
    if model:
        needles.append(model)
        if brand:
            needles.append(f"{brand} {model}")
    needles.extend(device.get("keywords", []))
    needles.extend(device.get("aliases", []))

    for raw_needle in needles:
        needle = normalize_fa(str(raw_needle).strip())
        if not needle or len(needle) < 2:
            continue
        if len(needle) <= 5:
            if re.search(rf"(?<![a-z0-9\u0600-\u06ff]){re.escape(needle)}(?![a-z0-9\u0600-\u06ff])", hay):
                return True
        elif needle in hay:
            return True
    return False


# ---------------------------------------------------------------------------
# Local dedup ledger (server-side idempotency is the durable guarantee)
# ---------------------------------------------------------------------------


class Ledger:
    def __init__(self, path: Path = LEDGER_PATH):
        self.db = sqlite3.connect(path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS seen (key TEXT PRIMARY KEY, kind TEXT, ts REAL)"
        )
        self.db.commit()

    def has(self, key: str) -> bool:
        return self.db.execute("SELECT 1 FROM seen WHERE key=?", (key,)).fetchone() is not None

    def mark(self, key: str, kind: str) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO seen (key, kind, ts) VALUES (?,?,?)",
            (key, kind, time.time()),
        )
        self.db.commit()


def idempotency_key(*parts: str) -> str:
    return hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# FixIndex ingestion & MCP client
# ---------------------------------------------------------------------------

# List sections merge by these identity keys so repeated crawls/runs accumulate
# instead of replacing previously collected entries.
_MERGE_KEYS = {
    "media": lambda m: m.get("video_id") or m.get("url"),
    "community": lambda m: m.get("url"),
    "diode_tables": lambda m: f"{m.get('connector')}:{m.get('pin')}",
    "ic_crossref": lambda m: str(m.get("ic", "")).strip().upper(),
    "firmware": lambda m: m.get("url"),
    "schematics": lambda m: m.get("url"),
    "evidence": lambda m: m.get("id") or m.get("url"),
    "failure_modes": lambda m: normalize_fa(str(m.get("title", "")).strip()),
    "checks": lambda m: normalize_fa(str(m.get("label", "")).strip()),
    "parts": lambda m: normalize_fa(str(m.get("name", "")).strip()),
    "error_codes": lambda m: str(m.get("code", "")).strip().upper(),
}


class FixIndexClient:
    def __init__(self, base_url: str, api_key: str, dry_run: bool = False):
        self.base_url = base_url.rstrip("/")
        self.dry_run = dry_run
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    def _post(self, path: str, payload: dict) -> dict | None:
        if self.dry_run:
            print(f"[dry-run] POST {path}\n{json.dumps(payload, ensure_ascii=False, indent=2)[:2000]}")
            return {"status": "dry-run"}
        resp = self.session.post(f"{self.base_url}{path}", json=payload, timeout=60)
        if resp.status_code in (200, 201):
            return resp.json()
        print(f"[warn] POST {path} -> {resp.status_code}: {resp.text[:500]}", file=sys.stderr)
        return None

    def get_device(self, slug: str) -> dict | None:
        try:
            resp = self.session.get(f"{self.base_url}/api/v1/devices/{slug}", timeout=20)
            if resp.status_code == 200:
                return resp.json().get("data")
        except requests.RequestException:
            return None
        return None

    def diagnose(self, query: str, device: str | None = None, brand: str | None = None) -> dict | None:
        params: dict[str, str] = {"q": query}
        if device:
            params["device"] = device
        if brand:
            params["brand"] = brand
        try:
            resp = self.session.get(f"{self.base_url}/api/v1/diagnose", params=params, timeout=20)
            if resp.status_code == 200:
                return resp.json().get("data")
        except requests.RequestException:
            return None
        return None

    def mcp_call(self, tool_name: str, arguments: dict) -> dict | None:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        try:
            resp = self.session.post(f"{self.base_url}/api/v1/mcp", json=payload, timeout=30)
            if resp.status_code == 200:
                return resp.json()
        except requests.RequestException:
            return None
        return None

    def enrich_device(
        self,
        device: dict,
        sections: dict,
        provenance: list[dict],
        variants: list[dict] | None = None,
        testpoints: list[dict] | None = None,
    ) -> dict | None:
        """Upsert device metadata & dossier sections, merging list entries with stored ones."""
        slug = device.get("slug")
        existing: dict = {}
        existing_profile: dict = {}
        if slug:
            current = self.get_device(slug)
            if current:
                existing = current
                existing_profile = current.get("profile") or {}

        merged_profile = {}
        for section, entries in sections.items():
            if not entries:
                continue
            key_fn = _MERGE_KEYS.get(section)
            old = existing_profile.get(section) or []
            if key_fn and isinstance(old, list) and isinstance(entries, list):
                by_key = {}
                for item in old:
                    if isinstance(item, dict):
                        k = key_fn(item)
                        if k:
                            by_key[k] = item
                for item in entries:
                    if isinstance(item, dict):
                        k = key_fn(item)
                        if k:
                            by_key[k] = item
                merged_profile[section] = list(by_key.values())
            else:
                merged_profile[section] = entries

        if not merged_profile:
            return None

        merged_aliases = list(dict.fromkeys([
            *(existing.get("aliases") or []),
            *(device.get("aliases") or []),
        ]))
        merged_specs = {
            **(existing.get("specs") or {}),
            **(device.get("specs") or {}),
        }
        merged_issues = list(dict.fromkeys([
            *(existing.get("known_issues") or []),
            *(device.get("known_issues") or []),
        ]))

        device_body: dict = {
            "brand": device["brand"],
            "model_family": device["model_family"],
            "platform": device.get("platform", "Android"),
            **({"slug": slug} if slug else {}),
            **({"release_year": device["release_year"]} if device.get("release_year") else {}),
            **({"board_identifier": device["board_identifier"]} if device.get("board_identifier") else {}),
            **({"aliases": merged_aliases} if merged_aliases else {}),
            **({"specs": merged_specs} if merged_specs else {}),
            **({"known_issues": merged_issues} if merged_issues else {}),
            "profile": merged_profile,
        }

        payload: dict = {
            "device": device_body,
            "provenance": provenance,
        }
        if variants:
            payload["variants"] = variants
        if testpoints:
            payload["testpoints"] = testpoints

        return self._post("/api/v1/agent/devices", payload)

    def submit_case(
        self,
        device: dict,
        title: str,
        text: str,
        source_url: str,
        source_title: str,
        key_parts: tuple[str, ...],
        language: str = "en",
        *,
        symptoms: list[str] | None = None,
        error_codes: list[str] | None = None,
        diagnostic_steps: list[str] | None = None,
        required_tools: list[str] | None = None,
        safety_hazards: list[str] | None = None,
        provenance_sources: list[dict] | None = None,
        outcome: str = "REPORTED_EXPERIENCE",
    ) -> dict | None:
        """Submit a diagnostic report as a draft case (review queue, never live)."""
        scrubber = PIIScrubber()
        text = scrubber.scrub(text)
        if scrubber.is_unsafe(text):
            print(f"[skip] unsafe content from {source_url}", file=sys.stderr)
            return None
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

        clean_symptoms = [scrubber.scrub(s)[:250] for s in (symptoms or []) if str(s).strip()]
        if not clean_symptoms:
            clean_symptoms = [lines[0][:250]] if lines else ["Community report"]

        clean_steps = [scrubber.scrub(s)[:900] for s in (diagnostic_steps or []) if str(s).strip()]
        if not clean_steps:
            clean_steps = ["Community-sourced report — verify measurements against the cited source before acting."]

        clean_hazards = [scrubber.scrub(h)[:600] for h in (safety_hazards or []) if str(h).strip()]
        if not clean_hazards:
            clean_hazards = ["Disconnect the battery before probing or disconnecting board flexes."]

        sources = provenance_sources or [{
            "url": source_url,
            "title": source_title[:200],
            "excerpt": text[:600],
        }]

        payload = {
            "idempotency_key": idempotency_key(*key_parts),
            "agent_metadata": {
                "agent_id": "fixindex-harness",
                "model_name": "grounded-evidence-extractor",
                "model_version": "2.0",
            },
            "device": {"brand": device["brand"], "model_family": device["model_family"]},
            "language": language,
            "title": scrubber.scrub(title)[:180],
            "symptoms": clean_symptoms,
            "error_codes": [str(c)[:40] for c in (error_codes or []) if str(c).strip()],
            "diagnostic_steps": clean_steps,
            "solution": {
                "description": text[:4000],
                "outcome": outcome,
                "required_tools": [str(t)[:80] for t in (required_tools or []) if str(t).strip()],
                "safety_hazards": clean_hazards,
            },
            "provenance_sources": sources,
        }
        return self._post("/api/v1/agent/submissions", payload)
