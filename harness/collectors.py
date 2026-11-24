"""Live multi-source evidence collectors for the FixIndex diagnostic harness."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "crawlers"))
from common import FixIndexClient, PIIScrubber, classify_topic, matches_device, normalize_fa  # noqa: E402
from query_parser import ParsedQuery  # noqa: E402

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 FixIndexResearch/2.0"

_PREFERRED_DOMAINS = (
    "ifixit.com",
    "repair.wiki",
    "deviceinfohw.ru",
    "techinsights.com",
    "cpschematicx.com",
    "reddit.com",
    "xdaforums.com",
    "gsmhosting.com",
    "badcaps.net",
    "northridgefix.com",
    "gsmarena.com",
    "hovatek.com",
    "xiaomi.eu",
    "c.mi.com",
    "new.c.mi.com",
    "en.wikipedia.org",
    "electroniclinic.com",
    "mobilecellphonerepairing.com",
    "onsemi.com",
    "ti.com",
)

_BLOCKED_DOMAINS = (
    "amazon.",
    "ebay.",
    "aliexpress.",
    "alibaba.",
    "pinterest.",
    "tiktok.",
    "facebook.com",
    "instagram.com",
    "duckduckgo.com",
    "bing.com",
    "google.com",
)

_REPAIR_INTENT = [
    "repair", "fix", "solved", "solution", "problem", "issue", "not working",
    "no power", "dead", "boot", "frp", "bypass", "flash", "unlock", "screen",
    "battery", "water", "charging", "motherboard", "jumper", "reball",
    "audio", "mic", "microphone", "speaker", "sound", "no sound", "no audio",
    "codec", "wcd", "wsa", "pmic", "backlight", "baseband", "no service",
    "wifi", "diode", "schematic", "short", "ic", "cpu", "flex", "subboard",
    "fast charge", "slow charging", "turbo", "ovp", "boot_cap", "bq25968",
    "bq25970", "smb1390", "fsa4480", "pm8150b", "usb_dm", "usb_dp",
    "تعمیر", "مشکل", "حل شد", "خاموش", "شارژ", "فست شارژ", "توربو", "بوت", "فلش", "باتری",
    "صدا", "میکروفن", "میکروفون", "اسپیکر", "کدک", "سی پی یو", "ریبال",
    "تصویر", "بک لایت", "تاچ", "وای فای", "آنتن", "بیس باند", "دوربین", "آی سی",
]
_NOT_REPAIR = [
    " vs ", "versus", "freeze test", "frozen test", "speed test", "unboxing",
    "camera test", "gaming test", "pubg", "genshin", "benchmarks", "antutu",
]


@dataclass
class EvidenceItem:
    id: str
    source_type: str  # wikipedia, ifixit, web, youtube
    url: str
    title: str
    publisher: str
    snippet: str
    full_text: str
    fetched_full_page: bool = False
    retrieved_at: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    extra: dict = field(default_factory=dict)


@dataclass
class EvidenceBundle:
    parsed: ParsedQuery
    fixindex_snapshot: dict | None = None
    items: list[EvidenceItem] = field(default_factory=list)
    youtube_videos: list[dict] = field(default_factory=list)
    wiki_specs: dict = field(default_factory=dict)
    ifixit_guides: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _device_dict(parsed: ParsedQuery) -> dict:
    keywords = [parsed.model_family, *parsed.siblings, *parsed.aliases]
    if parsed.codename:
        keywords.append(parsed.codename)
    return {
        "brand": parsed.brand,
        "model_family": parsed.model_family,
        "aliases": parsed.aliases,
        "keywords": keywords,
    }


def _make_ev_id(prefix: str, idx: int, url: str) -> str:
    host = urlparse(url).netloc.replace("www.", "").split(".")[0]
    host = re.sub(r"[^a-z0-9]+", "-", host.lower()).strip("-") or "src"
    return f"{prefix}-{host}-{idx}"[:70]


def check_fixindex(client: FixIndexClient | None, parsed: ParsedQuery) -> dict | None:
    if not client:
        return None
    return client.diagnose(parsed.raw_query, device=parsed.slug, brand=parsed.brand if parsed.brand != "Generic" else None)


def collect_wikipedia(parsed: ParsedQuery, scrubber: PIIScrubber) -> tuple[list[EvidenceItem], dict]:
    items: list[EvidenceItem] = []
    specs: dict = {}
    session = requests.Session()
    session.headers.update({"User-Agent": UA})

    titles_to_try = list(parsed.wikipedia_titles)
    # Also run opensearch so unlisted devices still find their Wikipedia page.
    try:
        r = session.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "opensearch", "search": f"{parsed.brand} {parsed.model_family}", "limit": 3, "format": "json"},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and len(data) > 1:
                for title in data[1]:
                    if title not in titles_to_try:
                        titles_to_try.append(title)
    except Exception:  # noqa: BLE001
        pass

    dev_dict = _device_dict(parsed)
    seen_urls: set[str] = set()
    for title in titles_to_try[:4]:
        try:
            r = session.get(
                "https://en.wikipedia.org/w/api.php",
                params={
                    "action": "query",
                    "prop": "extracts|info",
                    "inprop": "url",
                    "explaintext": 1,
                    "titles": title,
                    "redirects": 1,
                    "format": "json",
                },
                timeout=12,
            )
            if r.status_code != 200:
                continue
            pages = (r.json().get("query") or {}).get("pages") or {}
            for page in pages.values():
                if page.get("missing") is not None or not page.get("extract"):
                    continue
                url = page.get("fullurl") or f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"
                if url in seen_urls:
                    continue
                extract = scrubber.scrub(str(page["extract"]))
                page_title = scrubber.scrub(str(page.get("title", title)))
                if not matches_device(f"{page_title} {extract[:1500]}", dev_dict):
                    continue
                seen_urls.add(url)
                items.append(EvidenceItem(
                    id=_make_ev_id("wiki", len(items) + 1, url),
                    source_type="wikipedia",
                    url=url,
                    title=page_title,
                    publisher="Wikipedia",
                    snippet=extract[:600],
                    full_text=extract[:6000],
                    fetched_full_page=True,
                ))
                specs = _parse_wiki_specs(extract, specs, model_family=parsed.model_family)
                # Also parse Wikipedia infobox HTML for exact chipset, release year, battery, display
                try:
                    html_resp = session.get(
                        "https://en.wikipedia.org/w/api.php",
                        params={"action": "parse", "page": page_title, "prop": "text", "section": 0, "format": "json"},
                        timeout=12,
                    )
                    if html_resp.status_code == 200:
                        html_txt = ((html_resp.json().get("parse") or {}).get("text") or {}).get("*") or ""
                        if html_txt:
                            specs = _parse_wiki_infobox(html_txt, specs, model_family=parsed.model_family)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            continue

    return items, specs


def _extract_variant_value(value: str, model_family: str) -> str:
    """When a Wikipedia infobox cell lists multiple models (e.g. 'X3 NFC: Snapdragon 732G X3 Pro: Snapdragon 860'),
    prefer the segment matching the target model_family."""
    if not model_family or len(value) < 25:
        return value
    # Look for a variant tag like 'Pro:' or full model_family followed by ':'
    tokens = model_family.split()
    variant_hint = tokens[-1] if len(tokens) >= 2 else model_family
    for pat in (rf"{re.escape(model_family)}\s*[:\-]\s*([^;\n]+)", rf"\b{re.escape(variant_hint)}\s*[:\-]\s*([^;\n]+)"):
        m = re.search(pat, value, flags=re.I)
        if m:
            seg = m.group(1).strip()
            if len(seg) >= 4:
                return seg
    return value


def _parse_wiki_infobox(html: str, existing: dict, model_family: str = "") -> dict:
    specs = dict(existing)
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="infobox")
    if not table:
        return specs
    for row in table.find_all("tr"):
        th = row.find("th")
        td = row.find("td")
        if not th or not td:
            continue
        k = normalize_fa(th.get_text(" ", strip=True))
        v = re.sub(r"\[\d+\]", "", td.get_text(" ", strip=True)).strip()
        if not v:
            continue
        v_var = _extract_variant_value(v, model_family)
        if "system on chip" in k or "chipset" in k or "soc" == k:
            specs["cpu"] = v_var[:180]
        elif "memory" in k or k == "ram":
            specs.setdefault("ram", v_var[:120])
        elif "storage" in k:
            specs.setdefault("storage", v_var[:160])
        elif "battery" in k:
            specs.setdefault("battery", v_var[:160])
        elif "display" in k:
            specs.setdefault("display", v_var[:180])
        elif "codename" in k:
            specs.setdefault("codename", v_var[:80])
        elif "model" in k or "related" in k:
            specs.setdefault("models_note", v[:200])
        elif "released" in k or "first released" in k:
            m = re.search(r"\b(20\d{2})\b", v_var)
            if m and "release_year" not in specs:
                specs["release_year"] = int(m.group(1))
    return specs


def _parse_wiki_specs(extract: str, existing: dict, model_family: str = "") -> dict:
    specs = dict(existing)
    if "release_year" not in specs:
        m = re.search(r"\b(201\d|202\d)\b", extract[:1200])
        if m:
            specs["release_year"] = int(m.group(1))
    if "cpu" not in specs:
        # Prefer a sentence that mentions the exact model_family (avoids shared-page sibling SoC)
        search_region = extract
        if model_family:
            m_pos = re.search(re.escape(model_family), extract, flags=re.I)
            if m_pos:
                start = max(0, m_pos.start() - 100)
                search_region = extract[start : m_pos.end() + 600] + "\n" + extract
        m = re.search(r"(Snapdragon\s+[A-Za-z0-9\+]+|Exynos\s+[A-Za-z0-9]+|MediaTek\s+[A-Za-z0-9\s]+|Apple\s+A\d+\s*Bionic|Dimensity\s+\d+)", search_region)
        if m:
            specs["cpu"] = m.group(1).strip()
    return specs


def collect_ifixit(parsed: ParsedQuery, scrubber: PIIScrubber) -> tuple[list[EvidenceItem], list[dict]]:
    items: list[EvidenceItem] = []
    guides: list[dict] = []
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "application/json"})
    dev_dict = _device_dict(parsed)
    seen_urls: set[str] = set()

    for q in parsed.search_queries_ifixit[:2]:
        try:
            r = session.get(f"https://www.ifixit.com/api/2.0/search/{requests.utils.quote(q)}", params={"limit": 8}, timeout=12)
            if r.status_code != 200:
                continue
            results = r.json().get("results") or []
            for res in results:
                title = scrubber.scrub(str(res.get("title") or res.get("display_title") or ""))
                url = str(res.get("url") or "")
                if not url or url in seen_urls:
                    continue
                if not matches_device(f"{title} {url}", dev_dict):
                    continue
                seen_urls.add(url)
                summary = scrubber.scrub(str(res.get("summary") or ""))
                full_text = f"{title}\n{summary}"
                guide_id = res.get("guideid")
                data_type = res.get("dataType")
                if guide_id and data_type == "guide":
                    try:
                        gr = session.get(f"https://www.ifixit.com/api/2.0/guides/{guide_id}", timeout=12)
                        if gr.status_code == 200:
                            gd = gr.json()
                            parts = [p.get("text") for p in (gd.get("parts") or []) if p.get("text")]
                            tools = [t.get("text") for t in (gd.get("tools") or []) if t.get("text")]
                            step_lines = []
                            for st in (gd.get("steps") or [])[:12]:
                                for ln in (st.get("lines") or []):
                                    txt = ln.get("text_raw") or ""
                                    if txt:
                                        step_lines.append(txt)
                            guides.append({
                                "title": title,
                                "url": url,
                                "difficulty": gd.get("difficulty"),
                                "time_required": gd.get("time_required"),
                                "parts": parts[:10],
                                "tools": tools[:10],
                            })
                            full_text = scrubber.scrub("\n".join([
                                title,
                                str(gd.get("conclusion_raw") or ""),
                                "Tools: " + ", ".join(tools),
                                "Parts: " + ", ".join(parts),
                                *step_lines,
                            ]))
                    except Exception:  # noqa: BLE001
                        pass

                items.append(EvidenceItem(
                    id=_make_ev_id("ifixit", len(items) + 1, url),
                    source_type="ifixit",
                    url=url,
                    title=title[:200],
                    publisher="iFixit",
                    snippet=full_text[:600],
                    full_text=full_text[:6000],
                    fetched_full_page=True,
                ))
        except Exception:  # noqa: BLE001
            continue

    return items, guides


def _normalize_search_hit(url: str, title: str, snippet: str) -> dict | None:
    """Normalize and filter a raw search hit across all search backends."""
    href = (url or "").strip()
    if "uddg=" in href:
        qs = parse_qs(urlparse(href).query)
        if "uddg" in qs:
            href = unquote(qs["uddg"][0])
    if not href.startswith("http"):
        return None
    host = urlparse(href).netloc.lower()
    if any(b in host for b in _BLOCKED_DOMAINS):
        return None
    return {
        "url": href,
        "title": (title or "").strip(),
        "snippet": (snippet or "").strip(),
        "host": host,
    }


def _parse_ddg_html(html: str, limit: int = 8) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict] = []
    for div in soup.select(".result"):
        a = div.select_one("a.result__a")
        if not a:
            continue
        snip_el = div.select_one(".result__snippet")
        hit = _normalize_search_hit(
            str(a.get("href") or ""),
            a.get_text(" ", strip=True),
            snip_el.get_text(" ", strip=True) if snip_el else "",
        )
        if hit:
            results.append(hit)
        if len(results) >= limit:
            break
    return results


def _api_search(query: str, limit: int = 8) -> list[dict]:
    """Query configured search APIs (Tavily, Brave, Serper, or SearXNG)."""
    tavily_key = os.getenv("TAVILY_API_KEY", "").strip()
    if tavily_key:
        try:
            r = requests.post(
                "https://api.tavily.com/search",
                json={"api_key": tavily_key, "query": query, "max_results": limit},
                timeout=15,
            )
            if r.status_code == 200:
                hits = [
                    _normalize_search_hit(item.get("url", ""), item.get("title", ""), item.get("content", ""))
                    for item in (r.json().get("results") or [])
                ]
                return [h for h in hits if h][:limit]
        except Exception:  # noqa: BLE001
            pass

    brave_key = os.getenv("BRAVE_API_KEY", "").strip()
    if brave_key:
        try:
            r = requests.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": limit},
                headers={"Accept": "application/json", "X-Subscription-Token": brave_key},
                timeout=15,
            )
            if r.status_code == 200:
                hits = [
                    _normalize_search_hit(item.get("url", ""), item.get("title", ""), item.get("description", ""))
                    for item in ((r.json().get("web") or {}).get("results") or [])
                ]
                return [h for h in hits if h][:limit]
        except Exception:  # noqa: BLE001
            pass

    serper_key = os.getenv("SERPER_API_KEY", "").strip()
    if serper_key:
        try:
            r = requests.post(
                "https://google.serper.dev/search",
                json={"q": query, "num": limit},
                headers={"X-API-KEY": serper_key, "Content-Type": "application/json"},
                timeout=15,
            )
            if r.status_code == 200:
                hits = [
                    _normalize_search_hit(item.get("link", ""), item.get("title", ""), item.get("snippet", ""))
                    for item in (r.json().get("organic") or [])
                ]
                return [h for h in hits if h][:limit]
        except Exception:  # noqa: BLE001
            pass

    searxng_url = os.getenv("SEARXNG_URL", "").strip().rstrip("/")
    if searxng_url:
        try:
            r = requests.get(
                f"{searxng_url}/search",
                params={"q": query, "format": "json"},
                timeout=15,
            )
            if r.status_code == 200:
                hits = [
                    _normalize_search_hit(item.get("url", ""), item.get("title", ""), item.get("content", ""))
                    for item in (r.json().get("results") or [])
                ]
                return [h for h in hits if h][:limit]
        except Exception:  # noqa: BLE001
            pass

    return []


def _playwright_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


def _playwright_fetch_html(url: str, timeout_ms: int = 18000) -> str:
    """Fetch rendered page HTML via Playwright Chromium when available."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ""

    headless = os.getenv("FIXINDEX_PLAYWRIGHT_HEADLESS", "1").strip() != "0"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(user_agent=UA, locale="en-US")
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(1200)
            html = page.content()
            browser.close()
            return html
    except Exception:  # noqa: BLE001
        return ""


def _playwright_search(query: str, limit: int = 8) -> tuple[list[dict], bool]:
    """Run web search in a real Playwright Chromium session. Returns (hits, captcha_blocked)."""
    url = f"https://duckduckgo.com/?q={quote_plus(query)}&ia=web"
    html = _playwright_fetch_html(url)
    if not html:
        return [], False

    lower = html.lower()
    if "anomaly-modal" in lower or "bots use duckduckgo" in lower or "captcha" in lower:
        return [], True

    soup = BeautifulSoup(html, "html.parser")
    results: list[dict] = []
    for article in soup.select('article[data-testid="result"], .result'):
        a = article.select_one('a[data-testid="result-title-a"], a.result__a')
        if not a:
            continue
        snip_el = article.select_one('[data-result="snippet"], .result__snippet')
        hit = _normalize_search_hit(
            str(a.get("href") or ""),
            a.get_text(" ", strip=True),
            snip_el.get_text(" ", strip=True) if snip_el else "",
        )
        if hit:
            results.append(hit)
        if len(results) >= limit:
            break

    if not results:
        results = _parse_ddg_html(html, limit=limit)
    return results, False


def _ddg_search(query: str, limit: int = 8) -> list[dict]:
    headers = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
    try:
        r = requests.get("https://html.duckduckgo.com/html/", params={"q": query}, headers=headers, timeout=15)
        if r.status_code != 200:
            return []
        return _parse_ddg_html(r.text, limit=limit)
    except Exception:  # noqa: BLE001
        return []


def _web_search(query: str, limit: int = 8) -> tuple[list[dict], str | None]:
    """Select search backend via FIXINDEX_SEARCH_BACKEND (auto|api|playwright|ddg)."""
    backend = os.getenv("FIXINDEX_SEARCH_BACKEND", "auto").strip().lower()

    if backend in ("auto", "api"):
        api_hits = _api_search(query, limit=limit)
        if api_hits or backend == "api":
            return api_hits, None

    if backend in ("auto", "playwright") and _playwright_available():
        pw_hits, blocked = _playwright_search(query, limit=limit)
        if blocked:
            return [], "Playwright search encountered a CAPTCHA/anti-bot challenge; skipping query."
        if pw_hits or backend == "playwright":
            return pw_hits, None

    return _ddg_search(query, limit=limit), None


def _extract_html_text(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form", "noscript"]):
        tag.decompose()
    h1 = soup.find("h1") or soup.find("title")
    title = h1.get_text(" ", strip=True) if h1 else ""
    root = soup.find("article") or soup.find("main") or soup.body or soup
    seen: set[str] = set()
    chunks: list[str] = []
    for el in root.find_all(["h1", "h2", "h3", "p", "li", "pre", "code", "tr"]):
        txt = el.get_text(" ", strip=True)
        if len(txt) > 20 and txt not in seen:
            seen.add(txt)
            chunks.append(txt)
    return title[:200], "\n".join(chunks)[:7000]


def _fetch_page_text(url: str) -> tuple[bool, str, str]:
    backend = os.getenv("FIXINDEX_SEARCH_BACKEND", "auto").strip().lower()
    if backend == "playwright" and _playwright_available():
        html = _playwright_fetch_html(url)
        if html:
            title, body = _extract_html_text(html)
            if body:
                return True, title, body

    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=12)
        if r.status_code in (403, 503) and backend in ("auto", "playwright") and _playwright_available():
            html = _playwright_fetch_html(url)
            if html:
                title, body = _extract_html_text(html)
                if body:
                    return True, title, body
        if r.status_code != 200:
            return False, "", ""
        ctype = r.headers.get("Content-Type", "")
        if "html" not in ctype and "text" not in ctype:
            return False, "", ""
        title, body = _extract_html_text(r.text)
        return True, title, body
    except Exception:  # noqa: BLE001
        return False, "", ""


def collect_web_research(parsed: ParsedQuery, scrubber: PIIScrubber, max_pages: int = 8) -> list[EvidenceItem]:
    dev_dict = _device_dict(parsed)
    candidates: dict[str, dict] = {}

    for query in parsed.search_queries_web:
        hits, _warning = _web_search(query, limit=8)
        for hit in hits:
            url = hit["url"]
            if url in candidates:
                continue
            combined = f"{hit['title']} {hit['snippet']}"
            if not matches_device(combined, dev_dict):
                continue
            if scrubber.is_unsafe(combined):
                continue
            domain_rank = 2 if any(d in hit["host"] for d in _PREFERRED_DOMAINS) else 0
            intent_rank = 1 if any(normalize_fa(k) in normalize_fa(combined) for k in _REPAIR_INTENT) else 0
            hit["rank"] = domain_rank + intent_rank
            candidates[url] = hit
        time.sleep(0.4)

    ranked = sorted(candidates.values(), key=lambda x: x["rank"], reverse=True)[: max_pages + 4]
    items: list[EvidenceItem] = []

    def _process(hit: dict) -> EvidenceItem | None:
        url = hit["url"]
        ok, page_title, page_text = _fetch_page_text(url)
        title = scrubber.scrub(page_title or hit["title"])
        snippet = scrubber.scrub(hit["snippet"])
        body = scrubber.scrub(page_text if ok and len(page_text) > len(snippet) else snippet)
        if scrubber.is_unsafe(body) or scrubber.is_unsafe(title):
            return None
        if not body and not snippet:
            return None
        host = urlparse(url).netloc.replace("www.", "")
        return EvidenceItem(
            id="",  # assigned sequentially after gathering
            source_type="web",
            url=url,
            title=title[:200] or host,
            publisher=host[:120],
            snippet=(snippet or body)[:600],
            full_text=body[:7000],
            fetched_full_page=ok and len(page_text) > 150,
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_process, h) for h in ranked]
        for fut in as_completed(futures):
            item = fut.result()
            if item is not None:
                items.append(item)

    items.sort(key=lambda x: (1 if x.fetched_full_page else 0, len(x.full_text)), reverse=True)
    kept = items[:max_pages]
    for i, item in enumerate(kept, start=1):
        item.id = _make_ev_id("web", i, item.url)
    return kept


def _yt_search(query: str, n: int) -> list[dict]:
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--flat-playlist", "--dump-single-json",
        "--playlist-items", f"1-{n}",
        "--no-warnings", "--quiet",
        f"ytsearch{n}:{query}",
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
        if out.returncode != 0:
            return []
        return (json.loads(out.stdout or "{}")).get("entries") or []
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return []


def _yt_details(url: str) -> dict | None:
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--dump-json", "--skip-download", "--no-warnings", "--quiet", url,
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=35)
        if out.returncode == 0 and out.stdout:
            return json.loads(out.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass
    return None


def collect_youtube(parsed: ParsedQuery, scrubber: PIIScrubber, max_videos: int = 8) -> tuple[list[EvidenceItem], list[dict]]:
    dev_dict = _device_dict(parsed)
    raw_hits: dict[str, dict] = {}

    for query in parsed.search_queries_youtube[:4]:
        for entry in _yt_search(query, 6):
            vid = entry.get("id")
            if not vid or vid in raw_hits:
                continue
            title = str(entry.get("title") or "")
            t_norm = normalize_fa(title)
            if not matches_device(title, dev_dict):
                continue
            if not any(normalize_fa(k) in t_norm for k in _REPAIR_INTENT):
                continue
            if any(bad in t_norm for bad in _NOT_REPAIR):
                continue
            if scrubber.is_unsafe(title):
                continue
            raw_hits[vid] = {"entry": entry, "query": query}

    # Enrich top candidates with full description/duration/channel
    selected = list(raw_hits.items())[:max_videos]
    items: list[EvidenceItem] = []
    videos: list[dict] = []

    def _fetch(idx: int, vid: str, info: dict) -> tuple[dict, dict | None]:
        url = f"https://www.youtube.com/watch?v={vid}"
        details = _yt_details(url) if idx < 6 else None
        return {"vid": vid, "url": url, **info}, details

    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = [pool.submit(_fetch, idx, vid, info) for idx, (vid, info) in enumerate(selected)]
        for fut in as_completed(futs):
            meta, details = fut.result()
            entry = meta["entry"]
            vid = meta["vid"]
            url = meta["url"]
            title = scrubber.scrub(str((details or entry).get("title") or ""))
            desc = scrubber.scrub(str((details or {}).get("description") or ""))
            duration = (details or entry).get("duration")
            views = (details or entry).get("view_count")
            channel = (details or entry).get("channel") or (details or entry).get("uploader")
            if duration is not None and duration < 45:
                continue
            if scrubber.is_unsafe(desc):
                continue

            lang = "fa" if any("\u0600" <= ch <= "\u06ff" for ch in title) else "en"
            videos.append({
                "url": url,
                "title": title[:200],
                "kind": "youtube",
                "video_id": vid,
                "channel": (str(channel)[:120] if channel else None),
                "duration_seconds": int(duration) if duration else None,
                "views": int(views) if views is not None else None,
                "thumbnail_url": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                "topic": classify_topic(f"{title} {desc[:500]}"),
                "language": lang,
                "shows": f"Search match: {meta['query']}"[:900],
            })
            safe_vid = re.sub(r"[^a-z0-9-]+", "-", vid.lower()).strip("-") or "video"
            items.append(EvidenceItem(
                id=f"yt-{safe_vid}"[:70],
                source_type="youtube",
                url=url,
                title=title[:200],
                publisher=f"YouTube · {channel or 'repair channel'}"[:120],
                snippet=(desc or title)[:600],
                full_text=f"{title}\n{desc}"[:4000],
                fetched_full_page=bool(desc),
            ))

    return items, videos


def gather_all_evidence(
    parsed: ParsedQuery,
    client: FixIndexClient | None = None,
    *,
    max_pages: int = 8,
    skip_youtube: bool = False,
) -> EvidenceBundle:
    scrubber = PIIScrubber()
    bundle = EvidenceBundle(parsed=parsed)
    bundle.fixindex_snapshot = check_fixindex(client, parsed)

    with ThreadPoolExecutor(max_workers=4) as pool:
        f_wiki = pool.submit(collect_wikipedia, parsed, scrubber)
        f_ifixit = pool.submit(collect_ifixit, parsed, scrubber)
        f_web = pool.submit(collect_web_research, parsed, scrubber, max_pages)
        f_yt = None if skip_youtube else pool.submit(collect_youtube, parsed, scrubber, 8)

        try:
            wiki_items, wiki_specs = f_wiki.result()
            bundle.items.extend(wiki_items)
            bundle.wiki_specs = wiki_specs
        except Exception as exc:  # noqa: BLE001
            bundle.warnings.append(f"Wikipedia collector warning: {exc}")

        try:
            ifx_items, ifx_guides = f_ifixit.result()
            bundle.items.extend(ifx_items)
            bundle.ifixit_guides = ifx_guides
        except Exception as exc:  # noqa: BLE001
            bundle.warnings.append(f"iFixit collector warning: {exc}")

        try:
            web_items = f_web.result()
            bundle.items.extend(web_items)
        except Exception as exc:  # noqa: BLE001
            bundle.warnings.append(f"Web collector warning: {exc}")

        if f_yt is not None:
            try:
                yt_items, yt_videos = f_yt.result()
                bundle.items.extend(yt_items)
                bundle.youtube_videos = yt_videos
            except Exception as exc:  # noqa: BLE001
                bundle.warnings.append(f"YouTube collector warning: {exc}")

    return bundle
