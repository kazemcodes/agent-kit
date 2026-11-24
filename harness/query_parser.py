"""Bilingual (English + Persian) query parser and hardware search planner."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "crawlers"))
from common import normalize_fa  # noqa: E402

_PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

_BRAND_CANONICAL = {
    "xiaomi": "Xiaomi", "شیائومی": "Xiaomi", "شیاومی": "Xiaomi", "شیامی": "Xiaomi",
    "poco": "Xiaomi", "پوکو": "Xiaomi",
    "redmi": "Xiaomi", "ردمی": "Xiaomi",
    "mi": "Xiaomi",
    "samsung": "Samsung", "سامسونگ": "Samsung",
    "galaxy": "Samsung", "گلکسی": "Samsung",
    "apple": "Apple", "اپل": "Apple",
    "iphone": "Apple", "ایفون": "Apple",
    "ipad": "Apple", "ایپد": "Apple",
    "google": "Google", "گوگل": "Google",
    "pixel": "Google", "پیکسل": "Google",
    "huawei": "Huawei", "هواوی": "Huawei",
    "honor": "Honor", "انر": "Honor",
    "oneplus": "OnePlus", "وانپلاس": "OnePlus",
    "motorola": "Motorola", "موتورولا": "Motorola",
    "moto": "Motorola",
    "nokia": "Nokia", "نوکیا": "Nokia",
    "sony": "Sony", "سونی": "Sony",
    "realme": "Realme", "ریلمی": "Realme",
    "oppo": "Oppo", "اوپو": "Oppo",
    "vivo": "Vivo", "ویوو": "Vivo",
}

_BRAND_WORDS = set(_BRAND_CANONICAL.keys())
# Words that are both sub-brand triggers AND part of the model_family name.
_SUBBRAND_KEEP_IN_MODEL = {
    "poco", "پوکو",
    "redmi", "ردمی",
    "galaxy", "گلکسی",
    "iphone", "ایفون",
    "ipad", "ایپد",
    "pixel", "پیکسل",
    "moto",
}

_PERSIAN_MODEL_MAP = {
    "پوکو": "Poco",
    "ردمی": "Redmi",
    "نوت": "Note",
    "پرو": "Pro",
    "مکس": "Max",
    "پلاس": "Plus",
    "اولترا": "Ultra",
    "لایت": "Lite",
    "مینی": "Mini",
    "گلکسی": "Galaxy",
    "ایفون": "iPhone",
    "پیکسل": "Pixel",
}

_FA_CONVERSATION_STOPWORDS = {
    "من", "یه", "یک", "گوشی", "موبایل", "دستگاه", "با", "ایراد", "مشکل",
    "این", "اماده", "اومده", "است", "هست", "که", "ک", "نه", "و", "یا",
    "کار", "میکنه", "نمیکنه", "میکند", "نمیکند", "نمیشه", "میشه", "شده",
    "داره", "دارد", "لطفا", "را", "رو", "به", "از", "در", "هم", "برای",
    "هیچ", "اصلا", "دیگه", "دیگ", "چطور", "چرا", "ایا", "بدون", "نداره",
    "قطع", "خراب", "بهم", "ریخته", "تعمیر", "چیه", "حل", "میتونی", "بگو",
    "نیست", "میاد", "نمیاید", "نمیاد", "روی", "تو", "توی", "روشن", "خاموش",
    "میره", "میگرده", "برمیگرده", "میکشه", "نشون", "میده", "نمیده", "وقتی",
    "موقع", "لحظه", "بعدش", "بعد", "اخرش", "سریع", "فقط", "ولی", "اما",
    "ولت", "امپر", "میلی", "دهم", "اهم", "دیود", "سیستم", "کامپیوتر",
    "شناخته", "شناسایی", "نمیشناسه", "میشناسه", "صدا", "تستر", "داریم",
}

_EN_CONVERSATION_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "not", "no", "and", "or", "neither", "nor", "with", "without", "my",
    "phone", "device", "mobile", "handset", "problem", "issue", "fault",
    "fix", "repair", "help", "working", "work", "works", "does", "doesnt",
    "do", "cant", "can", "could", "you", "on", "in", "of", "to", "for",
    "at", "by", "from", "it", "its", "this", "that", "having", "has",
    "have", "both", "all", "also", "too", "dead", "broken",
    "failed", "failing", "stopped", "completely", "when", "powered", "off",
    "drops", "back", "then", "only", "shows", "reads", "unknown", "pc",
}

_FAULT_KEYWORDS: dict[str, list[str]] = {
    "mic": ["mic", "microphone", "میکروفن", "میکروفون", "میک"],
    "sound": ["sound", "audio", "speaker", "earpiece", "loudspeaker", "ringtone", "codec", "اسپیکر", "بلندگو", "بوق", "کپسول", "کدک", "صدا کار", "بدون صدا", "صدا نداره", "قطعی صدا"],
    "no-power": ["no power", "not turning on", "dead boot", "no boot", "0.00a", "خاموشی", "روشن نمیشه", "روشن نمی", "روشن نمیشود", "اتصالی اصلی"],
    "charging-fast": [
        "fast charg", "turbo charg", "mi turbo", "quick charge", "9v", "11v", "2.5a",
        "0.7a", "0.6a", "slow charg", "fake charg", "bq25968", "bq25970", "smb1390",
        "فست شارژ", "فست", "توربو", "می توربو", "9 ولت", "فیک شارژ", "شارژ کند",
        "پمپ شارژ", "0.7", "0.6", "2.5 امپر", "2 امپر", "7 دهم", "6 دهم",
    ],
    "charging": [
        "charg", "not charging", "slow charging", "vbus", "tristar", "hydra",
        "moisture", "ovp", "boot_cap", "شارژ", "سوکت شارژ", "شارژر",
    ],
    "usb-data": [
        "unknown usb", "not recognized", "device descriptor", "usb_dm", "usb_dp",
        "usb1_hs_dm", "usb1_hs_dp", "d-", "d+", "fsa4480", "u3402", "mtp",
        "سیستم نمیشناسه", "کامپیوتر نمیشناسه", "شناسایی نمیکنه", "شناخته نمیشه",
        "دی منفی", "دی مثبت", "دیتا", "پورت usb", "پورت یو اس بی", "یو اس بی",
    ],
    "boot-loop": ["boot loop", "bootloop", "restart", "stuck on logo", "nv data", "بوت لوپ", "ریستارت", "لوگو", "روی ارم"],
    "display-backlight": ["backlight", "no light", "black screen", "no display", "display", "lcd", "oled", "بک لایت", "نور صفحه", "تصویر", "ال سی دی", "صفحه سیاه"],
    "touch": ["touch", "digitizer", "ghost touch", "تاچ", "لمس"],
    "network-baseband": ["baseband", "no service", "signal", "imei", "modem", "qcn", "انتن", "بیس باند", "سرویس"],
    "wifi-bt": ["wifi", "wi-fi", "bluetooth", "wlan", "وای فای", "وایفای", "بلوتوث"],
    "camera": ["camera", "flash", "دوربین"],
    "water-damage": ["water", "liquid", "corrosion", "اب خوردگی", "ابخوردگی", "سولفاته"],
    "battery": ["battery", "swollen", "drain", "باتری", "باطری", "درصد باتری"],
    "sensors": ["sensor", "proximity", "fingerprint", "gyro", "سنسور", "اثر انگشت"],
    "overheating": ["overheat", "overheating", "thermal", "hot", "داغ", "حرارت"],
    "storage-ufs-emmc": ["emmc", "ufs", "nand", "storage", "4013", "error 9", "itunes 9", "خطای 9", "pit file", "pit error"],
    "frp-unlock": ["frp", "unlock", "bypass", "mdm", "اف ار پی", "بای پس", "قفل"],
}

_DEVICE_POWERS_ON_HINTS = (
    "گوشی روشن هست", "گوشی روشنه", "روشن هست", "روشن است", "روشن میشه",
    "روشن میکنم", "powers on", "turns on", "boots normally", "boots fine",
)

_SINGLE_FAULT_TOKENS = {
    tok
    for words in _FAULT_KEYWORDS.values()
    for phrase in words
    for tok in phrase.split()
} | {
    "error", "code", "کد", "خطا", "reball", "ریبال", "cpu", "ic", "ای", "سی",
    "port", "restore", "loop", "jack", "flex", "connector", "board", "subboard",
    "cable", "button", "glass", "module", "chip", "rail", "rails", "line", "lines",
    "mode", "pin", "pins", "sim", "slot", "sudden", "randomly", "intermittent",
    "after", "update", "drop", "dropped", "wont", "stuck", "blank", "black",
    "white", "green", "freeze", "frozen", "slow", "fast", "bad", "damaged",
    "corroded", "shorted", "not", "no", "working", "work", "sound", "mic",
}

# Known board / sibling relationships so cross-referenced hardware fixes (e.g.
# Redmi K40 == Poco F3 == Mi 11X) are automatically discovered during research.
_DEVICE_HINTS: dict[str, dict] = {
    "poco f3": {
        "brand": "Xiaomi",
        "model_family": "Poco F3",
        "platform": "Android",
        "codename": "alioth",
        "siblings": ["Redmi K40", "Mi 11X"],
        "board_identifier": "M2012K11AG",
        "aliases": ["M2012K11AG", "M2012K11AI", "M2012K11AC", "alioth", "Redmi K40", "Mi 11X", "پوکو F3", "پوکو اف ۳"],
        "wikipedia_titles": ["Poco F3", "Redmi K40"],
    },
    "poco x3 pro": {
        "brand": "Xiaomi",
        "model_family": "Poco X3 Pro",
        "platform": "Android",
        "codename": "vayu",
        "siblings": ["Poco X3 Pro bhima"],
        "board_identifier": "M2102J20SG",
        "aliases": ["M2102J20SG", "M2102J20SI", "vayu", "bhima", "پوکو X3 Pro", "پوکو X3 پرو"],
        "wikipedia_titles": ["Poco X3 Pro"],
    },
    "poco x3 nfc": {
        "brand": "Xiaomi",
        "model_family": "Poco X3 NFC",
        "platform": "Android",
        "codename": "surya",
        "siblings": ["Poco X3 karna"],
        "board_identifier": "M2007J20CG",
        "aliases": ["M2007J20CG", "M2007J20CI", "surya", "karna"],
        "wikipedia_titles": ["Poco X3"],
    },
    "mi 9t pro": {
        "brand": "Xiaomi",
        "model_family": "Mi 9T Pro",
        "platform": "Android",
        "codename": "raphael",
        "siblings": ["Redmi K20 Pro"],
        "board_identifier": "M1903F11G",
        "aliases": ["M1903F11G", "M1903F11A", "M1903F11I", "raphael", "Redmi K20 Pro"],
        "wikipedia_titles": ["Redmi K20"],
    },
    "redmi k20 pro": {
        "brand": "Xiaomi",
        "model_family": "Mi 9T Pro",
        "platform": "Android",
        "codename": "raphael",
        "siblings": ["Mi 9T Pro"],
        "board_identifier": "M1903F11A",
        "aliases": ["M1903F11A", "M1903F11G", "M1903F11I", "raphael", "Mi 9T Pro"],
        "wikipedia_titles": ["Redmi K20"],
    },
    "poco m3": {
        "brand": "Xiaomi",
        "model_family": "Poco M3",
        "platform": "Android",
        "codename": "citrus",
        "siblings": ["Redmi 9T", "Redmi 9 Power"],
        "board_identifier": "M2010J19CG",
        "aliases": ["M2010J19CG", "M2010J19CI", "citrus"],
        "wikipedia_titles": ["Poco M3"],
    },
    "redmi 9t": {
        "brand": "Xiaomi",
        "model_family": "Redmi 9T",
        "platform": "Android",
        "codename": "lime",
        "siblings": ["Poco M3", "Redmi 9 Power"],
        "board_identifier": "M2010J19SG",
        "aliases": ["M2010J19SG", "M2010J19SY", "lime"],
        "wikipedia_titles": ["Redmi 9T"],
    },
    "redmi k40": {
        "brand": "Xiaomi",
        "model_family": "Poco F3",
        "platform": "Android",
        "codename": "alioth",
        "siblings": ["Poco F3", "Mi 11X"],
        "board_identifier": "M2012K11AC",
        "aliases": ["M2012K11AC", "M2012K11AG", "alioth", "Redmi K40", "Mi 11X"],
        "wikipedia_titles": ["Redmi K40", "Poco F3"],
    },
    "redmi note 10 pro": {
        "brand": "Xiaomi",
        "model_family": "Redmi Note 10 Pro",
        "platform": "Android",
        "codename": "sweet",
        "siblings": ["Redmi Note 10 Pro Max"],
        "board_identifier": "M2101K6G",
        "aliases": ["M2101K6G", "M2101K6R", "sweet"],
        "wikipedia_titles": ["Redmi Note 10 Pro"],
    },
    "galaxy a10": {
        "brand": "Samsung",
        "model_family": "Galaxy A10",
        "platform": "Android",
        "board_identifier": "SM-A105F",
        "aliases": ["SM-A105F", "SM-A105FN", "SM-A105G", "SM-A105M", "A105F"],
        "wikipedia_titles": ["Samsung Galaxy A10"],
    },
    "galaxy a10s": {
        "brand": "Samsung",
        "model_family": "Galaxy A10s",
        "platform": "Android",
        "board_identifier": "SM-A107F",
        "aliases": ["SM-A107F", "SM-A107M", "A107F"],
        "wikipedia_titles": ["Samsung Galaxy A10s"],
    },
}

_MODEL_STOP_TOKENS = _FA_CONVERSATION_STOPWORDS | _EN_CONVERSATION_STOPWORDS | _SINGLE_FAULT_TOKENS


@dataclass
class ParsedQuery:
    raw_query: str
    brand: str
    model_family: str
    slug: str
    platform: str
    language: str
    fault_domains: list[str] = field(default_factory=list)
    error_codes: list[str] = field(default_factory=list)
    siblings: list[str] = field(default_factory=list)
    codename: str | None = None
    board_identifier: str | None = None
    aliases: list[str] = field(default_factory=list)
    wikipedia_titles: list[str] = field(default_factory=list)
    symptom_summary_en: str = ""
    symptom_summary_fa: str = ""
    search_queries_web: list[str] = field(default_factory=list)
    search_queries_youtube: list[str] = field(default_factory=list)
    search_queries_ifixit: list[str] = field(default_factory=list)


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s


def _detect_error_codes(text: str) -> list[str]:
    codes: list[str] = []
    for match in re.findall(r"\b(?:0x[0-9a-fA-F]{4,8}|4013|4014|4005|9008|SW\s*REV|Sahara\s*V?\d*)\b", text, flags=re.I):
        c = match.strip()
        if c not in codes:
            codes.append(c)
    return codes


def _keyword_matches(keyword: str, norm_text: str) -> bool:
    """Match a fault keyword safely without false substring hits on short terms (EN & FA)."""
    if not keyword:
        return False
    # For short single-word tokens (e.g. 'mic', 'usb', 'ovp', '9v', 'd-', 'میک'),
    # require word boundaries so 'mic' doesn't match 'ceramic' and 'میک' doesn't match 'نمیکنه'.
    if len(keyword) <= 4 and " " not in keyword:
        if keyword.isascii():
            return bool(re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", norm_text))
        return bool(re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", norm_text))
    return keyword in norm_text


def _detect_faults(norm_text: str) -> list[str]:
    found: list[str] = []
    has_mic = any(_keyword_matches(k, norm_text) for k in _FAULT_KEYWORDS["mic"])
    has_sound = any(_keyword_matches(k, norm_text) for k in _FAULT_KEYWORDS["sound"])
    # Avoid false positive 'sound' when user says "سیستم صدا میده" (PC chimes on USB connect)
    if has_sound and any(p in norm_text for p in ("سیستم صدا میده", "کامپیوتر صدا میده", "pc chimes", "connect sound")):
        has_sound = False
    if has_mic and has_sound:
        found.append("audio-mic-combined")
    if has_mic:
        found.append("audio-mic")
    if has_sound:
        found.append("audio-sound")

    powers_on = any(h in norm_text for h in _DEVICE_POWERS_ON_HINTS)

    for domain, keys in _FAULT_KEYWORDS.items():
        if domain in ("mic", "sound"):
            continue
        if domain == "no-power" and powers_on:
            continue
        if any(_keyword_matches(k, norm_text) for k in keys):
            found.append(domain)

    # Ensure charging-fast also carries the parent charging domain when relevant
    if "charging-fast" in found and "charging" not in found:
        found.append("charging")

    if not found:
        found.append("general")
    return list(dict.fromkeys(found))


def _extract_device_tokens(norm_text: str, explicit_device: str | None) -> tuple[str, str]:
    source = normalize_fa(explicit_device).translate(_PERSIAN_DIGITS) if explicit_device else norm_text
    raw_tokens = [t for t in re.split(r"[^\w\.\-]+", source) if t]

    brand = ""
    model_tokens: list[str] = []

    for idx, tok in enumerate(raw_tokens):
        if tok in _BRAND_CANONICAL:
            if not brand:
                brand = _BRAND_CANONICAL[tok]
            if tok in _SUBBRAND_KEEP_IN_MODEL:
                mapped = _PERSIAN_MODEL_MAP.get(tok, tok)
                model_tokens.append(mapped)
            continue
        if tok in _MODEL_STOP_TOKENS and not re.match(r"^(?:sm-|m\d{4}|a\d{4}|820-|f\d|x\d|a\d{2}|s\d{2})", tok):
            continue
        # Strip standalone generic error codes and bench measurements (0.7, 9v, 2.5a, 0.6, 5v)
        if tok in {"4013", "4014", "9008"} or tok.startswith("0x"):
            continue
        if not explicit_device:
            if re.match(r"^\d+\.\d+[a-z]*$", tok, re.I):
                continue
            if re.match(r"^\d+(?:v|mv|a|ma|w|r|k|ohm|uf|nf|pf)$", tok, re.I):
                continue
            # Bare single/double digit not immediately following a known model word (like Note 10, iPhone 13)
            if re.match(r"^\d{1,2}$", tok) and (
                not model_tokens
                or model_tokens[-1].lower() not in {"note", "iphone", "ipad", "pixel", "galaxy", "mi", "redmi", "poco", "oneplus", "mate", "nova"}
            ):
                continue
        mapped = _PERSIAN_MODEL_MAP.get(tok, tok)
        if not any("\u0600" <= ch <= "\u06ff" for ch in mapped):
            model_tokens.append(mapped)

    # Deduplicate consecutive duplicates
    deduped: list[str] = []
    for tok in model_tokens:
        if not deduped or deduped[-1].lower() != tok.lower():
            deduped.append(tok)

    def _fmt(tok: str) -> str:
        if re.match(r"^[a-z]\d+$", tok, re.I) or tok.lower() in {"gt", "fe", "se", "xr", "xs", "xl", "5g", "4g", "lte", "nfc"}:
            return tok.upper()
        if re.match(r"^(?:sm-|m\d{4}|a\d{4})", tok, re.I):
            return tok.upper()
        if tok.lower() == "iphone":
            return "iPhone"
        if tok.lower() == "ipad":
            return "iPad"
        return tok.capitalize()

    model_family = " ".join(_fmt(t) for t in deduped).strip()
    if not brand and model_family:
        low_m = model_family.lower()
        if low_m.startswith(("poco", "redmi", "mi ")):
            brand = "Xiaomi"
        elif low_m.startswith("galaxy"):
            brand = "Samsung"
        elif low_m.startswith(("iphone", "ipad")):
            brand = "Apple"
        elif low_m.startswith("pixel"):
            brand = "Google"

    if not brand:
        brand = "Generic"
    if not model_family:
        model_family = "Unknown Device"

    return brand, model_family


def _symptom_labels(faults: list[str], explicit_symptom: str | None) -> tuple[str, str]:
    en_parts: list[str] = []
    fa_parts: list[str] = []
    mapping = {
        "audio-mic-combined": ("no sound and microphone not working", "قطعی همزمان صدا و میکروفون"),
        "audio-mic": ("microphone not working", "از کار افتادن میکروفون"),
        "audio-sound": ("no sound / speaker audio failure", "قطعی صدا و اسپیکر"),
        "no-power": ("no power / dead phone", "خاموشی کامل / روشن نشدن"),
        "charging-fast": ("fast charging drops / slow charging", "افت فست‌شارژ / شارژ کند"),
        "charging": ("not charging / USB port failure", "مشکل شارژ نشدن"),
        "usb-data": ("USB data / PC not recognizing device", "شناخته نشدن در کامپیوتر / عیب مسیر دیتا USB"),
        "boot-loop": ("bootloop / stuck on logo", "بوت لوپ / ماندن روی لوگو"),
        "display-backlight": ("no display / dead backlight", "نداشتن تصویر / خرابی بک‌لایت"),
        "touch": ("touchscreen not responding", "از کار افتادن تاچ"),
        "network-baseband": ("no service / unknown baseband", "نداشتن آنتن / خرابی بیس‌باند"),
        "wifi-bt": ("Wi-Fi / Bluetooth failure", "از کار افتادن وای‌فای و بلوتوث"),
        "camera": ("camera failure", "خرابی دوربین"),
        "water-damage": ("liquid / water damage corrosion", "آبخوردگی و سولفاته برد"),
        "battery": ("battery drain / swollen pack", "خرابی یا بادکردگی باتری"),
        "sensors": ("proximity / fingerprint sensor failure", "خرابی سنسور"),
        "overheating": ("board overheating", "داغ کردن برد"),
        "storage-ufs-emmc": ("UFS / eMMC / NAND storage fault", "خرابی حافظه UFS/eMMC"),
        "frp-unlock": ("FRP / bootloader recovery", "ریکاوری قفل یا بوت‌لودر"),
    }
    for f in faults:
        if f in mapping:
            en, fa = mapping[f]
            if f in ("audio-mic", "audio-sound") and "audio-mic-combined" in faults:
                continue
            en_parts.append(en)
            fa_parts.append(fa)
    if explicit_symptom and not en_parts:
        en_parts.append(explicit_symptom.strip())
    if not en_parts:
        en_parts.append("hardware diagnostic fault")
    if not fa_parts:
        fa_parts.append("ایراد سخت‌افزاری برد")
    return ", ".join(en_parts), "، ".join(fa_parts)


def parse_query(
    raw_query: str,
    explicit_device: str | None = None,
    explicit_symptom: str | None = None,
) -> ParsedQuery:
    raw_norm = normalize_fa(raw_query).translate(_PERSIAN_DIGITS)
    has_persian = any("\u0600" <= ch <= "\u06ff" for ch in raw_query)
    language = "fa" if has_persian else "en"

    fault_input = normalize_fa(explicit_symptom).translate(_PERSIAN_DIGITS) if explicit_symptom else raw_norm
    faults = _detect_faults(fault_input)
    error_codes = _detect_error_codes(raw_query)

    brand, model_family = _extract_device_tokens(raw_norm, explicit_device)

    hint = _DEVICE_HINTS.get(model_family.lower())
    siblings: list[str] = []
    codename: str | None = None
    board_identifier: str | None = None
    aliases: list[str] = []
    wikipedia_titles: list[str] = [f"{brand} {model_family}".strip(), model_family]

    if hint:
        brand = hint["brand"]
        model_family = hint["model_family"]
        siblings = list(hint.get("siblings", []))
        codename = hint.get("codename")
        board_identifier = hint.get("board_identifier")
        aliases = list(hint.get("aliases", []))
        wikipedia_titles = list(hint.get("wikipedia_titles", wikipedia_titles))

    slug = slugify(f"{brand} {model_family}")
    platform = "iOS" if brand == "Apple" else "Android"
    sym_en, sym_fa = _symptom_labels(faults, explicit_symptom)

    # Multi-angle search plan tailored to device + sibling boards + fault domain
    full_name = f"{brand} {model_family}".strip() if brand != "Generic" else model_family
    short_name = model_family

    web_queries: list[str] = []
    yt_queries: list[str] = []
    ifixit_queries: list[str] = [full_name, short_name]

    if "audio-mic-combined" in faults or "audio-mic" in faults or "audio-sound" in faults:
        web_queries.extend([
            f'"{short_name}" no sound mic not working repair fix',
            f'"{short_name}" audio codec IC WCD OR WSA OR PMIC OR CPU reball',
            f'"{short_name}" microphone speaker problem reddit OR xdaforums OR ifixit OR gsmhosting',
        ])
        for sib in siblings[:2]:
            web_queries.append(f'"{sib}" no sound no mic audio IC CPU reball repair')
        if board_identifier:
            web_queries.append(f'"{board_identifier}" audio mic no sound schematic')
        yt_queries.extend([
            f"{full_name} no sound mic not working repair",
            f"{short_name} audio ic cpu reball no sound",
            f"{short_name} مشکل صدا و میکروفن تعمیر",
        ])
        for sib in siblings[:1]:
            yt_queries.append(f"{sib} no sound mic repair")
    elif "charging-fast" in faults or "charging" in faults or "usb-data" in faults:
        if "usb-data" in faults:
            web_queries.extend([
                f'"{short_name}" USB not recognized OR "USB_DM" OR "D-" short repair',
                f'"{short_name}" FSA4480 OR U3402 OR USB switch schematic',
            ])
        if "charging-fast" in faults or "charging" in faults:
            web_queries.extend([
                f'"{short_name}" fast charging drops OR slow charging repair solution',
                f'"{short_name}" charging IC PM8150B OR BQ25968 OR BQ25970 OR SMB1390 schematic',
                f'"{short_name}" not charging OVP BOOT_CAP repair.wiki OR ifixit OR reddit OR xdaforums',
            ])
        if board_identifier:
            web_queries.append(f'"{board_identifier}" charging schematic IC')
        for sib in siblings[:1]:
            web_queries.append(f'"{sib}" charging IC fast charge repair')
        yt_queries.extend([
            f"{full_name} fast charging slow charging repair solution",
            f"{short_name} charging ic problem solution",
            f"{short_name} مشکل شارژ فست شارژ تعمیر",
        ])
    elif "no-power" in faults:
        web_queries.extend([
            f'"{full_name}" no power dead repair solution',
            f'"{short_name}" no power PMIC OR CPU reball OR EDL 9008 schematic',
            f'"{short_name}" dead no power reddit OR ifixit OR gsmhosting OR repair.wiki',
        ])
        if board_identifier:
            web_queries.append(f'"{board_identifier}" no power schematic')
        yt_queries.extend([
            f"{full_name} dead no power repair",
            f"{short_name} خاموشی تعمیر برد",
        ])
    else:
        fault_kw = sym_en.split(",")[0]
        web_queries.extend([
            f'"{full_name}" {fault_kw} repair fix',
            f'"{short_name}" {fault_kw} schematic IC board repair',
            f'"{short_name}" {fault_kw} reddit OR xdaforums OR ifixit OR gsmhosting',
        ])
        for sib in siblings[:1]:
            web_queries.append(f'"{sib}" {fault_kw} repair')
        yt_queries.extend([
            f"{full_name} {fault_kw} repair",
            f"{short_name} {sym_fa} تعمیر",
        ])

    if error_codes:
        web_queries.insert(0, f'"{short_name}" "{error_codes[0]}" fix repair')
        yt_queries.insert(0, f"{full_name} {error_codes[0]} fix")

    # Deduplicate while preserving order
    web_queries = list(dict.fromkeys(web_queries))
    yt_queries = list(dict.fromkeys(yt_queries))
    ifixit_queries = list(dict.fromkeys(ifixit_queries))

    return ParsedQuery(
        raw_query=raw_query,
        brand=brand,
        model_family=model_family,
        slug=slug,
        platform=platform,
        language=language,
        fault_domains=faults,
        error_codes=error_codes,
        siblings=siblings,
        codename=codename,
        board_identifier=board_identifier,
        aliases=aliases,
        wikipedia_titles=wikipedia_titles,
        symptom_summary_en=sym_en,
        symptom_summary_fa=sym_fa,
        search_queries_web=web_queries,
        search_queries_youtube=yt_queries,
        search_queries_ifixit=ifixit_queries,
    )
